from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import requests


class XUIError(RuntimeError):
    pass


@dataclass(frozen=True)
class CreateParams:
    inbound_id: int
    count: int
    volume_gb: int
    days: int
    prefix: str
    start_index: int = 0
    limit_ip: int = 0


class XUIClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout_seconds: int = 25,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout_seconds = timeout_seconds
        self._http = requests.Session()
        self._is_logged_in = False

    def _build_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def _request(
        self,
        method: str,
        paths: list[str],
        *,
        retry_on_auth: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        errors: list[str] = []
        for path in paths:
            try:
                response = self._http.request(
                    method=method,
                    url=self._build_url(path),
                    timeout=self.timeout_seconds,
                    **kwargs,
                )
            except requests.RequestException as exc:
                errors.append(f"{path}: {exc}")
                continue

            if response.status_code == 401 and retry_on_auth:
                self.login(force=True)
                return self._request(
                    method=method,
                    paths=paths,
                    retry_on_auth=False,
                    **kwargs,
                )

            if response.status_code >= 400:
                errors.append(f"{path}: HTTP {response.status_code} - {response.text[:200]}")
                continue

            try:
                payload = response.json()
            except ValueError:
                errors.append(f"{path}: non-JSON response: {response.text[:250]}")
                continue

            if isinstance(payload, dict) and payload.get("success") is False:
                errors.append(f"{path}: API failed: {payload.get('msg', 'unknown error')}")
                continue

            if isinstance(payload, dict):
                return payload
            errors.append(f"{path}: invalid payload type")

        raise XUIError(" | ".join(errors) or "Unknown X-UI API error")

    def login(self, force: bool = False) -> None:
        if self._is_logged_in and not force:
            return
        payload = {"username": self.username, "password": self.password}
        _ = self._request(
            "POST",
            ["/login", "/panel/login"],
            json=payload,
            retry_on_auth=False,
        )
        self._is_logged_in = True

    def list_inbounds(self) -> list[dict[str, Any]]:
        self.login()
        payload = self._request("GET", ["/panel/api/inbounds/list", "/xui/API/inbounds/list"])
        data = payload.get("obj", [])
        return data if isinstance(data, list) else []

    def get_inbound(self, inbound_id: int) -> dict[str, Any]:
        for inbound in self.list_inbounds():
            if int(inbound.get("id", 0)) == inbound_id:
                return inbound
        raise XUIError(f"Inbound {inbound_id} not found")

    @staticmethod
    def _json_field(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return {}
        return {}

    def _existing_emails(self, inbound: dict[str, Any]) -> set[str]:
        settings = self._json_field(inbound.get("settings"))
        clients = settings.get("clients", [])
        return {
            str(client.get("email"))
            for client in clients
            if isinstance(client, dict) and client.get("email")
        }

    @staticmethod
    def _to_bytes(gb: int) -> int:
        if gb <= 0:
            return 0
        return gb * 1024 * 1024 * 1024

    @staticmethod
    def _expiry_ms(days: int) -> int:
        if days <= 0:
            return 0
        return int(time.time() * 1000) + days * 24 * 60 * 60 * 1000

    def _new_client_payload(
        self,
        *,
        email: str,
        total_gb: int,
        expiry_time: int,
        limit_ip: int,
        inbound: dict[str, Any],
    ) -> dict[str, Any]:
        settings = self._json_field(inbound.get("settings"))
        existing_clients = settings.get("clients", [])
        flow = ""
        if isinstance(existing_clients, list) and existing_clients:
            first_client = existing_clients[0]
            if isinstance(first_client, dict):
                flow = str(first_client.get("flow", "") or "")

        client_uuid = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "id": client_uuid,
            "email": email,
            "totalGB": total_gb,
            "expiryTime": expiry_time,
            "enable": True,
            "limitIp": max(limit_ip, 0),
            "subId": str(uuid.uuid4())[:16],
        }
        if flow:
            payload["flow"] = flow
        return payload

    def add_client(self, inbound_id: int, client: dict[str, Any]) -> None:
        request_payload = {
            "id": inbound_id,
            "settings": json.dumps({"clients": [client]}, separators=(",", ":")),
        }
        self.login()
        _ = self._request(
            "POST",
            ["/panel/api/inbounds/addClient", "/xui/inbound/addClient"],
            json=request_payload,
        )

    def create_clients(self, params: CreateParams) -> dict[str, Any]:
        inbound = self.get_inbound(params.inbound_id)
        existing_emails = self._existing_emails(inbound)
        created: list[dict[str, Any]] = []
        total_bytes = self._to_bytes(params.volume_gb)
        expiry_time = self._expiry_ms(params.days)

        for index in range(params.count):
            email_base = f"{params.prefix}{params.start_index + index}"
            email = email_base
            suffix = 1
            while email in existing_emails:
                email = f"{email_base}_{suffix}"
                suffix += 1
            existing_emails.add(email)

            client_payload = self._new_client_payload(
                email=email,
                total_gb=total_bytes,
                expiry_time=expiry_time,
                limit_ip=params.limit_ip,
                inbound=inbound,
            )
            self.add_client(params.inbound_id, client_payload)
            link = self.build_share_link(inbound, client_payload)
            created.append(
                {
                    "email": email,
                    "uuid": client_payload["id"],
                    "link": link,
                    "volume_gb": params.volume_gb,
                    "days": params.days,
                    "expiry_time": expiry_time,
                }
            )

        return {"inbound": inbound, "created": created}

    def _default_server_host(self, inbound: dict[str, Any]) -> str:
        stream_settings = self._json_field(inbound.get("streamSettings"))
        security = stream_settings.get("security", "")
        tls_settings = stream_settings.get("tlsSettings", {})
        reality_settings = stream_settings.get("realitySettings", {})
        server_name = ""

        if isinstance(tls_settings, dict):
            server_name = str(tls_settings.get("serverName", "")).strip()
        if not server_name and isinstance(reality_settings, dict):
            server_name = str(reality_settings.get("serverNames", [""])[0]).strip() if reality_settings.get("serverNames") else ""

        if server_name:
            return server_name

        ws_settings = stream_settings.get("wsSettings", {})
        if isinstance(ws_settings, dict):
            headers = ws_settings.get("headers", {})
            if isinstance(headers, dict):
                host = str(headers.get("Host", "")).strip()
                if host:
                    return host

        parsed = urlparse(self.base_url)
        return parsed.hostname or "127.0.0.1"

    @staticmethod
    def _stream_params(inbound: dict[str, Any]) -> dict[str, str]:
        stream = XUIClient._json_field(inbound.get("streamSettings"))
        params: dict[str, str] = {}
        network = str(stream.get("network", "tcp"))
        security = str(stream.get("security", "none"))

        params["type"] = network
        if security and security != "none":
            params["security"] = security

        if network == "ws":
            ws = stream.get("wsSettings", {})
            if isinstance(ws, dict):
                path = str(ws.get("path", "")).strip()
                headers = ws.get("headers", {})
                host = str(headers.get("Host", "")).strip() if isinstance(headers, dict) else ""
                if path:
                    params["path"] = path
                if host:
                    params["host"] = host
        elif network == "grpc":
            grpc = stream.get("grpcSettings", {})
            if isinstance(grpc, dict):
                service_name = str(grpc.get("serviceName", "")).strip()
                if service_name:
                    params["serviceName"] = service_name

        tls = stream.get("tlsSettings", {})
        if isinstance(tls, dict):
            sni = str(tls.get("serverName", "")).strip()
            if sni:
                params["sni"] = sni

        reality = stream.get("realitySettings", {})
        if isinstance(reality, dict):
            pbk = str(reality.get("publicKey", "")).strip()
            sid = str(reality.get("shortIds", [""])[0]).strip() if reality.get("shortIds") else ""
            sni_values = reality.get("serverNames", [])
            sni = str(sni_values[0]).strip() if isinstance(sni_values, list) and sni_values else ""
            if pbk:
                params["pbk"] = pbk
            if sid:
                params["sid"] = sid
            if sni and "sni" not in params:
                params["sni"] = sni

        return params

    def build_share_link(self, inbound: dict[str, Any], client: dict[str, Any]) -> str:
        protocol = str(inbound.get("protocol", "")).lower()
        server = self._default_server_host(inbound)
        port = int(inbound.get("port", 0))
        name = str(inbound.get("remark", f"inbound-{inbound.get('id', 'unknown')}"))
        email = str(client.get("email", "user"))
        label = f"{name}-{email}"

        if protocol == "vmess":
            stream_params = self._stream_params(inbound)
            vmess_payload = {
                "v": "2",
                "ps": label,
                "add": server,
                "port": str(port),
                "id": str(client.get("id", "")),
                "aid": "0",
                "scy": "auto",
                "net": stream_params.get("type", "tcp"),
                "type": "none",
                "host": stream_params.get("host", ""),
                "path": stream_params.get("path", ""),
                "tls": "tls" if stream_params.get("security") in {"tls", "reality"} else "",
                "sni": stream_params.get("sni", ""),
            }
            encoded = base64.b64encode(json.dumps(vmess_payload, separators=(",", ":")).encode()).decode()
            return f"vmess://{encoded}"

        stream_params = self._stream_params(inbound)
        query = {k: v for k, v in stream_params.items() if v}
        flow = str(client.get("flow", "")).strip()
        if flow:
            query["flow"] = flow
        query_string = urlencode(query, safe=":/", doseq=True)

        if protocol == "vless":
            link = f"vless://{client.get('id')}@{server}:{port}"
            if query_string:
                link += f"?{query_string}"
            return f"{link}#{quote(label)}"

        if protocol == "trojan":
            link = f"trojan://{client.get('id')}@{server}:{port}"
            if query_string:
                link += f"?{query_string}"
            return f"{link}#{quote(label)}"

        if protocol == "shadowsocks":
            settings = self._json_field(inbound.get("settings"))
            method = str(settings.get("method", "aes-128-gcm"))
            password = str(client.get("id"))
            user_info = base64.urlsafe_b64encode(f"{method}:{password}".encode()).decode().rstrip("=")
            return f"ss://{user_info}@{server}:{port}#{quote(label)}"

        return f"{protocol}://{client.get('id')}@{server}:{port}#{quote(label)}"
