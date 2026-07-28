"""FastRTC 真实前端 + LiveKit Agents 后端 桥接 demo。

架构:
    浏览器 (FastRTC Gradio 电话式 UI)
      <-WebRTC-> 本脚本 (AsyncStreamHandler 桥)
        <-WebRTC-> LiveKit Server (Docker :7880)
          -> Agent Worker (livekit-agents 1.1.7 + Silero VAD)
            -> 百炼 qwen3-omni-flash-realtime

每个浏览器连接 copy() 出一个新 handler,start_up 时加入一个全新的
随机房间 -> LiveKit 在房间创建时自动 dispatch Agent Worker。

运行前提:
    1. Docker livekit-server --dev 运行中 (7880)
    2. livekit-demo/minimal_agent.py dev 运行中 (Agent Worker)
    3. livekit-demo/token_server.py 运行中 (8899, 供 token)

运行:
    unset 代理环境变量后
    ./venv/Scripts/python.exe livekit_bridge_chat.py
然后打开 http://localhost:7860 ,点右上角电话图标开始通话。
"""

import asyncio
import secrets

import httpx
import numpy as np
from fastrtc import AsyncStreamHandler, Stream, wait_for_item
from livekit import rtc

TOKEN_SERVER = "http://127.0.0.1:8899/token"
LIVEKIT_URL = "ws://127.0.0.1:7880"  # 避开 localhost 的 IPv6 优先解析问题

IN_RATE = 16_000   # fastrtc 输入(麦克风)采样率
OUT_RATE = 24_000  # 播放给浏览器的采样率
FRAME_SAMPLES = IN_RATE // 100  # 10ms 一帧,LiveKit AudioSource 的标准粒度


