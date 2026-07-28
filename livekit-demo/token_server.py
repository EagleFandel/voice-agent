"""Token server + static host for the LiveKit voice-agent demo frontend.

Serves:
  GET /              -> web/index.html
  GET /token?room=R&identity=I  -> LiveKit access token (dev keys)
  GET /health        -> {"ok": bool, "livekit": "up"/"down"} (7880 端口连通性)
  GET /vendor/*      -> locally vendored JS (livekit-client UMD)

Run (from livekit-demo/):
    ./venv/Scripts/python.exe token_server.py
"""

import os
import re
import socket
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from livekit import api

# 密钥从环境变量读，缺省回退到 livekit-server --dev 的占位密钥
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "secret")

LIVEKIT_HOST = os.environ.get("LIVEKIT_HOST", "127.0.0.1")
LIVEKIT_PORT = int(os.environ.get("LIVEKIT_PORT", "7880"))

# 浏览器实际连接的 LiveKit 地址（生产经 Caddy 反代为 wss://域名/rtc）
PUBLIC_LIVEKIT_URL = os.environ.get("PUBLIC_LIVEKIT_URL", "ws://localhost:7880")

WEB_DIR = Path(__file__).parent / "web"

# room / identity 只允许字母数字下划线连字符，1~64 位
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

app = FastAPI(title="LiveKit Voice Agent Demo")


def _livekit_up() -> bool:
    """本机 7880 端口可连即认为 livekit-server 在跑。"""
    try:
        with socket.create_connection((LIVEKIT_HOST, LIVEKIT_PORT), timeout=2):
            return True
    except OSError:
        return False


@app.get("/health")
def health():
    up = _livekit_up()
    return JSONResponse({"ok": up, "livekit": "up" if up else "down"})


@app.get("/token")
def token(room: str = Query("voice-demo"), identity: str = Query("user")):
    if not _NAME_RE.match(room) or not _NAME_RE.match(identity):
        return JSONResponse(
            {"detail": "room/identity 只能含字母数字下划线连字符，长度 1~64"},
            status_code=422,
        )
    t = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .with_ttl(timedelta(hours=1))
        .to_jwt()
    )
    return JSONResponse({"token": t, "url": PUBLIC_LIVEKIT_URL})


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=WEB_DIR), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8899)
