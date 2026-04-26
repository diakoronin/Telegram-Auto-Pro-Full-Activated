"""Idempotent schema migrations; logs to standard logging."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger("app.migrate")


async def run_migrations(conn: AsyncConnection, dialect: str) -> None:
    logger.info("migrate_schema: start dialect=%s", dialect)

    if dialect == "postgresql":
        await _migrate_postgresql(conn)
    elif dialect == "sqlite":
        await _migrate_sqlite(conn)
    else:
        logger.warning("migrate_schema: unknown dialect %s, skipping alters", dialect)

    logger.info("migrate_schema: completed dialect=%s", dialect)


async def _migrate_postgresql(conn: AsyncConnection) -> None:
    stmts = [
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS low_stock_rearm BOOLEAN NOT NULL DEFAULT false",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS card_view_allowed BOOLEAN NOT NULL DEFAULT false",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS card_payment_enabled BOOLEAN NOT NULL DEFAULT true",
        "ALTER TABLE payment_cards ADD COLUMN IF NOT EXISTS card_number_full VARCHAR(32)",
        "ALTER TABLE payment_cards ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT true",
        "ALTER TABLE payment_requests ADD COLUMN IF NOT EXISTS assigned_card_id INTEGER REFERENCES payment_cards(id)",
        "ALTER TABLE payment_requests ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ",
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS display_name VARCHAR(120)",
        "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS server_id INTEGER REFERENCES servers(id)",
        "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS custom_service_name VARCHAR(120)",
        """UPDATE purchases p SET server_id = pl.server_id FROM plans pl
           WHERE pl.id = p.plan_id AND p.server_id IS NULL""",
        """UPDATE purchases p SET custom_service_name = COALESCE(NULLIF(TRIM(pl.display_name), ''), pl.name)
           FROM plans pl WHERE pl.id = p.plan_id AND (p.custom_service_name IS NULL OR p.custom_service_name = '')""",
        "ALTER TABLE servers ADD COLUMN IF NOT EXISTS location_label VARCHAR(120)",
        "ALTER TABLE servers ADD COLUMN IF NOT EXISTS panel_id INTEGER REFERENCES panels(id)",
        "ALTER TABLE servers ADD COLUMN IF NOT EXISTS panel_type VARCHAR(32)",
        "ALTER TABLE servers ADD COLUMN IF NOT EXISTS inbound_id INTEGER",
        "ALTER TABLE servers ADD COLUMN IF NOT EXISTS template_id INTEGER",
        "ALTER TABLE servers ADD COLUMN IF NOT EXISTS is_visible_to_users BOOLEAN NOT NULL DEFAULT true",
        "ALTER TABLE servers ADD COLUMN IF NOT EXISTS supports_location_change BOOLEAN NOT NULL DEFAULT true",
        "ALTER TABLE servers ADD COLUMN IF NOT EXISTS note TEXT",
        "ALTER TABLE servers ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS volume_gb INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS is_visible_to_users BOOLEAN NOT NULL DEFAULT true",
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS duration_days INTEGER NOT NULL DEFAULT 30",
        "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS user_service_id INTEGER",
        "ALTER TABLE panels ADD COLUMN IF NOT EXISTS marzban_proxies_json JSONB",
        "ALTER TABLE panels ADD COLUMN IF NOT EXISTS marzban_inbounds_json JSONB",
        "ALTER TABLE user_services ADD COLUMN IF NOT EXISTS location_change_month_key VARCHAR(7) NOT NULL DEFAULT ''",
        "ALTER TABLE user_services ADD COLUMN IF NOT EXISTS location_change_month_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS user_service_id INTEGER REFERENCES user_services(id)",
        """ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'open'""",
        "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS admin_reply TEXT",
        "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ]
    for s in stmts:
        try:
            await conn.execute(text(s))
        except Exception as e:
            logger.warning("migrate_schema PG stmt warn: %s | %s", e, s[:120])

    for alter in (
        "ALTER TABLE purchases ALTER COLUMN server_id SET NOT NULL",
        "ALTER TABLE purchases ALTER COLUMN custom_service_name SET NOT NULL",
    ):
        try:
            await conn.execute(text(alter))
        except Exception as e:
            logger.debug("migrate_schema PG optional: %s", e)

    try:
        await conn.execute(text("ALTER TABLE purchases ALTER COLUMN link_id DROP NOT NULL"))
    except Exception as e:
        logger.debug("migrate_schema link_id nullable: %s", e)

    # PurchaseStatus.pending for saga (PostgreSQL native enum)
    await conn.execute(
        text(
            """
            DO $migration$
            BEGIN
              IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'purchasestatus') THEN
                IF NOT EXISTS (
                  SELECT 1 FROM pg_enum e
                  JOIN pg_type t ON e.enumtypid = t.oid
                  WHERE t.typname = 'purchasestatus' AND e.enumlabel = 'pending'
                ) THEN
                  ALTER TYPE purchasestatus ADD VALUE 'pending';
                END IF;
              END IF;
            END
            $migration$;
            """
        )
    )

    try:
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_panel_accounts_one_active_per_service "
                "ON panel_accounts (user_service_id) WHERE is_active = true"
            )
        )
    except Exception as e:
        logger.warning("migrate_schema: partial unique index panel_accounts: %s", e)

    try:
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admins_telegram_id ON admins (telegram_id)"))
    except Exception as e:
        logger.debug("migrate_schema admins index: %s", e)


async def _migrate_sqlite(conn: AsyncConnection) -> None:
    async def cols(table: str) -> set[str]:
        r = await conn.execute(text(f"PRAGMA table_info({table})"))
        return {row[1] for row in r.fetchall()}

    # users
    ucols = await cols("users")
    if "card_view_allowed" not in ucols:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN card_view_allowed BOOLEAN NOT NULL DEFAULT 0")
        )
    if "card_payment_enabled" not in ucols:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN card_payment_enabled BOOLEAN NOT NULL DEFAULT 1")
        )

    # payment_cards
    ccols = await cols("payment_cards")
    if "card_number_full" not in ccols:
        await conn.execute(text("ALTER TABLE payment_cards ADD COLUMN card_number_full VARCHAR(32)"))
    if "is_public" not in ccols:
        await conn.execute(
            text("ALTER TABLE payment_cards ADD COLUMN is_public BOOLEAN NOT NULL DEFAULT 1")
        )

    # payment_requests
    prcols = await cols("payment_requests")
    if "assigned_card_id" not in prcols:
        await conn.execute(
            text(
                "ALTER TABLE payment_requests ADD COLUMN assigned_card_id INTEGER REFERENCES payment_cards(id)"
            )
        )
    if "expires_at" not in prcols:
        await conn.execute(text("ALTER TABLE payment_requests ADD COLUMN expires_at DATETIME"))

    # plans
    plcols = await cols("plans")
    if "low_stock_rearm" not in plcols:
        await conn.execute(text("ALTER TABLE plans ADD COLUMN low_stock_rearm BOOLEAN NOT NULL DEFAULT 0"))
    if "display_name" not in plcols:
        await conn.execute(text("ALTER TABLE plans ADD COLUMN display_name VARCHAR(120)"))
    if "volume_gb" not in plcols:
        await conn.execute(text("ALTER TABLE plans ADD COLUMN volume_gb INTEGER NOT NULL DEFAULT 1"))
    if "is_visible_to_users" not in plcols:
        await conn.execute(
            text("ALTER TABLE plans ADD COLUMN is_visible_to_users BOOLEAN NOT NULL DEFAULT 1")
        )
    if "duration_days" not in plcols:
        await conn.execute(text("ALTER TABLE plans ADD COLUMN duration_days INTEGER NOT NULL DEFAULT 30"))

    # servers
    try:
        scols = await cols("servers")
    except Exception:
        scols = set()
    if scols:
        adds = [
            ("location_label", "VARCHAR(120)"),
            ("panel_id", "INTEGER"),
            ("panel_type", "VARCHAR(32)"),
            ("inbound_id", "INTEGER"),
            ("template_id", "INTEGER"),
            ("is_visible_to_users", "BOOLEAN NOT NULL DEFAULT 1"),
            ("supports_location_change", "BOOLEAN NOT NULL DEFAULT 1"),
            ("note", "TEXT"),
            ("updated_at", "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"),
        ]
        for col, typ in adds:
            if col not in scols:
                await conn.execute(text(f"ALTER TABLE servers ADD COLUMN {col} {typ}"))

    # purchases
    purcols = await cols("purchases")
    if "server_id" not in purcols:
        await conn.execute(text("ALTER TABLE purchases ADD COLUMN server_id INTEGER REFERENCES servers(id)"))
    if "custom_service_name" not in purcols:
        await conn.execute(text("ALTER TABLE purchases ADD COLUMN custom_service_name VARCHAR(120)"))
    if "user_service_id" not in purcols:
        await conn.execute(text("ALTER TABLE purchases ADD COLUMN user_service_id INTEGER"))
    await conn.execute(
        text(
            "UPDATE purchases SET server_id = (SELECT server_id FROM plans WHERE plans.id = purchases.plan_id) "
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

    try:
        pcols = await cols("panels")
        if pcols:
            if "marzban_proxies_json" not in pcols:
                await conn.execute(text("ALTER TABLE panels ADD COLUMN marzban_proxies_json TEXT"))
            if "marzban_inbounds_json" not in pcols:
                await conn.execute(text("ALTER TABLE panels ADD COLUMN marzban_inbounds_json TEXT"))
    except Exception:
        pass

    try:
        uscols = await cols("user_services")
        if uscols:
            if "location_change_month_key" not in uscols:
                await conn.execute(
                    text(
                        "ALTER TABLE user_services ADD COLUMN location_change_month_key VARCHAR(7) NOT NULL DEFAULT ''"
                    )
                )
            if "location_change_month_count" not in uscols:
                await conn.execute(
                    text(
                        "ALTER TABLE user_services ADD COLUMN location_change_month_count INTEGER NOT NULL DEFAULT 0"
                    )
                )
    except Exception:
        pass

    # support_tickets
    try:
        tcols = await cols("support_tickets")
        if "user_service_id" not in tcols:
            await conn.execute(text("ALTER TABLE support_tickets ADD COLUMN user_service_id INTEGER"))
        if "status" not in tcols:
            await conn.execute(
                text("ALTER TABLE support_tickets ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'open'")
            )
        if "admin_reply" not in tcols:
            await conn.execute(text("ALTER TABLE support_tickets ADD COLUMN admin_reply TEXT"))
        if "updated_at" not in tcols:
            await conn.execute(
                text("ALTER TABLE support_tickets ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP")
            )
    except Exception:
        pass
