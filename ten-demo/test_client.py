# -*- coding: utf-8 -*-
"""
End-to-end test client for TEN websocket-example.

1. Synthesizes a Chinese question with edge-tts (16k PCM).
2. Streams it to ws://localhost:8765 in 20ms chunks (real-time paced).
3. Logs ASR results / LLM responses / TTS audio arrival and measures latency.

Usage: python test_client.py [ws_url] [text]
"""

import asyncio
import base64
import json
import sys
import time

import edge_tts
import miniaudio

WS_URL = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8765"
TEXT = sys.argv[2] if len(sys.argv) > 2 else "你好，你叫什么名字？"
CHUNK = 640  # 20ms of 16kHz s16le mono


async def synth_pcm(text: str) -> bytes:
    communicate = edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural")
    buf = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    decoded = miniaudio.decode(bytes(buf))
    samples = decoded.samples
    if decoded.sample_rate == 16000:
        return samples.tobytes()
    import array

    ratio = decoded.sample_rate / 16000.0
    out_len = int(len(samples) / ratio)
    out = array.array("h", bytes(2 * out_len))
    n = len(samples)
    for i in range(out_len):
        pos = i * ratio
        i0 = int(pos)
        i1 = min(i0 + 1, n - 1)
        frac = pos - i0
        out[i] = int(samples[i0] * (1 - frac) + samples[i1] * frac)
    return out.tobytes()


async def main():
    import websockets

    print(f"[test] synthesizing question: {TEXT}")
    pcm = await synth_pcm(TEXT)
    duration = len(pcm) / 2 / 16000
    print(f"[test] audio: {len(pcm)} bytes, {duration:.1f}s")

    t0 = time.monotonic()

    def ts():
        return f"{time.monotonic() - t0:7.2f}s"

    async with websockets.connect(WS_URL, max_size=10 * 1024 * 1024) as ws:
        print(f"[{ts()}] connected to {WS_URL}")

        tts_audio = bytearray()
        events = {"first_audio": None, "asr_final": None, "llm_text": None}
        done = asyncio.Event()

        async def receiver():
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    mtype = msg.get("type")
                    if mtype == "audio":
                        if events["first_audio"] is None:
                            events["first_audio"] = time.monotonic() - t0
                            print(f"[{ts()}] >>> FIRST TTS AUDIO")
                        data = base64.b64decode(msg.get("audio", ""))
                        tts_audio.extend(data)
                    elif mtype == "data":
                        name = msg.get("name")
                        d = msg.get("data", {})
                        if name == "asr_result":
                            text = d.get("text", "")
                            is_final = d.get("final", d.get("is_final", False))
                            print(
                                f"[{ts()}] ASR{'(final)' if is_final else ''}: {text}"
                            )
                            if is_final and events["asr_final"] is None:
                                events["asr_final"] = time.monotonic() - t0
                        elif name == "text_data":
                            print(
                                f"[{ts()}] TEXT[{d.get('role')}]"
                                f"{'(final)' if d.get('is_final') else ''}:"
                                f" {d.get('text','')}"
                            )
                        elif name in ("llm_response", "chat_message"):
                            text = d.get("text", d.get("content", ""))
                            if text and events["llm_text"] is None:
                                events["llm_text"] = time.monotonic() - t0
                            print(f"[{ts()}] LLM: {text}")
                    elif mtype == "cmd":
                        print(f"[{ts()}] CMD: {msg.get('name')}")
                    elif mtype == "error":
                        print(f"[{ts()}] ERROR: {msg.get('error')}")
            except Exception as e:  # noqa: BLE001
                print(f"[{ts()}] receiver ended: {e}")
            finally:
                done.set()

        recv_task = asyncio.create_task(receiver())

        # stream audio, real-time paced
        print(f"[{ts()}] streaming audio...")
        for i in range(0, len(pcm), CHUNK):
            chunk = pcm[i : i + CHUNK]
            await ws.send(json.dumps({"audio": base64.b64encode(chunk).decode()}))
            await asyncio.sleep(0.02)
        t_sent = time.monotonic() - t0
        print(f"[{ts()}] audio sent ({duration:.1f}s of speech)")

        # wait for response (max 60s)
        wait_start = time.monotonic()
        while time.monotonic() - wait_start < 60:
            if events["first_audio"] is not None:
                # keep collecting a bit more audio after first chunk
                await asyncio.sleep(5)
                break
            if done.is_set():
                break
            await asyncio.sleep(0.2)

        recv_task.cancel()

        print("\n===== RESULT =====")
        print(f"question           : {TEXT}")
        if events["asr_final"] is not None:
            print(
                f"ASR final latency  : {events['asr_final'] - t_sent:.2f}s "
                f"after end of speech"
            )
        else:
            print("ASR final          : NOT RECEIVED")
        if events["llm_text"] is not None:
            print(
                f"LLM first text     : {events['llm_text'] - t_sent:.2f}s "
                f"after end of speech"
            )
        else:
            print("LLM text           : NOT RECEIVED")
        if events["first_audio"] is not None:
            print(
                f"TTS first audio    : {events['first_audio'] - t_sent:.2f}s "
                f"after end of speech"
            )
            print(f"TTS audio received : {len(tts_audio)} bytes "
                  f"(~{len(tts_audio)/2/16000:.1f}s)")
        else:
            print("TTS audio          : NOT RECEIVED")


if __name__ == "__main__":
    asyncio.run(main())
