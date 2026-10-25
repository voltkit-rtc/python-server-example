"""Settings from the environment (.env). The VoltKit secret stays on this server."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class VoltKitSettings:
    base_url: str
    api_key: str


@dataclass(frozen=True)
class Config:
    app_key: str
    port: int
    host: str
    voltkit: VoltKitSettings


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ without overriding real env vars."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def read_config(env: Mapping[str, str] = os.environ) -> Config:
    """Validate the environment and return a Config, or raise ValueError naming the variable."""
    app_key = env.get("LAB_KEY", "")
    base_url = env.get("VOLTKIT_RTC_URL", "")
    api_key = env.get("VOLTKIT_RTC_SECRET", "")
    if len(app_key) < 16:
        raise ValueError("Set LAB_KEY: the key your app sends (16+ characters).")
    if not base_url.startswith("https://"):
        raise ValueError("Set VOLTKIT_RTC_URL, e.g. https://rtc.voltkit.in")
    if not api_key:
        raise ValueError("Set VOLTKIT_RTC_SECRET: your VoltKit API secret.")
    try:
        port = int(env.get("PORT", "7000"))
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be 1–65535.")
    return Config(
        app_key=app_key,
        port=port,
        host=env.get("HOST", "0.0.0.0"),
        voltkit=VoltKitSettings(base_url=base_url.rstrip("/"), api_key=api_key),
    )
