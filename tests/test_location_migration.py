"""Location migration validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.db.models import Server, User, UserService, UserServiceStatus
from app.services.location_migration import validate_location_change_prereqs


def _settings(**kwargs) -> Settings:
    base = dict(
        bot_token="t",
        owner_telegram_id=1,
        database_url="sqlite+aiosqlite:///:memory:",
        brand_name="x",
        low_stock_threshold=0,
        min_charge_amount=1,
        max_charge_amount=999,
        max_import_links=100,
        support_username="s",
        payment_expire_minutes=30,
        footer_enabled=True,
        timezone="Asia/Tehran",
        large_wallet_adjustment_amount=1,
        rate_limit_window_seconds=60,
        rate_limit_start_max=10,
        rate_limit_receipt_hour=5,
        rate_limit_purchase_minute=10,
        rate_limit_support_minute=5,
        rate_limit_admin_import_minute=3,
        public_base_url="",
        subscription_endpoint_enabled=False,
        sub_base64_enabled=False,
        subscription_bind_host="127.0.0.1",
        subscription_bind_port=8080,
        panel_credential_encryption_key="",
        multi_backend_active=False,
        traffic_sync_interval_seconds=60,
        traffic_safety_buffer_mb=0,
        traffic_sync_batch_size=10,
        location_change_enabled=True,
        location_change_cooldown_hours=24,
        location_change_max_per_month=3,
        location_change_require_admin_approval=False,
        location_change_fee=0,
        show_full_card_number_to_user=True,
        debug_card_logging=False,
        debug_mode=False,
        log_level="INFO",
        log_to_file=False,
        log_dir="logs",
        auto_backup_enabled=False,
        auto_backup_interval_minutes=60,
        send_env_backup=False,
        backup_unused_links=False,
        sub_rate_limit_per_minute=120,
        sub_ip_rate_limit_per_minute=300,
        delete_webhook_drop_pending=True,
        hourly_backup_retention=48,
        daily_backup_retention=30,
        backup_retention_hourly=48,
        backup_retention_daily=30,
        legacy_manual_mode=False,
    )
    base.update(kwargs)
    return Settings(**base)


@pytest.mark.asyncio
async def test_same_server_rejected() -> None:
    session = MagicMock()
    us = UserService(
        id=1,
        public_service_code="S",
        user_id=1,
        user_telegram_id=1,
        purchase_id=None,
        plan_id=1,
        custom_service_name="x",
        total_quota_bytes=1,
        used_traffic_bytes=0,
        remaining_traffic_bytes=1,
        expire_at=datetime.now(tz=UTC) + timedelta(days=1),
        status=UserServiceStatus.ACTIVE,
        subscription_token="t" * 32,
        subscription_enabled=True,
        location_change_enabled=True,
        location_change_count=0,
        location_change_month_key="",
        location_change_month_count=0,
        last_location_change_at=None,
        current_server_id=5,
    )
    tgt = Server(
        id=5,
        name="A",
        is_active=True,
        is_visible_to_users=True,
        supports_location_change=True,
        panel_id=1,
    )
    u = User(
        id=1,
        telegram_id=1,
        username=None,
        is_blocked=False,
        card_view_allowed=False,
        card_payment_enabled=True,
        wallet_balance=0,
    )
    ok, err = await validate_location_change_prereqs(
        session, settings=_settings(), us=us, target_server=tgt, user=u
    )
    assert ok is False
    assert err
