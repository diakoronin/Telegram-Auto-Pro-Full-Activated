"""MHSanaei / 3x-ui panel provider (cookie session).

API base: {base}{web_base_path}/panel/api/inbounds
See: https://github.com/MHSanaei/3x-ui Postman collection.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, Optional

import aiohttp

from bot_app.providers.base import AccountUsage, PanelErrorCode, ProviderResult
from bot_app.providers.http_util import request_json
from bot_app.security.crypto import decrypt_secret

logger = logging.getLogger("bot_app.providers")


class Sanaei3xuiProvider:
    """3x-ui async client using session cookies."""

    def __init__(
        self,
        *,
        request_id: str,
        base_url: str,
        web_base_path: Optional[str],
        username: str,
        password_encrypted: str,
        encryption_key: str,
        default_inbound_id: Optional[int],
        verify_ssl: bool = True,
        timeout_seconds: int = 30,
    ) -> None:
        self.request_id = request_id
        self.base_url = base_url.rstrip("/")
        wb = (web_base_path or "").strip().strip("/")
        self._root = f"{self.base_url}/{wb}" if wb else self.base_url
        self.username_plain = username
        self._password = decrypt_secret(encryption_key, password_encrypted)
        self.verify_ssl = verify_ssl
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._cookie_jar = aiohttp.CookieJar(unsafe=True)
        self._default_inbound_id = default_inbound_id

    def _inbounds_api(self, tail: str) -> str:
        t = tail if tail.startswith("/") else "/" + tail
        return f"{self._root}/panel/api/inbounds{t}"

    def _login_url(self) -> str:
        return f"{self._root}/login"

    async def _login(self, session: aiohttp.ClientSession) -> ProviderResult:
        login_url = self._login_url()
        data = aiohttp.FormData()
        data.add_field("username", self.username_plain)
        data.add_field("password", self._password)
        status, body = await request_json(
            session,
            "POST",
            login_url,
            request_id=self.request_id,
            data=data,
            ssl=self.verify_ssl,
            timeout=self.timeout,
        )
        if status != 200:
            return ProviderResult(
                ok=False,
                error_code=PanelErrorCode.invalid_credentials,
                raw_status=status,
            )
        if isinstance(body, dict) and body.get("success") is False:
            return ProviderResult(
                ok=False,
                error_code=PanelErrorCode.invalid_credentials,
                error_message=str(body.get("msg")),
            )
        return ProviderResult(ok=True)

    async def test_connection(self) -> ProviderResult:
        try:
            async with aiohttp.ClientSession(cookie_jar=self._cookie_jar) as session:
                r = await self._login(session)
                if not r.ok:
                    return r
                url = self._inbounds_api("/list")
                st, body = await request_json(
                    session,
                    "GET",
                    url,
                    request_id=self.request_id,
                    ssl=self.verify_ssl,
                    timeout=self.timeout,
                )
                if st in (401, 403):
                    await self._login(session)
                    st, body = await request_json(
                        session,
                        "GET",
                        url,
                        request_id=self.request_id,
                        ssl=self.verify_ssl,
                        timeout=self.timeout,
                    )
                if st != 200:
                    return ProviderResult(
                        ok=False,
                        error_code=PanelErrorCode.panel_unavailable,
                        raw_status=st,
                    )
                if self._default_inbound_id is not None:
                    ib = await self._find_inbound(session, self._default_inbound_id)
                    if not ib:
                        return ProviderResult(
                            ok=False,
                            error_code=PanelErrorCode.invalid_inbound,
                            error_message="inbound_not_found",
                        )
                return ProviderResult(ok=True, data={"inbounds": body})
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

    async def _find_inbound(self, session: aiohttp.ClientSession, inbound_id: int) -> Optional[dict]:
        url = self._inbounds_api("/list")
        st, body = await request_json(
            session,
            "GET",
            url,
            request_id=self.request_id,
            ssl=self.verify_ssl,
            timeout=self.timeout,
        )
        if st in (401, 403):
            await self._login(session)
            st, body = await request_json(
                session,
                "GET",
                url,
                request_id=self.request_id,
                ssl=self.verify_ssl,
                timeout=self.timeout,
            )
        if st != 200 or not isinstance(body, dict):
            return None
        obj = body.get("obj")
        if not isinstance(obj, list):
            return None
        for ib in obj:
            if int(ib.get("id", -1)) == inbound_id:
                return ib
        return None

    async def create_account(
        self,
        username: str,
        quota_bytes: int,
        expire_timestamp_ms: int,
        inbound_id: Optional[int] = None,
        email: Optional[str] = None,
    ) -> ProviderResult:
        ib_id = inbound_id or self._default_inbound_id
        if ib_id is None:
            return ProviderResult(ok=False, error_code=PanelErrorCode.invalid_inbound)
        client_id = str(uuid.uuid4())
        remark = username
        total_gb = round(quota_bytes / (1024**3), 6) if quota_bytes else 0
        client_obj = {
            "id": client_id,
            "email": remark,
            "limitIp": 0,
            "totalGB": total_gb,
            "expiryTime": expire_timestamp_ms,
            "enable": True,
            "tgId": "",
            "subId": "",
        }
        settings_wrapper = {"clients": [client_obj]}
        try:
            async with aiohttp.ClientSession(cookie_jar=self._cookie_jar) as session:
                lr = await self._login(session)
                if not lr.ok:
                    return lr
                ib = await self._find_inbound(session, ib_id)
                if not ib:
                    return ProviderResult(ok=False, error_code=PanelErrorCode.invalid_inbound)
                url = self._inbounds_api("/addClient")
                payload = {
                    "id": ib_id,
                    "settings": json.dumps(settings_wrapper),
                }
                st, body = await request_json(
                    session,
                    "POST",
                    url,
                    request_id=self.request_id,
                    json_body=payload,
                    ssl=self.verify_ssl,
                    timeout=self.timeout,
                )
                if st in (401, 403):
                    await self._login(session)
                    st, body = await request_json(
                        session,
                        "POST",
                        url,
                        request_id=self.request_id,
                        json_body=payload,
                        ssl=self.verify_ssl,
                        timeout=self.timeout,
                    )
                ok = st == 200 and (not isinstance(body, dict) or body.get("success", True) is not False)
                if isinstance(body, dict) and body.get("success") is False:
                    msg = str(body.get("msg", ""))
                    if "duplicate" in msg.lower() or "exists" in msg.lower() or "already" in msg.lower():
                        return ProviderResult(
                            ok=False,
                            error_code=PanelErrorCode.user_already_exists,
                            error_message=msg,
                        )
                return ProviderResult(
                    ok=ok,
                    data={"client_uuid": client_id, "email": remark, "response": body},
                    raw_status=st,
                )
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_code=PanelErrorCode.unknown_provider_error,
                error_message=str(e),
            )

    async def get_account_usage(self, username: str) -> ProviderResult:
        try:
            async with aiohttp.ClientSession(cookie_jar=self._cookie_jar) as session:
                await self._login(session)
                url = self._inbounds_api(f"/getClientTraffics/{username}")
                st, body = await request_json(
                    session,
                    "GET",
                    url,
                    request_id=self.request_id,
                    ssl=self.verify_ssl,
                    timeout=self.timeout,
                )
                if st in (401, 403):
                    await self._login(session)
                    st, body = await request_json(
                        session,
                        "GET",
                        url,
                        request_id=self.request_id,
                        ssl=self.verify_ssl,
                        timeout=self.timeout,
                    )
                if st == 404:
                    return ProviderResult(ok=False, error_code=PanelErrorCode.user_not_found)
                if st != 200:
                    return ProviderResult(ok=False, error_code=PanelErrorCode.panel_unavailable, raw_status=st)
                up = down = 0
                if isinstance(body, dict) and isinstance(body.get("obj"), dict):
                    o = body["obj"]
                    up = int(o.get("up", 0) or 0)
                    down = int(o.get("down", 0) or 0)
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

    async def _post_update_client_full(
        self, session: aiohttp.ClientSession, client_uuid: str, patch: Dict[str, Any]
    ) -> ProviderResult:
        url = self._inbounds_api(f"/updateClient/{client_uuid}")
        st, body = await request_json(
            session,
            "POST",
            url,
            request_id=self.request_id,
            json_body=patch,
            ssl=self.verify_ssl,
            timeout=self.timeout,
        )
        if st in (401, 403):
            await self._login(session)
            st, body = await request_json(
                session,
                "POST",
                url,
                request_id=self.request_id,
                json_body=patch,
                ssl=self.verify_ssl,
                timeout=self.timeout,
            )
        ok = st == 200 and (not isinstance(body, dict) or body.get("success", True) is not False)
        return ProviderResult(ok=ok, data={"response": body}, raw_status=st)

    async def _del_by_email(self, session: aiohttp.ClientSession, inbound_id: int, email: str) -> ProviderResult:
        url = self._inbounds_api(f"/{inbound_id}/delClientByEmail/{email}")
        st, body = await request_json(
            session,
            "POST",
            url,
            request_id=self.request_id,
            json_body={},
            ssl=self.verify_ssl,
            timeout=self.timeout,
        )
        if st in (401, 403):
            await self._login(session)
            st, body = await request_json(
                session,
                "POST",
                url,
                request_id=self.request_id,
                json_body={},
                ssl=self.verify_ssl,
                timeout=self.timeout,
            )
        ok = st == 200 and (not isinstance(body, dict) or body.get("success", True) is not False)
        return ProviderResult(ok=ok or st == 404, data={"response": body}, raw_status=st)

    async def disable_account(self, username: str) -> ProviderResult:
        client_uuid = await self._resolve_client_uuid(username)
        if not client_uuid:
            return ProviderResult(ok=False, error_code=PanelErrorCode.user_not_found)
        try:
            async with aiohttp.ClientSession(cookie_jar=self._cookie_jar) as session:
                await self._login(session)
                return await self._post_update_client_full(session, client_uuid, {"enable": False})
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_code=PanelErrorCode.unknown_provider_error,
                error_message=str(e),
            )

    async def enable_account(self, username: str) -> ProviderResult:
        client_uuid = await self._resolve_client_uuid(username)
        if not client_uuid:
            return ProviderResult(ok=False, error_code=PanelErrorCode.user_not_found)
        try:
            async with aiohttp.ClientSession(cookie_jar=self._cookie_jar) as session:
                await self._login(session)
                return await self._post_update_client_full(session, client_uuid, {"enable": True})
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_code=PanelErrorCode.unknown_provider_error,
                error_message=str(e),
            )

    async def delete_account(self, username: str) -> ProviderResult:
        ib_id = self._default_inbound_id
        if ib_id is None:
            return ProviderResult(ok=False, error_code=PanelErrorCode.invalid_inbound)
        try:
            async with aiohttp.ClientSession(cookie_jar=self._cookie_jar) as session:
                await self._login(session)
                return await self._del_by_email(session, ib_id, username)
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_code=PanelErrorCode.unknown_provider_error,
                error_message=str(e),
            )

    async def get_config_links(self, username: str) -> ProviderResult:
        try:
            async with aiohttp.ClientSession(cookie_jar=self._cookie_jar) as session:
                await self._login(session)
                url = self._inbounds_api(f"/getClientTraffics/{username}")
                st, body = await request_json(
                    session,
                    "GET",
                    url,
                    request_id=self.request_id,
                    ssl=self.verify_ssl,
                    timeout=self.timeout,
                )
                links: list[str] = []
                if isinstance(body, dict):
                    obj = body.get("obj")
                    if isinstance(obj, dict) and obj.get("link"):
                        links.append(str(obj["link"]))
                return ProviderResult(
                    ok=True,
                    data={"links": links, "subscription_url": links[0] if links else None},
                )
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_code=PanelErrorCode.unknown_provider_error,
                error_message=str(e),
            )

    async def update_quota(self, username: str, quota_bytes: int) -> ProviderResult:
        client_uuid = await self._resolve_client_uuid(username)
        if not client_uuid:
            return ProviderResult(ok=False, error_code=PanelErrorCode.user_not_found)
        total_gb = round(quota_bytes / (1024**3), 6) if quota_bytes else 0
        return await self._post_update_client(client_uuid, {"totalGB": total_gb})

    async def update_expire(self, username: str, expire_timestamp_ms: int) -> ProviderResult:
        client_uuid = await self._resolve_client_uuid(username)
        if not client_uuid:
            return ProviderResult(ok=False, error_code=PanelErrorCode.user_not_found)
        return await self._post_update_client(client_uuid, {"expiryTime": expire_timestamp_ms})

    async def _resolve_client_uuid(self, email: str) -> Optional[str]:
        ib_id = self._default_inbound_id
        if ib_id is None:
            return None
        try:
            async with aiohttp.ClientSession(cookie_jar=self._cookie_jar) as session:
                await self._login(session)
                ib = await self._find_inbound(session, ib_id)
                if not ib:
                    return None
                settings_raw = ib.get("settings")
                if not settings_raw:
                    return None
                try:
                    settings = json.loads(settings_raw) if isinstance(settings_raw, str) else settings_raw
                except json.JSONDecodeError:
                    return None
                clients = settings.get("clients") if isinstance(settings, dict) else None
                if not isinstance(clients, list):
                    return None
                for c in clients:
                    if str(c.get("email", "")) == email:
                        return str(c.get("id", "")) or None
        except Exception:
            return None
        return None

    async def _post_update_client(self, client_uuid: str, patch: Dict[str, Any]) -> ProviderResult:
        try:
            async with aiohttp.ClientSession(cookie_jar=self._cookie_jar) as session:
                await self._login(session)
                return await self._post_update_client_full(session, client_uuid, patch)
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_code=PanelErrorCode.unknown_provider_error,
                error_message=str(e),
            )
