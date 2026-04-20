"""HTTP client for 3x-ui panel (MHSanaei/3x-ui) — login + inbounds API."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx


@dataclass
class PanelError(Exception):
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


def _join(base: str, path: str) -> str:
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


class XuiPanelClient:
    """
    base_url: root of the panel, e.g. https://example.com:2053 or https://host/prefix
    (no trailing /panel — the client adds /panel/api/...).
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        two_factor_code: str = "",
        verify_tls: bool = True,
        timeout: float = 60.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._two_factor = two_factor_code
        self._client = httpx.Client(timeout=timeout, verify=verify_tls, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> XuiPanelClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def login(self) -> None:
        url = _join(self._base, "login")
        payload = {
            "username": self._username,
            "password": self._password,
            "twoFactorCode": self._two_factor or "",
        }
        r = self._client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise PanelError(data.get("msg") or "ورود ناموفق")

    def _get_json(self, path: str) -> dict[str, Any]:
        url = _join(self._base, path)
        r = self._client.get(url)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise PanelError(data.get("msg") or path)
        return data

    def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = _join(self._base, path)
        r = self._client.post(url, json=body)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise PanelError(data.get("msg") or path)
        return data

    def list_inbounds(self) -> list[dict[str, Any]]:
        data = self._get_json("panel/api/inbounds/list")
        obj = data.get("obj")
        if obj is None:
            return []
        if isinstance(obj, list):
            return obj
        return []

    def get_inbound(self, inbound_id: int) -> dict[str, Any]:
        data = self._get_json(f"panel/api/inbounds/get/{inbound_id}")
        obj = data.get("obj")
        if not isinstance(obj, dict):
            raise PanelError("پاسخ نامعتبر از پنل")
        return obj

    def add_clients_bulk(
        self,
        inbound_id: int,
        count: int,
        *,
        total_bytes: int,
        expiry_time_ms: int,
        email_prefix: str,
    ) -> list[dict[str, Any]]:
        """
        total_bytes: 0 = نامحدود (مطابق منطق 3x-ui: total==0 یعنی بدون سقف ترافیک)
        expiry_time_ms: 0 = نامحدود زمان
        """
        inbound = self.get_inbound(inbound_id)
        protocol = str(inbound.get("protocol") or "").lower()
        settings_raw = inbound.get("settings") or "{}"
        try:
            settings = json.loads(settings_raw) if isinstance(settings_raw, str) else settings_raw
        except json.JSONDecodeError as e:
            raise PanelError("تنظیمات inbound قابل خواندن نیست") from e

        clients_existing = settings.get("clients")
        template: dict[str, Any]
        if isinstance(clients_existing, list) and clients_existing:
            template = json.loads(json.dumps(clients_existing[0]))
        else:
            template = _default_client_template(protocol)

        batch_tag = uuid.uuid4().hex[:10]
        created: list[dict[str, Any]] = []
        for i in range(count):
            client = json.loads(json.dumps(template))
            email = f"{email_prefix}_{batch_tag}_{i+1:03d}"
            _apply_new_client_fields(client, protocol, email, total_bytes, expiry_time_ms)
            body = {"id": inbound_id, "settings": json.dumps({"clients": [client]}, separators=(",", ":"))}
            self._post_json("panel/api/inbounds/addClient", body)
            created.append(
                {
                    "email": email,
                    "id": client.get("id"),
                    "password": client.get("password"),
                    "sub_id": client.get("subId") or client.get("sub_id"),
                    "protocol": protocol,
                }
            )
        return created


def _default_client_template(protocol: str) -> dict[str, Any]:
    if protocol == "trojan":
        return {
            "password": "",
            "email": "",
            "limitIp": 0,
            "totalGB": 0,
            "expiryTime": 0,
            "enable": True,
            "tgId": 0,
            "subId": "",
        }
    if protocol == "shadowsocks":
        return {
            "email": "",
            "password": "",
            "limitIp": 0,
            "totalGB": 0,
            "expiryTime": 0,
            "enable": True,
            "tgId": 0,
            "subId": "",
        }
    # vless, vmess, …
    return {
        "id": "",
        "email": "",
        "security": "auto",
        "flow": "",
        "limitIp": 0,
        "totalGB": 0,
        "expiryTime": 0,
        "enable": True,
        "tgId": 0,
        "subId": "",
    }


def _apply_new_client_fields(
    client: dict[str, Any],
    protocol: str,
    email: str,
    total_bytes: int,
    expiry_time_ms: int,
) -> None:
    client["email"] = email
    client["totalGB"] = int(total_bytes)
    client["expiryTime"] = int(expiry_time_ms)
    client["enable"] = True
    if "tgId" in client and client["tgId"] in ("", None):
        client["tgId"] = 0
    if not client.get("subId"):
        client["subId"] = uuid.uuid4().hex[:16]

    if protocol == "trojan":
        client["password"] = uuid.uuid4().hex
    elif protocol == "shadowsocks":
        client["password"] = uuid.uuid4().hex[:16]
    else:
        client["id"] = str(uuid.uuid4())
        if protocol == "vless" and not client.get("flow"):
            client["flow"] = ""
