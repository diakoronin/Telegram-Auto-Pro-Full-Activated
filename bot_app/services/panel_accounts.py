"""Enforce at most one active panel account per API service."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot_app.db.models import PanelAccount


async def count_active_accounts(session: AsyncSession, user_service_id: int) -> int:
    r = await session.execute(
        select(func.count())
        .select_from(PanelAccount)
        .where(PanelAccount.user_service_id == user_service_id, PanelAccount.is_active.is_(True))
    )
    return int(r.scalar_one() or 0)


async def has_duplicate_active(session: AsyncSession, user_service_id: int) -> bool:
    return await count_active_accounts(session, user_service_id) > 1
