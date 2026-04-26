"""Request-scoped logging context and redaction helpers."""

from __future__ import annotations

import contextvars
import logging
import re
import secrets
import uuid
from typing import Any

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def set_request_id(rid: str | None) -> contextvars.Token[str | None]:
    return _request_id.set(rid)


def reset_request_id(token: contextvars.Token[str | None]) -> None:
    _request_id.reset(token)


def get_request_id() -> str:
    v = _request_id.get()
    return v if v else "-"


def mask_subscription_token(tok: str | None) -> str:
    if not tok or len(tok) < 8:
        return "***"
    return f"{tok[:4]}…{tok[-4:]}"


def redact_secrets(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in (
                "password",
                "token",
                "access_token",
                "secret",
                "cookie",
                "authorization",
                "set-cookie",
            ):
                out[k] = "***"
            else:
                out[k] = redact_secrets(v)
        return out
    if isinstance(obj, list):
        return [redact_secrets(x) for x in obj]
    if isinstance(obj, str) and re.search(r"(?i)(bearer\s+|password=|token=)", obj):
        return "[redacted string]"
    return obj


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id()
        if not hasattr(record, "service_code"):
            record.service_code = getattr(record, "service_code", "-")
        if not hasattr(record, "user_id"):
            record.user_id = getattr(record, "user_id", "-")
        return True
