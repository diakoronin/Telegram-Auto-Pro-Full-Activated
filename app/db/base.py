from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
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
    await migrate_schema(database_url)


async def migrate_schema(database_url: str) -> None:
    """Lightweight additive migrations for existing SQLite/Postgres DBs."""
    engine = get_engine(database_url)
    dialect = engine.dialect.name
    async with engine.begin() as conn:
        if dialect == "sqlite":
            r = await conn.execute(text("PRAGMA table_info(plans)"))
            cols = {row[1] for row in r.fetchall()}
            if "low_stock_rearm" not in cols:
                await conn.execute(
                    text(
                        "ALTER TABLE plans ADD COLUMN low_stock_rearm "
                        "BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
            r2 = await conn.execute(text("PRAGMA table_info(users)"))
            ucols = {row[1] for row in r2.fetchall()}
            if "card_view_allowed" not in ucols:
                await conn.execute(
                    text(
                        "ALTER TABLE users ADD COLUMN card_view_allowed "
                        "BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
        elif dialect == "postgresql":
            await conn.execute(
                text(
                    "ALTER TABLE plans ADD COLUMN IF NOT EXISTS low_stock_rearm "
                    "BOOLEAN NOT NULL DEFAULT false"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS card_view_allowed "
                    "BOOLEAN NOT NULL DEFAULT false"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS card_payment_enabled "
                    "BOOLEAN NOT NULL DEFAULT true"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE payment_cards ADD COLUMN IF NOT EXISTS card_number_full "
                    "VARCHAR(32)"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE payment_cards ADD COLUMN IF NOT EXISTS is_public "
                    "BOOLEAN NOT NULL DEFAULT true"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE payment_requests ADD COLUMN IF NOT EXISTS assigned_card_id "
                    "INTEGER REFERENCES payment_cards(id)"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE payment_requests ADD COLUMN IF NOT EXISTS expires_at "
                    "TIMESTAMPTZ"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE plans ADD COLUMN IF NOT EXISTS display_name VARCHAR(120)"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS server_id "
                    "INTEGER REFERENCES servers(id)"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS custom_service_name "
                    "VARCHAR(120)"
                )
            )
            await conn.execute(
                text(
                    "UPDATE purchases p SET server_id = pl.server_id "
                    "FROM plans pl WHERE pl.id = p.plan_id AND p.server_id IS NULL"
                )
            )
            await conn.execute(
                text(
                    "UPDATE purchases p SET custom_service_name = COALESCE(NULLIF(TRIM(pl.display_name), ''), pl.name) "
                    "FROM plans pl WHERE pl.id = p.plan_id AND (p.custom_service_name IS NULL OR p.custom_service_name = '')"
                )
            )
            try:
                await conn.execute(
                    text("ALTER TABLE purchases ALTER COLUMN server_id SET NOT NULL")
                )
                await conn.execute(
                    text(
                        "ALTER TABLE purchases ALTER COLUMN custom_service_name SET NOT NULL"
                    )
                )
            except Exception:
                pass
        if dialect == "sqlite":
            r3 = await conn.execute(text("PRAGMA table_info(users)"))
            ucols3 = {row[1] for row in r3.fetchall()}
            if "card_payment_enabled" not in ucols3:
                await conn.execute(
                    text(
                        "ALTER TABLE users ADD COLUMN card_payment_enabled "
                        "BOOLEAN NOT NULL DEFAULT 1"
                    )
                )
            r4 = await conn.execute(text("PRAGMA table_info(payment_cards)"))
            ccols = {row[1] for row in r4.fetchall()}
            if "card_number_full" not in ccols:
                await conn.execute(
                    text("ALTER TABLE payment_cards ADD COLUMN card_number_full VARCHAR(32)")
                )
            if "is_public" not in ccols:
                await conn.execute(
                    text(
                        "ALTER TABLE payment_cards ADD COLUMN is_public "
                        "BOOLEAN NOT NULL DEFAULT 1"
                    )
                )
            r5 = await conn.execute(text("PRAGMA table_info(payment_requests)"))
            prcols = {row[1] for row in r5.fetchall()}
            if "assigned_card_id" not in prcols:
                await conn.execute(
                    text(
                        "ALTER TABLE payment_requests ADD COLUMN assigned_card_id "
                        "INTEGER REFERENCES payment_cards(id)"
                    )
                )
            if "expires_at" not in prcols:
                await conn.execute(
                    text("ALTER TABLE payment_requests ADD COLUMN expires_at DATETIME")
                )
            r_pl = await conn.execute(text("PRAGMA table_info(plans)"))
            plcols = {row[1] for row in r_pl.fetchall()}
            if "display_name" not in plcols:
                await conn.execute(
                    text("ALTER TABLE plans ADD COLUMN display_name VARCHAR(120)")
                )
            r_pur2 = await conn.execute(text("PRAGMA table_info(purchases)"))
            purcols = {row[1] for row in r_pur2.fetchall()}
            if "server_id" not in purcols:
                await conn.execute(
                    text("ALTER TABLE purchases ADD COLUMN server_id INTEGER REFERENCES servers(id)")
                )
            if "custom_service_name" not in purcols:
                await conn.execute(
                    text("ALTER TABLE purchases ADD COLUMN custom_service_name VARCHAR(120)")
                )
            await conn.execute(
                text(
                    "UPDATE purchases SET server_id = "
                    "(SELECT server_id FROM plans WHERE plans.id = purchases.plan_id) "
                    "WHERE server_id IS NULL"
                )
            )
            await conn.execute(
                text(
                    "UPDATE purchases SET custom_service_name = "
                    "(SELECT COALESCE(NULLIF(TRIM(display_name), ''), name) FROM plans WHERE plans.id = purchases.plan_id) "
                    "WHERE custom_service_name IS NULL OR custom_service_name = ''"
                )
            )


async def session_scope(
    database_url: str,
) -> AsyncIterator[AsyncSession]:
    factory = get_session_factory(database_url)
    async with factory() as session:
        yield session
