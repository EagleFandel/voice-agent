"""生成生产环境密钥并写入 deploy/.env（不存在的项才写，已有项保留）。"""

import secrets
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"


def main() -> None:
    existing = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()

    defaults = {
        "LIVEKIT_API_KEY": "API" + secrets.token_urlsafe(12),
        "LIVEKIT_API_SECRET": secrets.token_urlsafe(32),
        "SERVER_PUBLIC_IP": "你的服务器公网IP",
        "PUBLIC_LIVEKIT_URL": "wss://your-domain.example.com/rtc",
        "DASHSCOPE_API_KEY": "sk-填你的百炼key",
    }
    merged = {**defaults, **existing}
    ENV_PATH.write_text(
        "\n".join(f"{k}={v}" for k, v in merged.items()) + "\n", encoding="utf-8"
    )
    print(f"已写入 {ENV_PATH}")
    for k, v in merged.items():
        print(f"  {k} = {'(已存在,保留)' if k in existing else v}")


if __name__ == "__main__":
    main()
