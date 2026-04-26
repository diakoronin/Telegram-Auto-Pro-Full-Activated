"""Wallet operations with row locking."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot_app.db.models import PaymentRequest, User, WalletTransaction

logger = logging.getLogger(__name__)


async def get_user_for_update(session: AsyncSession, user_id: int) -> Optional[User]:
    r = await session.execute(select(User).where(User.id == user_id).with_for_update())
    return r.scalar_one_or_none()


async def adjust_balance(
    session: AsyncSession,
    *,
    user_id: int,
    delta: int,
    tx_type: str,
    reference: Optional[str] = None,
    purchase_id: Optional[int] = None,
    payment_request_id: Optional[int] = None,
    request_id: Optional[str] = None,
) -> Tuple[bool, int, int]:
    user = await get_user_for_update(session, user_id)
    if not user:
        return False, 0, 0
    before = int(user.wallet_balance)
    after = before + int(delta)
    if after < 0:
        logger.warning("[PAYMENT] negative_wallet_blocked user=%s before=%s delta=%s", user_id, before, delta)
        return False, before, before
    user.wallet_balance = after
    session.add(
        WalletTransaction(
            user_id=user_id,
            type=tx_type,
            amount=int(delta),
            balance_before=before,
            balance_after=after,
            reference=reference,
            purchase_id=purchase_id,
            payment_request_id=payment_request_id,
            request_id=request_id,
        )
    )
    return True, before, after


async def try_approve_payment(
    session: AsyncSession,
    *,
    payment_request_id: int,
    admin_db_id: int,
    request_id: str,
) -> Tuple[str, Optional[int]]:
    r = await session.execute(
        select(PaymentRequest).where(PaymentRequest.id == payment_request_id).with_for_update()
    )
    pr = r.scalar_one_or_none()
    if not pr:
        return "not_found", None
    if pr.status != "pending":
        return "already", pr.user_id
    if pr.locked_at is not None and pr.approved_by_admin_id != admin_db_id:
        return "locked_other", pr.user_id
    uid = pr.user_id
    amount = int(pr.amount)
    ok, before, after = await adjust_balance(
        session,
        user_id=uid,
        delta=amount,
        tx_type="deposit_approved",
        reference=f"payment_request:{payment_request_id}",
        payment_request_id=payment_request_id,
        request_id=request_id,
    )
    if not ok:
        return "wallet_error", uid
    pr.status = "approved"
    pr.approved_by_admin_id = admin_db_id
    pr.locked_at = None
    pr.updated_at = datetime.now(timezone.utc)
    return "approved", uid
