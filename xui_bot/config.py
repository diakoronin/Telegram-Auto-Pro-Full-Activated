"""Environment-based configuration for the x-ui Telegram bot."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _parse_ids(raw: str) -> List[int]:
    ids: List[int] = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return ids


@dataclass
class Config:
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
    telegram_admin_ids: List[int] = field(
        default_factory=lambda: _parse_ids(os.getenv("TELEGRAM_ADMIN_IDS", ""))
    )

    xui_base_url: str = field(default_factory=lambda: os.getenv("XUI_BASE_URL", "").strip().rstrip("/"))
    xui_username: str = field(default_factory=lambda: os.getenv("XUI_USERNAME", "").strip())
    xui_password: str = field(default_factory=lambda: os.getenv("XUI_PASSWORD", "").strip())
    xui_web_base_path: str = field(default_factory=lambda: os.getenv("XUI_WEB_BASE_PATH", "/").strip() or "/")
    xui_insecure_tls: bool = field(
        default_factory=lambda: os.getenv("XUI_INSECURE_TLS", "false").lower() in {"1", "true", "yes", "on"}
    )

    default_send_chat_id: str = field(default_factory=lambda: os.getenv("DEFAULT_SEND_CHAT_ID", "").strip())

    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "xui_bot.sqlite3").strip())
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").strip().upper())

    def validate(self) -> None:
        missing = []
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.telegram_admin_ids:
            missing.append("TELEGRAM_ADMIN_IDS")
        if not self.xui_base_url:
            missing.append("XUI_BASE_URL")
        if not self.xui_username:
            missing.append("XUI_USERNAME")
        if not self.xui_password:
            missing.append("XUI_PASSWORD")
        if missing:
            raise RuntimeError(
                "Missing required environment variables: " + ", ".join(missing)
            )

    @property
    def web_base_path(self) -> str:
        path = self.xui_web_base_path.strip()
        if not path.startswith("/"):
            path = "/" + path
        if not path.endswith("/"):
            path = path + "/"
        return path


config = Config()
