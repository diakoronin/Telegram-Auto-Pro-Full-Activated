from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from sqlalchemy.ext.asyncio import AsyncSession

from app import texts_fa as T
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
        if isinstance(event, Message):
            tid = event.from_user.id if event.from_user else None
            text = event.text or ""
            if tid and (text.startswith("/start") or text.startswith("/menu")):
                ok = await consume_rate(
                    session,
                    key=f"start:{tid}",
                    window_seconds=settings.rate_limit_window_seconds,
                    max_count=settings.rate_limit_start_max,
                )
                if not ok:
                    await event.answer(T.RATE_LIMIT)
                    return None
        elif isinstance(event, CallbackQuery):
            tid = event.from_user.id if event.from_user else None
            data_cb = event.data or ""
            if tid and data_cb in ("main_menu", "shop", "wallet", "charge", "support"):
                ok = await consume_rate(
                    session,
                    key=f"cb:{tid}",
                    window_seconds=settings.rate_limit_window_seconds,
                    max_count=settings.rate_limit_start_max,
                )
                if not ok:
                    await event.answer(T.RATE_LIMIT, show_alert=True)
                    return None
        return await handler(event, data)
