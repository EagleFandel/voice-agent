"""Token server + static host for the LiveKit voice-agent demo frontend.

Serves:
  GET /              -> web/index.html
  GET /token?room=R&identity=I  -> LiveKit access token (dev keys)
  GET /vendor/*      -> locally vendored JS (livekit-client UMD)

Run (from livekit-demo/):
    ./venv/Scripts/python.exe token_server.py
"""

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from livekit import api

LIVEKIT_API_KEY = "devkey"
LIVEKIT_API_SECRET = "secret"  # livekit-server --dev placeholder keys

WEB_DIR = Path(__file__).parent / "web"

app = FastAPI(title="LiveKit Voice Agent Demo")


@app.get("/token")
def token(room: str = Query("voice-demo"), identity: str = Query("user")):
    t = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .to_jwt()
    )
    return JSONResponse({"token": t, "url": "ws://localhost:7880"})


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=WEB_DIR), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8899)
