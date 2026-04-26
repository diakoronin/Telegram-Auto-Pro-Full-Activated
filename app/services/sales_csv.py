"""CSV export for completed purchases in a time window (SQL-only row fetch)."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Plan, Purchase, PurchaseStatus, Server


async def export_purchases_csv(
    session: AsyncSession,
    *,
    start_utc: datetime,
    end_utc: datetime,
) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["purchase_id", "created_at", "user_id", "amount", "status", "plan", "server"]
    )
    q = (
        select(Purchase, Plan, Server)
        .join(Plan, Plan.id == Purchase.plan_id)
        .join(Server, Server.id == Purchase.server_id)
        .where(
            Purchase.created_at >= start_utc,
            Purchase.created_at <= end_utc,
            Purchase.status == PurchaseStatus.COMPLETED,
            Purchase.is_refunded.is_(False),
        )
        .order_by(Purchase.id.asc())
    )
    for pur, pl, srv in (await session.execute(q)).all():
        lbl = (pl.display_name or "").strip() or pl.name
        w.writerow(
            [
                pur.id,
                pur.created_at.isoformat() if pur.created_at else "",
                pur.user_id,
                pur.amount_paid,
                pur.status.value,
                lbl,
                srv.name,
            ]
        )
    return buf.getvalue()
