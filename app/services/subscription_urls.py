"""Stable per-service subscription URLs: one token per user_service, never per user."""

from __future__ import annotations

from app.config import Settings


def stable_subscription_url(settings: Settings, subscription_token: str) -> str:
    """Public URL for this service only; token is unique per user_services row."""
    base = (settings.public_base_url or "").strip().rstrip("/")
    tok = (subscription_token or "").strip()
    if not base or not tok:
        return ""
    return f"{base}/sub/{tok}"
