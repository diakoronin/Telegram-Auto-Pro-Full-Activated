"""Application configuration from environment."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(..., alias="BOT_TOKEN")
    owner_id: int = Field(..., alias="OWNER_ID")
    database_url: str = Field(..., alias="DATABASE_URL")

    public_base_url: str = Field("https://your-domain.com", alias="PUBLIC_BASE_URL")
    brand_name: str = Field("Sakabot", alias="BRAND_NAME")
    support_username: str = Field("", alias="SUPPORT_USERNAME")
    timezone: str = Field("Asia/Tehran", alias="TIMEZONE")
    footer_enabled: bool = Field(True, alias="FOOTER_ENABLED")

    subscription_endpoint_enabled: bool = Field(True, alias="SUBSCRIPTION_ENDPOINT_ENABLED")
    sub_base64_enabled: bool = Field(False, alias="SUB_BASE64_ENABLED")
    multi_backend_active: bool = Field(False, alias="MULTI_BACKEND_ACTIVE")

    traffic_sync_interval_seconds: int = Field(300, alias="TRAFFIC_SYNC_INTERVAL_SECONDS")
    traffic_sync_batch_size: int = Field(100, alias="TRAFFIC_SYNC_BATCH_SIZE")
    traffic_safety_buffer_mb: int = Field(200, alias="TRAFFIC_SAFETY_BUFFER_MB")

    location_change_enabled: bool = Field(True, alias="LOCATION_CHANGE_ENABLED")
    location_change_cooldown_hours: int = Field(24, alias="LOCATION_CHANGE_COOLDOWN_HOURS")
    location_change_max_per_month: int = Field(3, alias="LOCATION_CHANGE_MAX_PER_MONTH")
    location_change_require_admin_approval: bool = Field(
        False, alias="LOCATION_CHANGE_REQUIRE_ADMIN_APPROVAL"
    )
    location_change_fee: int = Field(0, alias="LOCATION_CHANGE_FEE")

    api_products_enabled: bool = Field(True, alias="API_PRODUCTS_ENABLED")
    manual_mode_enabled: bool = Field(True, alias="MANUAL_MODE_ENABLED")
    allow_user_manual_products: bool = Field(False, alias="ALLOW_USER_MANUAL_PRODUCTS")
    legacy_manual_mode: bool = Field(False, alias="LEGACY_MANUAL_MODE")

    show_full_card_number_to_user: bool = Field(True, alias="SHOW_FULL_CARD_NUMBER_TO_USER")
    debug_card_logging: bool = Field(False, alias="DEBUG_CARD_LOGGING")

    debug_mode: bool = Field(False, alias="DEBUG_MODE")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_to_file: bool = Field(True, alias="LOG_TO_FILE")
    log_dir: str = Field("logs", alias="LOG_DIR")

    auto_backup_enabled: bool = Field(True, alias="AUTO_BACKUP_ENABLED")
    auto_backup_interval_minutes: int = Field(60, alias="AUTO_BACKUP_INTERVAL_MINUTES")
    send_env_backup: bool = Field(False, alias="SEND_ENV_BACKUP")
    backup_unused_links: bool = Field(False, alias="BACKUP_UNUSED_LINKS")
    backup_retention_hourly: int = Field(48, alias="BACKUP_RETENTION_HOURLY")
    backup_retention_daily: int = Field(30, alias="BACKUP_RETENTION_DAILY")

    min_charge_amount: int = Field(10_000, alias="MIN_CHARGE_AMOUNT")
    max_charge_amount: int = Field(50_000_000, alias="MAX_CHARGE_AMOUNT")
    low_stock_threshold: int = Field(5, alias="LOW_STOCK_THRESHOLD")
    max_import_links: int = Field(1000, alias="MAX_IMPORT_LINKS")

    panel_credential_encryption_key: str = Field(..., alias="PANEL_CREDENTIAL_ENCRYPTION_KEY")

    subscription_api_host: str = Field("127.0.0.1", alias="SUBSCRIPTION_API_HOST")
    subscription_api_port: int = Field(8080, alias="SUBSCRIPTION_API_PORT")

    @field_validator("owner_id", mode="before")
    @classmethod
    def parse_owner(cls, v):
        if v is None or v == "":
            raise ValueError("OWNER_ID is required")
        return int(v)

    @property
    def public_base_url_normalized(self) -> str:
        return self.public_base_url.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def clear_settings_cache() -> None:
    get_settings.cache_clear()
