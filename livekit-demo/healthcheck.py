"""LiveKit 全链路体检脚本（probe_room.py 升级版）。

逐项检查并打印体检报告（√/×）：
  a. docker 容器 livekit-server 在跑
  b. 7880 端口可连
  c. http://127.0.0.1:8899/health 返回 ok
  d. http://127.0.0.1:7860/ 返回 200（FastRTC 桥；不在只算 WARN 不算 FAIL）
  e. 用 livekit SDK 加随机房间，25 秒内收到 agent_state data channel 消息

两个 venv 都能跑（都装有 livekit + httpx）：
    livekit-demo/venv/Scripts/python.exe healthcheck.py
    ../fastrtc-demo/venv/Scripts/python.exe healthcheck.py
"""

import asyncio
import json
import secrets
import socket
import subprocess

import httpx
from livekit import rtc

TOKEN_URL = "http://127.0.0.1:8899/token"
HEALTH_URL = "http://127.0.0.1:8899/health"
BRIDGE_URL = "http://127.0.0.1:7860/"
LIVEKIT_URL = "ws://127.0.0.1:7880"
LIVEKIT_HOST = "127.0.0.1"
LIVEKIT_PORT = 7880

# 每项结果: (标记, 名称, 说明)  标记 ∈ {"PASS", "WARN", "FAIL"}
results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))


def check_docker() -> None:
    try:
        out = subprocess.run(
            ["docker", "ps", "--filter", "name=livekit-server", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        record("FAIL", "docker 容器 livekit-server", f"docker 命令不可用: {e}")
        return
    if "livekit-server" in out.stdout:
        record("PASS", "docker 容器 livekit-server", "运行中")
    else:
        record("FAIL", "docker 容器 livekit-server", "未在运行（docker start livekit-server）")


def check_port() -> None:
    try:
        with socket.create_connection((LIVEKIT_HOST, LIVEKIT_PORT), timeout=3):
            record("PASS", "7880 端口可连", "livekit-server 端口正常")
    except OSError as e:
        record("FAIL", "7880 端口可连", f"连不上: {e}")


async def check_health() -> None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(HEALTH_URL)
        data = r.json()
        if r.status_code == 200 and data.get("ok"):
            record("PASS", "token server /health", f"ok, livekit={data.get('livekit')}")
        else:
            record("FAIL", "token server /health", f"status={r.status_code} body={data}")
    except Exception as e:  # noqa: BLE001
        record("FAIL", "token server /health", f"请求失败: {e}")


async def check_bridge() -> None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(BRIDGE_URL)
        if r.status_code == 200:
            record("PASS", "FastRTC 桥 (:7860)", "返回 200")
        else:
            record("WARN", "FastRTC 桥 (:7860)", f"status={r.status_code}（桥未起不影响主链路）")
    except Exception as e:  # noqa: BLE001
        record("WARN", "FastRTC 桥 (:7860)", f"连不上（可选组件）: {e}")


async def check_agent_join() -> None:
    room_name = "healthcheck-" + secrets.token_hex(3)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            token = (
                await client.get(TOKEN_URL, params={"room": room_name, "identity": "probe"})
            ).json()["token"]
    except Exception as e:  # noqa: BLE001
        record("FAIL", "agent 进房 + 状态推送", f"取 token 失败: {e}")
        return

    room = rtc.Room()
    got: list[dict] = []

    @room.on("data_received")
    def on_data(packet) -> None:  # noqa: ANN001
        try:
            got.append(json.loads(packet.data))
        except Exception:  # noqa: BLE001
            pass

    try:
        await room.connect(LIVEKIT_URL, token)
    except Exception as e:  # noqa: BLE001
        record("FAIL", "agent 进房 + 状态推送", f"连房间失败: {e}")
        return

    for _ in range(25):
        await asyncio.sleep(1)
        if any(m.get("type") == "agent_state" for m in got):
            break

    states = [m["state"] for m in got if m.get("type") == "agent_state"]
    await room.disconnect()

    if states:
        record("PASS", "agent 进房 + 状态推送", f"agent_state 序列: {states}")
    else:
        record("FAIL", "agent 进房 + 状态推送", "25 秒内未收到任何 agent_state")


async def main() -> None:
    print("=" * 56)
    print(" LiveKit 语音 Demo 全链路体检")
    print("=" * 56)

    check_docker()
    check_port()
    await check_health()
    await check_bridge()
    await check_agent_join()

    mark = {"PASS": "√", "WARN": "!", "FAIL": "×"}
    print()
    for status, name, detail in results:
        line = f"  [{mark[status]}] {name}"
        if detail:
            line += f"  —— {detail}"
        print(line)

    fails = sum(1 for s, _, _ in results if s == "FAIL")
    warns = sum(1 for s, _, _ in results if s == "WARN")
    print()
    print("-" * 56)
    overall = "PASS" if fails == 0 else "FAIL"
    print(f" 总体: {overall}  （FAIL {fails} 项 / WARN {warns} 项 / 共 {len(results)} 项）")
    print("-" * 56)
    raise SystemExit(0 if fails == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
