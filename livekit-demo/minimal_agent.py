"""LiveKit Agents voice demo using Alibaba DashScope (百炼) realtime model.

框架能力示例（相比最初 minimal 版新增的）：
- 配置外置：模型 / 声音 / 指令 / base_url 全部走环境变量（见下方 CONFIG）
- 工具调用：query_today_tasks（读知识库 daily-tasks.md 今日待办）、
  search_wiki（RAG-lite：百炼 embedding 检索知识库目录）、
  deep_think（双 LLM：把深度问题转发给高推理模型 qwen3-max）
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
import pickle
import re
from datetime import date
from pathlib import Path

import httpx
import numpy as np
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
        "你有三个工具，按场景选用："
        "用户问今天有什么待办、今天要做什么、日程安排时，调用 query_today_tasks；"
        "用户问 wiki、文档、笔记、之前记录的关于某个话题的内容时，调用 search_wiki 检索知识库；"
        "用户问需要深度分析、推理、战略思考的问题（比如说深入想想、分析一下、你怎么看）时，"
        "先口头说一句让我仔细想想，再调用 deep_think。",
    ),
}

# 仓库根目录（本文件位于 project/voice-agent-comparison/livekit-demo/）
REPO_ROOT = Path(__file__).resolve().parents[3]

# 知识库目录：query_today_tasks / search_wiki 从这里读 .md。
# 运营方部署时用 KNOWLEDGE_DIR 环境变量指向资料目录（compose 挂载），
# 本地开发缺省回退到仓库 wiki/。
KNOWLEDGE_DIR = Path(os.getenv("KNOWLEDGE_DIR") or (REPO_ROOT / "wiki"))

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
    section = _read_today_section(KNOWLEDGE_DIR / "daily-tasks.md")
    if not section:
        return "今天的待办记录为空或没有找到对应日期的小节。"
    logger.info("query_today_tasks: 命中今日待办 %d 字", len(section))
    return section


# --- RAG-lite：知识库向量索引（模块级懒加载 + 缓存） -----------------------
# 首次调用 search_wiki 时扫描 KNOWLEDGE_DIR/**/*.md，按小节切块并用百炼
# text-embedding-v3 向量化，结果缓存到 _WIKI_INDEX，避免每次调用重建。
DASHSCOPE_HTTP_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
EMBEDDING_MODEL = "text-embedding-v3"
DEEP_THINK_MODELS = ["qwen3-max", "qwen-plus"]  # 前者 404 时降级后者

# 索引结构：{"chunks": [(relpath, text), ...], "vectors": np.ndarray | None}
# vectors 为 None 表示 embedding 构建失败、只能用关键词兜底。
_WIKI_INDEX: dict | None = None

# 磁盘缓存：embedding 全量构建要 1k+ 次 HTTP 分批请求（分钟级），构建成功后
# 持久化到本文件，之后进程重启只要 wiki 文件没变就直接命中缓存。
_WIKI_INDEX_CACHE = Path(__file__).parent / ".wiki_index_cache.pkl"


def _wiki_manifest() -> list[tuple[str, int, int]]:
    """知识库目录的文件清单 (相对路径, mtime_ns, size)，用于判断缓存是否过期。"""
    manifest: list[tuple[str, int, int]] = []
    for md in sorted(KNOWLEDGE_DIR.rglob("*.md")):
        try:
            st = md.stat()
        except OSError:
            continue
        manifest.append((str(md.relative_to(KNOWLEDGE_DIR)), st.st_mtime_ns, st.st_size))
    return manifest


def _load_cached_index(manifest: list[tuple[str, int, int]]) -> dict | None:
    """manifest 一致时从磁盘缓存恢复索引，否则返回 None。"""
    if not _WIKI_INDEX_CACHE.exists():
        return None
    try:
        with _WIKI_INDEX_CACHE.open("rb") as f:
            payload = pickle.load(f)
        if payload.get("manifest") != manifest or payload.get("vectors") is None:
            return None
        logger.info(
            "search_wiki: 命中磁盘缓存，%d 块向量直接恢复", len(payload["chunks"])
        )
        return {"chunks": payload["chunks"], "vectors": payload["vectors"]}
    except Exception as e:  # noqa: BLE001
        logger.warning("search_wiki: 磁盘缓存读取失败，将重建: %s", e)
        return None


def _save_cached_index(manifest: list[tuple[str, int, int]], index: dict) -> None:
    """把构建成功的索引（含向量）持久化；向量缺失时不写，留给下次重试。"""
    if index["vectors"] is None:
        return
    try:
        payload = {
            "manifest": manifest,
            "chunks": index["chunks"],
            "vectors": index["vectors"],
        }
        tmp = _WIKI_INDEX_CACHE.with_suffix(".tmp")
        with tmp.open("wb") as f:
            pickle.dump(payload, f)
        tmp.replace(_WIKI_INDEX_CACHE)
        logger.info("search_wiki: 索引已写入磁盘缓存 %s", _WIKI_INDEX_CACHE)
    except Exception as e:  # noqa: BLE001
        logger.warning("search_wiki: 磁盘缓存写入失败: %s", e)


def _chunk_markdown(text: str, max_len: int = 500) -> list[str]:
    """按 ## 标题切小节，过长小节再按 ~max_len 字窗口细分。"""
    chunks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        block = "\n".join(current).strip()
        if not block:
            return
        # 超长块按窗口再切（带一点重叠，避免切断语义）
        while len(block) > max_len:
            chunks.append(block[:max_len])
            block = block[max_len - 50 :]
        if block.strip():
            chunks.append(block)

    for line in text.splitlines():
        if re.match(r"^#{1,3}\s", line) and current:
            flush()
            current = [line]
        else:
            current.append(line)
    flush()
    return chunks


