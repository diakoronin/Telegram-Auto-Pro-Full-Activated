from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import ErrorEvent

from app.bot.handlers import setup_routers
from app.bot.middlewares.blocked import BlockedUserMiddleware
from app.bot.middlewares.context import UserContextMiddleware
from app.bot.middlewares.db import DbSessionMiddleware
from app.bot.middlewares.rate_limit_user import UserRateLimitMiddleware
from app.bot.middlewares.seller_scope import SellerUserFlowBlockMiddleware
from app.bot.middlewares.settings import SettingsMiddleware
from app.config import load_settings
from app.db.base import init_db
from app import texts_fa as T

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = load_settings()
    await init_db(settings.database_url)

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
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
        logger.exception("Unhandled exception: %s", exc)
        try:
            if event.update.message:
                await event.update.message.answer(T.GENERIC_ERROR)
            elif event.update.callback_query:
                await event.update.callback_query.answer(
                    T.GENERIC_ERROR, show_alert=True
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

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
