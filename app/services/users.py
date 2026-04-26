from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Admin, AdminRole, User


async def get_user_by_telegram(session: AsyncSession, telegram_id: int) -> User | None:
    r = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return r.scalar_one_or_none()


async def get_or_create_user(
    session: AsyncSession, telegram_id: int, username: str | None
) -> User:
    u = await get_user_by_telegram(session, telegram_id)
    if u:
        if username and u.username != username:
            u.username = username
        return u
    u = User(telegram_id=telegram_id, username=username)
    session.add(u)
    await session.flush()
    return u


async def get_admin_by_telegram(session: AsyncSession, telegram_id: int) -> Admin | None:
    r = await session.execute(
        select(Admin).where(
            Admin.telegram_id == telegram_id,
            Admin.is_active.is_(True),
        )
    )
    return r.scalar_one_or_none()


async def ensure_owner_admin(
    session: AsyncSession, owner_telegram_id: int
) -> Admin:
    a = await get_admin_by_telegram(session, owner_telegram_id)
    if a:
        if a.role != AdminRole.OWNER:
            a.role = AdminRole.OWNER
        return a
    a = Admin(telegram_id=owner_telegram_id, role=AdminRole.OWNER, is_active=True)
    session.add(a)
    await session.flush()
    return a
