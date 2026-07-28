# -*- coding: utf-8 -*-
"""
Mock OpenAI-compatible server for TEN Framework websocket-example demo.

Provides:
  POST /v1/chat/completions  - rule-based Chinese LLM replies (SSE streaming)
  POST /v1/audio/speech      - TTS via edge-tts (free), returns raw PCM 16kHz s16le mono

Run:  python mock_server.py  (listens on 0.0.0.0:9000)
No API keys required.
"""

import asyncio
import json
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import edge_tts
import miniaudio

HOST = "0.0.0.0"
PORT = 9000
TTS_VOICE_DEFAULT = "zh-CN-XiaoxiaoNeural"


# ---------------------------------------------------------------- LLM logic

CANNED = [
    (r"你好|您好|hello|hi", "你好呀！很高兴见到你，今天想聊点什么？"),
    (r"名字|你是谁|叫什么", "我是 TEN 框架的本地语音助手，跑在 Docker 里，用的是 mock 大脑。"),
    (r"天气", "我现在没有联网查天气的能力，不过你可以给我接一个天气工具试试。"),
    (r"时间|几点", "我没有实时时钟，但我知道你现在正在测试一个语音对话 demo。"),
    (r"再见|拜拜|bye", "再见！欢迎下次再来测试。"),
    (r"延迟|快不快|速度", "我的语音链路是本地 whisper 识别加 mock 大模型加 edge TTS 合成，整体延迟大概两三秒。"),
    (r"十|TEN|框架", "TEN 是一个实时语音智能体框架，用图的方式把 STT、LLM、TTS 串成一条流水线。"),
]


def make_reply(user_text: str) -> str:
    text = user_text.strip()
    for pattern, reply in CANNED:
        if re.search(pattern, text, re.IGNORECASE):
            return reply
    short = text if len(text) <= 20 else text[:20] + "……"
    return f"我听到你说：{short}。这是一个本地 mock 回复，接上真实大模型后我会回答得更聪明。"


def handle_chat_completions(body: dict, handler: BaseHTTPRequestHandler):
    messages = body.get("messages", [])
    user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_text = m.get("content", "")
            if isinstance(user_text, list):
                user_text = " ".join(
                    p.get("text", "") for p in user_text if isinstance(p, dict)
                )
            break
    reply = make_reply(user_text)
    print(f"[LLM] user: {user_text[:60]} -> {reply[:60]}", flush=True)

    model = body.get("model", "mock-llm")
    comp_id = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())

    if body.get("stream"):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.end_headers()

        def chunk(delta, finish=None):
            payload = {
                "id": comp_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {"index": 0, "delta": delta, "finish_reason": finish}
                ],
            }
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode(
                "utf-8"
            )

        handler.wfile.write(chunk({"role": "assistant"}))
        # stream the reply in small pieces to simulate token streaming
        step = 4
        for i in range(0, len(reply), step):
            handler.wfile.write(chunk({"content": reply[i : i + step]}))
            handler.wfile.flush()
            time.sleep(0.02)
        handler.wfile.write(chunk({}, "stop"))
        handler.wfile.write(b"data: [DONE]\n\n")
        handler.wfile.flush()
    else:
        payload = {
            "id": comp_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": reply},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)


# ---------------------------------------------------------------- TTS logic


async def _synth_mp3(text: str, voice: str) -> bytes:
    communicate = edge_tts.Communicate(text, voice)
    buf = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf)


def tts_pcm_16k(text: str, voice: str) -> bytes:
    """Synthesize text -> raw PCM s16le 16kHz mono bytes."""
    mp3_data = asyncio.run(_synth_mp3(text, voice))
    if not mp3_data:
        return b""
    decoded = miniaudio.decode(mp3_data)  # 24kHz mono s16
    samples = decoded.samples  # array('h')
    src_rate = decoded.sample_rate
    if src_rate == 16000:
        return samples.tobytes()
    # naive linear resample to 16kHz
    import array

    ratio = src_rate / 16000.0
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


def handle_audio_speech(body: dict, handler: BaseHTTPRequestHandler):
    text = body.get("input", "")
    voice = body.get("voice", TTS_VOICE_DEFAULT)
    # openai voice names -> fall back to a zh voice
    if not voice.startswith("zh-") and not voice.startswith("en-"):
        voice = TTS_VOICE_DEFAULT
    print(f"[TTS] '{text[:60]}' voice={voice}", flush=True)
    try:
        pcm = tts_pcm_16k(text, voice)
    except Exception as e:  # noqa: BLE001
        print(f"[TTS] error: {e}", flush=True)
        data = json.dumps({"error": {"message": str(e)}}).encode()
        handler.send_response(500)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return
    handler.send_response(200)
    handler.send_header("Content-Type", "audio/pcm")
    handler.send_header("Content-Length", str(len(pcm)))
    handler.end_headers()
    handler.wfile.write(pcm)
    print(f"[TTS] sent {len(pcm)} bytes pcm", flush=True)


# ---------------------------------------------------------------- HTTP server


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter logs
        pass

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_POST(self):  # noqa: N802
        path = self.path.rstrip("/")
        body = self._read_body()
        if path.endswith("/chat/completions"):
            try:
                handle_chat_completions(body, self)
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif path.endswith("/audio/speech"):
            try:
                handle_audio_speech(body, self)
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") in ("", "/health"):
            data = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Mock OpenAI server listening on http://{HOST}:{PORT}", flush=True)
    print("  POST /v1/chat/completions  (mock LLM, SSE streaming)", flush=True)
    print("  POST /v1/audio/speech      (edge-tts -> pcm 16k)", flush=True)
    server.serve_forever()
