from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Link, LinkStatus


async def bulk_import_links(
    session: AsyncSession,
    *,
    server_id: int,
    plan_id: int,
    lines: list[str],
    max_lines: int,
    max_link_len: int,
) -> tuple[int, int, int, str | None]:
    """
    Returns (added, dup_in_batch, dup_in_db, error_fa).
    """
    if len(lines) > max_lines:
        return 0, 0, 0, "تعداد خطوط بیش از حد مجاز است."

    seen: set[str] = set()
    cleaned: list[str] = []
    dup_batch = 0
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if len(s) > max_link_len:
            return 0, 0, 0, "طول یکی از لینک‌ها بیش از حد مجاز است."
        if s in seen:
            dup_batch += 1
            continue
        seen.add(s)
        cleaned.append(s)

    if not cleaned:
        return 0, dup_batch, 0, None

    bind = session.get_bind()
    dup_db = 0
    added = 0

    if bind is not None and bind.dialect.name == "postgresql":
        stmt = pg_insert(Link).values(
            [
                {
                    "server_id": server_id,
                    "plan_id": plan_id,
                    "link_text": t,
                    "status": LinkStatus.UNUSED,
                }
                for t in cleaned
            ]
        )
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["server_id", "plan_id", "link_text"]
        )
        r = await session.execute(stmt)
        added = int(r.rowcount or 0)
        dup_db = len(cleaned) - added
    else:
        for t in cleaned:
            exists = await session.execute(
                select(Link.id).where(
                    Link.server_id == server_id,
                    Link.plan_id == plan_id,
                    Link.link_text == t,
                )
            )
            if exists.scalar_one_or_none() is not None:
                dup_db += 1
                continue
            session.add(
                Link(
                    server_id=server_id,
                    plan_id=plan_id,
                    link_text=t,
                    status=LinkStatus.UNUSED,
                )
            )
            added += 1

    await session.flush()
    return added, dup_batch, dup_db, None


async def bulk_import_links_detailed(
    session: AsyncSession,
    *,
    server_id: int,
    plan_id: int,
    lines: list[str],
    max_lines: int,
    max_link_len: int,
) -> tuple[int, int, int, int, int, str | None]:
    """
    Returns (added, dup_in_batch, dup_in_db, invalid_lines, total_nonempty, error_fa).

    *total_nonempty*: non-empty lines after strip (before dedup / DB).
    *invalid_lines*: too long after strip.
    """
    nonempty = [raw.strip() for raw in lines if raw.strip()]
    total_nonempty = len(nonempty)
    if total_nonempty > max_lines:
        return 0, 0, 0, 0, total_nonempty, "تعداد خطوط بیش از حد مجاز است."

    seen: set[str] = set()
    cleaned: list[str] = []
    dup_batch = 0
    invalid = 0
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if len(s) > max_link_len:
            invalid += 1
            continue
        if s in seen:
            dup_batch += 1
            continue
        seen.add(s)
        cleaned.append(s)

    if not cleaned:
        return 0, dup_batch, 0, invalid, total_nonempty, None

    bind = session.get_bind()
    dup_db = 0
    added = 0

    if bind is not None and bind.dialect.name == "postgresql":
        stmt = pg_insert(Link).values(
            [
                {
                    "server_id": server_id,
                    "plan_id": plan_id,
                    "link_text": t,
                    "status": LinkStatus.UNUSED,
                }
                for t in cleaned
            ]
        )
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["server_id", "plan_id", "link_text"]
        )
        r = await session.execute(stmt)
        added = int(r.rowcount or 0)
        dup_db = len(cleaned) - added
    else:
        for t in cleaned:
            exists = await session.execute(
                select(Link.id).where(
                    Link.server_id == server_id,
                    Link.plan_id == plan_id,
                    Link.link_text == t,
                )
            )
            if exists.scalar_one_or_none() is not None:
                dup_db += 1
                continue
            session.add(
                Link(
                    server_id=server_id,
                    plan_id=plan_id,
                    link_text=t,
                    status=LinkStatus.UNUSED,
                )
            )
            added += 1

    await session.flush()
    return added, dup_batch, dup_db, invalid, total_nonempty, None
