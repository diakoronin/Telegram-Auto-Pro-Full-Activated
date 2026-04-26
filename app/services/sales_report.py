"""Daily / range sales aggregates (completed purchases, non-refunded)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Delivery, Link, Plan, Purchase, PurchaseStatus, Server
from app.services.plan_gb import gb_from_plan


@dataclass(frozen=True)
class SalesWindow:
    start_utc: datetime
    end_utc: datetime
    label_fa: str


def window_today(settings: Settings) -> SalesWindow:
    tz = ZoneInfo(settings.timezone)
    now_local = datetime.now(tz=tz)
    start_local = datetime.combine(now_local.date(), time.min, tzinfo=tz)
    end_local = now_local
    return SalesWindow(
        start_utc=start_local.astimezone(ZoneInfo("UTC")),
        end_utc=end_local.astimezone(ZoneInfo("UTC")),
        label_fa="امروز تا الان",
    )


def window_yesterday(settings: Settings) -> SalesWindow:
    tz = ZoneInfo(settings.timezone)
    now_local = datetime.now(tz=tz)
    y = now_local.date() - timedelta(days=1)
    start_local = datetime.combine(y, time.min, tzinfo=tz)
    end_local = datetime.combine(y, time.max.replace(microsecond=0), tzinfo=tz)
    return SalesWindow(
        start_utc=start_local.astimezone(ZoneInfo("UTC")),
        end_utc=end_local.astimezone(ZoneInfo("UTC")),
        label_fa="دیروز",
    )


def window_this_month(settings: Settings) -> SalesWindow:
    tz = ZoneInfo(settings.timezone)
    now_local = datetime.now(tz=tz)
    start_local = datetime.combine(
        now_local.date().replace(day=1), time.min, tzinfo=tz
    )
    end_local = now_local
    return SalesWindow(
        start_utc=start_local.astimezone(ZoneInfo("UTC")),
        end_utc=end_local.astimezone(ZoneInfo("UTC")),
        label_fa="این ماه تا الان",
    )


@dataclass
class SalesAggregate:
    total_gb: float
    total_orders: int
    total_revenue: int
    by_plan_label: dict[str, tuple[int, int]]
    by_server: dict[str, tuple[float, int, int]]
    user_channel_gb: float
    user_channel_orders: int
    admin_channel_gb: float
    admin_channel_orders: int


async def aggregate_sales(
    session: AsyncSession,
    *,
    start_utc: datetime,
    end_utc: datetime,
) -> SalesAggregate:
    q = (
        select(Purchase, Plan, Server)
        .join(Plan, Plan.id == Purchase.plan_id)
        .join(Server, Server.id == Purchase.server_id)
        .where(
            Purchase.status == PurchaseStatus.COMPLETED,
            Purchase.is_refunded.is_(False),
            Purchase.created_at >= start_utc,
            Purchase.created_at <= end_utc,
        )
    )
    rows = (await session.execute(q)).all()
    pids = [pur.id for pur, _, _ in rows]
    ch_map: dict[int, str | None] = {}
    if pids:
        dr = await session.execute(
            select(Delivery.purchase_id, Delivery.channel).where(
                Delivery.purchase_id.in_(pids)
            )
        )
        for pid, ch in dr:
            if pid not in ch_map:
                ch_map[pid] = ch
    total_gb = 0.0
    total_orders = len(rows)
    total_revenue = 0
    by_plan: dict[str, tuple[int, int]] = {}
    by_srv: dict[str, tuple[float, int, int]] = {}
    user_gb_f = 0.0
    user_ord_i = 0
    admin_gb_f = 0.0
    admin_ord_i = 0

    for pur, pl, srv in rows:
        del_ch = ch_map.get(pur.id)
        gb = gb_from_plan(pl)
        amt = int(pur.amount_paid)
        total_revenue += amt
        total_gb += gb
        lbl = (
            pl.display_name.strip()
            if (pl.display_name or "").strip()
            else pl.name
        )
        c, r = by_plan.get(lbl, (0, 0))
        by_plan[lbl] = (c + 1, r + amt)
        sg, so, sr = by_srv.get(srv.name, (0.0, 0, 0))
        by_srv[srv.name] = (sg + gb, so + 1, sr + amt)
        if del_ch == "admin_manual":
            admin_gb_f += gb
            admin_ord_i += 1
        else:
            user_gb_f += gb
            user_ord_i += 1

    md_q = (
        select(Delivery, Plan)
        .join(Link, Link.id == Delivery.link_id)
        .join(Plan, Plan.id == Link.plan_id)
        .where(
            Delivery.channel == "admin_manual",
            Delivery.purchase_id.is_(None),
            Delivery.created_at >= start_utc,
            Delivery.created_at <= end_utc,
        )
    )
    for _d, pl in (await session.execute(md_q)).all():
        gb = gb_from_plan(pl)
        admin_gb_f += gb
        admin_ord_i += 1

    return SalesAggregate(
        total_gb=total_gb,
        total_orders=total_orders,
        total_revenue=total_revenue,
        by_plan_label=by_plan,
        by_server=by_srv,
        user_channel_gb=user_gb_f,
        user_channel_orders=user_ord_i,
        admin_channel_gb=admin_gb_f,
        admin_channel_orders=admin_ord_i,
    )
