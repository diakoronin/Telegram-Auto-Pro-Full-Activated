"""Idempotent schema migrations for PostgreSQL and SQLite (dev)."""

from __future__ import annotations

import logging
from typing import List, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

logger = logging.getLogger(__name__)

MIGRATION_VERSION = 2


async def _table_exists(conn: AsyncConnection, name: str, dialect: str) -> bool:
    if dialect == "postgresql":
        r = await conn.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = :n"),
            {"n": name},
        )
        return r.scalar() is not None
    r = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": name},
    )
    return r.scalar() is not None


async def _column_exists(conn: AsyncConnection, table: str, column: str, dialect: str) -> bool:
    if dialect == "postgresql":
        r = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name=:t AND column_name=:c"
            ),
            {"t": table, "c": column},
        )
        return r.scalar() is not None
    r = await conn.execute(text(f'PRAGMA table_info("{table}")'))
    rows = r.fetchall()
    for row in rows:
        if row[1] == column:
            return True
    return False


async def _index_exists(conn: AsyncConnection, index_name: str, dialect: str) -> bool:
    if dialect == "postgresql":
        r = await conn.execute(
            text("SELECT 1 FROM pg_indexes WHERE indexname = :n"),
            {"n": index_name},
        )
        return r.scalar() is not None
    r = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='index' AND name=:n"),
        {"n": index_name},
    )
    return r.scalar() is not None


async def _exec_ignore(conn: AsyncConnection, sql: str, dialect: str) -> None:
    try:
        await conn.execute(text(sql))
    except Exception as e:
        if dialect == "sqlite" and "duplicate column" in str(e).lower():
            return
        if dialect == "postgresql" and "already exists" in str(e).lower():
            return
        raise


