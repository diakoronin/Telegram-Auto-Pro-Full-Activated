from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RateLimitBucket


async def consume_rate(
    session: AsyncSession,
    *,
    key: str,
    window_seconds: int,
    max_count: int,
) -> bool:
    """Return True if allowed, False if rate limited."""
    now = datetime.now(tz=UTC)
    epoch = int(now.timestamp())
    bucket = epoch - (epoch % window_seconds)
    window_start = datetime.fromtimestamp(bucket, tz=UTC)
    q = await session.execute(
        select(RateLimitBucket).where(
            RateLimitBucket.key == key,
            RateLimitBucket.window_start == window_start,
        )
    )
    row = q.scalar_one_or_none()
    if row is None:
        session.add(
            RateLimitBucket(key=key, window_start=window_start, count=1)
        )
        await session.flush()
        return True
    if row.count >= max_count:
        return False
    row.count += 1
    await session.flush()
    return True


async def cleanup_old_buckets(session: AsyncSession, older_than_hours: int = 24) -> None:
    cutoff = datetime.now(tz=UTC) - timedelta(hours=older_than_hours)
    await session.execute(delete(RateLimitBucket).where(RateLimitBucket.window_start < cutoff))
