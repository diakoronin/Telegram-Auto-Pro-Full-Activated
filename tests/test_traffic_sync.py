"""Traffic sync: owner alert when failure streak reaches threshold."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import Settings
from app.services.traffic_sync import FAIL_ALERT_STREAK, _streak_owner_alert


def _settings() -> Settings:
    return Settings(
        bot_token="t",
        owner_telegram_id=424242,
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


@pytest.mark.asyncio
async def test_owner_alert_when_panel_account_streak_reaches_threshold() -> None:
    settings = _settings()
    bot = AsyncMock()
    panel = MagicMock(id=7, name="Panel-A", traffic_sync_fail_streak=0)
    pa = MagicMock(id=3, username="acc", sync_fail_streak=FAIL_ALERT_STREAK)
    us = MagicMock(public_service_code="SVC999")
    await _streak_owner_alert(bot, settings, "سینک", panel, pa, us, "timeout")
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args[0][0] == settings.owner_telegram_id
    msg = bot.send_message.await_args[0][1]
    assert "سینک" in msg or "Panel" in msg or "SVC999" in msg
