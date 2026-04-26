"""Per-purchased-service secrets: each user_services row gets its own subscription_token."""

from __future__ import annotations

import secrets


def generate_subscription_token() -> str:
    """URL-safe random token; unique per user_service (not per Telegram user)."""
    return secrets.token_urlsafe(32)


def generate_public_service_code() -> str:
    """Non-secret public code SVC + 6 digits."""
    return f"SVC{secrets.randbelow(1_000_000):06d}"