class LiveKitBridgeHandler(AsyncStreamHandler):
    """把 FastRTC 前端的音频轨桥进一个 LiveKit 房间。"""

    def __init__(self) -> None:
        super().__init__(
            expected_layout="mono",
            output_sample_rate=OUT_RATE,
            input_sample_rate=IN_RATE,
        )
        self.room: rtc.Room | None = None
        self.source: rtc.AudioSource | None = None
        self.output_queue: asyncio.Queue = asyncio.Queue()
        self._in_buf = np.zeros(0, dtype=np.int16)  # 10ms 切片缓冲
        self._pump_tasks: list[asyncio.Task] = []

    def copy(self) -> "LiveKitBridgeHandler":
        return LiveKitBridgeHandler()

    # ---------- LiveKit 连接 ----------

    async def start_up(self) -> None:
        room_name = "fastrtc-lk-" + secrets.token_hex(3)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                TOKEN_SERVER,
                params={"room": room_name, "identity": "fastrtc-" + secrets.token_hex(2)},
            )
            token = resp.json()["token"]

        room = rtc.Room()
        self.room = room

        @room.on("track_subscribed")
        def on_track_subscribed(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                print(f"[bridge] 订阅 {participant.identity} 的音频轨")
                self._pump_tasks.append(asyncio.ensure_future(self._pump(track)))

        @room.on("participant_connected")
        def on_participant(participant):
            print(f"[bridge] 参与者加入: {participant.identity}")

        await room.connect(LIVEKIT_URL, token)
        print(f"[bridge] 已加入房间 {room_name},等待 Agent dispatch…")

        # 本地麦克风轨 -> 房间(Agent 侧 Silero VAD 负责轮次/打断)
        self.source = rtc.AudioSource(sample_rate=IN_RATE, num_channels=1)
        mic_track = rtc.LocalAudioTrack.create_audio_track("mic", self.source)
        await room.local_participant.publish_track(mic_track)

    async def _pump(self, track) -> None:
        """Agent 音频轨 -> 播放队列(重采样到 24kHz 单声道)。"""
        stream = rtc.AudioStream(track, sample_rate=OUT_RATE, num_channels=1)
        async for event in stream:
            data = bytes(event.frame.data)
            if not data:
                continue
            arr = np.frombuffer(data, dtype=np.int16).reshape(1, -1)
            await self.output_queue.put((OUT_RATE, arr))

    # ---------- fastrtc 接口 ----------

    async def receive(self, frame: tuple[int, np.ndarray]) -> None:
        """浏览器麦克风音频 -> LiveKit 房间。"""
        if not self.source:
            return
        _, array = frame
        samples = array.squeeze().astype(np.int16)
        self._in_buf = np.concatenate([self._in_buf, samples])
        # 切成 10ms 标准帧推入 AudioSource(capture_frame 自带实时 pacing)
        while len(self._in_buf) >= FRAME_SAMPLES:
            chunk, self._in_buf = self._in_buf[:FRAME_SAMPLES], self._in_buf[FRAME_SAMPLES:]
            await self.source.capture_frame(
                rtc.AudioFrame(
                    data=chunk.tobytes(),
                    sample_rate=IN_RATE,
                    num_channels=1,
                    samples_per_channel=FRAME_SAMPLES,
                )
            )

    async def emit(self):
        """播放队列 -> 浏览器。"""
        return await wait_for_item(self.output_queue)

    async def shutdown(self) -> None:
        for t in self._pump_tasks:
            t.cancel()
        self._pump_tasks.clear()
        if self.room:
            await self.room.disconnect()
            self.room = None
        self.source = None


SUBTITLE_HTML = """
<div style="max-width: 760px; margin: 0 auto;">
  <div style="display:flex; flex-wrap:wrap; gap:6px; justify-content:center; margin-bottom:10px;">
    <span style="border:1px solid #c7d2fe; background:#eef2ff; border-radius:6px; padding:3px 9px; font-size:12px;">框架 <b style="color:#2563eb;">LiveKit Agents 1.1.7</b></span>
    <span style="border:1px solid #c7d2fe; background:#eef2ff; border-radius:6px; padding:3px 9px; font-size:12px;">传输 <b style="color:#2563eb;">WebRTC ×2（浏览器↔桥↔LiveKit Server）</b></span>
    <span style="border:1px solid #c7d2fe; background:#eef2ff; border-radius:6px; padding:3px 9px; font-size:12px;">模型 <b style="color:#2563eb;">百炼 qwen3-omni-flash-realtime</b></span>
    <span style="border:1px solid #c7d2fe; background:#eef2ff; border-radius:6px; padding:3px 9px; font-size:12px;">VAD <b style="color:#2563eb;">Silero（本地）</b></span>
    <span style="border:1px solid #c7d2fe; background:#eef2ff; border-radius:6px; padding:3px 9px; font-size:12px;">打断 <b style="color:#2563eb;">barge-in</b></span>
    <span style="border:1px solid #c7d2fe; background:#eef2ff; border-radius:6px; padding:3px 9px; font-size:12px;">实测首音频延迟 <b style="color:#2563eb;">~514ms</b></span>
    <span style="border:1px solid #c7d2fe; background:#eef2ff; border-radius:6px; padding:3px 9px; font-size:12px;">前端 <b style="color:#2563eb;">FastRTC 电话式 UI</b></span>
  </div>
  <div style="font-size:12px; color:#6b7280; line-height:1.7;">
    链路：浏览器 → WebRTC → 桥接服务 → LiveKit Server（Docker 本地）→ Agent Worker（livekit-agents + Silero VAD）→ 百炼 Realtime<br>
    玩法：点电话图标开始通话，授权麦克风后直接说话；助手说话时可插话打断。
  </div>
</div>
"""

stream = Stream(
    LiveKitBridgeHandler(),
    mode="send-receive",
    modality="audio",
    ui_args={
        "title": "超脑 · LiveKit Agents 语音助手 Demo",
        "subtitle": SUBTITLE_HTML,
        "pulse_color": "#2563eb",
        "icon_button_color": "#2563eb",
    },
)

if __name__ == "__main__":
    # 浏览器标签页标题（Gradio 默认显示 "Gradio"）
    stream.ui.title = "超脑语音助手 · LiveKit Agents"
    stream.ui.launch(server_port=7860)
