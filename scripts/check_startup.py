#!/usr/bin/env python3
"""Simulate bot startup (settings, DB schema, dispatcher wiring) without polling.

Run from repo root: .venv/bin/python scripts/check_startup.py
Exit 0 on success; non-zero with a clear error (for diagnose.sh).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.chdir(ROOT)


async def _main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    from app.bot.handlers import setup_routers
    from app.bot.middlewares.blocked import BlockedUserMiddleware
    from app.bot.middlewares.context import UserContextMiddleware
    from app.bot.middlewares.db import DbSessionMiddleware
    from app.bot.middlewares.rate_limit_user import UserRateLimitMiddleware
    from app.bot.middlewares.seller_scope import SellerUserFlowBlockMiddleware
    from app.bot.middlewares.settings import SettingsMiddleware
    from app.config import load_settings
    from app.db.base import init_db

    from aiogram import Bot, Dispatcher
    from aiogram.client.default import DefaultBotProperties

    settings = load_settings()
    await asyncio.wait_for(init_db(settings.database_url), timeout=60)

    bot = Bot(settings.bot_token, default=DefaultBotProperties())
    dp = Dispatcher()
    dp.update.outer_middleware(SettingsMiddleware(settings))
    dp.update.outer_middleware(DbSessionMiddleware(settings))
    dp.update.outer_middleware(UserContextMiddleware())
    dp.update.outer_middleware(SellerUserFlowBlockMiddleware())
    dp.update.outer_middleware(BlockedUserMiddleware())
    dp.update.outer_middleware(UserRateLimitMiddleware())
    dp.include_router(setup_routers())

    await bot.session.close()
    print("OK: startup simulation passed (settings, DB, routers, middlewares)")


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(_main(), timeout=90))
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
