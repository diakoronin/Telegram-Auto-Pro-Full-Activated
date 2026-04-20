#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
x-ui API Wrapper
Handles all communication with the 3x-ui panel REST API.
"""

import requests
import json
import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


class XUIClient:
    """Client for interacting with 3x-ui panel API."""

    def __init__(self, base_url: str, username: str, password: str, verify_ssl: bool = False):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self._logged_in = False

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def login(self) -> bool:
        """Login to x-ui panel and store session cookie."""
        try:
            resp = self.session.post(
                f"{self.base_url}/login",
                data={"username": self.username, "password": self.password},
                timeout=15,
            )
            data = resp.json()
            if data.get("success"):
                self._logged_in = True
                logger.info("x-ui login successful")
                return True
            logger.error("x-ui login failed: %s", data.get("msg"))
            return False
        except Exception as exc:
            logger.exception("x-ui login error: %s", exc)
            return False

    def _ensure_login(self):
        if not self._logged_in:
            self.login()

    # ------------------------------------------------------------------
    # Inbound helpers
    # ------------------------------------------------------------------

    def list_inbounds(self) -> list:
        """Return all inbounds from the panel."""
        self._ensure_login()
        try:
            resp = self.session.get(f"{self.base_url}/xui/inbound/list", timeout=15)
            data = resp.json()
            if data.get("success"):
                return data.get("obj", [])
            logger.error("list_inbounds failed: %s", data.get("msg"))
            return []
        except Exception as exc:
            logger.exception("list_inbounds error: %s", exc)
            return []

    def get_inbound(self, inbound_id: int) -> Optional[dict]:
        """Get a single inbound by id."""
        inbounds = self.list_inbounds()
        for ib in inbounds:
            if ib.get("id") == inbound_id:
                return ib
        return None

    # ------------------------------------------------------------------
    # Client helpers
    # ------------------------------------------------------------------

    def list_clients(self, inbound_id: int) -> list:
        """Return clients for a specific inbound."""
        inbound = self.get_inbound(inbound_id)
        if not inbound:
            return []
        settings_raw = inbound.get("settings", "{}")
        try:
            settings = json.loads(settings_raw) if isinstance(settings_raw, str) else settings_raw
            return settings.get("clients", [])
        except Exception:
            return []

    def add_clients(self, inbound_id: int, clients: list) -> bool:
        """
        Add one or more clients to an inbound.
        clients: list of dicts, each must at minimum have keys compatible with
                 the inbound protocol (vless/vmess/trojan).
        """
        self._ensure_login()
        payload = {
            "id": inbound_id,
            "settings": json.dumps({"clients": clients}),
        }
        try:
            resp = self.session.post(
                f"{self.base_url}/xui/inbound/addClient",
                json=payload,
                timeout=15,
            )
            data = resp.json()
            if data.get("success"):
                return True
            logger.error("add_clients failed: %s", data.get("msg"))
            return False
        except Exception as exc:
            logger.exception("add_clients error: %s", exc)
            return False

    def delete_client(self, inbound_id: int, client_uuid: str) -> bool:
        """Delete a client from an inbound by UUID."""
        self._ensure_login()
        try:
            resp = self.session.post(
                f"{self.base_url}/xui/inbound/{inbound_id}/delClient/{client_uuid}",
                timeout=15,
            )
            data = resp.json()
            return data.get("success", False)
        except Exception as exc:
            logger.exception("delete_client error: %s", exc)
            return False

    def reset_client_traffic(self, inbound_id: int, email: str) -> bool:
        """Reset traffic for a specific client by email."""
        self._ensure_login()
        try:
            resp = self.session.post(
                f"{self.base_url}/xui/inbound/{inbound_id}/resetClientTraffic/{email}",
                timeout=15,
            )
            data = resp.json()
            return data.get("success", False)
        except Exception as exc:
            logger.exception("reset_client_traffic error: %s", exc)
            return False

    def get_client_traffic(self, email: str) -> Optional[dict]:
        """Get client traffic info by email (3x-ui endpoint)."""
        self._ensure_login()
        try:
            resp = self.session.get(
                f"{self.base_url}/xui/inbound/getClientTraffics/{email}",
                timeout=15,
            )
            data = resp.json()
            if data.get("success"):
                return data.get("obj")
            return None
        except Exception as exc:
            logger.exception("get_client_traffic error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Inbound-level controls
    # ------------------------------------------------------------------

    def enable_inbound(self, inbound_id: int) -> bool:
        self._ensure_login()
        try:
            resp = self.session.post(
                f"{self.base_url}/xui/inbound/enable/{inbound_id}", timeout=15
            )
            return resp.json().get("success", False)
        except Exception as exc:
            logger.exception("enable_inbound error: %s", exc)
            return False

    def disable_inbound(self, inbound_id: int) -> bool:
        self._ensure_login()
        try:
            resp = self.session.post(
                f"{self.base_url}/xui/inbound/disable/{inbound_id}", timeout=15
            )
            return resp.json().get("success", False)
        except Exception as exc:
            logger.exception("disable_inbound error: %s", exc)
            return False

    def delete_inbound(self, inbound_id: int) -> bool:
        self._ensure_login()
        try:
            resp = self.session.post(
                f"{self.base_url}/xui/inbound/del/{inbound_id}", timeout=15
            )
            return resp.json().get("success", False)
        except Exception as exc:
            logger.exception("delete_inbound error: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Server status
    # ------------------------------------------------------------------

    def server_status(self) -> Optional[dict]:
        self._ensure_login()
        try:
            resp = self.session.post(
                f"{self.base_url}/server/status", timeout=15
            )
            data = resp.json()
            if data.get("success"):
                return data.get("obj")
            return None
        except Exception as exc:
            logger.exception("server_status error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Convenience: build a vless/vmess/trojan client dict
    # ------------------------------------------------------------------

    @staticmethod
    def build_vless_client(
        email: str,
        total_gb: float = 0,
        expire_days: int = 0,
        limit_ip: int = 0,
    ) -> dict:
        """
        Build a VLESS client dict suitable for add_clients().
        total_gb=0 means unlimited; expire_days=0 means never expires.
        """
        import time

        expire_time = 0
        if expire_days > 0:
            expire_time = int((time.time() + expire_days * 86400) * 1000)

        total_bytes = int(total_gb * 1024 ** 3) if total_gb > 0 else 0

        return {
            "id": str(uuid.uuid4()),
            "alterId": 0,
            "email": email,
            "limitIp": limit_ip,
            "totalGB": total_bytes,
            "expiryTime": expire_time,
            "enable": True,
            "tgId": "",
            "subId": str(uuid.uuid4())[:8],
            "reset": 0,
        }

    # ------------------------------------------------------------------
    # Build share link
    # ------------------------------------------------------------------

    def build_vless_link(
        self,
        inbound: dict,
        client: dict,
        server_host: str,
        remark: str = "",
    ) -> Optional[str]:
        """
        Build a VLESS share URI from inbound + client data.
        Works for common stream settings (ws, tcp, grpc).
        """
        try:
            stream_raw = inbound.get("streamSettings", "{}")
            stream = json.loads(stream_raw) if isinstance(stream_raw, str) else stream_raw
            net = stream.get("network", "tcp")
            security = stream.get("security", "none")

            port = inbound.get("port", 443)
            uid = client.get("id", "")
            email = client.get("email", remark or "service")

            params = f"type={net}&security={security}"

            if net == "ws":
                ws_settings = stream.get("wsSettings", {})
                path = ws_settings.get("path", "/")
                host = ws_settings.get("headers", {}).get("Host", server_host)
                params += f"&path={requests.utils.quote(path)}&host={host}"
            elif net == "grpc":
                grpc = stream.get("grpcSettings", {})
                service = grpc.get("serviceName", "")
                params += f"&serviceName={service}&mode=gun"

            if security == "tls":
                tls = stream.get("tlsSettings", {})
                sni = tls.get("serverName", server_host)
                params += f"&sni={sni}&fp=chrome&alpn=h2%2Chttp%2F1.1"
            elif security == "reality":
                real = stream.get("realitySettings", {})
                sni = real.get("serverNames", [server_host])[0]
                pub_key = real.get("settings", {}).get("publicKey", "")
                short_id = real.get("shortIds", [""])[0]
                spider = real.get("settings", {}).get("spiderX", "/")
                params += (
                    f"&sni={sni}&pbk={pub_key}&sid={short_id}"
                    f"&spx={requests.utils.quote(spider)}&fp=chrome"
                )

            tag = remark or email
            link = f"vless://{uid}@{server_host}:{port}?{params}#{requests.utils.quote(tag)}"
            return link
        except Exception as exc:
            logger.exception("build_vless_link error: %s", exc)
            return None
