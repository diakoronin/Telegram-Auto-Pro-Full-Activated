"""Daily / range sales aggregates via SQL (no full-table Python loops)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Delivery, Link, Plan, Purchase, PurchaseStatus, Server


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
    admin_deliv = exists().where(
        Delivery.purchase_id == Purchase.id,
        Delivery.channel == "admin_manual",
    )

    tot = await session.execute(
        select(
            func.count().label("c"),
            func.coalesce(func.sum(Purchase.amount_paid), 0).label("rev"),
            func.coalesce(func.sum(Plan.volume_gb), 0).label("vgb"),
            func.coalesce(
                func.sum(case((admin_deliv, Plan.volume_gb), else_=0)),
                0,
            ).label("admin_gb"),
            func.coalesce(
                func.sum(case((admin_deliv, 1), else_=0)),
                0,
            ).label("admin_cnt"),
        ).select_from(Purchase).join(Plan, Plan.id == Purchase.plan_id).where(
            Purchase.status == PurchaseStatus.COMPLETED,
            Purchase.is_refunded.is_(False),
            Purchase.created_at >= start_utc,
            Purchase.created_at <= end_utc,
        )
    )
    trow = tot.one()
    total_orders = int(trow.c or 0)
    total_revenue = int(trow.rev or 0)
    total_gb = float(trow.vgb or 0)
    admin_gb_p = float(trow.admin_gb or 0)
    admin_ord_p = int(trow.admin_cnt or 0)

    by_plan_rows = await session.execute(
        select(
            func.coalesce(func.nullif(func.trim(Plan.display_name), ""), Plan.name).label("lbl"),
            func.count().label("cnt"),
            func.coalesce(func.sum(Purchase.amount_paid), 0).label("rev"),
        )
        .select_from(Purchase)
        .join(Plan, Plan.id == Purchase.plan_id)
        .where(
            Purchase.status == PurchaseStatus.COMPLETED,
            Purchase.is_refunded.is_(False),
            Purchase.created_at >= start_utc,
            Purchase.created_at <= end_utc,
        )
        .group_by("lbl")
    )
    by_plan: dict[str, tuple[int, int]] = {}
    for lbl, cnt, rev in by_plan_rows:
        by_plan[str(lbl)] = (int(cnt or 0), int(rev or 0))

    by_srv_rows = await session.execute(
        select(
            Server.name.label("sn"),
            func.coalesce(func.sum(Plan.volume_gb), 0).label("vgb"),
            func.count().label("cnt"),
            func.coalesce(func.sum(Purchase.amount_paid), 0).label("rev"),
        )
        .select_from(Purchase)
        .join(Plan, Plan.id == Purchase.plan_id)
        .join(Server, Server.id == Purchase.server_id)
        .where(
            Purchase.status == PurchaseStatus.COMPLETED,
            Purchase.is_refunded.is_(False),
            Purchase.created_at >= start_utc,
            Purchase.created_at <= end_utc,
        )
        .group_by(Server.name)
    )
    by_srv: dict[str, tuple[float, int, int]] = {}
    for sn, vgb, cnt, rev in by_srv_rows:
        by_srv[str(sn)] = (float(vgb or 0), int(cnt or 0), int(rev or 0))

    md_q = (
        select(func.coalesce(func.sum(Plan.volume_gb), 0), func.count())
        .select_from(Delivery)
        .join(Link, Link.id == Delivery.link_id)
        .join(Plan, Plan.id == Link.plan_id)
        .where(
            Delivery.channel == "admin_manual",
            Delivery.purchase_id.is_(None),
            Delivery.created_at >= start_utc,
            Delivery.created_at <= end_utc,
        )
    )
    md_row = (await session.execute(md_q)).one()
    admin_gb_extra = float(md_row[0] or 0)
    admin_ord_extra = int(md_row[1] or 0)

    admin_gb = admin_gb_p + admin_gb_extra
    admin_ord = admin_ord_p + admin_ord_extra
    user_gb = max(0.0, total_gb - admin_gb)
    user_ord = max(0, total_orders - admin_ord)

    return SalesAggregate(
        total_gb=total_gb,
        total_orders=total_orders,
        total_revenue=total_revenue,
        by_plan_label=by_plan,
        by_server=by_srv,
        user_channel_gb=user_gb,
        user_channel_orders=user_ord,
        admin_channel_gb=admin_gb,
        admin_channel_orders=admin_ord,
    )
