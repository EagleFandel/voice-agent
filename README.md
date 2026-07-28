# 超脑语音助手 Demo（LiveKit 全链路）

> 给 Michael / 演示用：双击 `start.bat`，等 10 秒，打开浏览器即可语音通话。

## 这是什么

「编排框架 + Realtime 一体模型」路线的可运行 demo：浏览器语音 → WebRTC → 本地 LiveKit Server → Agent Worker（livekit-agents + Silero VAD）→ 阿里百炼 qwen3-omni-flash-realtime（STT+LLM+TTS 一体）。支持全双工对话、插话打断（barge-in），实测首音频延迟 ~514ms。

## 快速开始

**前置条件**（仅首次）：
1. 安装并启动 Docker Desktop
2. 本仓库两个 Python 环境已就绪（`livekit-demo/venv`、`fastrtc-demo/venv`，已随机器配好）

**启动**：双击 `start.bat` —— 自动拉起 4 个组件（各自一个最小化窗口）。

**停止**：双击 `stop.bat`。

## 打开哪个页面

| 页面 | 地址 | 说明 |
|---|---|---|
| 架构图 + 能力分析 | http://localhost:8899/architecture.html | SVG 架构图、能力范围、优劣势、与 5 个开源框架的对比结论 |
| Demo A · 脉冲球前端 | http://localhost:8899 | 手写前端，圆球随音量呼吸，含技术徽章 |
| Demo B · 电话式前端 | http://localhost:7860 | FastRTC 原版 UI 接入 LiveKit 链路 |

两个前端任选其一，授权麦克风后直接说话；助手说话时可以插话测试打断。

## 组件清单

| 组件 | 进程 | 端口 |
|---|---|---|
| LiveKit Server（SFU） | Docker 容器 `livekit-server`（--dev --node-ip 127.0.0.1） | 7880 |
| Agent Worker | `livekit-demo/minimal_agent.py dev` | — |
| Token + 静态页 | `livekit-demo/token_server.py` | 8899 |
| FastRTC 桥 + 前端 | `fastrtc-demo/livekit_bridge_chat.py` | 7860 |

## 常见问题

- **页面打不开**：确认 4 个最小化窗口都在；缺哪个就到对应目录手动跑一条命令看报错。
- **连不上 / 提示 pc connection**：确认 Docker 容器是用 `--node-ip 127.0.0.1` 起的（start.bat 已处理）。
- **Agent 不加入房间**：LiveKit 只在房间**创建**时分发 agent；前端已用随机房间名，正常不会遇到。
- **本机代理坑**：Clash 关闭后残留的 HTTP_PROXY 环境变量会导致连接失败，start.bat 已自动清空。

## 已知兼容处理（给工程师）

- livekit 插件锁定 **1.1.7**：≥1.2 发 OpenAI GA 协议，百炼兼容端点仅支持 beta，会静默超时。
- 百炼不回显 `response.metadata`，`minimal_agent.py` 里打了关联回退 monkey-patch。
- 详细对比与坑清单：`outputs/voice-agent-comparison-2026-07-27/comparison-report.md`