def _scan_wiki_chunks() -> list[tuple[str, str]]:
    """扫描知识库目录下所有 .md，返回 (相对路径, 切块文本) 列表。"""
    chunks: list[tuple[str, str]] = []
    for md in sorted(KNOWLEDGE_DIR.rglob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning("search_wiki: 读取 %s 失败: %s", md, e)
            continue
        rel = str(md.relative_to(KNOWLEDGE_DIR))
        for piece in _chunk_markdown(text):
            chunks.append((rel, piece))
    return chunks


async def _embed_texts(texts: list[str], client: httpx.AsyncClient | None = None) -> np.ndarray:
    """调用百炼 embedding，返回 (N, dim) 的 float32 矩阵。失败抛异常由上层兜底。

    参数 client: 外部传入复用连接（构建批量索引时避免每批创建新 TLS 握手）。
    """
    api_key = os.environ["DASHSCOPE_API_KEY"]
    own = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        own = True
    try:
        resp = await client.post(
            f"{DASHSCOPE_HTTP_BASE}/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": EMBEDDING_MODEL, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()
    finally:
        if own:
            await client.aclose()
    # 兼容端点按 index 排序返回，稳妥起见按 index 重排
    items = sorted(data["data"], key=lambda d: d["index"])
    return np.array([d["embedding"] for d in items], dtype=np.float32)


async def _embed_with_retry(
    texts: list[str], client: httpx.AsyncClient | None = None, attempts: int = 3
) -> np.ndarray:
    """带重试的批量 embedding：并发构建时偶发 ConnectTimeout，退避重试。"""
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            return await _embed_texts(texts, client=client)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if i < attempts - 1:
                await asyncio.sleep(2 * (i + 1))
    raise last_err  # type: ignore[misc]


async def _build_wiki_index() -> dict:
    """构建（或重建）wiki 索引；优先命中磁盘缓存，embedding 失败时降级关键词。"""
    manifest = _wiki_manifest()
    cached = _load_cached_index(manifest)
    if cached is not None:
        return cached

    chunks = _scan_wiki_chunks()
    logger.info("search_wiki: 扫描到 %d 个文本块", len(chunks))
    index: dict = {"chunks": chunks, "vectors": None}
    if not chunks:
        return index
    try:
        # 百炼 embedding 单批有上限，分批请求；限流并发以缩短冷启动
        batch = 10
        batches = [
            [c[1] for c in chunks[i : i + batch]] for i in range(0, len(chunks), batch)
        ]
        sem = asyncio.Semaphore(8)
        async with httpx.AsyncClient(timeout=30.0) as _client:

            async def _embed_limited(texts: list[str]) -> np.ndarray:
                async with sem:
                    return await _embed_with_retry(texts, client=_client)

            vectors = await asyncio.gather(*[_embed_limited(t) for t in batches])
            index["vectors"] = np.vstack(vectors)
            logger.info("search_wiki: embedding 索引构建完成，共 %d 块", len(chunks))
            _save_cached_index(manifest, index)
    except Exception as e:  # noqa: BLE001
        logger.warning("search_wiki: embedding 构建失败，降级为关键词匹配: %s", e)
        index["vectors"] = None
    return index


async def _get_wiki_index() -> dict:
    """懒加载 + 缓存索引。"""
    global _WIKI_INDEX
    if _WIKI_INDEX is None:
        _WIKI_INDEX = await _build_wiki_index()
    return _WIKI_INDEX


def _keyword_search(chunks: list[tuple[str, str]], query: str, top_k: int) -> list[tuple[str, str]]:
    """关键词兜底：query 分词后按命中数排序。"""
    tokens = [t for t in re.split(r"\W+", query.lower()) if t]
    scored: list[tuple[int, tuple[str, str]]] = []
    for rel, text in chunks:
        hay = text.lower()
        hits = sum(hay.count(tok) for tok in tokens)
        if hits > 0:
            scored.append((hits, (rel, text)))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]


def _format_snippets(results: list[tuple[str, str]], total_limit: int = 1500) -> str:
    """把 (路径, 文本) 拼成带标注的片段，总长限制 total_limit 字。"""
    out: list[str] = []
    used = 0
    for rel, text in results:
        snippet = f"【来源: {rel}】\n{text}"
        if used + len(snippet) > total_limit:
            remaining = total_limit - used
            if remaining > 50:
                out.append(snippet[:remaining] + "…")
            break
        out.append(snippet)
        used += len(snippet)
    return "\n\n".join(out)


@function_tool
async def search_wiki(query: str) -> str:
    """检索用户的 wiki 知识库（文档/笔记/之前记录的内容）。

    用户问"wiki/文档/笔记/之前记录的关于 X"时使用。query 用简洁的检索词。
    """
    try:
        index = await _get_wiki_index()
    except Exception as e:  # noqa: BLE001
        logger.warning("search_wiki: 索引构建失败: %s", e)
        return "知识库暂不可用。"
    chunks: list[tuple[str, str]] = index["chunks"]
    if not chunks:
        return "知识库暂不可用。"

    results: list[tuple[str, str]] = []
    if index["vectors"] is not None:
        try:
            qvec = await _embed_texts([query])
            mat = index["vectors"]
            # 余弦相似度
            q = qvec[0]
            q_norm = np.linalg.norm(q)
            m_norm = np.linalg.norm(mat, axis=1)
            denom = np.maximum(m_norm * q_norm, 1e-8)
            sims = (mat @ q) / denom
            top_idx = np.argsort(-sims)[:3]
            results = [chunks[i] for i in top_idx]
            logger.info("search_wiki: 向量检索 top-3，最高分 %.3f", float(sims[top_idx[0]]))
        except Exception as e:  # noqa: BLE001
            logger.warning("search_wiki: 查询向量化失败，降级关键词: %s", e)
            results = _keyword_search(chunks, query, 3)
    else:
        results = _keyword_search(chunks, query, 3)

    if not results:
        return "知识库里没有找到相关内容。"
    return _format_snippets(results)


# --- deep_think：双 LLM 演示（快答 realtime + 深思高推理模型） ---------------
@function_tool
async def deep_think(question: str) -> str:
    """把需要深度分析/推理/战略思考的问题转发给高推理模型，返回其回答。

    演示"快答 + 深思"双 LLM 架构：realtime flash 模型负责快对话，本工具把
    深度问题交给 qwen3-max（不存在则降级 qwen-plus）。用户说"深入想想/
    分析一下/你怎么看"等需要深度推理时使用。
    """
    api_key = os.environ["DASHSCOPE_API_KEY"]
    last_err: Exception | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        for model in DEEP_THINK_MODELS:
            try:
                resp = await client.post(
                    f"{DASHSCOPE_HTTP_BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": question}],
                        "extra_body": {"enable_thinking": True},
                    },
                )
                if resp.status_code == 404:
                    logger.warning("deep_think: 模型 %s 不存在(404)，尝试降级", model)
                    last_err = httpx.HTTPStatusError(
                        "404", request=resp.request, response=resp
                    )
                    continue
                resp.raise_for_status()
                data = resp.json()
                answer = data["choices"][0]["message"]["content"] or ""
                answer = answer.strip()
                if not answer:
                    return "深度思考暂时不可用，我先按已有知识回答。"
                logger.info("deep_think: 模型 %s 返回答案 %d 字", model, len(answer))
                if len(answer) > 800:
                    answer = answer[:800] + "…（已截断）"
                return answer
            except Exception as e:  # noqa: BLE001
                logger.warning("deep_think: 模型 %s 调用失败: %s", model, e)
                last_err = e
                continue
    logger.error("deep_think: 所有模型均失败: %s", last_err)
    return "深度思考暂时不可用，我先按已有知识回答。"


class MyAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=CONFIG["instructions"],
            tools=[query_today_tasks, search_wiki, deep_think],
        )

    async def on_enter(self) -> None:
        self.session.generate_reply(instructions="用中文向用户打招呼并简短介绍你自己")


async def entrypoint(ctx: agents.JobContext) -> None:
    # the job runs in a separate process, so load .env here as well
    load_dotenv()

    # 后台预热 wiki 索引：冷启动 build 约 8s，趁 session.start / 用户打招呼的间隙先建好，
    # 确保第一次 search_wiki 调用时秒级返回。提前启动，不阻塞 session.start。
    _wiki_warmup_task: asyncio.Task | None = None

    async def _warmup_wiki() -> None:
        try:
            await _get_wiki_index()
            logger.info("wiki 索引预热完成")
        except Exception as e:  # noqa: BLE001
            logger.warning("wiki 索引预热失败（不影响对话）: %s", e)

    _wiki_warmup_task = asyncio.create_task(_warmup_wiki())

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

    @session.on("error")
    def _on_error(ev) -> None:  # noqa: ANN001
        # 会话级错误兜底：只记日志，不让异常静默吞掉
        err = getattr(ev, "error", ev)
        src = getattr(ev, "source", None)
        logger.error("session error (source=%s): %s", src, err)

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
    logger.info("agent ready: 已进入房间 %s", ctx.room.name)


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
