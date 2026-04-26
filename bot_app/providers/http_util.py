"""Shared aiohttp helpers for providers."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import aiohttp

from bot_app.utils.redact import redact_body_preview

logger = logging.getLogger("bot_app.providers")


async def request_json(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    request_id: str,
    headers: Optional[Dict[str, str]] = None,
    json_body: Any = None,
    data: Any = None,
    ssl: Any = True,
    timeout: aiohttp.ClientTimeout,
) -> tuple[int, Any]:
    extra = {"request_id": request_id}
    logger.info("[API REQUEST] %s %s", method, url, extra=extra)
    try:
        async with session.request(
            method,
            url,
            headers=headers,
            json=json_body,
            data=data,
            ssl=ssl,
            timeout=timeout,
        ) as resp:
            text = await resp.text()
            logger.info(
                "[API RESPONSE] status=%s preview=%s",
                resp.status,
                redact_body_preview(text),
                extra=extra,
            )
            try:
                body = json.loads(text) if text else None
            except json.JSONDecodeError:
                body = text
            return resp.status, body
    except aiohttp.ClientConnectorError as e:
        logger.warning("[API RESPONSE] connection_refused %s", e, extra=extra)
        raise
    except aiohttp.ServerTimeoutError as e:
        logger.warning("[API RESPONSE] timeout %s", e, extra=extra)
        raise
    except aiohttp.ClientSSLError as e:
        logger.warning("[API RESPONSE] ssl %s", e, extra=extra)
        raise
