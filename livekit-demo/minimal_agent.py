"""LiveKit Agents minimal voice demo using Alibaba DashScope (百炼) realtime model.

Uses the OpenAI-Realtime-compatible endpoint of DashScope so a single
DASHSCOPE_API_KEY provides STT + LLM + TTS in one speech-to-speech model.

Pinned to livekit-agents/openai-plugin 1.1.7, which speaks the OpenAI Realtime
*beta* event format (flat session fields) that DashScope's compatible endpoint
implements. Newer plugin versions (>=1.2) send the GA format and time out.

Run:
    python minimal_agent.py console      # terminal audio, no LiveKit server needed
    python minimal_agent.py dev          # connect to LiveKit server/cloud
"""

import logging
import os

from dotenv import load_dotenv

from livekit import agents
from livekit.agents import Agent, AgentSession
from livekit.plugins import openai as lk_openai
from livekit.plugins import silero
from livekit.plugins.openai.realtime.realtime_model import RealtimeSession

logger = logging.getLogger("dashscope-voice-agent")

load_dotenv()

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


class MyAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "你是一个中文语音助手，名字叫小超。通过语音与用户交流，"
                "回答简洁口语化，不要使用 emoji、星号、markdown 等特殊字符。"
            )
        )

    async def on_enter(self) -> None:
        self.session.generate_reply(instructions="用中文向用户打招呼并简短介绍你自己")


async def entrypoint(ctx: agents.JobContext) -> None:
    # the job runs in a separate process, so load .env here as well
    load_dotenv()
    session = AgentSession(
        llm=lk_openai.realtime.RealtimeModel(
            model="qwen3-omni-flash-realtime",
            voice="Cherry",
            base_url="https://dashscope.aliyuncs.com/api-ws/v1/realtime",
            api_key=os.environ["DASHSCOPE_API_KEY"],
            # DashScope realtime does not support OpenAI's input_audio_transcription field
            input_audio_transcription=None,
            # default turn_detection is server_vad, which DashScope supports
        ),
        vad=silero.VAD.load(),
    )

    await session.start(agent=MyAgent(), room=ctx.room)


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
