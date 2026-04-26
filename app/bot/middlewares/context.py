from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.dispatcher.middlewares.user_context import EVENT_CONTEXT_KEY, EventContext
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.bot.middlewares.update_compat import callback_from_event, message_from_event
from app.services.users import get_admin_by_telegram, get_or_create_user


class UserContextMiddleware(BaseMiddleware):
    """Attach db User and Admin (if any) to handler data.

    Registered on ``dp.update``: the *event* is an :class:`Update`, not ``Message``.
    Use aiogram's ``event_context`` (from built-in :class:`UserContextMiddleware`)
    or unwrap ``message`` / ``callback_query`` for user id.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        session: AsyncSession = data["session"]
        settings: Settings = data["settings"]

        data["db_user"] = None
        data["admin"] = None

        tid: int | None = None
        username: str | None = None

        if isinstance(event, Update):
            ctx: EventContext | None = data.get(EVENT_CONTEXT_KEY)  # type: ignore[assignment]
            if ctx is not None and ctx.user is not None:
                tid = ctx.user.id
                username = ctx.user.username
            else:
                msg = message_from_event(event)
                if msg and msg.from_user:
                    tid = msg.from_user.id
                    username = msg.from_user.username
                else:
                    cq = callback_from_event(event)
                    if cq and cq.from_user:
                        tid = cq.from_user.id
                        username = cq.from_user.username
        elif isinstance(event, Message):
            tid = event.from_user.id if event.from_user else None
            username = event.from_user.username if event.from_user else None
        elif isinstance(event, CallbackQuery):
            tid = event.from_user.id if event.from_user else None
            username = event.from_user.username if event.from_user else None

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
