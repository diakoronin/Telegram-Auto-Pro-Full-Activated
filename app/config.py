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


@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_telegram_id: int
    database_url: str
    low_stock_threshold: int
    min_charge_amount: int
    max_charge_amount: int
    max_import_links: int
    support_username: str
    large_wallet_adjustment_amount: int
    rate_limit_window_seconds: int
    rate_limit_start_max: int
    rate_limit_receipt_hour: int
    rate_limit_purchase_minute: int
    rate_limit_support_minute: int
    rate_limit_admin_import_minute: int


def load_settings() -> Settings:
    bot_token = _req("BOT_TOKEN")
    database_url = _req("DATABASE_URL")
    owner_id = int(_req("OWNER_ID"))
    support_username = _req("SUPPORT_USERNAME").lstrip("@")

    min_charge = _int("MIN_CHARGE_AMOUNT", 10_000)
    max_charge = _int("MAX_CHARGE_AMOUNT", 50_000_000)
    if min_charge <= 0 or max_charge < min_charge:
        raise RuntimeError("Invalid MIN_CHARGE_AMOUNT / MAX_CHARGE_AMOUNT")

    max_import = _int("MAX_IMPORT_LINKS", 1000)
    if max_import <= 0 or max_import > 50_000:
        raise RuntimeError("Invalid MAX_IMPORT_LINKS")

    return Settings(
        bot_token=bot_token,
        owner_telegram_id=owner_id,
        database_url=database_url,
        low_stock_threshold=max(0, _int("LOW_STOCK_THRESHOLD", 5)),
        min_charge_amount=min_charge,
        max_charge_amount=max_charge,
        max_import_links=max_import,
        support_username=support_username,
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
    )
