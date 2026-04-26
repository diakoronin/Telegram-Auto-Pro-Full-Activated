from __future__ import annotations

import asyncio
import contextlib
import logging
import logging.handlers
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import ErrorEvent

from app.bot.handlers import setup_routers
from app.bot.middlewares.blocked import BlockedUserMiddleware
from app.bot.middlewares.context import UserContextMiddleware
from app.bot.middlewares.db import DbSessionMiddleware
from app.bot.middlewares.rate_limit_user import UserRateLimitMiddleware
from app.bot.middlewares.seller_scope import SellerUserFlowBlockMiddleware
from app.bot.middlewares.settings import SettingsMiddleware
from app.config import load_settings
from app.db.base import get_session_factory, init_db
from app import texts_fa as T
from app.message_format import format_message
from app.services.owner_backup import send_backup_to_owner
from app.services.traffic_sync import run_traffic_sync_cycle
from app.structured_log import RequestIdFilter
from app.subscription_api import create_subscription_app

logger = logging.getLogger(__name__)


def _configure_logging(settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s rid=%(request_id)s %(message)s",
        defaults={"request_id": "-"},
    )
    root.handlers.clear()
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.addFilter(RequestIdFilter())
    root.addHandler(sh)
    if settings.log_to_file:
        log_dir = Path(settings.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        for name, fname in (
            ("", "bot.log"),
            ("app.panel", "panel_api.log"),
            ("app.errors", "errors.log"),
        ):
            lg = logging.getLogger(name) if name else root
            fh = logging.handlers.RotatingFileHandler(
                log_dir / fname,
                maxBytes=8 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            fh.setFormatter(fmt)
            fh.addFilter(RequestIdFilter())
            lg.addHandler(fh)
    logging.getLogger("app.errors").setLevel(logging.ERROR)


async def _traffic_loop(bot: Bot, settings) -> None:
    factory = get_session_factory(settings.database_url)
    while True:
        try:
            await asyncio.sleep(settings.traffic_sync_interval_seconds)
            async with factory() as session:
                await run_traffic_sync_cycle(
                    session,
                    settings,
                    bot=bot,
                    batch_size=settings.traffic_sync_batch_size,
                )
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("traffic_sync loop error")


async def _backup_loop(bot: Bot, settings) -> None:
    factory = get_session_factory(settings.database_url)
    while True:
        try:
            await asyncio.sleep(settings.auto_backup_interval_minutes * 60)
            if settings.auto_backup_enabled:
                ok, err = await send_backup_to_owner(bot, settings, session_factory=factory)
                if not ok:
                    logger.error("auto backup failed: %s", err)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("backup loop error")


async def main() -> None:
    settings = load_settings()
    _configure_logging(settings)
    await init_db(settings.database_url)

    bot = Bot(settings.bot_token, default=DefaultBotProperties())
    try:
        await bot.delete_webhook(drop_pending_updates=settings.delete_webhook_drop_pending)
    except Exception:
        logger.debug("delete_webhook skipped")

    dp = Dispatcher()

    dp.update.outer_middleware(SettingsMiddleware(settings))
    dp.update.outer_middleware(DbSessionMiddleware(settings))
    dp.update.outer_middleware(UserContextMiddleware())
    dp.update.outer_middleware(SellerUserFlowBlockMiddleware())
    dp.update.outer_middleware(BlockedUserMiddleware())
    dp.update.outer_middleware(UserRateLimitMiddleware())

    dp.include_router(setup_routers())
    dp.workflow_data["settings"] = settings

    @dp.errors()
    async def _errors(event: ErrorEvent) -> None:
        exc = event.exception
        logging.getLogger("app.errors").exception("Unhandled: %s", exc)
        try:
            if event.update.message:
                await event.update.message.answer(
                    format_message(settings, T.GENERIC_ERROR)
                )
            elif event.update.callback_query:
                await event.update.callback_query.answer(
                    format_message(settings, T.GENERIC_ERROR),
                    show_alert=True,
                )
        except Exception:
            logger.exception("Failed to send user-friendly error")
        try:
            st = None
            if event.data:
                st = event.data.get("settings")
            if st is None:
                st = dp.workflow_data.get("settings")
            if st:
                await event.update.bot.send_message(
                    st.owner_telegram_id,
                    f"خطای بحرانی در ربات: {type(exc).__name__}",
                )
        except Exception:
            logger.debug("owner notify skipped")

    traffic_task = asyncio.create_task(_traffic_loop(bot, settings))
    backup_task = asyncio.create_task(_backup_loop(bot, settings))
    try:
        if settings.subscription_endpoint_enabled:
            import uvicorn

            app = create_subscription_app(settings)
            cfg = uvicorn.Config(
                app,
                host=settings.subscription_bind_host,
                port=settings.subscription_bind_port,
                log_level="warning",
            )
            server = uvicorn.Server(cfg)
            await asyncio.gather(server.serve(), dp.start_polling(bot))
        else:
            await dp.start_polling(bot)
    finally:
        traffic_task.cancel()
        backup_task.cancel()
        with contextlib.suppress(Exception):
            await traffic_task
        with contextlib.suppress(Exception):
            await backup_task


if __name__ == "__main__":
    asyncio.run(main())
