"""Audit logging."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from bot_app.db.models import AuditLog


async def audit_log(
    session: AsyncSession,
    *,
    action: str,
    admin_telegram_id: int | None = None,
    user_telegram_id: int | None = None,
    details: str | None = None,
    request_id: str | None = None,
) -> None:
    session.add(
        AuditLog(
            action=action,
            admin_telegram_id=admin_telegram_id,
            user_telegram_id=user_telegram_id,
            details=details,
            request_id=request_id,
        )
    )
