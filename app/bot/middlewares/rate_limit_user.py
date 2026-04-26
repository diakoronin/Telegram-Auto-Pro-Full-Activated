from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from sqlalchemy.ext.asyncio import AsyncSession

from app import texts_fa as T
from app.bot.middlewares.update_compat import callback_from_event, message_from_event
from app.config import Settings
from app.services.rate_limit import consume_rate


class UserRateLimitMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        session: AsyncSession = data["session"]
        settings: Settings = data["settings"]
        msg = message_from_event(event)
        if msg is not None:
            tid = msg.from_user.id if msg.from_user else None
            text = msg.text or ""
            if tid and (
                text.startswith("/start")
                or text.startswith("/menu")
                or text.startswith("/ping")
            ):
                ok = await consume_rate(
                    session,
                    key=f"start:{tid}",
                    window_seconds=settings.rate_limit_window_seconds,
                    max_count=settings.rate_limit_start_max,
                )
                if not ok:
                    await msg.answer(T.RATE_LIMIT)
                    return None
        cq = callback_from_event(event)
        if cq is not None:
            tid = cq.from_user.id if cq.from_user else None
            data_cb = cq.data or ""
            if tid and data_cb in (
                "main_menu",
                "shop",
                "wallet",
                "charge",
                "support",
                "show_cards",
            ):
                ok = await consume_rate(
                    session,
                    key=f"cb:{tid}",
                    window_seconds=settings.rate_limit_window_seconds,
                    max_count=settings.rate_limit_start_max,
                )
                if not ok:
                    await cq.answer(T.RATE_LIMIT, show_alert=True)
                    return None
        return await handler(event, data)
