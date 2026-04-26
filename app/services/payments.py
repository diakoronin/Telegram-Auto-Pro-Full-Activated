from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Admin,
    AdminRole,
    PaymentRequest,
    PaymentRequestStatus,
    User,
    WalletTransaction,
    WalletTransactionType,
)


async def count_pending_for_user(session: AsyncSession, user_id: int) -> int:
    r = await session.execute(
        select(func.count())
        .select_from(PaymentRequest)
        .where(
            PaymentRequest.user_id == user_id,
            PaymentRequest.status == PaymentRequestStatus.PENDING,
        )
    )
    return int(r.scalar_one() or 0)


async def count_receipts_last_hour(session: AsyncSession, user_id: int) -> int:
    since = datetime.now(tz=UTC) - timedelta(hours=1)
    r = await session.execute(
        select(func.count())
        .select_from(PaymentRequest)
        .where(
            PaymentRequest.user_id == user_id,
            PaymentRequest.created_at >= since,
        )
    )
    return int(r.scalar_one() or 0)


async def create_payment_request(
    session: AsyncSession,
    *,
    user: User,
    amount: int,
    receipt_file_id: str,
    receipt_kind: str,
) -> PaymentRequest:
    pr = PaymentRequest(
        user_id=user.id,
        amount=amount,
        receipt_file_id=receipt_file_id,
        receipt_kind=receipt_kind,
        status=PaymentRequestStatus.PENDING,
    )
    session.add(pr)
    await session.flush()
    return pr


async def approve_payment_request(
    session: AsyncSession,
    *,
    request_id: int,
    reviewer: Admin,
) -> tuple[PaymentRequest | None, str | None]:
    """
    Approve in one transaction with row lock. Returns (request, error_fa).
    """
    if reviewer.role not in (AdminRole.OWNER, AdminRole.MANAGER):
        return None, "دسترسی مجاز نیست."

    stmt = (
        select(PaymentRequest)
        .where(PaymentRequest.id == request_id)
        .with_for_update()
    )
    r = await session.execute(stmt)
    pr = r.scalar_one_or_none()
    if pr is None:
        return None, "درخواست یافت نشد."
    if pr.status != PaymentRequestStatus.PENDING:
        return None, "این درخواست قبلاً بررسی شده است."

    u_stmt = select(User).where(User.id == pr.user_id).with_for_update()
    ur = await session.execute(u_stmt)
    user = ur.scalar_one_or_none()
    if user is None:
        return None, "کاربر یافت نشد."

    before = int(user.wallet_balance)
    after = before + int(pr.amount)
    user.wallet_balance = after
    pr.status = PaymentRequestStatus.APPROVED
    pr.reviewed_by_admin_id = reviewer.id
    pr.reviewed_at = datetime.now(tz=UTC)

    session.add(
        WalletTransaction(
            user_id=user.id,
            type=WalletTransactionType.CHARGE_APPROVED,
            amount_delta=int(pr.amount),
            balance_before=before,
            balance_after=after,
            reason="payment_request_approved",
            related_payment_request_id=pr.id,
        )
    )
    await session.flush()
    return pr, None


async def reject_payment_request(
    session: AsyncSession,
    *,
    request_id: int,
    reviewer: Admin,
    reason: str,
) -> tuple[PaymentRequest | None, str | None]:
    if reviewer.role not in (AdminRole.OWNER, AdminRole.MANAGER):
        return None, "دسترسی مجاز نیست."

    stmt = (
        select(PaymentRequest)
        .where(PaymentRequest.id == request_id)
        .with_for_update()
    )
    r = await session.execute(stmt)
    pr = r.scalar_one_or_none()
    if pr is None:
        return None, "درخواست یافت نشد."
    if pr.status != PaymentRequestStatus.PENDING:
        return None, "این درخواست قبلاً بررسی شده است."

    pr.status = PaymentRequestStatus.REJECTED
    pr.reviewed_by_admin_id = reviewer.id
    pr.reviewed_at = datetime.now(tz=UTC)
    pr.rejection_reason = reason
    await session.flush()
    return pr, None
