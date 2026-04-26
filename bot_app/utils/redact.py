"""Redact secrets from log messages."""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional


def redact_token(token: Optional[str], keep: int = 4) -> str:
    if not token:
        return ""
    if len(token) <= keep * 2:
        return "***"
    return f"{token[:keep]}…{token[-keep:]}"


def redact_card(card: Optional[str]) -> str:
    if not card or len(card) < 8:
        return "***"
    return f"{card[:4]}****{card[-4:]}"


def redact_headers(headers: Mapping[str, Any]) -> dict:
    out = {}
    sensitive = {"authorization", "cookie", "set-cookie", "x-api-key"}
    for k, v in headers.items():
        lk = str(k).lower()
        if lk in sensitive:
            out[k] = "***"
        else:
            out[k] = v
    return out


def redact_body_preview(body: str, max_len: int = 500) -> str:
    if not body:
        return ""
    s = body[:max_len]
    s = re.sub(r'"password"\s*:\s*"[^"]*"', '"password":"***"', s, flags=re.I)
    s = re.sub(r'"token"\s*:\s*"[^"]*"', '"token":"***"', s, flags=re.I)
    return s
