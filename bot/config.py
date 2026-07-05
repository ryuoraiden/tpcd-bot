"""Environment-driven configuration. Everything the bot needs comes from .env
so the same code runs unchanged on any host.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _int_env(name: str, default: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _id_list(name: str) -> list[int]:
    raw = os.getenv(name, "").strip()
    return [int(part) for part in raw.split(",") if part.strip()]


@dataclass(frozen=True)
class Config:
    token: str = os.getenv("DISCORD_TOKEN", "")
    poll_channel_id: int = _int_env("POLL_CHANNEL_ID")
    poll_ping_role_id: int = _int_env("POLL_PING_ROLE_ID")
    welcome_channel_id: int = _int_env("WELCOME_CHANNEL_ID")
    goodbye_channel_id: int = _int_env("GOODBYE_CHANNEL_ID")
    owner_id: int = _int_env("OWNER_ID")
    admin_role_ids: list[int] = field(default_factory=lambda: _id_list("ADMIN_ROLE_IDS"))
    post_time: str = os.getenv("POST_TIME", "09:00")
    timezone: str = os.getenv("TIMEZONE", "Asia/Kolkata")
    poll_duration_hours: int = _int_env("POLL_DURATION_HOURS", 24)
    weekly_recap: bool = os.getenv("WEEKLY_RECAP", "1") == "1"
    db_path: Path = PROJECT_ROOT / os.getenv("DB_PATH", "data/tpcd.db")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def post_hour(self) -> int:
        return int(self.post_time.split(":")[0])

    @property
    def post_minute(self) -> int:
        return int(self.post_time.split(":")[1])

    def validate(self) -> None:
        missing = []
        if not self.token:
            missing.append("DISCORD_TOKEN")
        if not self.poll_channel_id:
            missing.append("POLL_CHANNEL_ID")
        if missing:
            raise SystemExit(
                f"Missing required .env values: {', '.join(missing)}. "
                "Copy .env.example to .env and fill them in."
            )


config = Config()
