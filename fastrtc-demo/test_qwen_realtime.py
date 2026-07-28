"""端到端验证百炼 qwen-omni-turbo-realtime:不依赖浏览器/麦克风。

流程:读一段 wav -> 按 realtime 协议分块发送 -> 收 response.audio.delta
-> 保存回复音频到 qwen_response.pcm (24kHz 16bit mono)。
同时记录首包延迟和转写文本。
"""

import asyncio
import base64
import json
import os
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from dotenv import load_dotenv
from websockets.asyncio.client import connect

load_dotenv()
load_dotenv(Path(__file__).parent.parent.parent / "voice-agent" / ".env")

API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
assert API_KEY, "缺少 DASHSCOPE_API_KEY"

API_URL = (
    "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    "?model=qwen3-omni-flash-realtime"
)
WAV = (
    Path(__file__).parent
    / "fastrtc/backend/fastrtc/speech_to_text/test_file.wav"
)


def msg_id() -> str:
    return f"event_{os.urandom(10).hex()}"


async def main():
    audio, sr = sf.read(str(WAV), dtype="int16")
    print(f"input wav: {sr}Hz, {len(audio)/sr:.1f}s")
    if sr != 16000:
        import librosa

        audio = librosa.resample(
            audio.astype(np.float32) / 32768.0, orig_sr=sr, target_sr=16000
        )
        audio = (audio * 32768.0).astype(np.int16)
        sr = 16000
        print(f"resampled to 16kHz, {len(audio)/sr:.1f}s")
    # 末尾补 1.5s 静音,让服务端 VAD 能检测到说话结束
    audio = np.concatenate([audio, np.zeros(int(sr * 1.5), dtype=np.int16)])

    t0 = time.time()
    async with connect(
        API_URL, additional_headers={"Authorization": "Bearer " + API_KEY}
    ) as conn:
        await conn.send(
            json.dumps(
                {
                    "event_id": msg_id(),
                    "type": "session.update",
                    "session": {
                        "modalities": ["text", "audio"],
                        "voice": "Cherry",
                        "input_audio_format": "pcm16",
                        "turn_detection": {"type": "server_vad"},
                        "input_audio_transcription": {"model": "gummy-realtime-v1"},
                    },
                }
            )
        )

        # 按 100ms 一块模拟实时流
        chunk = 1600
        for i in range(0, len(audio), chunk):
            payload = base64.b64encode(audio[i : i + chunk].tobytes()).decode()
            await conn.send(
                json.dumps(
                    {
                        "event_id": msg_id(),
                        "type": "input_audio_buffer.append",
                        "audio": payload,
                    }
                )
            )
            await asyncio.sleep(0.02)
        print(f"audio sent at {time.time()-t0:.2f}s, waiting for response...")

        audio_out = bytearray()
        first_audio_at = None
        transcripts = []
        try:
            async with asyncio.timeout(60):
                async for data in conn:
                    event = json.loads(data)
                    etype = event.get("type", "")
                    if etype in (
                        "error",
                        "input_audio_buffer.speech_started",
                        "input_audio_buffer.speech_stopped",
                        "conversation.item.input_audio_transcription.completed",
                        "response.audio_transcript.done",
                        "response.done",
                    ):
                        print(f"[{time.time()-t0:6.2f}s] {etype}",
                              event.get("transcript", event.get("message", "")))
                    if etype == "conversation.item.input_audio_transcription.completed":
                        transcripts.append("USER: " + event.get("transcript", ""))
                    if etype == "response.audio_transcript.done":
                        transcripts.append("ASSISTANT: " + event.get("transcript", ""))
                    if etype == "response.audio.delta":
                        if first_audio_at is None:
                            first_audio_at = time.time() - t0
                            print(f"[{first_audio_at:6.2f}s] first audio delta")
                        audio_out.extend(base64.b64decode(event["delta"]))
                    if etype == "response.done":
                        break
                    if etype == "error":
                        break
        except TimeoutError:
            print("TIMEOUT waiting for response")

        print("\n--- transcripts ---")
        for t in transcripts:
            print(t)
        if audio_out:
            out = np.frombuffer(bytes(audio_out), dtype=np.int16)
            sf.write("qwen_response.wav", out, 24000)
            print(f"\nsaved qwen_response.wav ({len(out)/24000:.1f}s @ 24kHz)")
        if first_audio_at:
            print(f"first-audio latency: {first_audio_at:.2f}s from connect")


asyncio.run(main())
