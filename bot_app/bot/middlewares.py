"""Aiogram middlewares."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot_app.db.models import User


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with self.session_factory() as session:
            data["session"] = session
            return await handler(event, data)


class BlockedUserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        from aiogram.types import Message, CallbackQuery

        session = data.get("session")
        if session is None:
            return await handler(event, data)
        tg_id = None
        if isinstance(event, Message) and event.from_user:
            tg_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            tg_id = event.from_user.id
        if tg_id is None:
            return await handler(event, data)
        r = await session.execute(select(User).where(User.telegram_id == tg_id))
        u = r.scalar_one_or_none()
        if u and u.is_blocked:
            msg = (
                "دسترسی شما محدود شده است.\n"
                "در صورت نیاز با پشتیبانی تماس بگیرید."
            )
            if isinstance(event, Message):
                await event.answer(msg)
            elif isinstance(event, CallbackQuery):
                await event.answer(msg, show_alert=True)
            return None
        return await handler(event, data)
