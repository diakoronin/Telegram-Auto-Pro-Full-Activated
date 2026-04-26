from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app import texts_fa as T
from app.bot.middlewares.update_compat import callback_from_event, message_from_event
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
            msg = message_from_event(event)
            if msg is not None:
                await msg.answer(T.BLOCKED_USER)
            else:
                cq = callback_from_event(event)
                if cq is not None:
                    await cq.answer(T.BLOCKED_USER, show_alert=True)
            return None
        return await handler(event, data)
