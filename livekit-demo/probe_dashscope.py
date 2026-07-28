"""Probe DashScope's OpenAI-Realtime-compatible endpoint directly.

Connects, prints every server event (audio deltas summarized), sends a
session.update in OpenAI beta format, then a response.create greeting.
"""

import asyncio
import base64
import json
import os

import websockets
from dotenv import load_dotenv

load_dotenv()

URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-omni-flash-realtime"
KEY = os.environ["DASHSCOPE_API_KEY"]


def short(evt: dict) -> str:
    t = evt.get("type", "?")
    if t == "response.audio.delta":
        return f"{t} ({len(evt.get('delta', ''))} b64 chars)"
    return json.dumps(evt, ensure_ascii=False)[:500]


async def main() -> None:
    async with websockets.connect(
        URL,
        additional_headers={"Authorization": f"Bearer {KEY}"},
    ) as ws:
        print("connected")

        async def recv_loop() -> None:
            async for raw in ws:
                evt = json.loads(raw)
                print("<-", short(evt))

        task = asyncio.create_task(recv_loop())

        session_update = {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "voice": "Cherry",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "instructions": "You are a helpful assistant. Speak Chinese.",
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 600,
                },
            },
        }
        print("->", json.dumps(session_update))
        await ws.send(json.dumps(session_update))
        await asyncio.sleep(2)

        response_create = {
            "type": "response.create",
            "response": {
                "modalities": ["text", "audio"],
                "instructions": "用中文简短打个招呼",
            },
        }
        print("->", json.dumps(response_create, ensure_ascii=False))
        await ws.send(json.dumps(response_create, ensure_ascii=False))
        await asyncio.sleep(12)

        task.cancel()
        print("done")


asyncio.run(main())
