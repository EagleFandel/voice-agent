FROM python:3.11-slim

WORKDIR /app

# 依赖按本地验证过的版本锁定（livekit 插件 ≥1.2 与百炼不兼容，勿升级）
RUN pip install --no-cache-dir \
    "livekit==1.1.10" \
    "livekit-agents==1.1.7" \
    "livekit-plugins-openai==1.1.7" \
    "livekit-plugins-silero==1.1.7" \
    "python-dotenv==1.2.2" \
    "numpy==2.5.1" \
    "httpx==0.28.1"

COPY livekit-demo/minimal_agent.py /app/minimal_agent.py

# 容器内无 .env，全靠环境变量注入
CMD ["python", "minimal_agent.py", "start"]
