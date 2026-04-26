"""Public service codes, subscription tokens, backend usernames."""

from __future__ import annotations

import random
import secrets
import string


def generate_public_service_code() -> str:
    return "SVC" + "".join(random.choices(string.digits, k=6))


def generate_subscription_token() -> str:
    return secrets.token_urlsafe(32)


def backend_username(telegram_id: int, public_service_code: str, volume_gb: int) -> str:
    code = public_service_code.replace("-", "_")
    base = f"tg{telegram_id}_{code}_{volume_gb}gb"
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in base.lower())
    return safe[:60]
