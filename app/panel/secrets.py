from __future__ import annotations

from app.config import Settings
from app.crypto_store import decrypt_secret
from app.db.models import Panel


def panel_plain_password(panel: Panel, settings: Settings) -> str:
    return decrypt_secret(panel.password_encrypted, encryption_key=settings.panel_credential_encryption_key) or ""


def panel_plain_api_token(panel: Panel, settings: Settings) -> str | None:
    if not panel.api_token_encrypted:
        return None
    return decrypt_secret(panel.api_token_encrypted, encryption_key=settings.panel_credential_encryption_key)
