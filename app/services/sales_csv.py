"""CSV exports using streaming row iteration."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Panel,
    PanelAccount,
    PaymentRequest,
    PaymentRequestStatus,
    Plan,
    Purchase,
    PurchaseStatus,
    Server,
    User,
    UserService,
)


async def export_purchases_csv(
    session: AsyncSession,
    *,
    start_utc: datetime,
    end_utc: datetime,
) -> str:
    q = (
        select(
            Purchase.created_at,
            Purchase.id,
            User.telegram_id,
            UserService.public_service_code,
            PanelAccount.username,
            Server.name,
            Plan.name,
            Plan.volume_gb,
            Purchase.amount_paid,
            Purchase.status,
        )
        .join(User, User.id == Purchase.user_id)
        .join(Plan, Plan.id == Purchase.plan_id)
        .join(Server, Server.id == Purchase.server_id)
        .outerjoin(UserService, UserService.id == Purchase.user_service_id)
        .outerjoin(
            PanelAccount,
            (PanelAccount.user_service_id == UserService.id)
            & (PanelAccount.is_active.is_(True)),
        )
        .where(
            Purchase.created_at >= start_utc,
            Purchase.created_at <= end_utc,
            Purchase.status == PurchaseStatus.COMPLETED,
            Purchase.is_refunded.is_(False),
        )
        .order_by(Purchase.id.asc())
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "date",
            "purchase_id",
            "user_telegram_id",
            "public_service_code",
            "backend_username",
            "server",
            "plan",
            "volume_gb",
            "price",
            "status",
        ]
    )
    r = await session.stream(q)
    async for row in r:
        dt, pid, tid, code, be, srv, pln, vol, price, st = row
        w.writerow(
            [
                dt.isoformat() if dt else "",
                pid,
                tid,
                code or "",
                be or "",
                srv,
                pln,
                vol,
                price,
                st.value if hasattr(st, "value") else st,
            ]
        )
    return buf.getvalue()


async def export_payments_csv(
    session: AsyncSession,
    *,
    start_utc: datetime,
    end_utc: datetime,
) -> str:
    q = (
        select(
            PaymentRequest.created_at,
            PaymentRequest.id,
            User.telegram_id,
            PaymentRequest.amount,
            PaymentRequest.status,
        )
        .join(User, User.id == PaymentRequest.user_id)
        .where(
            PaymentRequest.created_at >= start_utc,
            PaymentRequest.created_at <= end_utc,
            PaymentRequest.status == PaymentRequestStatus.APPROVED,
        )
        .order_by(PaymentRequest.id.asc())
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "payment_request_id", "user_telegram_id", "amount", "status"])
    r = await session.stream(q)
    async for row in r:
        w.writerow(
            [
                row[0].isoformat() if row[0] else "",
                row[1],
                row[2],
                row[3],
                row[4].value if hasattr(row[4], "value") else row[4],
            ]
        )
    return buf.getvalue()


async def export_services_csv(session: AsyncSession) -> str:
    q = (
        select(
            UserService,
            User.telegram_id,
            Server.name,
            Panel.name,
            PanelAccount.username,
        )
        .join(User, User.id == UserService.user_id)
        .outerjoin(Server, Server.id == UserService.current_server_id)
        .outerjoin(
            PanelAccount,
            (PanelAccount.user_service_id == UserService.id)
            & (PanelAccount.is_active.is_(True)),
        )
        .outerjoin(Panel, Panel.id == PanelAccount.panel_id)
        .order_by(UserService.id.asc())
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "public_service_code",
            "user_telegram_id",
            "custom_name",
            "server",
            "panel",
            "backend_username",
            "total_gb",
            "used_gb",
            "remaining_gb",
            "status",
            "expire_at",
        ]
    )
    r = await session.stream(q)
    async for us, tid, srv, pan, be in r:
        w.writerow(
            [
                us.public_service_code,
                tid,
                us.custom_service_name,
                srv or "",
                pan or "",
                be or "",
                round(us.total_quota_bytes / (1024**3), 4),
                round(us.used_traffic_bytes / (1024**3), 4),
                round(us.remaining_traffic_bytes / (1024**3), 4),
                us.status.value,
                us.expire_at.isoformat() if us.expire_at else "",
            ]
        )
    return buf.getvalue()


async def export_daily_report_csv(
    session: AsyncSession,
    *,
    start_utc: datetime,
    end_utc: datetime,
    jalali_date: str,
    window_label: str,
) -> str:
    from app.services.reports_aggregate import (
        aggregate_payments_approved,
        count_completed_purchases,
        count_user_services_by_status,
    )
    from app.services.sales_report import aggregate_sales

    agg = await aggregate_sales(session, start_utc=start_utc, end_utc=end_utc)
    pay = await aggregate_payments_approved(session, start_utc=start_utc, end_utc=end_utc)
    svc = await count_user_services_by_status(session)
    n_pur = await count_completed_purchases(session, start_utc=start_utc, end_utc=end_utc)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["metric", "value"])
    w.writerow(["jalali_date", jalali_date])
    w.writerow(["window", window_label])
    w.writerow(["total_volume_gb", agg.total_gb])
    w.writerow(["completed_orders", n_pur])
    w.writerow(["revenue_toman", agg.total_revenue])
    w.writerow(["approved_payments_count", pay.approved_count])
    w.writerow(["approved_payments_amount", pay.approved_amount])
    w.writerow(["services_active", svc.active])
    w.writerow(["services_limited", svc.limited])
    w.writerow(["services_expired", svc.expired])
    w.writerow(["services_disabled", svc.disabled])
    w.writerow(["services_error", svc.error])
    return buf.getvalue()
