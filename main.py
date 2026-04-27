"""Application entry: Telegram bot + optional subscription API + workers."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot_app.bot.handlers import admin_handlers, user_handlers
from bot_app.bot.middlewares import BlockedUserMiddleware, DbSessionMiddleware
from bot_app.config import Settings, get_settings
from bot_app.db.session import async_session_factory, get_engine, reset_engine
from bot_app.logging_setup import setup_file_logging
from bot_app.migrations.runner import run_migrations
from bot_app.services.traffic_sync import sync_batch
from bot_app.subscription_api.app import create_subscription_app
from bot_app.workers.backup_worker import backup_loop

logger = logging.getLogger(__name__)


def validate_settings(s: Settings) -> None:
    if not s.bot_token or s.bot_token == "changeme":
        raise RuntimeError("BOT_TOKEN is required")
    if not s.database_url:
        raise RuntimeError("DATABASE_URL is required")
    if not s.panel_credential_encryption_key or len(s.panel_credential_encryption_key) < 16:
        raise RuntimeError("PANEL_CREDENTIAL_ENCRYPTION_KEY must be at least 16 characters")


async def ensure_owner_admin(session_factory):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from bot_app.db.models import Admin

    s = get_settings()
    async with session_factory() as session:  # type: AsyncSession
        r = await session.execute(select(Admin).where(Admin.telegram_id == s.owner_id))
        if r.scalar_one_or_none():
            await session.commit()
            return
        session.add(Admin(telegram_id=s.owner_id, role="owner", is_active=True))
        await session.commit()
        logger.info("[DB WRITE] owner admin created")


async def traffic_worker_loop(session_factory):
    while True:
        try:
            await sync_batch(session_factory, settings=get_settings())
        except Exception:
            logger.exception("[TRAFFIC_SYNC] worker error")
        await asyncio.sleep(get_settings().traffic_sync_interval_seconds)


async def run_bot() -> None:
    reset_engine()
    s = get_settings()
    validate_settings(s)
    setup_file_logging(s)

    engine = get_engine(s.database_url)
    await run_migrations(engine)
    factory = async_session_factory(s.database_url)
    await ensure_owner_admin(factory)

    bot = Bot(s.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.delete_webhook(drop_pending_updates=True)

    async def send_backup_to_owner(path):
        from pathlib import Path

        from aiogram.types import FSInputFile

        p = Path(path)
        if p.stat().st_size > 48 * 1024 * 1024:
            logger.warning("[BACKUP] file too large for Telegram: %s", p)
            try:
                await bot.send_message(
                    s.owner_id,
                    f"بکاپ محلی ذخیره شد (حجم زیاد برای ارسال در تلگرام):\n<code>{p}</code>",
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception("[BACKUP] notify owner failed")
            return
        try:
            await bot.send_document(s.owner_id, FSInputFile(p))
        except Exception:
            logger.exception("[BACKUP] send_document failed")

    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(DbSessionMiddleware(factory))
    dp.update.middleware(BlockedUserMiddleware())
    # Admin first: user flows (e.g. purchase) must not eat reply-keyboard button text before admin handlers
    dp.include_router(admin_handlers.router)
    dp.include_router(user_handlers.router)

    tasks = [asyncio.create_task(dp.start_polling(bot))]

    if s.subscription_endpoint_enabled:
        import uvicorn

        app = create_subscription_app(factory, sub_base64_enabled=s.sub_base64_enabled)
        config = uvicorn.Config(
            app,
            host=s.subscription_api_host,
            port=int(s.subscription_api_port),
            log_level="info",
        )
        server = uvicorn.Server(config)
        tasks.append(asyncio.create_task(server.serve()))

    tasks.append(asyncio.create_task(traffic_worker_loop(factory)))

    if s.auto_backup_enabled and "postgres" in s.database_url.lower():
        tasks.append(asyncio.create_task(backup_loop(s, send_to_owner=send_backup_to_owner)))

    await asyncio.gather(*tasks)


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
