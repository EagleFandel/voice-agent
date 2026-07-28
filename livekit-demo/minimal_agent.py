"""LiveKit Agents voice demo using Alibaba DashScope (百炼) realtime model.

框架能力示例（相比最初 minimal 版新增的）：
- 配置外置：模型 / 声音 / 指令 / base_url 全部走环境变量（见下方 CONFIG）
- 工具调用：示例工具 query_today_tasks（读 wiki/daily-tasks.md 今日待办）
- 状态推送：agent 状态(listening/thinking/speaking)经 data channel 发给前端
- 字幕推送：对话文本（能拿到多少推多少）经 data channel 发给前端

Pinned to livekit-agents/openai-plugin 1.1.7, which speaks the OpenAI Realtime
*beta* event format (flat session fields) that DashScope's compatible endpoint
implements. Newer plugin versions (>=1.2) send the GA format and time out.

Run:
    python minimal_agent.py console      # terminal audio, no LiveKit server needed
    python minimal_agent.py dev          # connect to LiveKit server/cloud
"""

import asyncio
import json
import logging
import os
import re
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from livekit import agents
from livekit.agents import Agent, AgentSession
from livekit.agents.llm import function_tool
from livekit.plugins import openai as lk_openai
from livekit.plugins import silero
from livekit.plugins.openai.realtime.realtime_model import RealtimeSession

logger = logging.getLogger("dashscope-voice-agent")

load_dotenv()

# --- 配置（环境变量覆盖，默认值即当前 demo 设置） ----------------------------
CONFIG = {
    "model": os.getenv("AGENT_MODEL", "qwen3-omni-flash-realtime"),
    "voice": os.getenv("AGENT_VOICE", "Cherry"),
    "base_url": os.getenv(
        "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api-ws/v1/realtime"
    ),
    "instructions": os.getenv(
        "AGENT_INSTRUCTIONS",
        "你是一个中文语音助手，名字叫小超。通过语音与用户交流，"
        "回答简洁口语化，不要使用 emoji、星号、markdown 等特殊字符。"
        "用户问今天有什么待办、今天要做什么时，调用 query_today_tasks 工具查询后再回答。",
    ),
}

# 仓库根目录（本文件位于 project/voice-agent-comparison/livekit-demo/）
REPO_ROOT = Path(__file__).resolve().parents[3]

# --- DashScope compatibility patch -----------------------------------------
# DashScope does not echo `response.metadata` back in response.created, so the
# openai plugin cannot correlate client_event_id -> response_id and every
# user-initiated generate_reply() "times out" even though the reply plays fine.
# Fall back to matching the oldest pending handle when metadata is missing.
_orig_handle_response_created = RealtimeSession._handle_response_created


def _patched_handle_response_created(self, event):  # noqa: ANN001, ANN202
    metadata = getattr(event.response, "metadata", None)
    if not (isinstance(metadata, dict) and metadata.get("client_event_id")):
        pending = self._response_created_futures
        for key in list(pending.keys()):
            if key.startswith("response_create_"):
                pending[event.response.id] = pending.pop(key)
                break
    return _orig_handle_response_created(self, event)


RealtimeSession._handle_response_created = _patched_handle_response_created
# ---------------------------------------------------------------------------


def _read_today_section(md_path: Path) -> str:
    """从 daily-tasks.md 中抽出今天日期对应的小节。"""
    if not md_path.exists():
        return ""
    text = md_path.read_text(encoding="utf-8")
    today = date.today()
    # 匹配 "07-28" / "2026-07-28" / "7月28日" 等常见标题写法
    patterns = [
        today.strftime("%Y-%m-%d"),
        today.strftime("%m-%d"),
        f"{today.month}月{today.day}日",
    ]
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("#") and any(p in line for p in patterns):
            start = i
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^#{1,2}\s", lines[j]):
            end = j
            break
    return "\n".join(lines[start:end]).strip()


@function_tool
async def query_today_tasks() -> str:
    """查询用户今天（当日）的待办事项。用户问"今天要做什么/有什么待办/日程安排"时使用。"""
    section = _read_today_section(REPO_ROOT / "wiki" / "daily-tasks.md")
    if not section:
        return "今天的待办记录为空或没有找到对应日期的小节。"
    logger.info("query_today_tasks: 命中今日待办 %d 字", len(section))
    return section


class MyAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=CONFIG["instructions"], tools=[query_today_tasks])

    async def on_enter(self) -> None:
        self.session.generate_reply(instructions="用中文向用户打招呼并简短介绍你自己")


async def entrypoint(ctx: agents.JobContext) -> None:
    # the job runs in a separate process, so load .env here as well
    load_dotenv()
    session = AgentSession(
        llm=lk_openai.realtime.RealtimeModel(
            model=CONFIG["model"],
            voice=CONFIG["voice"],
            base_url=CONFIG["base_url"],
            api_key=os.environ["DASHSCOPE_API_KEY"],
            # DashScope realtime does not support OpenAI's input_audio_transcription field
            input_audio_transcription=None,
            # default turn_detection is server_vad, which DashScope supports
        ),
        vad=silero.VAD.load(),
    )

    # --- 状态 / 字幕经 data channel 推给前端 --------------------------------
    def push(payload: dict) -> None:
        async def _send() -> None:
            try:
                await ctx.room.local_participant.publish_data(
                    json.dumps(payload, ensure_ascii=False),
                    reliable=True,
                    topic="agent",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("publish_data failed: %s", e)

        asyncio.create_task(_send())

    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:  # noqa: ANN001
        push({"type": "agent_state", "state": ev.new_state})

    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:  # noqa: ANN001
        push({"type": "user_state", "state": ev.new_state})

    @session.on("user_input_transcribed")
    def _on_user_transcript(ev) -> None:  # noqa: ANN001
        # 百炼 realtime 不支持 input_audio_transcription，正常不会触发；
        # 换 OpenAI Realtime 后用户字幕会从这里来
        if ev.is_final:
            push({"type": "transcript", "role": "user", "text": ev.transcript})

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:  # noqa: ANN001
        item = ev.item
        role = getattr(item, "role", None)
        if role not in ("user", "assistant"):
            return
        text = None
        if hasattr(item, "text_content"):
            try:
                text = item.text_content
            except Exception:  # noqa: BLE001
                text = None
        if text:
            push({"type": "transcript", "role": role, "text": text})

    await session.start(agent=MyAgent(), room=ctx.room)


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
