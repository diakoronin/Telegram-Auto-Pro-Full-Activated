from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Delivery, Link, LinkStatus, Plan, Purchase, PurchaseStatus, Server, User
from app.db.models import WalletTransaction, WalletTransactionType


async def pick_unused_link(
    session: AsyncSession, *, server_id: int, plan_id: int
) -> Link | None:
    bind = session.get_bind()
    stmt = (
        select(Link)
        .where(
            Link.server_id == server_id,
            Link.plan_id == plan_id,
            Link.status == LinkStatus.UNUSED,
        )
        .order_by(Link.id.asc())
        .limit(1)
    )
    # Do not use SKIP LOCKED here: under contention every visible row can be
    # skipped in one statement and the query returns no row while COUNT(*)
    # without FOR UPDATE still shows stock (confusing "ناموجود" for users).
    stmt = stmt.with_for_update()
    r = await session.execute(stmt)
    return r.scalar_one_or_none()


async def purchase_plan_for_user(
    session: AsyncSession,
    *,
    user: User,
    plan: Plan,
    custom_service_name: str,
) -> tuple[bool, str | None, str | None, int | None]:
    """
    Atomic purchase: deduct wallet, mark link used, purchase + wallet_tx + delivery.
    Returns (ok, link_text or None, error_fa or None, purchase_id or None).
    """
    if not plan.is_active:
        return False, None, "این پلن غیرفعال است.", None
    srv = await session.get(Server, plan.server_id)
    if srv is None or not srv.is_active:
        return False, None, "سرور غیرفعال است.", None

    # Lock plan first so concurrent purchases for the same plan are serialized.
    # (Avoids races / deadlocks between User row lock and Link row locks.)
    await session.execute(select(Plan).where(Plan.id == plan.id).with_for_update())

    u = await session.execute(
        select(User).where(User.id == user.id).with_for_update()
    )
    locked_user = u.scalar_one()
    if locked_user.is_blocked:
        return False, None, "حساب مسدود است.", None
    price = int(plan.price)
    if int(locked_user.wallet_balance) < price:
        return False, None, "موجودی کافی نیست.", None

    link = await pick_unused_link(
        session, server_id=plan.server_id, plan_id=plan.id
    )
    if link is None:
        return False, None, "موجودی لینک تمام شده است.", None

    before = int(locked_user.wallet_balance)
    after = before - price
    locked_user.wallet_balance = after
    link.status = LinkStatus.USED

    purchase = Purchase(
        user_id=locked_user.id,
        server_id=plan.server_id,
        plan_id=plan.id,
        link_id=link.id,
        custom_service_name=custom_service_name.strip()[:120],
        amount_paid=price,
        status=PurchaseStatus.COMPLETED,
    )
    session.add(purchase)
    await session.flush()

    session.add(
        WalletTransaction(
            user_id=locked_user.id,
            type=WalletTransactionType.PURCHASE,
            amount_delta=-price,
            balance_before=before,
            balance_after=after,
            reason="purchase",
            related_purchase_id=purchase.id,
        )
    )
    session.add(
        Delivery(
            link_id=link.id,
            user_id=locked_user.id,
            admin_id=None,
            purchase_id=purchase.id,
            channel="user_purchase",
            metadata_json={"plan_id": plan.id},
        )
    )
    await session.flush()
    return True, link.link_text, None, purchase.id


async def admin_manual_deliver(
    session: AsyncSession,
    *,
    admin_id: int,
    server_id: int,
    plan_id: int,
    customer_info: str | None,
) -> tuple[bool, str | None, str | None, int | None]:
    plan = await session.get(Plan, plan_id)
    if plan is None or plan.server_id != server_id:
        return False, None, "پلن نامعتبر است.", None
    if not plan.is_active:
        return False, None, "پلن غیرفعال است.", None

    link = await pick_unused_link(session, server_id=server_id, plan_id=plan_id)
    if link is None:
        return False, None, "لینک خالی موجود نیست.", None

    link.status = LinkStatus.USED
    meta: dict[str, Any] = {"server_id": server_id, "plan_id": plan_id}
    if customer_info:
        meta["customer"] = customer_info
    d = Delivery(
        link_id=link.id,
        user_id=None,
        admin_id=admin_id,
        purchase_id=None,
        channel="admin_manual",
        metadata_json=meta,
    )
    session.add(d)
    await session.flush()
    return True, link.link_text, None, d.id


async def return_link(
    session: AsyncSession,
    *,
    link_id: int,
) -> tuple[bool, str | None]:
    stmt = select(Link).where(Link.id == link_id).with_for_update()
    r = await session.execute(stmt)
    link = r.scalar_one_or_none()
    if link is None:
        return False, "لینک یافت نشد."
    if link.status != LinkStatus.USED:
        return False, "فقط لینک مصرف‌شده قابل بازگشت است."
    link.status = LinkStatus.UNUSED
    await session.flush()
    return True, None
