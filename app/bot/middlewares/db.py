from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.base import get_session_factory


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._factory = get_session_factory(settings.database_url)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with self._factory() as session:  # type: AsyncSession
            data["session"] = session
            data["settings"] = self._settings
            after: List[Callable[[], Awaitable[None]]] = []
            data["after_commit"] = after
            try:
                result = await handler(event, data)
                await session.commit()
                for cb in after:
                    await cb()
                return result
            except BaseException:
                await session.rollback()
                raise
