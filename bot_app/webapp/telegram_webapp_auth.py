"""Verify Telegram WebApp initData (HMAC per Telegram docs)."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl

logger = logging.getLogger(__name__)


def parse_init_data(init_data: str) -> Dict[str, str]:
    """Parse application/x-www-form-urlencoded initData into dict."""
    if not init_data or not init_data.strip():
        return {}
    return dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))


def verify_telegram_webapp_init_data(init_data: str, bot_token: str) -> Optional[Dict[str, Any]]:
    """
    Return parsed user payload if signature is valid, else None.
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app
    """
    if not init_data or not bot_token:
        return None
    try:
        parsed = parse_init_data(init_data)
    except Exception:
        return None
    if "hash" not in parsed:
        return None
    received_hash = parsed.pop("hash")
    # Build data_check_string: sorted key=value lines
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        logger.warning("[WEBAPP] initData hash mismatch")
        return None
    # user is JSON string in initData
    user_obj: Optional[Dict[str, Any]] = None
    if "user" in parsed:
        import json

        try:
            user_obj = json.loads(parsed["user"])
        except json.JSONDecodeError:
            return None
    return {
        "raw": parsed,
        "user": user_obj,
        "auth_date": parsed.get("auth_date"),
    }


def get_telegram_user_id_from_verified(verified: Dict[str, Any]) -> Optional[int]:
    u = verified.get("user")
    if isinstance(u, dict) and "id" in u:
        return int(u["id"])
    return None
