"""无麦克风探针：以普通参与者身份加入一个新房间，验证：
1. 房间创建触发 agent dispatch
2. agent 携带 tools 的 session.update 不被百炼拒绝（看 data channel 是否有正常状态/字幕推送）
3. agent_state / transcript 推送是否到达前端通道

运行（fastrtc-demo 的 venv 里有 livekit SDK）：
    ../fastrtc-demo/venv/Scripts/python.exe probe_room.py
"""

import asyncio
import json

import httpx
from livekit import rtc

TOKEN_SERVER = "http://127.0.0.1:8899/token"
LIVEKIT_URL = "ws://127.0.0.1:7880"


async def main() -> None:
    import secrets

    room_name = "probe-" + secrets.token_hex(3)
    async with httpx.AsyncClient(timeout=10) as client:
        token = (
            await client.get(TOKEN_SERVER, params={"room": room_name, "identity": "probe"})
        ).json()["token"]

    room = rtc.Room()
    got: list[dict] = []

    @room.on("data_received")
    def on_data(packet) -> None:  # noqa: ANN001
        try:
            got.append(json.loads(packet.data))
        except Exception:  # noqa: BLE001
            pass

    @room.on("participant_connected")
    def on_participant(p) -> None:  # noqa: ANN001
        print(f"[probe] 参与者加入: {p.identity}")

    await room.connect(LIVEKIT_URL, token)
    print(f"[probe] 已加入 {room_name}，等待 agent（最多 25 秒）…")

    for _ in range(25):
        await asyncio.sleep(1)
        if any(m.get("type") == "agent_state" and m.get("state") == "speaking" for m in got):
            break

    states = [m["state"] for m in got if m.get("type") == "agent_state"]
    transcripts = [m for m in got if m.get("type") == "transcript"]
    print(f"[probe] agent_state 序列: {states}")
    print(f"[probe] transcript 消息数: {len(transcripts)}")
    for t in transcripts[:3]:
        print(f"  [{t['role']}] {t['text'][:80]}")

    ok = bool(states)
    print("[probe] " + ("PASS：agent 已进房且状态推送正常" if ok else "FAIL：未收到任何 agent 状态"))
    await room.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
