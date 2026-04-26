from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from app.panel.errors import PanelErrorCode

logger = logging.getLogger("app.panel")


def _classify_exc(exc: BaseException) -> tuple[PanelErrorCode, str]:
    if isinstance(exc, httpx.TimeoutException):
        return PanelErrorCode.CONNECTION_TIMEOUT, str(exc) or "timeout"
    if isinstance(exc, httpx.ConnectError):
        return PanelErrorCode.CONNECTION_REFUSED, str(exc) or "connect error"
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code in (401, 403):
            return PanelErrorCode.INVALID_CREDENTIALS, str(exc)
        return PanelErrorCode.PANEL_UNAVAILABLE, str(exc)
    if isinstance(exc, ssl.SSLError):  # type: ignore[attr-defined]
        return PanelErrorCode.SSL_ERROR, str(exc)
    return PanelErrorCode.UNKNOWN_PROVIDER_ERROR, str(exc)


try:
    import ssl
except ImportError:
    ssl = None  # type: ignore


def redact_json(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in ("password", "token", "secret", "cookie", "authorization"):
                out[k] = "***"
            else:
                out[k] = redact_json(v)
        return out
    if isinstance(obj, list):
        return [redact_json(x) for x in obj]
    return obj


async def http_request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    data_form: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    timeout: float = 30.0,
    verify: bool = True,
) -> tuple[int, Any | None, str | None]:
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(verify=verify, timeout=timeout, follow_redirects=True) as client:
            r = await client.request(
                method,
                url,
                headers=headers,
                json=json_body,
                data=data_form,
                cookies=cookies,
            )
        ms = int((time.monotonic() - t0) * 1000)
        body: Any = None
        ct = (r.headers.get("content-type") or "").lower()
        if "json" in ct:
            try:
                body = r.json()
            except Exception:
                body = r.text
        else:
            body = r.text if r.text else None
        if r.status_code >= 400:
            err = str(body)[:500] if body else r.reason_phrase
            logger.warning("panel HTTP %s %s -> %s (%sms)", method, url, r.status_code, ms)
            return r.status_code, body, err
        logger.debug("panel HTTP %s %s -> %s (%sms)", method, url, r.status_code, ms)
        return r.status_code, body, None
    except Exception as e:
        code, msg = _classify_exc(e)
        logger.warning("panel HTTP fail %s %s: %s", method, url, msg)
        return 0, None, msg
