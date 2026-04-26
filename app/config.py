from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _req(name: str) -> str:
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return str(v).strip()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return int(raw)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_telegram_id: int
    database_url: str
    brand_name: str
    low_stock_threshold: int
    min_charge_amount: int
    max_charge_amount: int
    max_import_links: int
    support_username: str
    payment_expire_minutes: int
    footer_enabled: bool
    timezone: str
    large_wallet_adjustment_amount: int
    rate_limit_window_seconds: int
    rate_limit_start_max: int
    rate_limit_receipt_hour: int
    rate_limit_purchase_minute: int
    rate_limit_support_minute: int
    rate_limit_admin_import_minute: int
    public_base_url: str
    subscription_endpoint_enabled: bool
    sub_base64_enabled: bool
    subscription_bind_host: str
    subscription_bind_port: int
    panel_credential_encryption_key: str
    multi_backend_active: bool
    traffic_sync_interval_seconds: int
    traffic_safety_buffer_mb: int
    traffic_sync_batch_size: int
    location_change_enabled: bool
    location_change_cooldown_hours: int
    location_change_max_per_month: int
    location_change_require_admin_approval: bool
    location_change_fee: int
    show_full_card_number_to_user: bool
    debug_card_logging: bool
    debug_mode: bool
    log_level: str
    log_to_file: bool
    log_dir: str
    auto_backup_enabled: bool
    auto_backup_interval_minutes: int
    send_env_backup: bool
    backup_unused_links: bool
    sub_rate_limit_per_minute: int
    sub_ip_rate_limit_per_minute: int
    delete_webhook_drop_pending: bool
    hourly_backup_retention: int
    daily_backup_retention: int


def load_settings() -> Settings:
    bot_token = _req("BOT_TOKEN")
    database_url = _req("DATABASE_URL")
    owner_id = int(_req("OWNER_ID"))
    support_username = _req("SUPPORT_USERNAME").lstrip("@")
    brand_name = os.getenv("BRAND_NAME", "ساکانت").strip() or "ساکانت"
    pay_expire = max(5, min(7 * 24 * 60, _int("PAYMENT_EXPIRE_MINUTES", 30)))
    footer_enabled = _bool("FOOTER_ENABLED", True)
    timezone = os.getenv("TIMEZONE", "Asia/Tehran").strip() or "Asia/Tehran"

    min_charge = _int("MIN_CHARGE_AMOUNT", 10_000)
    max_charge = _int("MAX_CHARGE_AMOUNT", 50_000_000)
    if min_charge <= 0 or max_charge < min_charge:
        raise RuntimeError("Invalid MIN_CHARGE_AMOUNT / MAX_CHARGE_AMOUNT")

    max_import = _int("MAX_IMPORT_LINKS", 1000)
    if max_import <= 0 or max_import > 50_000:
        raise RuntimeError("Invalid MAX_IMPORT_LINKS")

    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    sub_enabled = _bool("SUBSCRIPTION_ENDPOINT_ENABLED", True)
    sub_b64 = _bool("SUB_BASE64_ENABLED", False)
    sub_host = os.getenv("SUBSCRIPTION_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"
    sub_port = max(1, min(65535, _int("SUBSCRIPTION_BIND_PORT", 8080)))
    panel_key = os.getenv("PANEL_CREDENTIAL_ENCRYPTION_KEY", "").strip()

    return Settings(
        bot_token=bot_token,
        owner_telegram_id=owner_id,
        database_url=database_url,
        brand_name=brand_name,
        low_stock_threshold=max(0, _int("LOW_STOCK_THRESHOLD", 5)),
        min_charge_amount=min_charge,
        max_charge_amount=max_charge,
        max_import_links=max_import,
        support_username=support_username,
        payment_expire_minutes=pay_expire,
        footer_enabled=footer_enabled,
        timezone=timezone,
        large_wallet_adjustment_amount=max(
            1, _int("LARGE_WALLET_ADJUSTMENT_AMOUNT", 10_000_000)
        ),
        rate_limit_window_seconds=max(5, _int("RATE_LIMIT_WINDOW_SECONDS", 60)),
        rate_limit_start_max=max(1, _int("RATE_LIMIT_START_MAX", 20)),
        rate_limit_receipt_hour=max(1, _int("RATE_LIMIT_RECEIPT_HOUR", 5)),
        rate_limit_purchase_minute=max(1, _int("RATE_LIMIT_PURCHASE_MINUTE", 10)),
        rate_limit_support_minute=max(1, _int("RATE_LIMIT_SUPPORT_MINUTE", 5)),
        rate_limit_admin_import_minute=max(
            1, _int("RATE_LIMIT_ADMIN_IMPORT_MINUTE", 3)
        ),
        public_base_url=public_base_url,
        subscription_endpoint_enabled=sub_enabled,
        sub_base64_enabled=sub_b64,
        subscription_bind_host=sub_host,
        subscription_bind_port=sub_port,
        panel_credential_encryption_key=panel_key,
        multi_backend_active=_bool("MULTI_BACKEND_ACTIVE", False),
        traffic_sync_interval_seconds=max(60, _int("TRAFFIC_SYNC_INTERVAL_SECONDS", 300)),
        traffic_safety_buffer_mb=max(0, _int("TRAFFIC_SAFETY_BUFFER_MB", 200)),
        traffic_sync_batch_size=max(1, min(500, _int("TRAFFIC_SYNC_BATCH_SIZE", 50))),
        location_change_enabled=_bool("LOCATION_CHANGE_ENABLED", True),
        location_change_cooldown_hours=max(1, _int("LOCATION_CHANGE_COOLDOWN_HOURS", 24)),
        location_change_max_per_month=max(1, _int("LOCATION_CHANGE_MAX_PER_MONTH", 3)),
        location_change_require_admin_approval=_bool(
            "LOCATION_CHANGE_REQUIRE_ADMIN_APPROVAL", False
        ),
        location_change_fee=max(0, _int("LOCATION_CHANGE_FEE", 0)),
        show_full_card_number_to_user=_bool("SHOW_FULL_CARD_NUMBER_TO_USER", True),
        debug_card_logging=_bool("DEBUG_CARD_LOGGING", False),
        debug_mode=_bool("DEBUG_MODE", False),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        log_to_file=_bool("LOG_TO_FILE", True),
        log_dir=os.getenv("LOG_DIR", "logs").strip() or "logs",
        auto_backup_enabled=_bool("AUTO_BACKUP_ENABLED", True),
        auto_backup_interval_minutes=max(15, _int("AUTO_BACKUP_INTERVAL_MINUTES", 60)),
        send_env_backup=_bool("SEND_ENV_BACKUP", False),
        backup_unused_links=_bool("BACKUP_UNUSED_LINKS", False),
        sub_rate_limit_per_minute=max(10, _int("SUB_RATE_LIMIT_PER_MINUTE", 120)),
        sub_ip_rate_limit_per_minute=max(30, _int("SUB_IP_RATE_LIMIT_PER_MINUTE", 300)),
        delete_webhook_drop_pending=_bool("DELETE_WEBHOOK_DROP_PENDING", True),
        hourly_backup_retention=max(1, _int("HOURLY_BACKUP_RETENTION", 48)),
        daily_backup_retention=max(1, _int("DAILY_BACKUP_RETENTION", 30)),
    )