async def run_migrations(engine: AsyncEngine) -> None:
    dialect = engine.dialect.name
    logger.info("[DB WRITE] migration start dialect=%s", dialect)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                    if dialect == "postgresql"
                    else """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            r = await conn.execute(text("SELECT MAX(version) FROM schema_migrations"))
            max_applied = r.scalar()
            if max_applied is None:
                max_applied = 0

            def pg(sql: str) -> str:
                return sql

            def lite(sql: str) -> str:
                s = sql
                s = s.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
                s = s.replace("BIGSERIAL", "INTEGER")
                s = s.replace("BIGINT", "INTEGER")
                s = s.replace("SERIAL", "INTEGER")
                s = s.replace("BOOLEAN", "INTEGER")
                s = s.replace("JSONB", "TEXT")
                s = s.replace("TIMESTAMP WITH TIME ZONE", "TIMESTAMP")
                s = s.replace("DEFAULT false", "DEFAULT 0")
                s = s.replace("DEFAULT true", "DEFAULT 1")
                s = s.replace("DEFAULT now()", "DEFAULT CURRENT_TIMESTAMP")
                s = s.replace("DEFAULT CURRENT_TIMESTAMP", "DEFAULT CURRENT_TIMESTAMP")
                return s

            adapt = pg if dialect == "postgresql" else lite

            if max_applied < 1:
                # Core tables (CREATE IF NOT EXISTS style per table)
                stmts: List[Tuple[str, str]] = []

                users_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        telegram_id BIGINT NOT NULL UNIQUE,
                        username VARCHAR(255),
                        first_name VARCHAR(255),
                        last_name VARCHAR(255),
                        phone VARCHAR(32),
                        wallet_balance BIGINT NOT NULL DEFAULT 0,
                        is_blocked BOOLEAN NOT NULL DEFAULT false,
                        card_payment_enabled BOOLEAN NOT NULL DEFAULT true,
                        admin_note TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(users_sql))
    
                admins_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS admins (
                        id SERIAL PRIMARY KEY,
                        telegram_id BIGINT NOT NULL UNIQUE,
                        role VARCHAR(32) NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT true,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(admins_sql))
    
                app_settings_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS app_settings (
                        id SERIAL PRIMARY KEY,
                        key VARCHAR(128) NOT NULL UNIQUE,
                        value TEXT NOT NULL,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(app_settings_sql))
    
                panels_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS panels (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        type VARCHAR(32) NOT NULL,
                        base_url VARCHAR(512) NOT NULL,
                        web_base_path VARCHAR(255),
                        username VARCHAR(255) NOT NULL,
                        password_encrypted TEXT NOT NULL,
                        api_token_encrypted TEXT,
                        verify_ssl BOOLEAN NOT NULL DEFAULT true,
                        timeout_seconds INTEGER NOT NULL DEFAULT 30,
                        inbound_id INTEGER,
                        marzban_proxies_json JSONB,
                        marzban_inbounds_json JSONB,
                        is_active BOOLEAN NOT NULL DEFAULT true,
                        last_test_status VARCHAR(64),
                        last_test_error TEXT,
                        last_test_at TIMESTAMP WITH TIME ZONE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(panels_sql))
    
                servers_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS servers (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        location_label VARCHAR(255) NOT NULL,
                        panel_id INTEGER NOT NULL REFERENCES panels(id),
                        panel_type VARCHAR(32) NOT NULL,
                        inbound_id INTEGER,
                        template_id INTEGER,
                        is_active BOOLEAN NOT NULL DEFAULT true,
                        is_visible_to_users BOOLEAN NOT NULL DEFAULT true,
                        supports_location_change BOOLEAN NOT NULL DEFAULT true,
                        note TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(servers_sql))
    
                plans_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS plans (
                        id SERIAL PRIMARY KEY,
                        server_id INTEGER NOT NULL REFERENCES servers(id),
                        display_name VARCHAR(255) NOT NULL,
                        volume_gb INTEGER NOT NULL,
                        total_quota_bytes BIGINT NOT NULL,
                        duration_days INTEGER NOT NULL,
                        price BIGINT NOT NULL,
                        description TEXT,
                        is_active BOOLEAN NOT NULL DEFAULT true,
                        is_visible_to_users BOOLEAN NOT NULL DEFAULT true,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(plans_sql))
    
                purchases_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS purchases (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        user_telegram_id BIGINT NOT NULL,
                        purchase_type VARCHAR(16) NOT NULL,
                        user_service_id INTEGER,
                        manual_delivery_id INTEGER,
                        server_id INTEGER REFERENCES servers(id),
                        plan_id INTEGER REFERENCES plans(id),
                        manual_server_id INTEGER,
                        manual_plan_id INTEGER,
                        price BIGINT NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(purchases_sql))
    
                user_services_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS user_services (
                        id SERIAL PRIMARY KEY,
                        public_service_code VARCHAR(32) NOT NULL UNIQUE,
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        user_telegram_id BIGINT NOT NULL,
                        purchase_id INTEGER,
                        plan_id INTEGER NOT NULL REFERENCES plans(id),
                        current_server_id INTEGER NOT NULL REFERENCES servers(id),
                        custom_service_name VARCHAR(255) NOT NULL,
                        total_quota_bytes BIGINT NOT NULL,
                        used_traffic_bytes BIGINT NOT NULL DEFAULT 0,
                        remaining_traffic_bytes BIGINT NOT NULL,
                        expire_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        subscription_token VARCHAR(128) NOT NULL UNIQUE,
                        subscription_enabled BOOLEAN NOT NULL DEFAULT true,
                        location_change_enabled BOOLEAN NOT NULL DEFAULT true,
                        location_change_count INTEGER NOT NULL DEFAULT 0,
                        location_change_month_key VARCHAR(16),
                        last_location_change_at TIMESTAMP WITH TIME ZONE,
                        sync_failure_count INTEGER NOT NULL DEFAULT 0,
                        last_sync_error TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(user_services_sql))
    
                panel_accounts_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS panel_accounts (
                        id SERIAL PRIMARY KEY,
                        user_service_id INTEGER NOT NULL REFERENCES user_services(id),
                        panel_id INTEGER NOT NULL REFERENCES panels(id),
                        server_id INTEGER NOT NULL REFERENCES servers(id),
                        panel_type VARCHAR(32) NOT NULL,
                        panel_account_id VARCHAR(255),
                        username VARCHAR(255) NOT NULL,
                        config_links_json JSONB,
                        raw_subscription_url TEXT,
                        quota_bytes_assigned BIGINT NOT NULL,
                        usage_baseline_bytes BIGINT NOT NULL DEFAULT 0,
                        upload_bytes BIGINT NOT NULL DEFAULT 0,
                        download_bytes BIGINT NOT NULL DEFAULT 0,
                        total_used_bytes BIGINT NOT NULL DEFAULT 0,
                        final_used_bytes BIGINT,
                        last_synced_at TIMESTAMP WITH TIME ZONE,
                        activated_at TIMESTAMP WITH TIME ZONE,
                        disabled_at TIMESTAMP WITH TIME ZONE,
                        is_active BOOLEAN NOT NULL DEFAULT true,
                        status VARCHAR(32) NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(panel_accounts_sql))
    
                snapshots_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS traffic_usage_snapshots (
                        id SERIAL PRIMARY KEY,
                        user_service_id INTEGER NOT NULL REFERENCES user_services(id),
                        panel_account_id INTEGER NOT NULL REFERENCES panel_accounts(id),
                        upload_bytes BIGINT NOT NULL,
                        download_bytes BIGINT NOT NULL,
                        total_used_bytes BIGINT NOT NULL,
                        calculated_service_used_bytes BIGINT NOT NULL,
                        remaining_traffic_bytes BIGINT NOT NULL,
                        source_panel VARCHAR(255) NOT NULL,
                        request_id VARCHAR(64) NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(snapshots_sql))
    
                loc_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS location_change_requests (
                        id SERIAL PRIMARY KEY,
                        user_service_id INTEGER NOT NULL REFERENCES user_services(id),
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        from_server_id INTEGER NOT NULL REFERENCES servers(id),
                        to_server_id INTEGER NOT NULL REFERENCES servers(id),
                        status VARCHAR(32) NOT NULL,
                        fee_amount BIGINT NOT NULL DEFAULT 0,
                        admin_id INTEGER REFERENCES admins(id),
                        request_id VARCHAR(64) NOT NULL,
                        error_message TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(loc_sql))
    
                manual_servers_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS manual_servers (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        note TEXT,
                        is_active BOOLEAN NOT NULL DEFAULT true,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(manual_servers_sql))
    
                manual_plans_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS manual_plans (
                        id SERIAL PRIMARY KEY,
                        manual_server_id INTEGER NOT NULL REFERENCES manual_servers(id),
                        display_name VARCHAR(255) NOT NULL,
                        volume_label VARCHAR(64) NOT NULL,
                        price BIGINT,
                        description TEXT,
                        is_active BOOLEAN NOT NULL DEFAULT true,
                        is_visible_to_admins BOOLEAN NOT NULL DEFAULT true,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(manual_plans_sql))
    
                manual_links_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS manual_links (
                        id SERIAL PRIMARY KEY,
                        manual_server_id INTEGER NOT NULL REFERENCES manual_servers(id),
                        manual_plan_id INTEGER NOT NULL REFERENCES manual_plans(id),
                        link_text TEXT NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT true,
                        imported_by_admin_id INTEGER NOT NULL REFERENCES admins(id),
                        used_by_user_id INTEGER REFERENCES users(id),
                        used_by_admin_id INTEGER REFERENCES admins(id),
                        used_at TIMESTAMP WITH TIME ZONE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(manual_links_sql))
    
                manual_deliveries_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS manual_deliveries (
                        id SERIAL PRIMARY KEY,
                        manual_link_id INTEGER NOT NULL UNIQUE REFERENCES manual_links(id),
                        user_id INTEGER REFERENCES users(id),
                        user_telegram_id BIGINT,
                        admin_id INTEGER NOT NULL REFERENCES admins(id),
                        customer_info TEXT,
                        manual_server_id INTEGER NOT NULL REFERENCES manual_servers(id),
                        manual_plan_id INTEGER NOT NULL REFERENCES manual_plans(id),
                        status VARCHAR(32) NOT NULL,
                        delivered_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        returned_at TIMESTAMP WITH TIME ZONE,
                        return_reason TEXT
                    )
                    """
                )
                await conn.execute(text(manual_deliveries_sql))
    
                payment_cards_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS payment_cards (
                        id SERIAL PRIMARY KEY,
                        card_number VARCHAR(32) NOT NULL,
                        card_holder_name VARCHAR(255) NOT NULL,
                        bank_name VARCHAR(255) NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT true,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(payment_cards_sql))
    
                payment_requests_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS payment_requests (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        amount BIGINT NOT NULL,
                        card_id INTEGER NOT NULL REFERENCES payment_cards(id),
                        status VARCHAR(32) NOT NULL,
                        receipt_file_id VARCHAR(255),
                        approved_by_admin_id INTEGER REFERENCES admins(id),
                        locked_at TIMESTAMP WITH TIME ZONE,
                        expires_at TIMESTAMP WITH TIME ZONE,
                        request_id VARCHAR(64),
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(payment_requests_sql))
    
                wallet_tx_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS wallet_transactions (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        type VARCHAR(64) NOT NULL,
                        amount BIGINT NOT NULL,
                        balance_before BIGINT NOT NULL,
                        balance_after BIGINT NOT NULL,
                        reference VARCHAR(255),
                        purchase_id INTEGER REFERENCES purchases(id),
                        payment_request_id INTEGER REFERENCES payment_requests(id),
                        request_id VARCHAR(64),
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(wallet_tx_sql))
    
                tickets_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS support_tickets (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        user_service_id INTEGER REFERENCES user_services(id),
                        manual_delivery_id INTEGER REFERENCES manual_deliveries(id),
                        status VARCHAR(32) NOT NULL,
                        message TEXT NOT NULL,
                        admin_reply TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(tickets_sql))
    
                audit_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        id SERIAL PRIMARY KEY,
                        action VARCHAR(128) NOT NULL,
                        admin_telegram_id BIGINT,
                        user_telegram_id BIGINT,
                        details TEXT,
                        request_id VARCHAR(64),
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(audit_sql))
    
                # SQLite FK enforcement
                if dialect == "sqlite":
                    await conn.execute(text("PRAGMA foreign_keys = ON"))
    
                # Indexes (idempotent)
                indexes = [
                    ("ix_users_telegram_id", "CREATE INDEX IF NOT EXISTS ix_users_telegram_id ON users(telegram_id)"),
                    ("ix_admins_telegram_id", "CREATE INDEX IF NOT EXISTS ix_admins_telegram_id ON admins(telegram_id)"),
                    (
                        "ix_user_services_user_id",
                        "CREATE INDEX IF NOT EXISTS ix_user_services_user_id ON user_services(user_id)",
                    ),
                    (
                        "ix_user_services_status",
                        "CREATE INDEX IF NOT EXISTS ix_user_services_status ON user_services(status)",
                    ),
                    (
                        "ix_panel_accounts_user_service",
                        "CREATE INDEX IF NOT EXISTS ix_panel_accounts_user_service ON panel_accounts(user_service_id)",
                    ),
                    (
                        "ix_panel_accounts_active",
                        "CREATE INDEX IF NOT EXISTS ix_panel_accounts_active ON panel_accounts(is_active)",
                    ),
                    (
                        "ix_panel_accounts_status",
                        "CREATE INDEX IF NOT EXISTS ix_panel_accounts_status ON panel_accounts(status)",
                    ),
                    (
                        "ix_purchases_user_id",
                        "CREATE INDEX IF NOT EXISTS ix_purchases_user_id ON purchases(user_id)",
                    ),
                    (
                        "ix_purchases_created_at",
                        "CREATE INDEX IF NOT EXISTS ix_purchases_created_at ON purchases(created_at)",
                    ),
                    (
                        "ix_payment_requests_status",
                        "CREATE INDEX IF NOT EXISTS ix_payment_requests_status ON payment_requests(status)",
                    ),
                    (
                        "ix_wallet_tx_user",
                        "CREATE INDEX IF NOT EXISTS ix_wallet_tx_user ON wallet_transactions(user_id)",
                    ),
                    (
                        "ix_audit_created",
                        "CREATE INDEX IF NOT EXISTS ix_audit_created ON audit_logs(created_at)",
                    ),
                    (
                        "ix_tickets_user",
                        "CREATE INDEX IF NOT EXISTS ix_tickets_user ON support_tickets(user_id)",
                    ),
                    (
                        "ix_tickets_status",
                        "CREATE INDEX IF NOT EXISTS ix_tickets_status ON support_tickets(status)",
                    ),
                    (
                        "ix_manual_links_server",
                        "CREATE INDEX IF NOT EXISTS ix_manual_links_server ON manual_links(manual_server_id)",
                    ),
                    (
                        "ix_manual_links_plan",
                        "CREATE INDEX IF NOT EXISTS ix_manual_links_plan ON manual_links(manual_plan_id)",
                    ),
                    (
                        "ix_manual_links_status",
                        "CREATE INDEX IF NOT EXISTS ix_manual_links_status ON manual_links(status)",
                    ),
                ]
                for _, sql in indexes:
                    await conn.execute(text(sql))
    
                # Partial unique: one active panel_account per user_service (PostgreSQL)
                if dialect == "postgresql":
                    if not await _index_exists(conn, "uq_panel_account_one_active", dialect):
                        await conn.execute(
                            text(
                                """
                                CREATE UNIQUE INDEX uq_panel_account_one_active
                                ON panel_accounts (user_service_id)
                                WHERE is_active = true
                                """
                            )
                        )

                if dialect == "postgresql":
                    await conn.execute(
                        text(
                            """
                            INSERT INTO schema_migrations (version)
                            SELECT :v WHERE NOT EXISTS (
                                SELECT 1 FROM schema_migrations WHERE version = :v2
                            )
                            """
                        ),
                        {"v": 1, "v2": 1},
                    )
                else:
                    await conn.execute(
                        text("INSERT OR IGNORE INTO schema_migrations (version) VALUES (:v)"),
                        {"v": 1},
                    )

            if max_applied < 2 and not await _table_exists(conn, "education_articles", dialect):
                educ_sql = adapt(
                    """
                    CREATE TABLE IF NOT EXISTS education_articles (
                        id SERIAL PRIMARY KEY,
                        title VARCHAR(255) NOT NULL,
                        body_text TEXT NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT true,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                    )
                    """
                )
                await conn.execute(text(educ_sql))
            if max_applied < 2:
                if dialect == "postgresql":
                    await conn.execute(
                        text(
                            """
                            INSERT INTO schema_migrations (version)
                            SELECT :v WHERE NOT EXISTS (
                                SELECT 1 FROM schema_migrations WHERE version = :v2
                            )
                            """
                        ),
                        {"v": 2, "v2": 2},
                    )
                else:
                    await conn.execute(
                        text("INSERT OR IGNORE INTO schema_migrations (version) VALUES (:v)"),
                        {"v": 2},
                    )

        logger.info("[DB WRITE] migration success up_to version=%s", MIGRATION_VERSION)
    except Exception:
        logger.exception("[ERROR] migration failed")
        raise
