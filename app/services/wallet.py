from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Admin, AdminRole, Purchase, PurchaseStatus, User, WalletTransaction, WalletTransactionType


async def manual_adjust_wallet(
    session: AsyncSession,
    *,
    admin: Admin,
    user: User,
    delta: int,
    reason: str,
    large_threshold: int,
) -> tuple[bool, str | None]:
    if admin.role not in (AdminRole.OWNER, AdminRole.MANAGER):
        return False, "دسترسی مجاز نیست."
    if abs(delta) >= large_threshold and admin.role != AdminRole.OWNER:
        return False, "تنظیم مبلغ بزرگ فقط توسط مالک مجاز است."

    u = await session.execute(select(User).where(User.id == user.id).with_for_update())
    locked = u.scalar_one_or_none()
    if locked is None:
        return False, "کاربر یافت نشد."
    before = int(locked.wallet_balance)
    after = before + int(delta)
    if after < 0:
        return False, "موجودی منفی مجاز نیست."
    locked.wallet_balance = after
    session.add(
        WalletTransaction(
            user_id=locked.id,
            type=WalletTransactionType.MANUAL_ADJUST,
            amount_delta=int(delta),
            balance_before=before,
            balance_after=after,
            reason=reason,
        )
    )
    await session.flush()
    return True, None


async def refund_purchase(
    session: AsyncSession,
    *,
    admin: Admin,
    purchase_id: int,
    reason: str,
    return_link: bool,
) -> tuple[bool, str | None]:
    if admin.role != AdminRole.OWNER:
        return False, "فقط مالک می‌تواند بازپرداخت کند."

    p = await session.execute(
        select(Purchase).where(Purchase.id == purchase_id).with_for_update()
    )
    purchase = p.scalar_one_or_none()
    if purchase is None:
        return False, "خرید یافت نشد."
    if purchase.is_refunded:
        return False, "این خرید قبلاً بازپرداخت شده است."

    u = await session.execute(
        select(User).where(User.id == purchase.user_id).with_for_update()
    )
    user = u.scalar_one()
    before = int(user.wallet_balance)
    amt = int(purchase.amount_paid)
    after = before + amt
    user.wallet_balance = after

    purchase.is_refunded = True
    purchase.refund_reason = reason
    purchase.refunded_at = datetime.now(tz=UTC)
    purchase.status = PurchaseStatus.REFUNDED

    session.add(
        WalletTransaction(
            user_id=user.id,
            type=WalletTransactionType.REFUND,
            amount_delta=amt,
            balance_before=before,
            balance_after=after,
            reason=reason,
            related_purchase_id=purchase.id,
        )
    )

    if return_link:
        from app.db.models import Link, LinkStatus

        lr = await session.execute(
            select(Link).where(Link.id == purchase.link_id).with_for_update()
        )
        link = lr.scalar_one_or_none()
        if link and link.status == LinkStatus.USED:
            link.status = LinkStatus.RETURNED

    await session.flush()
    return True, None
