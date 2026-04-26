from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app import texts_fa as T
from app.db.models import User


class BlockedUserMiddleware(BaseMiddleware):
    """Block shop/support flows for blocked users (admins exempt)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        admin = data.get("admin")
        db_user: User | None = data.get("db_user")
        if admin is not None:
            return await handler(event, data)
        if db_user and db_user.is_blocked:
            if isinstance(event, Message):
                await event.answer(T.BLOCKED_USER)
            elif isinstance(event, CallbackQuery):
                await event.answer(T.BLOCKED_USER, show_alert=True)
            return None
        return await handler(event, data)
