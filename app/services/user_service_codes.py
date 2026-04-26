from __future__ import annotations

import re
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserService


async def allocate_public_service_code(session: AsyncSession) -> str:
    for _ in range(50):
        code = f"SVC{secrets.randbelow(1_000_000):06d}"
        r = await session.execute(
            select(UserService.id).where(UserService.public_service_code == code).limit(1)
        )
        if r.scalar_one_or_none() is None:
            return code
    return f"SVC{secrets.token_hex(4).upper()}"


def backend_panel_username(*, telegram_id: int, public_service_code: str, volume_gb: int) -> str:
    """tg{telegram_id}_{SVCxxxxxx}_{N}gb — Latin only, Marzban max 32 chars."""
    code = re.sub(r"[^a-zA-Z0-9_]", "_", public_service_code)[:12]
    base = f"tg{telegram_id}_{code}_{int(volume_gb)}gb"
    return base[:32].lower()
