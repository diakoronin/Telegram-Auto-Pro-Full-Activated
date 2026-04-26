"""Shared handler helpers."""

from __future__ import annotations

from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot_app.db.models import Admin, User


async def get_or_create_user(session: AsyncSession, telegram_user) -> User:
    r = await session.execute(select(User).where(User.telegram_id == telegram_user.id))
    u = r.scalar_one_or_none()
    if u:
        u.username = telegram_user.username
        u.first_name = telegram_user.first_name
        u.last_name = telegram_user.last_name
        await session.flush()
        return u
    u = User(
        telegram_id=telegram_user.id,
        username=telegram_user.username,
        first_name=telegram_user.first_name,
        last_name=telegram_user.last_name,
    )
    session.add(u)
    await session.flush()
    return u


async def get_admin(session: AsyncSession, telegram_id: int) -> Optional[Admin]:
    r = await session.execute(
        select(Admin).where(Admin.telegram_id == telegram_id, Admin.is_active.is_(True))
    )
    return r.scalar_one_or_none()


def is_owner_or_manager(admin: Admin) -> bool:
    return admin.role in ("owner", "manager")
