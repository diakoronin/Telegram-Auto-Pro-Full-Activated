"""Stock counts per plan/server (unused / used / inactive links)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Link, LinkStatus, Plan, Server


@dataclass(frozen=True)
class PlanStock:
    plan_id: int
    plan_name: str
    price: int
    unused_count: int
    used_count: int
    inactive_count: int

    @property
    def total_count(self) -> int:
        return self.unused_count + self.used_count + self.inactive_count


async def get_plan_stock(
    session: AsyncSession, *, server_id: int, plan_id: int
) -> tuple[int, int, int, int]:
    """Returns (unused, used, inactive, total) for one plan."""
    stmt = (
        select(Link.status, func.count())
        .where(Link.server_id == server_id, Link.plan_id == plan_id)
        .group_by(Link.status)
    )
    rows = (await session.execute(stmt)).all()
    counts: dict[str, int] = {}
    for st, cnt in rows:
        key = st.value if hasattr(st, "value") else str(st)
        counts[key] = int(cnt or 0)
    unused = counts.get(LinkStatus.UNUSED.value, 0)
    used = counts.get(LinkStatus.USED.value, 0)
    returned = counts.get(LinkStatus.RETURNED.value, 0)
    inactive = returned
    total = unused + used + inactive
    return unused, used, inactive, total


async def get_server_stock_summary(
    session: AsyncSession, *, server_id: int, only_active_plans: bool = True
) -> list[PlanStock]:
    q = select(Plan).where(Plan.server_id == server_id).order_by(Plan.id)
    if only_active_plans:
        q = q.where(Plan.is_active.is_(True))
    plans = (await session.execute(q)).scalars().all()
    out: list[PlanStock] = []
    for pl in plans:
        u, us, ia, _ = await get_plan_stock(session, server_id=server_id, plan_id=pl.id)
        out.append(
            PlanStock(
                plan_id=pl.id,
                plan_name=pl.name,
                price=int(pl.price),
                unused_count=u,
                used_count=us,
                inactive_count=ia,
            )
        )
    return out


@dataclass(frozen=True)
class ServerStockLine:
    server_id: int
    server_name: str
    plans: list[PlanStock]

    @property
    def total_unused(self) -> int:
        return sum(p.unused_count for p in self.plans)


async def get_all_servers_stock_summary(
    session: AsyncSession, *, only_active_servers: bool = True
) -> list[ServerStockLine]:
    q = select(Server).order_by(Server.id)
    if only_active_servers:
        q = q.where(Server.is_active.is_(True))
    servers = (await session.execute(q)).scalars().all()
    lines: list[ServerStockLine] = []
    for srv in servers:
        plans = await get_server_stock_summary(session, server_id=srv.id)
        lines.append(ServerStockLine(server_id=srv.id, server_name=srv.name, plans=plans))
    return lines


async def count_unused_for_plan(session: AsyncSession, *, plan_id: int) -> int:
    r = await session.execute(
        select(func.count())
        .select_from(Link)
        .where(Link.plan_id == plan_id, Link.status == LinkStatus.UNUSED)
    )
    return int(r.scalar_one() or 0)
