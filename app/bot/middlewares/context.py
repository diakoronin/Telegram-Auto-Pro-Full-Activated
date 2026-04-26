from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Admin
from app.services.users import get_admin_by_telegram, get_or_create_user


class UserContextMiddleware(BaseMiddleware):
    """Attach db User and Admin (if any) to handler data."""

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
            username = event.from_user.username if event.from_user else None
        elif isinstance(event, CallbackQuery):
            tid = event.from_user.id if event.from_user else None
            username = event.from_user.username if event.from_user else None
        else:
            return await handler(event, data)

        # Always set keys so handlers never get TypeError from missing injection
        # (e.g. channel posts / edge updates with no from_user).
        data["db_user"] = None
        data["admin"] = None
        if tid is None:
            return await handler(event, data)

        db_user = await get_or_create_user(session, tid, username)
        admin = await get_admin_by_telegram(session, tid)
        if admin is None and tid == settings.owner_telegram_id:
            from app.services.users import ensure_owner_admin

            admin = await ensure_owner_admin(session, settings.owner_telegram_id)

        data["db_user"] = db_user
        data["admin"] = admin
        return await handler(event, data)
