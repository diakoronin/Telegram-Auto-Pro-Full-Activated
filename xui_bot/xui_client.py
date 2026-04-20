"""Async client for x-ui / 3x-ui panel API.

Tested against the MHSanaei/3x-ui panel. Most endpoints also work on the
`alireza0/x-ui` fork. The client authenticates by POSTing to `/login` and then
uses the returned session cookie for subsequent requests.

Main endpoints:
  POST {base}/login
  GET  {base}/panel/api/inbounds/list
  GET  {base}/panel/api/inbounds/get/{id}
  POST {base}/panel/api/inbounds/addClient  (body: {id, settings})
  POST {base}/panel/api/inbounds/{id}/delClient/{uuid}
  POST {base}/panel/api/inbounds/updateClient/{uuid} (body: {id, settings})
  POST {base}/panel/api/inbounds/resetClientTraffic/{id}/{email}
  GET  {base}/panel/api/inbounds/getClientTrafficsById/{uuid}
"""
from __future__ import annotations

import json
import logging
import ssl
import time
import uuid as _uuid
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)


class XUIError(RuntimeError):
    """Raised when an x-ui API call fails."""


class XUIClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        web_base_path: str = "/",
        verify_tls: bool = True,
        timeout: float = 25.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        if not web_base_path.startswith("/"):
            web_base_path = "/" + web_base_path
        if not web_base_path.endswith("/"):
            web_base_path = web_base_path + "/"
        self.web_base_path = web_base_path
        self.verify_tls = verify_tls
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._logged_in_at: float = 0.0
        self._login_ttl_seconds: float = 30 * 60

    def _url(self, path: str) -> str:
        if path.startswith("/"):
            path = path[1:]
        return f"{self.base_url}{self.web_base_path}{path}"

    async def __aenter__(self) -> "XUIClient":
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            verify: Any = True
            if not self.verify_tls:
                verify = ssl.create_default_context()
                verify.check_hostname = False
                verify.verify_mode = ssl.CERT_NONE
            self._client = httpx.AsyncClient(
                verify=verify,
                timeout=self.timeout,
                follow_redirects=True,
                headers={"Accept": "application/json, text/plain, */*"},
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def login(self) -> None:
        client = await self._ensure_client()
        resp = await client.post(
            self._url("login"),
            data={"username": self.username, "password": self.password},
        )
        if resp.status_code != 200:
            raise XUIError(f"Login failed: HTTP {resp.status_code} — {resp.text[:200]}")
        try:
            payload = resp.json()
        except Exception as exc:
            raise XUIError(f"Login returned non-JSON: {resp.text[:200]}") from exc
        if not payload.get("success"):
            raise XUIError(f"Login failed: {payload.get('msg') or payload}")
        self._logged_in_at = time.time()
        log.info("x-ui login OK as %s", self.username)

    async def _ensure_login(self) -> None:
        if (time.time() - self._logged_in_at) > self._login_ttl_seconds or self._logged_in_at == 0:
            await self.login()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        await self._ensure_login()
        client = await self._ensure_client()
        url = self._url(path)
        resp = await client.request(method, url, **kwargs)
        if resp.status_code == 401 or resp.status_code == 403 or "login" in str(resp.url):
            await self.login()
            resp = await client.request(method, url, **kwargs)
        if resp.status_code >= 400:
            raise XUIError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            payload = resp.json()
        except Exception as exc:
            raise XUIError(f"{method} {path} returned non-JSON: {resp.text[:300]}") from exc
        if isinstance(payload, dict) and payload.get("success") is False:
            raise XUIError(f"{method} {path} API error: {payload.get('msg') or payload}")
        return payload

    async def list_inbounds(self) -> List[Dict[str, Any]]:
        payload = await self._request("GET", "panel/api/inbounds/list")
        return payload.get("obj") or []

    async def get_inbound(self, inbound_id: int) -> Dict[str, Any]:
        payload = await self._request("GET", f"panel/api/inbounds/get/{inbound_id}")
        return payload.get("obj") or {}

    async def add_client(self, inbound_id: int, client_obj: Dict[str, Any]) -> Dict[str, Any]:
        body = {
            "id": inbound_id,
            "settings": json.dumps({"clients": [client_obj]}),
        }
        return await self._request(
            "POST",
            "panel/api/inbounds/addClient",
            json=body,
        )

    async def del_client(self, inbound_id: int, client_uuid_or_id: str) -> Dict[str, Any]:
        return await self._request(
            "POST",
            f"panel/api/inbounds/{inbound_id}/delClient/{quote(client_uuid_or_id, safe='')}",
        )

    async def reset_client_traffic(self, inbound_id: int, email: str) -> Dict[str, Any]:
        return await self._request(
            "POST",
            f"panel/api/inbounds/resetClientTraffic/{inbound_id}/{quote(email, safe='')}",
        )

    async def get_client_traffics(self, email: str) -> Dict[str, Any]:
        payload = await self._request(
            "GET",
            f"panel/api/inbounds/getClientTraffics/{quote(email, safe='')}",
        )
        return payload.get("obj") or {}


def build_client_object(
    protocol: str,
    email: str,
    total_gb: int = 0,
    expiry_time_ms: int = 0,
    limit_ip: int = 0,
    flow: str = "",
    enable: bool = True,
    sub_id: Optional[str] = None,
    tg_id: str = "",
) -> Dict[str, Any]:
    """Build a client entry for addClient based on the inbound protocol.

    total_gb=0 means unlimited traffic. expiry_time_ms=0 means never expires.
    """
    protocol = protocol.lower()
    base: Dict[str, Any] = {
        "email": email,
        "enable": enable,
        "limitIp": int(limit_ip),
        "totalGB": int(total_gb) * 1024 * 1024 * 1024 if total_gb else 0,
        "expiryTime": int(expiry_time_ms),
        "tgId": tg_id,
        "subId": sub_id or _uuid.uuid4().hex[:16],
        "reset": 0,
    }
    if protocol == "vless":
        base.update({"id": str(_uuid.uuid4()), "flow": flow or ""})
    elif protocol == "vmess":
        base.update({"id": str(_uuid.uuid4())})
    elif protocol == "trojan":
        base.update({"password": _uuid.uuid4().hex})
    elif protocol == "shadowsocks":
        base.update({"password": _uuid.uuid4().hex, "method": "chacha20-ietf-poly1305"})
    else:
        raise XUIError(f"Unsupported protocol: {protocol}")
    return base
