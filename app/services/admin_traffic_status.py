"""Admin-readable traffic sync status (Persian)."""

from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Panel, PanelAccount, UserService


async def build_traffic_sync_status_text(session: AsyncSession) -> str:
    pr = await session.execute(
        select(
            func.count().label("n"),
            func.sum(case((Panel.last_traffic_sync_ok.is_(True), 1), else_=0)).label("ok"),
            func.max(Panel.last_traffic_sync_at).label("last_at"),
        ).select_from(Panel)
    )
    prow = pr.one()

    ur = await session.execute(
        select(
            func.count().label("n"),
            func.sum(case((UserService.last_traffic_sync_ok.is_(True), 1), else_=0)).label("ok"),
            func.max(UserService.last_traffic_sync_at).label("last_at"),
        ).select_from(UserService)
    )
    urow = ur.one()

    ar = await session.execute(
        select(
            func.count().label("n"),
            func.sum(case((PanelAccount.last_sync_ok.is_(True), 1), else_=0)).label("ok"),
            func.max(PanelAccount.last_synced_at).label("last_at"),
        ).select_from(PanelAccount)
    )
    arow = ar.one()

    return (
        "📡 وضعیت سینک مصرف\n\n"
        f"پنل‌ها: {int(prow.n or 0)} | آخرین سینک موفق (پنل): {int(prow.ok or 0)} | آخرین زمان: {prow.last_at or '—'}\n"
        f"سرویس‌ها: {int(urow.n or 0)} | آخرین سینک موفق (سرویس): {int(urow.ok or 0)} | آخرین زمان: {urow.last_at or '—'}\n"
        f"اکانت‌های پنل: {int(arow.n or 0)} | آخرین سینک موفق (اکانت): {int(arow.ok or 0)} | آخرین زمان: {arow.last_at or '—'}\n"
    )
