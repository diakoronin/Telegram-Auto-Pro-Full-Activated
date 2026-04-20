import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def _parse_admin_ids(raw_value: str) -> set[int]:
    if not raw_value.strip():
        return set()
    values = set()
    for part in raw_value.split(","):
        part = part.strip()
        if not part:
            continue
        values.add(int(part))
    return values


@dataclass(frozen=True)
class Settings:
    bot_token: str
    xui_base_url: str
    xui_username: str
    xui_password: str
    admin_ids: set[int]
    request_timeout_seconds: int
    default_group_chat_id: str | None
    state_file: str

    @staticmethod
    def from_env() -> "Settings":
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        xui_base_url = os.getenv("XUI_BASE_URL", "").strip().rstrip("/")
        xui_username = os.getenv("XUI_USERNAME", "").strip()
        xui_password = os.getenv("XUI_PASSWORD", "").strip()
        admin_ids = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))
        timeout = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "25"))
        default_group_chat_id = os.getenv("DEFAULT_GROUP_CHAT_ID", "").strip() or None
        state_file = os.getenv("STATE_FILE", "bot_state.json")

        if not bot_token:
            raise ValueError("BOT_TOKEN is required")
        if not xui_base_url:
            raise ValueError("XUI_BASE_URL is required")
        if not xui_username:
            raise ValueError("XUI_USERNAME is required")
        if not xui_password:
            raise ValueError("XUI_PASSWORD is required")

        return Settings(
            bot_token=bot_token,
            xui_base_url=xui_base_url,
            xui_username=xui_username,
            xui_password=xui_password,
            admin_ids=admin_ids,
            request_timeout_seconds=timeout,
            default_group_chat_id=default_group_chat_id,
            state_file=state_file,
        )
