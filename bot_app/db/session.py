"""Async engine and session factory."""

from __future__ import annotations

from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine(database_url: str) -> AsyncEngine:
    global _engine
    if _engine is None:
        url = database_url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("sqlite://"):
            url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)
        _engine = create_async_engine(
            url,
            echo=False,
            pool_pre_ping=True,
            pool_size=20,
            max_overflow=40,
        )
    return _engine


def async_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        engine = get_engine(database_url)
        _session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    return _session_factory


async def get_session(database_url: str) -> AsyncGenerator[AsyncSession, None]:
    factory = async_session_factory(database_url)
    async with factory() as session:
        yield session


def reset_engine() -> None:
    global _engine, _session_factory
    _engine = None
    _session_factory = None
