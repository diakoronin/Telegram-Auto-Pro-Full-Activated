from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Link, LinkStatus


async def delete_unused_links(
    session: AsyncSession,
    *,
    server_id: int,
    plan_id: int,
) -> int:
    """Delete unused links for plan with row-level locking (SQLite/Postgres)."""
    stmt = (
        select(Link.id)
        .where(
            Link.server_id == server_id,
            Link.plan_id == plan_id,
            Link.status == LinkStatus.UNUSED,
        )
        .order_by(Link.id.asc())
        .with_for_update()
    )
    r = await session.execute(stmt)
    ids = [row[0] for row in r.all()]
    if not ids:
        return 0
    await session.execute(delete(Link).where(Link.id.in_(ids)))
    await session.flush()
    return len(ids)
