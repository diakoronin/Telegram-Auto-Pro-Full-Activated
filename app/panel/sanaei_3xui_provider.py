"""
MHSanaei 3x-ui panel HTTP API (from source web/controller + session):
- POST /login — JSON {username, password}; session cookie
- GET /panel/api/inbounds/list
- POST /panel/api/inbounds/addClient — body Inbound {id, settings: JSON string with clients[]}
- POST /panel/api/inbounds/updateClient/:clientId
- POST /panel/api/inbounds/:id/delClient/:clientId
- GET /panel/api/inbounds/getClientTraffics/:email
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import Settings
from app.db.models import Panel, Plan, Server, UserService
from app.panel.errors import PanelErrorCode
from app.panel.base import PanelProvider
from app.panel.panel_urls import panel_root_url, xui_api_url
from app.panel.secrets import panel_plain_password
from app.panel.types import (
    PanelActionResult,
    PanelCreateResult,
    PanelLogContext,
    PanelTestResult,
    PanelUsageResult,
)
from app.structured_log import redact_secrets

log = logging.getLogger("app.panel.3xui")


class Sanaei3xuiProvider(PanelProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._cookie_jar: dict[int, httpx.Cookies] = {}

    def _root(self, panel: Panel) -> str:
        return panel_root_url(panel).rstrip("/")

    async def _login(self, panel: Panel, ctx: PanelLogContext | None) -> tuple[httpx.Cookies | None, str | None]:
        if panel.id in self._cookie_jar:
            return self._cookie_jar[panel.id], None
        rid = ctx.request_id if ctx else "-"
        url = self._root(panel) + "/login"
        body = {"username": panel.username, "password": panel_plain_password(panel, self._settings)}
        log.info("[3XUI REQUEST] rid=%s POST %s sanitized=%s", rid, url, redact_secrets(body))
        try:
            async with httpx.AsyncClient(
                verify=panel.verify_ssl,
                timeout=float(panel.timeout_seconds or 30),
                follow_redirects=True,
            ) as client:
                r = await client.post(url, json=body)
            log.info("[3XUI RESPONSE] rid=%s status=%s cookie_present=%s", rid, r.status_code, bool(r.cookies))
            if r.status_code not in (200, 201):
                return None, f"login failed http {r.status_code}"
            if not r.cookies:
                return None, "no session cookie"
            self._cookie_jar[panel.id] = r.cookies
            return r.cookies, None
        except Exception as e:
            log.warning("[3XUI RESPONSE] rid=%s login error %s", rid, e)
            return None, str(e)

    def _forget_session(self, panel: Panel) -> None:
        self._cookie_jar.pop(panel.id, None)

    async def _request(
        self,
        panel: Panel,
        method: str,
        rel: str,
        *,
        json_body: Any = None,
        ctx: PanelLogContext | None = None,
    ) -> tuple[int, Any]:
        cookies, err = await self._login(panel, ctx)
        if err or not cookies:
            return 0, err or "no session"
        rid = ctx.request_id if ctx else "-"
        url = xui_api_url(panel, rel)
        log.info(
            "[3XUI REQUEST] rid=%s %s %s body=%s cookie_present=true",
            rid,
            method,
            url,
            json.dumps(redact_secrets(json_body), ensure_ascii=False)[:600] if json_body else "{}",
        )
        try:
            async with httpx.AsyncClient(
                verify=panel.verify_ssl,
                timeout=float(panel.timeout_seconds or 30),
                cookies=cookies,
                follow_redirects=True,
            ) as client:
                r = await client.request(method, url, json=json_body)
            if r.status_code in (401, 403):
                self._forget_session(panel)
            ct = (r.headers.get("content-type") or "").lower()
            body: Any = r.text
            if "json" in ct:
                try:
                    body = r.json()
                except Exception:
                    pass
            log.info(
                "[3XUI RESPONSE] rid=%s status=%s body=%s",
                rid,
                r.status_code,
                json.dumps(redact_secrets(body), ensure_ascii=False)[:800]
                if isinstance(body, (dict, list))
                else str(body)[:400],
            )
            return r.status_code, body
        except Exception as e:
            log.warning("[3XUI RESPONSE] rid=%s error %s", rid, e)
            return 0, str(e)

    async def test_connection(self, panel: Panel, *, ctx: PanelLogContext | None = None) -> PanelTestResult:
        t0 = time.monotonic()
        st, body = await self._request(panel, "GET", "/list", ctx=ctx)
        ms = int((time.monotonic() - t0) * 1000)
        if st != 200:
            return PanelTestResult(
                ok=False,
                duration_ms=ms,
                error_code=PanelErrorCode.INVALID_CREDENTIALS if st in (401, 403) else PanelErrorCode.PANEL_UNAVAILABLE,
                message=str(body)[:300],
            )
        return PanelTestResult(ok=True, duration_ms=ms)

    def _client_json_vless(self, email: str, total_bytes: int, expire_ms: int) -> dict[str, Any]:
        cid = str(uuid.uuid4())
        return {
            "id": cid,
            "email": email,
            "enable": True,
            "flow": "",
            "limitIp": 0,
            "totalGB": 0,
            "expiryTime": expire_ms,
            "tgId": 0,
            "subId": "",
            "comment": "",
            "reset": 0,
            "security": "auto",
        }

    async def create_account(
        self,
        *,
        user_service: UserService,
        panel: Panel,
        server: Server,
        plan: Plan,
        quota_bytes: int,
        expire_at: datetime | None,
        backend_username: str,
        ctx: PanelLogContext | None = None,
    ) -> PanelCreateResult:
        iid = server.inbound_id
        if iid is None:
            return PanelCreateResult(
                ok=False,
                error_code=PanelErrorCode.INVALID_INBOUND,
                message="server.inbound_id is required for 3x-ui",
            )
        expire_ms = 0
        if expire_at is not None:
            expire_ms = int(expire_at.astimezone(UTC).timestamp() * 1000)
        client = self._client_json_vless(backend_username, quota_bytes, expire_ms)
        settings_obj = {"clients": [client]}
        payload = {"id": int(iid), "settings": json.dumps(settings_obj)}
        st, body = await self._request(panel, "POST", "/addClient", json_body=payload, ctx=ctx)
        if st not in (200, 201):
            code, msg = (PanelErrorCode.PANEL_UNAVAILABLE, str(body)[:400])
            if st in (401, 403):
                code = PanelErrorCode.INVALID_CREDENTIALS
            return PanelCreateResult(ok=False, error_code=code, message=msg)
        st2, inbound = await self._request(panel, "GET", f"/get/{int(iid)}", ctx=ctx)
        links: list[str] = []
        raw_sub = None
        if st2 == 200 and isinstance(inbound, dict):
            listen = (inbound.get("listen") or "0.0.0.0").strip()
            port = int(inbound.get("port") or 443)
            host = listen if listen not in ("0.0.0.0", "::", "") else "127.0.0.1"
            proto = str(inbound.get("protocol") or "vless").lower()
            if proto == "vless":
                # Minimal share line for clients that accept vless URI (panel-specific params omitted).
                links = [
                    f"vless://{client['id']}@{host}:{port}?encryption=none&security=none&type=tcp#{backend_username}"
                ]
        return PanelCreateResult(
            ok=True,
            panel_account_id=client["id"],
            username=backend_username,
            config_links=links,
            raw_subscription_url=raw_sub,
        )

    async def get_account_usage(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        ctx: PanelLogContext | None = None,
    ) -> PanelUsageResult:
        st, body = await self._request(
            panel, "GET", f"/getClientTraffics/{panel_username}", ctx=ctx
        )
        if st != 200 or not isinstance(body, dict):
            return PanelUsageResult(
                ok=False,
                error_code=PanelErrorCode.USER_NOT_FOUND if st == 404 else PanelErrorCode.PANEL_UNAVAILABLE,
                message=str(body)[:300],
            )
        up = int(body.get("upload") or body.get("up") or 0)
        down = int(body.get("download") or body.get("down") or 0)
        total = up + down
        return PanelUsageResult(ok=True, upload_bytes=up, download_bytes=down, total_used_bytes=total)

    async def disable_account(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        client_id: str | None,
        ctx: PanelLogContext | None = None,
    ) -> PanelActionResult:
        return await self._update_client_enable(panel, inbound_id, client_id, False, ctx)

    async def enable_account(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        client_id: str | None,
        ctx: PanelLogContext | None = None,
    ) -> PanelActionResult:
        return await self._update_client_enable(panel, inbound_id, client_id, True, ctx)

    async def _update_client_enable(
        self,
        panel: Panel,
        inbound_id: int | None,
        client_id: str | None,
        enable: bool,
        ctx: PanelLogContext | None,
    ) -> PanelActionResult:
        if inbound_id is None or not client_id:
            return PanelActionResult(ok=False, message="missing inbound_id or client_id")
        st_in, inbound = await self._request(panel, "GET", f"/get/{int(inbound_id)}", ctx=ctx)
        if st_in != 200 or not isinstance(inbound, dict):
            return PanelActionResult(ok=False, message="cannot load inbound")
        settings_s = inbound.get("settings") or "{}"
        try:
            settings = json.loads(settings_s) if isinstance(settings_s, str) else settings_s
        except Exception:
            settings = {}
        clients = settings.get("clients") or []
        found = False
        for c in clients:
            if isinstance(c, dict) and c.get("id") == client_id:
                c["enable"] = enable
                found = True
                break
        if not found:
            return PanelActionResult(ok=False, message="client not found in inbound")
        payload = {"id": int(inbound_id), "settings": json.dumps({"clients": clients})}
        st, body = await self._request(
            panel, "POST", f"/updateClient/{client_id}", json_body=payload, ctx=ctx
        )
        if st not in (200, 201):
            return PanelActionResult(ok=False, message=str(body)[:300])
        return PanelActionResult(ok=True)

    async def delete_account(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        client_id: str | None,
        ctx: PanelLogContext | None = None,
    ) -> PanelActionResult:
        if inbound_id is None or not client_id:
            return PanelActionResult(ok=False, message="missing inbound_id or client_id")
        st, body = await self._request(
            panel,
            "POST",
            f"/{int(inbound_id)}/delClient/{client_id}",
            json_body=None,
            ctx=ctx,
        )
        if st not in (200, 201, 204) and st != 404:
            return PanelActionResult(ok=False, message=str(body)[:300])
        return PanelActionResult(ok=True)

    async def update_quota(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        client_id: str | None,
        quota_bytes: int,
        ctx: PanelLogContext | None = None,
    ) -> PanelActionResult:
        # 3x-ui client traffic limit often via client totalGB or updateClient with new client dict
        if inbound_id is None or not client_id:
            return PanelActionResult(ok=False, message="missing inbound_id or client_id")
        st_in, inbound = await self._request(panel, "GET", f"/get/{int(inbound_id)}", ctx=ctx)
        if st_in != 200 or not isinstance(inbound, dict):
            return PanelActionResult(ok=False, message="cannot load inbound")
        settings = json.loads(inbound["settings"]) if isinstance(inbound.get("settings"), str) else {}
        clients = settings.get("clients") or []
        gb = max(1, int(quota_bytes // (1024**3)))
        for c in clients:
            if isinstance(c, dict) and c.get("id") == client_id:
                c["totalGB"] = gb
                break
        payload = {"id": int(inbound_id), "settings": json.dumps({"clients": clients})}
        st, body = await self._request(
            panel, "POST", f"/updateClient/{client_id}", json_body=payload, ctx=ctx
        )
        if st not in (200, 201):
            return PanelActionResult(ok=False, message=str(body)[:300])
        return PanelActionResult(ok=True)

    async def update_expire(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        client_id: str | None,
        expire_at: datetime | None,
        ctx: PanelLogContext | None = None,
    ) -> PanelActionResult:
        if inbound_id is None or not client_id:
            return PanelActionResult(ok=False, message="missing inbound_id or client_id")
        st_in, inbound = await self._request(panel, "GET", f"/get/{int(inbound_id)}", ctx=ctx)
        if st_in != 200 or not isinstance(inbound, dict):
            return PanelActionResult(ok=False, message="cannot load inbound")
        settings = json.loads(inbound["settings"]) if isinstance(inbound.get("settings"), str) else {}
        clients = settings.get("clients") or []
        exp_ms = 0 if expire_at is None else int(expire_at.astimezone(UTC).timestamp() * 1000)
        for c in clients:
            if isinstance(c, dict) and c.get("id") == client_id:
                c["expiryTime"] = exp_ms
                break
        payload = {"id": int(inbound_id), "settings": json.dumps({"clients": clients})}
        st, body = await self._request(
            panel, "POST", f"/updateClient/{client_id}", json_body=payload, ctx=ctx
        )
        if st not in (200, 201):
            return PanelActionResult(ok=False, message=str(body)[:300])
        return PanelActionResult(ok=True)

    async def get_config_links(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        client_id: str | None,
        ctx: PanelLogContext | None = None,
    ) -> tuple[list[str], str | None]:
        st, body = await self._request(
            panel, "GET", f"/getClientTraffics/{panel_username}", ctx=ctx
        )
        if st != 200:
            return [], str(body)[:200]
        return [], None
