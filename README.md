# 超脑语音助手 Demo（LiveKit 全链路）

> 给 Michael / 演示用：双击 `start.bat`，等 10 秒，打开浏览器即可语音通话。

## 这是什么

「编排框架 + Realtime 一体模型」路线的可运行 demo：浏览器语音 → WebRTC → 本地 LiveKit Server → Agent Worker（livekit-agents + Silero VAD）→ 阿里百炼 qwen3-omni-flash-realtime（STT+LLM+TTS 一体）。支持全双工对话、插话打断（barge-in），实测首音频延迟 ~514ms。

## 框架能力清单

| 能力 | 状态 | 说明 |
|---|---|---|
| 全双工语音对话 | ✅ 已实测 | server_vad + Silero 本地 VAD |
| 插话打断 barge-in | ✅ 已实测 | Agent 侧 VAD 处理 |
| 工具调用 function calling | ✅ 已接入 | 示例工具 `query_today_tasks`（读 `wiki/daily-tasks.md` 今日待办），问"我今天有什么待办"即可触发 |
| 会话状态推送 | ✅ 已接入 | listening/thinking/speaking 经 data channel 到前端实时显示 |
| 对话字幕 | ⚠️ 预留 | 前端与 data channel 已就绪；**百炼 realtime 不回传文本转写**，换 OpenAI Realtime 后自动生效 |
| 断线自动重连 | ✅ 已接入 | 前端最多自动重试 3 次（手动挂断不触发） |
| 双前端 | ✅ | 8899 手写脉冲球 / 7860 FastRTC 原版 UI |
| RAG / 双 LLM / SIP | ⬜ 未做 | 框架支持，待目标场景对齐后决定 |

## 配置项（环境变量）

在 `livekit-demo/.env` 中覆盖，不改代码：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DASHSCOPE_API_KEY` | （必填） | 百炼 API key |
| `AGENT_MODEL` | `qwen3-omni-flash-realtime` | realtime 模型 |
| `AGENT_VOICE` | `Cherry` | 音色 |
| `AGENT_INSTRUCTIONS` | 小超人设 | 系统指令 |
| `DASHSCOPE_BASE_URL` | 百炼 api-ws | 换 OpenAI Realtime 时改这里+模型 |

## 如何给助手加新工具

`minimal_agent.py` 里照 `query_today_tasks` 的样子写：

```python
@function_tool
async def my_tool(param: str) -> str:
    """工具描述（模型据此决定何时调用）。参数写清楚。"""
    return "结果文本（模型会把它读给用户听）"
```

然后把 `my_tool` 加进 `MyAgent.__init__` 的 `tools=[...]` 列表，重启 agent worker 即可。工具返回值会被模型转成口语播报。


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
