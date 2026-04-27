"""Verify Telegram initData HMAC (test vector from official algorithm)."""

import hashlib
import hmac
import urllib.parse

import pytest

from bot_app.webapp.telegram_webapp_auth import verify_telegram_webapp_init_data


def _make_valid_init_data(bot_token: str, user_id: int) -> str:
    user_json = f'{{"id":{user_id},"first_name":"A"}}'
    # raw pairs without hash
    data = {
        "user": user_json,
        "auth_date": "123",
        "query_id": "q1",
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    hsh = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    data["hash"] = hsh
    return urllib.parse.urlencode(data)


def test_init_data_rejects_tamper():
    bot = "test_token" * 4  # long enough
    s = _make_valid_init_data(bot, 99)
    assert verify_telegram_webapp_init_data(s, bot) is not None
    assert verify_telegram_webapp_init_data(s + "x", bot) is None
    assert verify_telegram_webapp_init_data("", bot) is None
