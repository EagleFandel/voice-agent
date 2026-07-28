# -*- coding: utf-8 -*-
"""In-container variant of test_client: reads raw 16k s16le PCM from file."""
import asyncio
import base64
import json
import sys
import time

WS_URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8765"
PCM_FILE = sys.argv[2] if len(sys.argv) > 2 else "/tmp/question.pcm"
CHUNK = 640  # 20ms @16k s16 mono


async def main():
    import websockets

    with open(PCM_FILE, "rb") as f:
        pcm = f.read()
    duration = len(pcm) / 2 / 16000
    print(f"[test] audio: {len(pcm)} bytes, {duration:.1f}s -> {WS_URL}", flush=True)

    t0 = time.monotonic()

    def ts():
        return f"{time.monotonic() - t0:7.2f}s"

    async with websockets.connect(WS_URL, max_size=10 * 1024 * 1024) as ws:
        print(f"[{ts()}] connected", flush=True)
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
                            print(f"[{ts()}] >>> FIRST TTS AUDIO", flush=True)
                        tts_audio.extend(base64.b64decode(msg.get("audio", "")))
                    elif mtype == "data":
                        name = msg.get("name")
                        d = msg.get("data", {})
                        if name == "asr_result":
                            print(
                                f"[{ts()}] ASR"
                                f"{'(final)' if d.get('final', d.get('is_final', False)) else ''}"
                                f": {d.get('text', '')}",
                                flush=True,
                            )
                            if d.get("final", d.get("is_final", False)) and events["asr_final"] is None:
                                events["asr_final"] = time.monotonic() - t0
                        elif name == "text_data":
                            role = d.get("role")
                            is_final = d.get("is_final")
                            print(
                                f"[{ts()}] TEXT[{role}]"
                                f"{'(final)' if is_final else ''}: {d.get('text','')}",
                                flush=True,
                            )
                            if role == "user" and is_final:
                                events["asr_final"] = time.monotonic() - t0
                            elif role == "assistant" and events["llm_text"] is None:
                                events["llm_text"] = time.monotonic() - t0
                        elif name in ("llm_response", "chat_message"):
                            text = d.get("text", d.get("content", ""))
                            if text and events["llm_text"] is None:
                                events["llm_text"] = time.monotonic() - t0
                            print(f"[{ts()}] LLM: {text}", flush=True)
                    elif mtype == "cmd":
                        print(f"[{ts()}] CMD: {msg.get('name')}", flush=True)
                    elif mtype == "error":
                        print(f"[{ts()}] ERROR: {msg.get('error')}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[{ts()}] receiver ended: {e}", flush=True)
            finally:
                done.set()

        recv_task = asyncio.create_task(receiver())
        for i in range(0, len(pcm), CHUNK):
            await ws.send(json.dumps({"audio": base64.b64encode(pcm[i : i + CHUNK]).decode()}))
            await asyncio.sleep(0.02)
        t_sent = time.monotonic() - t0
        print(f"[{ts()}] audio sent", flush=True)

        wait_start = time.monotonic()
        while time.monotonic() - wait_start < 60:
            if events["first_audio"] is not None:
                await asyncio.sleep(5)
                break
            if done.is_set():
                break
            await asyncio.sleep(0.2)
        recv_task.cancel()

        print("\n===== RESULT =====", flush=True)
        if events["asr_final"] is not None:
            print(f"ASR final   : +{events['asr_final'] - t_sent:.2f}s after speech end", flush=True)
        else:
            print("ASR final   : NOT RECEIVED", flush=True)
        if events["llm_text"] is not None:
            print(f"LLM text    : +{events['llm_text'] - t_sent:.2f}s after speech end", flush=True)
        else:
            print("LLM text    : NOT RECEIVED", flush=True)
        if events["first_audio"] is not None:
            print(f"TTS audio   : +{events['first_audio'] - t_sent:.2f}s after speech end", flush=True)
            print(f"TTS bytes   : {len(tts_audio)} (~{len(tts_audio)/2/16000:.1f}s)", flush=True)
        else:
            print("TTS audio   : NOT RECEIVED", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
