"""Marzban panel HTTP provider."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import aiohttp

from bot_app.providers.base import AccountUsage, PanelErrorCode, PanelProvider, ProviderResult
from bot_app.providers.http_util import request_json
from bot_app.security.crypto import decrypt_secret

logger = logging.getLogger("bot_app.providers")


class MarzbanProvider:
    """Async Marzban REST API client."""

    def __init__(
        self,
        *,
        request_id: str,
        base_url: str,
        username: str,
        password_encrypted: str,
        api_token_encrypted: Optional[str],
        encryption_key: str,
        verify_ssl: bool = True,
        timeout_seconds: int = 30,
        api_prefix: Optional[str] = None,
    ) -> None:
        self.request_id = request_id
        self.base_url = base_url.rstrip("/")
        self.username_plain = username
        self._password = decrypt_secret(encryption_key, password_encrypted)
        self._api_token = (
            decrypt_secret(encryption_key, api_token_encrypted) if api_token_encrypted else None
        )
        self.verify_ssl = verify_ssl
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.api_prefix = (api_prefix or os.environ.get("MARZBAN_API_PREFIX") or "/api").rstrip("/")
        self._access_token: Optional[str] = None

    def _headers(self) -> Dict[str, str]:
        if self._api_token:
            return {"Authorization": f"Bearer {self._api_token}"}
        if self._access_token:
            return {"Authorization": f"Bearer {self._access_token}"}
        return {}

    async def _ensure_token(self, session: aiohttp.ClientSession) -> None:
        if self._api_token or self._access_token:
            return
        url = f"{self.base_url}{self.api_prefix}/admin/token"
        status, body = await request_json(
            session,
            "POST",
            url,
            request_id=self.request_id,
            data=aiohttp.FormData(
                {
                    "username": self.username_plain,
                    "password": self._password,
                    "grant_type": "password",
                }
            ),
            ssl=self.verify_ssl,
            timeout=self.timeout,
        )
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError(f"token_failed status={status}")
        self._access_token = body.get("access_token")

    async def test_connection(self) -> ProviderResult:
        try:
            async with aiohttp.ClientSession() as session:
                await self._ensure_token(session)
                url = f"{self.base_url}{self.api_prefix}/admin"
                status, body = await request_json(
                    session,
                    "GET",
                    url,
                    request_id=self.request_id,
                    headers=self._headers(),
                    ssl=self.verify_ssl,
                    timeout=self.timeout,
                )
                if status == 401:
                    return ProviderResult(
                        ok=False,
                        error_code=PanelErrorCode.invalid_credentials,
                        error_message="احراز هویت ناموفق",
                        raw_status=status,
                    )
                if status >= 500:
                    return ProviderResult(
                        ok=False,
                        error_code=PanelErrorCode.panel_unavailable,
                        raw_status=status,
                    )
                ok = 200 <= status < 300
                return ProviderResult(ok=ok, data={"body": body}, raw_status=status)
        except aiohttp.ClientConnectorError:
            return ProviderResult(ok=False, error_code=PanelErrorCode.connection_refused)
        except aiohttp.ServerTimeoutError:
            return ProviderResult(ok=False, error_code=PanelErrorCode.connection_timeout)
        except aiohttp.ClientSSLError:
            return ProviderResult(ok=False, error_code=PanelErrorCode.ssl_error)
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_code=PanelErrorCode.unknown_provider_error,
                error_message=str(e),
            )

    async def create_account(
        self,
        username: str,
        quota_bytes: int,
        expire_timestamp_ms: int,
        inbound_id: Optional[int] = None,
        email: Optional[str] = None,
    ) -> ProviderResult:
        payload: Dict[str, Any] = {
            "username": username,
            "status": "active",
            "data_limit": quota_bytes if quota_bytes > 0 else None,
            "expire": expire_timestamp_ms // 1000 if expire_timestamp_ms else None,
        }
        if email:
            payload["note"] = email
        try:
            async with aiohttp.ClientSession() as session:
                await self._ensure_token(session)
                url = f"{self.base_url}{self.api_prefix}/user"
                status, body = await request_json(
                    session,
                    "POST",
                    url,
                    request_id=self.request_id,
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json_body=payload,
                    ssl=self.verify_ssl,
                    timeout=self.timeout,
                )
                if status == 409:
                    return ProviderResult(
                        ok=False,
                        error_code=PanelErrorCode.user_already_exists,
                        raw_status=status,
                    )
                if status == 422:
                    return ProviderResult(
                        ok=False,
                        error_code=PanelErrorCode.quota_error,
                        raw_status=status,
                    )
                if status == 401:
                    return ProviderResult(
                        ok=False,
                        error_code=PanelErrorCode.invalid_credentials,
                        raw_status=status,
                    )
                ok = 200 <= status < 300
                return ProviderResult(ok=ok, data={"response": body}, raw_status=status)
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_code=PanelErrorCode.unknown_provider_error,
                error_message=str(e),
            )

    async def get_account_usage(self, username: str) -> ProviderResult:
        try:
            async with aiohttp.ClientSession() as session:
                await self._ensure_token(session)
                url = f"{self.base_url}{self.api_prefix}/user/{username}/usage"
                status, body = await request_json(
                    session,
                    "GET",
                    url,
                    request_id=self.request_id,
                    headers=self._headers(),
                    ssl=self.verify_ssl,
                    timeout=self.timeout,
                )
                if status == 404:
                    return ProviderResult(ok=False, error_code=PanelErrorCode.user_not_found, raw_status=status)
                if not (200 <= status < 300):
                    return ProviderResult(ok=False, error_code=PanelErrorCode.panel_unavailable, raw_status=status)
                up = down = 0
                if isinstance(body, dict):
                    up = int(body.get("upload", 0) or 0)
                    down = int(body.get("download", 0) or 0)
                total = up + down
                return ProviderResult(
                    ok=True,
                    data={"usage": AccountUsage(upload_bytes=up, download_bytes=down, total_used_bytes=total)},
                )
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_code=PanelErrorCode.unknown_provider_error,
                error_message=str(e),
            )

    async def disable_account(self, username: str) -> ProviderResult:
        return await self._patch_status(username=username, status="disabled")

    async def enable_account(self, username: str) -> ProviderResult:
        return await self._patch_status(username=username, status="active")

    async def _patch_status(self, username: str, status: str) -> ProviderResult:
        try:
            async with aiohttp.ClientSession() as session:
                await self._ensure_token(session)
                url = f"{self.base_url}{self.api_prefix}/user/{username}"
                st, body = await request_json(
                    session,
                    "PUT",
                    url,
                    request_id=self.request_id,
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json_body={"status": status},
                    ssl=self.verify_ssl,
                    timeout=self.timeout,
                )
                ok = 200 <= st < 300
                return ProviderResult(ok=ok, data={"response": body}, raw_status=st)
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_code=PanelErrorCode.unknown_provider_error,
                error_message=str(e),
            )

    async def delete_account(self, username: str) -> ProviderResult:
        try:
            async with aiohttp.ClientSession() as session:
                await self._ensure_token(session)
                url = f"{self.base_url}{self.api_prefix}/user/{username}"
                st, body = await request_json(
                    session,
                    "DELETE",
                    url,
                    request_id=self.request_id,
                    headers=self._headers(),
                    ssl=self.verify_ssl,
                    timeout=self.timeout,
                )
                ok = st in (200, 204) or st == 404
                return ProviderResult(ok=ok, data={"response": body}, raw_status=st)
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_code=PanelErrorCode.unknown_provider_error,
                error_message=str(e),
            )

    async def get_config_links(self, username: str) -> ProviderResult:
        try:
            async with aiohttp.ClientSession() as session:
                await self._ensure_token(session)
                url = f"{self.base_url}{self.api_prefix}/user/{username}"
                st, body = await request_json(
                    session,
                    "GET",
                    url,
                    request_id=self.request_id,
                    headers=self._headers(),
                    ssl=self.verify_ssl,
                    timeout=self.timeout,
                )
                if st == 404:
                    return ProviderResult(ok=False, error_code=PanelErrorCode.user_not_found, raw_status=st)
                links: list[str] = []
                sub_url = None
                if isinstance(body, dict):
                    sub_url = body.get("subscription_url") or body.get("links", [None])[0]
                    if isinstance(body.get("links"), list):
                        links = [str(x) for x in body["links"] if x]
                    elif sub_url:
                        links = [str(sub_url)]
                return ProviderResult(
                    ok=True,
                    data={"subscription_url": sub_url, "links": links},
                    raw_status=st,
                )
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_code=PanelErrorCode.unknown_provider_error,
                error_message=str(e),
            )

    async def update_quota(self, username: str, quota_bytes: int) -> ProviderResult:
        try:
            async with aiohttp.ClientSession() as session:
                await self._ensure_token(session)
                url = f"{self.base_url}{self.api_prefix}/user/{username}"
                st, body = await request_json(
                    session,
                    "PUT",
                    url,
                    request_id=self.request_id,
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json_body={"data_limit": quota_bytes if quota_bytes > 0 else None},
                    ssl=self.verify_ssl,
                    timeout=self.timeout,
                )
                return ProviderResult(ok=200 <= st < 300, data={"response": body}, raw_status=st)
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_code=PanelErrorCode.unknown_provider_error,
                error_message=str(e),
            )

    async def update_expire(self, username: str, expire_timestamp_ms: int) -> ProviderResult:
        try:
            async with aiohttp.ClientSession() as session:
                await self._ensure_token(session)
                url = f"{self.base_url}{self.api_prefix}/user/{username}"
                st, body = await request_json(
                    session,
                    "PUT",
                    url,
                    request_id=self.request_id,
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json_body={"expire": expire_timestamp_ms // 1000},
                    ssl=self.verify_ssl,
                    timeout=self.timeout,
                )
                return ProviderResult(ok=200 <= st < 300, data={"response": body}, raw_status=st)
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_code=PanelErrorCode.unknown_provider_error,
                error_message=str(e),
            )
