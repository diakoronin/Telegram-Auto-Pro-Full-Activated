"""Runtime key-value settings stored in app_settings table."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting

KEY_LEGACY_LINK_ADMIN = "legacy_link_admin_tools_enabled"


async def get_setting(session: AsyncSession, key: str, default: str = "") -> str:
    r = await session.execute(select(AppSetting).where(AppSetting.key == key))
    row = r.scalar_one_or_none()
    if row is None:
        return default
    return (row.value or "").strip()


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    r = await session.execute(select(AppSetting).where(AppSetting.key == key))
    row = r.scalar_one_or_none()
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    await session.flush()
