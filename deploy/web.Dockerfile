FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi==0.140.2" \
    "uvicorn==0.51.0" \
    "livekit-api==1.1.1"

COPY livekit-demo/token_server.py /app/token_server.py
COPY livekit-demo/web /app/web

CMD ["uvicorn", "token_server:app", "--host", "0.0.0.0", "--port", "8899"]
