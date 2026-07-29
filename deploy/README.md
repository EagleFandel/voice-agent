# 公网部署指南 · 超脑语音助手

把整套 LiveKit 语音栈部署到一台有云服务器，Michael（或任何人）打开 `https://你的域名` 即可通话。

## 服务器要求

- 配置：2 核 4G 起步（Silero VAD 本地推理 + agent worker）
- 系统：任意 Linux + Docker & Docker Compose
- 域名：一个解析到服务器 IP 的域名（**必须**——浏览器在非 localhost 页面使用麦克风/WebRTC 要求 HTTPS，Caddy 自动签证书）
- 防火墙/安全组放行：

| 端口 | 协议 | 用途 |
|---|---|---|
| 80, 443 | TCP | Caddy（HTTP→HTTPS、页面、信令反代） |
| 7881 | TCP | RTC over TCP fallback |
| 3478 | TCP+UDP | 内嵌 TURN（NAT 打不通时的保底中继） |
| 50000-50100 | UDP | RTC 媒体流 |

## 部署步骤

```bash
# 1. 仓库拷到服务器（project/voice-agent-comparison 即可，知识库独立配置）
cd project/voice-agent-comparison/deploy

# 2. 生成密钥
python3 gen_keys.py
# 然后编辑 .env：填入 SERVER_PUBLIC_IP、域名形式的 PUBLIC_LIVEKIT_URL、DASHSCOPE_API_KEY

# 3. 把 Caddyfile 里的 your-domain.example.com 换成真实域名

# 4. 构建并启动
docker compose --env-file .env up -d --build

# 5. 验证
curl https://你的域名/health        # {"ok":true,...}
docker compose logs -f agent       # 看到 registered worker 即就绪
```

浏览器打开 `https://你的域名`，点大圆球开始通话。

## 架构（与本地一致，仅多一层 Caddy）

```
浏览器 --HTTPS/WSS--> Caddy(443) --+--> web:8899 (页面/token)
                                   +--> livekit:7880 (信令, /rtc 路径)
浏览器 --UDP 50000+/TURN 3478-----> livekit-server (媒体)
livekit-server --房间事件--> agent worker --WS--> 百炼 realtime
```

## 注意事项

- **livekit 插件锁 1.1.7**：agent.Dockerfile 已锁定，勿升级（≥1.2 与百炼 realtime 协议不兼容）
- **知识库挂载（方案一：运营方配置）**：`search_wiki` / `query_today_tasks` 读容器内 `/knowledge`（compose 把宿主机 `deploy/knowledge/` 只读挂载，可用 `.env` 的 `KNOWLEDGE_HOST_DIR` 改路径）。把课程/培训 .md 扔进该目录即生效，下次会话自动重建索引，无需重启；目录为空时这两个工具返回「知识库暂不可用」，不影响通话。agent 代码侧用 `KNOWLEDGE_DIR` 环境变量定位，本地开发缺省回退到仓库 `wiki/`
- **密钥安全**：`deploy/.env` 含全部密钥，已在 git 中忽略，不要外发
- **TURN/TLS（可选加固）**：企业网/4G 下如果 UDP 全被拦，编辑 livekit.yaml 打开 `turn.tls_port: 5349` + domain，并放行 5349
- 本地开发不受影响：本目录只服务生产部署，本地仍用根目录 start.bat
