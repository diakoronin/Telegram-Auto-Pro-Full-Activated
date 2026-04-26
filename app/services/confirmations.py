from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PendingConfirmation


async def create_confirmation(
    session: AsyncSession,
    *,
    admin_telegram_id: int,
    action_type: str,
    payload: dict[str, Any],
    ttl_minutes: int = 10,
) -> int:
    exp = datetime.now(tz=UTC) + timedelta(minutes=ttl_minutes)
    row = PendingConfirmation(
        admin_telegram_id=admin_telegram_id,
        action_type=action_type,
        payload_json=payload,
        expires_at=exp,
    )
    session.add(row)
    await session.flush()
    return int(row.id)


async def take_confirmation_if_valid(
    session: AsyncSession,
    *,
    confirmation_id: int,
    admin_telegram_id: int,
) -> dict[str, Any] | None:
    r = await session.execute(
        select(PendingConfirmation).where(PendingConfirmation.id == confirmation_id)
    )
    row = r.scalar_one_or_none()
    if row is None:
        return None
    if row.admin_telegram_id != admin_telegram_id:
        return None
    if row.expires_at < datetime.now(tz=UTC):
        await session.delete(row)
        await session.flush()
        return None
    payload = dict(row.payload_json)
    await session.delete(row)
    await session.flush()
    return payload


async def purge_expired(session: AsyncSession) -> None:
    await session.execute(
        delete(PendingConfirmation).where(
            PendingConfirmation.expires_at < datetime.now(tz=UTC)
        )
    )
