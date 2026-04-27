"""Key/value settings in `app_settings` (used for card-to-card display text, etc.)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot_app.db.models import AppSetting

_DEFAULT_CARD_INSTRUCTIONS = (
    "پس از واریز، مبلغ و ساعت واریز را در ربات ارسال کنید و در صورت نیاز رسید را ضمیمه کنید."
)


async def get_setting_value(session: AsyncSession, key: str, default: str = "") -> str:
    r = await session.execute(select(AppSetting.value).where(AppSetting.key == key))
    v = r.scalar_one_or_none()
    return (v or default) if v is not None else default


async def set_setting_value(session: AsyncSession, key: str, value: str) -> None:
    r = await session.execute(select(AppSetting).where(AppSetting.key == key))
    row = r.scalar_one_or_none()
    if row:
        row.value = value
    else:
        session.add(AppSetting(key=key, value=value))
    await session.flush()


async def get_card_to_card_instruction(session: AsyncSession) -> str:
    return await get_setting_value(
        session, "card_to_card_instruction", _DEFAULT_CARD_INSTRUCTIONS
    )


async def set_card_to_card_instruction(session: AsyncSession, text: str) -> None:
    await set_setting_value(session, "card_to_card_instruction", text)
