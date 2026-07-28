"""FastRTC + 阿里百炼 qwen-omni-turbo-realtime 中文语音对话 demo。

基于 fastrtc 官方 demo/qwen_phone_chat 改造:
- 电话接入改为浏览器 Gradio UI (stream.ui.launch)
- API key 读取 DASHSCOPE_API_KEY
- 打开 server_vad 实现自动轮次检测 + 打断

运行:
    set DASHSCOPE_API_KEY=sk-...   # 或从 ../voice-agent/.env 加载
    python qwen_voice_chat.py
然后打开 http://localhost:7860 ,点右上角开始说话。
"""

import asyncio
import base64
import json
import os
import secrets
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from fastrtc import AsyncStreamHandler, Stream, wait_for_item
from websockets.asyncio.client import connect

# 优先读当前目录 .env,其次读 voice-agent 项目的 .env
load_dotenv()
load_dotenv(Path(__file__).parent.parent.parent / "voice-agent" / ".env")

API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
assert API_KEY, "缺少 DASHSCOPE_API_KEY"

API_URL = (
    "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    "?model=qwen3-omni-flash-realtime"
)
HEADERS = {"Authorization": "Bearer " + API_KEY}


class QwenOmniHandler(AsyncStreamHandler):
    def __init__(self) -> None:
        super().__init__(
            expected_layout="mono",
            output_sample_rate=24_000,
            input_sample_rate=16_000,
        )
        self.connection = None
        self.output_queue = asyncio.Queue()

    def copy(self):
        return QwenOmniHandler()

    @staticmethod
    def msg_id() -> str:
        return f"event_{secrets.token_hex(10)}"

    async def start_up(self):
        """建立与百炼 realtime 的 WebSocket 长连接。"""
        async with connect(API_URL, additional_headers=HEADERS) as conn:
            self.connection = conn
            await conn.send(
                json.dumps(
                    {
                        "event_id": self.msg_id(),
                        "type": "session.update",
                        "session": {
                            "modalities": ["text", "audio"],
                            "voice": "Cherry",  # 中文女声
                            "input_audio_format": "pcm16",
                            # 服务端 VAD:自动检测说话开始/结束,支持打断
                            "turn_detection": {"type": "server_vad"},
                        },
                    }
                )
            )
            try:
                async for data in self.connection:
                    event = json.loads(data)
                    if "type" not in event:
                        continue
                    # 用户开始说话 -> 清空播放队列,实现打断
                    if event["type"] == "input_audio_buffer.speech_started":
                        self.clear_queue()
                    if event["type"] == "response.audio.delta":
                        await self.output_queue.put(
                            (
                                self.output_sample_rate,
                                np.frombuffer(
                                    base64.b64decode(event["delta"]), dtype=np.int16
                                ).reshape(1, -1),
                            ),
                        )
            except Exception as e:
                print("connection closed:", e)

    async def receive(self, frame: tuple[int, np.ndarray]) -> None:
        if not self.connection:
            return
        _, array = frame
        array = array.squeeze()
        audio_message = base64.b64encode(array.tobytes()).decode("utf-8")
        try:
            await self.connection.send(
                json.dumps(
                    {
                        "event_id": self.msg_id(),
                        "type": "input_audio_buffer.append",
                        "audio": audio_message,
                    }
                )
            )
        except Exception as e:
            print("send error:", e)

    async def emit(self):
        return await wait_for_item(self.output_queue)

    async def shutdown(self):
        if self.connection:
            await self.connection.close()
            self.connection = None


stream = Stream(
    QwenOmniHandler(),
    mode="send-receive",
    modality="audio",
)

if __name__ == "__main__":
    stream.ui.launch(server_port=7860)
