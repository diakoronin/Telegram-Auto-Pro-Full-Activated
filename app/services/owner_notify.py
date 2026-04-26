"""Send short operational alerts to OWNER_ID (private chat only)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram import Bot

log = logging.getLogger(__name__)


async def notify_owner_text(bot: "Bot", owner_telegram_id: int, text: str) -> None:
    try:
        await bot.send_message(int(owner_telegram_id), text[:4090])
    except Exception:
        log.exception("notify_owner_text failed")
