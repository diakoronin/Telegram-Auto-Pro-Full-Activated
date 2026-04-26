import asyncio
import os

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("BOT_TOKEN", "123456:ABC-DEF")
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:///:memory:",
)
os.environ.setdefault("PANEL_CREDENTIAL_ENCRYPTION_KEY", "test_encryption_key_16bytes!!")
os.environ.setdefault("PUBLIC_BASE_URL", "https://example.com")

from bot_app.config import clear_settings_cache, get_settings
from bot_app.migrations.runner import run_migrations


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture()
async def engine():
    clear_settings_cache()
    s = get_settings()
    eng = create_async_engine(s.database_url, echo=False)
    await run_migrations(eng)
    yield eng
    await eng.dispose()


@pytest.fixture()
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture()
async def session(session_factory) -> AsyncSession:
    async with session_factory() as s:
        yield s
