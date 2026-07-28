"""Probe DashScope OpenAI-compatible chat endpoint via LiveKit's openai LLM plugin.

This validates the pipeline-mode (STT -> LLM -> TTS) "high-reasoning" leg:
any strong text LLM served over an OpenAI-compatible API can be swapped in.
"""

import asyncio
import os
import time

from dotenv import load_dotenv

from livekit.agents import llm as lk_llm
from livekit.plugins import openai as lk_openai

load_dotenv()


async def main() -> None:
    llm = lk_openai.LLM(
        model="qwen-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key=os.environ["DASHSCOPE_API_KEY"],
    )
    t0 = time.time()
    first = None
    chunks = []
    async with llm.chat(
        chat_ctx=lk_llm.ChatContext(
            [
                lk_llm.ChatMessage(
                    role="user", content=["用一句话介绍杭州，不超过30字"]
                )
            ]
        )
    ) as stream:
        async for chunk in stream:
            if first is None:
                first = time.time() - t0
            if chunk.delta and chunk.delta.content:
                chunks.append(chunk.delta.content)
    total = time.time() - t0
    print(f"first_token={first:.2f}s total={total:.2f}s")
    print("reply:", "".join(chunks))


asyncio.run(main())
