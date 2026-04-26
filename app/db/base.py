from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(database_url: str) -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            database_url,
            echo=False,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(database_url),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def init_db(database_url: str) -> None:
    from app.db import models  # noqa: F401

    engine = get_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)


async def session_scope(
    database_url: str,
) -> AsyncIterator[AsyncSession]:
    factory = get_session_factory(database_url)
    async with factory() as session:
        yield session
