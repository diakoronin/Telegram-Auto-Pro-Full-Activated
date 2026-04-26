"""Central quota: sum usage across all panel_accounts for one user_service."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PanelAccount, UserService


def consumed_from_account(pa: PanelAccount) -> int:
    if not pa.is_active and pa.final_used_bytes is not None:
        return int(pa.final_used_bytes)
    raw = int(pa.total_used_bytes or 0) - int(pa.usage_baseline_bytes or 0)
    return max(0, raw)


async def recompute_user_service_traffic(session: AsyncSession, us: UserService) -> int:
    r = await session.execute(
        select(PanelAccount).where(PanelAccount.user_service_id == us.id)
    )
    total_used = 0
    for pa in r.scalars().all():
        total_used += consumed_from_account(pa)
    us.used_traffic_bytes = total_used
    us.remaining_traffic_bytes = max(0, int(us.total_quota_bytes) - total_used)
    await session.flush()
    return total_used
