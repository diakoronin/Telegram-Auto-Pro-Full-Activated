from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiogram import Bot

from app.config import Settings
from app.db.base import get_session_factory
from app.db.models import Admin, AdminRole, Link, LinkStatus, Plan, Server

logger = logging.getLogger(__name__)


async def _unused_link_count(session: AsyncSession, plan_id: int) -> int:
    r = await session.execute(
        select(func.count())
        .select_from(Link)
        .where(Link.plan_id == plan_id, Link.status == LinkStatus.UNUSED)
    )
    return int(r.scalar_one() or 0)


async def after_stock_change(
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
    *,
    plan_id: int,
) -> None:
    """
    Update low_stock_rearm and optionally notify owner/managers once per low cycle.

    - When unused > threshold: set low_stock_rearm True (armed for next low).
    - When unused <= threshold and low_stock_rearm: send alert, set rearm False.
    """
    thr = settings.low_stock_threshold
    if thr <= 0:
        return

    plan = await session.get(Plan, plan_id)
    if plan is None or not plan.is_active:
        return

    unused = await _unused_link_count(session, plan_id)

    if unused > thr:
        if not plan.low_stock_rearm:
            plan.low_stock_rearm = True
            await session.flush()
        return

    if unused <= thr and plan.low_stock_rearm:
        srv = await session.get(Server, plan.server_id)
        srv_name = srv.name if srv else "?"
        text = (
            f"هشدار کم‌بودن لینک\n"
            f"پلن: {srv_name} / {plan.name} (plan_id={plan_id})\n"
            f"لینک‌های بلااستفاده: {unused}\n"
            f"آستانه: {thr}"
        )
        admins = (
            await session.execute(
                select(Admin).where(
                    Admin.is_active.is_(True),
                    Admin.role.in_((AdminRole.OWNER, AdminRole.MANAGER)),
                )
            )
        ).scalars().all()
        for a in admins:
            try:
                await bot.send_message(a.telegram_id, text)
            except Exception:
                logger.exception("low stock notify failed for admin %s", a.telegram_id)
        plan.low_stock_rearm = False
        await session.flush()


async def run_stock_check_after_commit(
    database_url: str,
    settings: Settings,
    bot: Bot,
    *,
    plan_id: int,
) -> None:
    """Run in after_commit hook (uses a fresh session)."""
    factory = get_session_factory(database_url)
    async with factory() as session:
        await after_stock_change(session, bot, settings, plan_id=plan_id)
        await session.commit()
