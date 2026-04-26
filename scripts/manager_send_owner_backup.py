#!/usr/bin/env python3
"""One-shot: send backup to OWNER_ID (Telegram)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from app.config import load_settings
from app.services.owner_backup import send_backup_to_owner


async def main() -> None:
    s = load_settings()
    b = Bot(s.bot_token, default=DefaultBotProperties())
    try:
        ok, err = await send_backup_to_owner(b, s, trigger="manual_cli")
        print("OK" if ok else "FAIL", err)
    finally:
        await b.session.close()


if __name__ == "__main__":
    asyncio.run(main())
