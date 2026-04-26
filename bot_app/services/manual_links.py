"""Manual link import and delivery."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot_app.db.models import ManualDelivery, ManualLink, ManualPlan, ManualServer, User
from bot_app.services.audit import audit_log

logger = logging.getLogger(__name__)


def parse_import_lines(text: str, max_links: int) -> List[str]:
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        lines.append(s)
        if len(lines) >= max_links:
            break
    return lines


async def bulk_import_links(
    session: AsyncSession,
    *,
    lines: List[str],
    manual_server_id: int,
    manual_plan_id: int,
    admin_db_id: int,
    max_links: int,
    max_link_length: int,
    request_id: str,
) -> dict:
    total = len(lines)
    dup_file = set()
    seen = set()
    unique_lines: List[str] = []
    dup_in_file = 0
    for ln in lines[:max_links]:
        if ln in seen:
            dup_in_file += 1
            continue
        seen.add(ln)
        unique_lines.append(ln)

    invalid = 0
    added = 0
    dup_db = 0
    for ln in unique_lines:
        if len(ln) > max_link_length or len(ln) < 8:
            invalid += 1
            continue
        ex = await session.execute(
            select(ManualLink.id).where(ManualLink.link_text == ln, ManualLink.status != "deleted")
        )
        if ex.scalar_one_or_none():
            dup_db += 1
            continue
        session.add(
            ManualLink(
                manual_server_id=manual_server_id,
                manual_plan_id=manual_plan_id,
                link_text=ln,
                status="unused",
                imported_by_admin_id=admin_db_id,
            )
        )
        added += 1
    await session.flush()
    logger.info(
        "[MANUAL IMPORT] rid=%s total=%s added=%s dup_file=%s dup_db=%s invalid=%s",
        request_id,
        total,
        added,
        dup_in_file,
        dup_db,
        invalid,
    )
    await audit_log(
        session,
        action="manual_link_import",
        details=f"added={added}",
        request_id=request_id,
    )
    return {
        "total": total,
        "added": added,
        "duplicate_in_file": dup_in_file,
        "duplicate_in_db": dup_db,
        "invalid": invalid,
    }


async def deliver_one_link(
    session: AsyncSession,
    *,
    manual_server_id: int,
    manual_plan_id: int,
    admin_db_id: int,
    user_telegram_id: int | None,
    customer_info: str | None,
    request_id: str,
) -> tuple[bool, str, dict | None]:
    """Pick unused link with SKIP LOCKED on PostgreSQL; fallback on SQLite."""
    from sqlalchemy import text

    dialect = session.get_bind().dialect.name if session.get_bind() else "postgresql"
    if dialect == "postgresql":
        row = (
            await session.execute(
                text(
                    """
                    SELECT id FROM manual_links
                    WHERE manual_server_id = :sid AND manual_plan_id = :pid
                      AND status = 'unused' AND is_active = true
                    ORDER BY id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                ),
                {"sid": manual_server_id, "pid": manual_plan_id},
            )
        ).first()
    else:
        row = (
            await session.execute(
                text(
                    """
                    SELECT id FROM manual_links
                    WHERE manual_server_id = :sid AND manual_plan_id = :pid
                      AND status = 'unused' AND is_active = true
                    ORDER BY id ASC
                    LIMIT 1
                    """
                ),
                {"sid": manual_server_id, "pid": manual_plan_id},
            )
        ).first()
    if not row:
        return False, "no_stock", None
    link_id = row[0]
    r = await session.execute(select(ManualLink).where(ManualLink.id == link_id).with_for_update())
    link = r.scalar_one_or_none()
    if not link or link.status != "unused":
        return False, "race", None

    user_id = None
    if user_telegram_id:
        ur = await session.execute(select(User).where(User.telegram_id == user_telegram_id))
        u = ur.scalar_one_or_none()
        if u:
            user_id = u.id

    link.status = "used"
    link.used_at = datetime.now(timezone.utc)
    link.used_by_user_id = user_id
    link.used_by_admin_id = admin_db_id

    delivery = ManualDelivery(
        manual_link_id=link.id,
        user_id=user_id,
        user_telegram_id=user_telegram_id,
        admin_id=admin_db_id,
        customer_info=customer_info,
        manual_server_id=manual_server_id,
        manual_plan_id=manual_plan_id,
        status="delivered",
    )
    session.add(delivery)
    await session.flush()

    await audit_log(
        session,
        action="manual_delivery",
        user_telegram_id=user_telegram_id,
        details=f"delivery_id={delivery.id}",
        request_id=request_id,
    )
    logger.info("[MANUAL DELIVERY] rid=%s delivery_id=%s link_id=%s", request_id, delivery.id, link.id)

    srv = (await session.execute(select(ManualServer).where(ManualServer.id == manual_server_id))).scalar_one()
    pln = (await session.execute(select(ManualPlan).where(ManualPlan.id == manual_plan_id))).scalar_one()

    return True, "ok", {
        "delivery_id": delivery.id,
        "link": link.link_text,
        "manual_server_name": srv.name,
        "manual_plan_name": pln.display_name,
    }
