from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Admin,
    AdminRole,
    PaymentCard,
    PaymentRequest,
    PaymentRequestStatus,
    User,
    WalletTransaction,
    WalletTransactionType,
)


async def expire_stale_draft_payment_requests(session: AsyncSession) -> None:
    """Mark expired draft invoices (no receipt yet) as rejected."""
    now = datetime.now(tz=UTC)
    await session.execute(
        update(PaymentRequest)
        .where(
            PaymentRequest.status == PaymentRequestStatus.PENDING,
            PaymentRequest.receipt_kind == "pending",
            PaymentRequest.expires_at.is_not(None),
            PaymentRequest.expires_at < now,
        )
        .values(
            status=PaymentRequestStatus.REJECTED,
            rejection_reason="مهلت پرداخت این فاکتور به پایان رسید.",
            reviewed_at=now,
        )
    )


async def count_pending_for_user(session: AsyncSession, user_id: int) -> int:
    await expire_stale_draft_payment_requests(session)
    r = await session.execute(
        select(func.count())
        .select_from(PaymentRequest)
        .where(
            PaymentRequest.user_id == user_id,
            PaymentRequest.status == PaymentRequestStatus.PENDING,
            PaymentRequest.receipt_kind != "pending",
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
            PaymentRequest.receipt_kind != "pending",
        )
    )
    return int(r.scalar_one() or 0)


async def create_draft_payment_request(
    session: AsyncSession,
    *,
    user: User,
    amount: int,
    card: PaymentCard,
    expire_minutes: int,
) -> PaymentRequest:
    """Draft invoice: no receipt until user uploads after confirmation."""
    now = datetime.now(tz=UTC)
    pr = PaymentRequest(
        user_id=user.id,
        amount=amount,
        receipt_file_id="",
        receipt_kind="pending",
        assigned_card_id=card.id,
        expires_at=now + timedelta(minutes=expire_minutes),
        status=PaymentRequestStatus.PENDING,
    )
    session.add(pr)
    await session.flush()
    return pr


async def attach_receipt_to_payment_request(
    session: AsyncSession,
    *,
    pr: PaymentRequest,
    receipt_file_id: str,
    receipt_kind: str,
) -> tuple[bool, str | None]:
    if pr.status != PaymentRequestStatus.PENDING:
        return False, "این درخواست دیگر قابل ثبت رسید نیست."
    if pr.receipt_kind not in (None, "pending") or (pr.receipt_file_id or "").strip():
        return False, "برای این فاکتور قبلاً رسید ثبت شده است."
    now = datetime.now(tz=UTC)
    if pr.expires_at is not None and pr.expires_at < now:
        return False, "مهلت این فاکتور به پایان رسیده است. لطفاً فاکتور جدید بسازید."
    pr.receipt_file_id = receipt_file_id
    pr.receipt_kind = receipt_kind
    await session.flush()
    return True, None


async def cancel_payment_request_by_user(
    session: AsyncSession, *, pr: PaymentRequest
) -> tuple[bool, str | None]:
    if pr.status != PaymentRequestStatus.PENDING:
        return False, "این فاکتور قابل لغو نیست."
    now = datetime.now(tz=UTC)
    pr.status = PaymentRequestStatus.REJECTED
    pr.rejection_reason = "لغو توسط کاربر"
    pr.reviewed_at = now
    await session.flush()
    return True, None


async def create_payment_request(
    session: AsyncSession,
    *,
    user: User,
    amount: int,
    receipt_file_id: str,
    receipt_kind: str,
) -> PaymentRequest:
    """Legacy: create pending request with receipt already attached."""
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


async def list_pending_for_review(
    session: AsyncSession, *, limit: int = 20
) -> list[PaymentRequest]:
    await expire_stale_draft_payment_requests(session)
    r = await session.execute(
        select(PaymentRequest)
        .where(
            PaymentRequest.status == PaymentRequestStatus.PENDING,
            PaymentRequest.receipt_kind != "pending",
        )
        .order_by(PaymentRequest.id.desc())
        .limit(limit)
    )
    return list(r.scalars().all())


async def approve_payment_request(
    session: AsyncSession,
    *,
    request_id: int,
    reviewer: Admin,
) -> tuple[PaymentRequest | None, int | None, int | None, str | None]:
    """
    Approve in one transaction with row lock.
    Returns (request, balance_before, balance_after, error_fa).
    """
    if reviewer.role not in (AdminRole.OWNER, AdminRole.MANAGER):
        return None, None, None, "دسترسی مجاز نیست."

    stmt = (
        select(PaymentRequest)
        .where(PaymentRequest.id == request_id)
        .with_for_update()
    )
    r = await session.execute(stmt)
    pr = r.scalar_one_or_none()
    if pr is None:
        return None, None, None, "درخواست یافت نشد."
    if pr.status != PaymentRequestStatus.PENDING:
        return None, None, None, "این درخواست قبلاً بررسی شده است."
    if pr.receipt_kind == "pending" or not (pr.receipt_file_id or "").strip():
        return None, None, None, "هنوز رسیدی برای این درخواست ثبت نشده است."

    u_stmt = select(User).where(User.id == pr.user_id).with_for_update()
    ur = await session.execute(u_stmt)
    user = ur.scalar_one_or_none()
    if user is None:
        return None, None, None, "کاربر یافت نشد."

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
    return pr, before, after, None


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
