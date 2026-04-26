"""SQL-only aggregates for admin reports (no full row loads)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    PaymentRequest,
    PaymentRequestStatus,
    Purchase,
    PurchaseStatus,
    UserService,
    UserServiceStatus,
)


@dataclass(frozen=True)
class ServiceStatusCounts:
    active: int
    limited: int
    expired: int
    disabled: int
    error: int


@dataclass(frozen=True)
class PaymentAggregate:
    approved_count: int
    approved_amount: int


async def count_user_services_by_status(
    session: AsyncSession,
) -> ServiceStatusCounts:
    q = select(
        func.sum(case((UserService.status == UserServiceStatus.ACTIVE, 1), else_=0)).label("a"),
        func.sum(case((UserService.status == UserServiceStatus.LIMITED, 1), else_=0)).label("l"),
        func.sum(case((UserService.status == UserServiceStatus.EXPIRED, 1), else_=0)).label("e"),
        func.sum(case((UserService.status == UserServiceStatus.DISABLED, 1), else_=0)).label("d"),
        func.sum(case((UserService.status == UserServiceStatus.ERROR, 1), else_=0)).label("x"),
    ).select_from(UserService)
    row = (await session.execute(q)).one()
    return ServiceStatusCounts(
        active=int(row.a or 0),
        limited=int(row.l or 0),
        expired=int(row.e or 0),
        disabled=int(row.d or 0),
        error=int(row.x or 0),
    )


async def aggregate_payments_approved(
    session: AsyncSession, *, start_utc: datetime, end_utc: datetime
) -> PaymentAggregate:
    q = select(
        func.count().label("c"),
        func.coalesce(func.sum(PaymentRequest.amount), 0).label("s"),
    ).where(
        PaymentRequest.status == PaymentRequestStatus.APPROVED,
        PaymentRequest.created_at >= start_utc,
        PaymentRequest.created_at <= end_utc,
    )
    row = (await session.execute(q)).one()
    return PaymentAggregate(approved_count=int(row.c or 0), approved_amount=int(row.s or 0))


async def count_completed_purchases(
    session: AsyncSession, *, start_utc: datetime, end_utc: datetime
) -> int:
    q = select(func.count()).where(
        Purchase.status == PurchaseStatus.COMPLETED,
        Purchase.is_refunded.is_(False),
        Purchase.created_at >= start_utc,
        Purchase.created_at <= end_utc,
    )
    return int((await session.execute(q)).scalar_one() or 0)
