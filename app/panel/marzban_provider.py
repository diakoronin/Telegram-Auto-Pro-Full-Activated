"""
Marzban REST API (verified against Gozargah/Marzban source):
- POST /api/admin/token — OAuth2 form username/password
- POST /api/user — create user (JSON body UserCreate)
- GET /api/user/{username} — get user (used for usage + links)
- PUT /api/user/{username} — modify (status, data_limit, expire)
- DELETE /api/user/{username} — remove
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import Settings
from app.db.models import Panel, Plan, Server, UserService
from app.panel.errors import PanelErrorCode
from app.panel.base import PanelProvider
from app.panel.panel_urls import marzban_api_url
from app.panel.secrets import panel_plain_api_token, panel_plain_password
from app.panel.types import (
    PanelActionResult,
    PanelCreateResult,
    PanelLogContext,
    PanelTestResult,
    PanelUsageResult,
)
from app.structured_log import redact_secrets

log = logging.getLogger("app.panel.marzban")


def _map_err(status: int, body: Any) -> tuple[PanelErrorCode, str]:
    if status in (401, 403):
        return PanelErrorCode.INVALID_CREDENTIALS, str(body)[:300]
    if status == 404:
        return PanelErrorCode.USER_NOT_FOUND, str(body)[:300]
    if status == 409:
        return PanelErrorCode.USER_ALREADY_EXISTS, str(body)[:300]
    if status == 0:
        return PanelErrorCode.CONNECTION_REFUSED, str(body)[:300]
    return PanelErrorCode.PANEL_UNAVAILABLE, str(body)[:300]


class MarzbanProvider(PanelProvider):
    async def _request(
        self,
        panel: Panel,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        data_form: dict[str, str] | None = None,
        token: str | None = None,
        ctx: PanelLogContext | None = None,
    ) -> tuple[int, Any | None]:
        rid = ctx.request_id if ctx else "-"
        url = marzban_api_url(panel, path)
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        t0 = time.monotonic()
        log.info(
            "[MARZBAN REQUEST] rid=%s method=%s url=%s body=%s",
            rid,
            method,
            url,
            json.dumps(redact_secrets(json_body) if json_body else {}, ensure_ascii=False)[:800],
        )
        try:
            async with httpx.AsyncClient(
                verify=panel.verify_ssl,
                timeout=float(panel.timeout_seconds or 30),
                follow_redirects=True,
            ) as client:
                r = await client.request(method, url, headers=headers, json=json_body, data=data_form)
            ms = int((time.monotonic() - t0) * 1000)
            body: Any = None
            ct = (r.headers.get("content-type") or "").lower()
            if "json" in ct:
                try:
                    body = r.json()
                except Exception:
                    body = r.text
            else:
                body = r.text
            log.info(
                "[MARZBAN RESPONSE] rid=%s status=%s duration_ms=%s body=%s",
                rid,
                r.status_code,
                ms,
                json.dumps(redact_secrets(body), ensure_ascii=False)[:1200] if isinstance(body, (dict, list)) else str(body)[:500],
            )
            return r.status_code, body
        except httpx.TimeoutException as e:
            log.warning("[MARZBAN RESPONSE] rid=%s timeout %s", rid, e)
            return 0, str(e)
        except Exception as e:
            log.warning("[MARZBAN RESPONSE] rid=%s error %s", rid, e)
            return 0, str(e)

    async def _get_token(self, panel: Panel, ctx: PanelLogContext | None) -> tuple[str | None, str | None]:
        tok = panel_plain_api_token(panel, self._settings)
        if tok:
            return tok.strip(), None
        pw = panel_plain_password(panel, self._settings)
        form = {"username": panel.username, "password": pw}
        status, body = await self._request(
            panel, "POST", "/admin/token", data_form=form, ctx=ctx
        )
        if status != 200 or not isinstance(body, dict):
            code, msg = _map_err(status, body)
            return None, msg
        at = body.get("access_token")
        if not at:
            return None, "no access_token in response"
        return str(at), None

    async def test_connection(self, panel: Panel, *, ctx: PanelLogContext | None = None) -> PanelTestResult:
        t0 = time.monotonic()
        token, err = await self._get_token(panel, ctx)
        if err or not token:
            return PanelTestResult(
                ok=False,
                duration_ms=int((time.monotonic() - t0) * 1000),
                error_code=PanelErrorCode.INVALID_CREDENTIALS,
                message=err or "auth failed",
            )
        st, _ = await self._request(panel, "GET", "/admin", token=token, ctx=ctx)
        ms = int((time.monotonic() - t0) * 1000)
        if st != 200:
            code, msg = _map_err(st, _)
            return PanelTestResult(ok=False, duration_ms=ms, error_code=code, message=msg)
        return PanelTestResult(ok=True, duration_ms=ms)

    def _default_proxies_inbounds(self, panel: Panel) -> tuple[dict[str, Any], dict[str, Any]]:
        px = panel.marzban_proxies_json or {}
        ib = panel.marzban_inbounds_json or {}
        if not px or not ib:
            raise ValueError(
                "Marzban panel needs marzban_proxies_json and marzban_inbounds_json in DB "
                "(see Marzban UserCreate: proxies + inbounds per protocol)."
            )
        return px, ib

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
        token, err = await self._get_token(panel, ctx)
        if err or not token:
            return PanelCreateResult(ok=False, error_code=PanelErrorCode.INVALID_CREDENTIALS, message=err)
        try:
            proxies, inbounds = self._default_proxies_inbounds(panel)
        except ValueError as e:
            return PanelCreateResult(
                ok=False, error_code=PanelErrorCode.INVALID_INBOUND, message=str(e)
            )
        expire_ts = 0
        if expire_at is not None:
            expire_ts = int(expire_at.astimezone(UTC).timestamp())
        payload: dict[str, Any] = {
            "username": backend_username[:32],
            "proxies": proxies,
            "inbounds": inbounds,
            "expire": expire_ts,
            "data_limit": int(quota_bytes),
            "data_limit_reset_strategy": "no_reset",
            "status": "active",
        }
        st, body = await self._request(panel, "POST", "/user", json_body=payload, token=token, ctx=ctx)
        if st == 409:
            # Idempotency: fetch existing user
            st2, body2 = await self._request(
                panel, "GET", f"/user/{backend_username}", token=token, ctx=ctx
            )
            if st2 != 200 or not isinstance(body2, dict):
                code, msg = _map_err(st2, body2)
                return PanelCreateResult(ok=False, error_code=code, message=msg)
            body = body2
        elif st not in (200, 201) or not isinstance(body, dict):
            code, msg = _map_err(st, body)
            return PanelCreateResult(ok=False, error_code=code, message=msg)
        links = body.get("links") or []
        if not isinstance(links, list):
            links = []
        sub_url = str(body.get("subscription_url") or "")
        return PanelCreateResult(
            ok=True,
            panel_account_id=backend_username,
            username=backend_username,
            config_links=[str(x) for x in links if str(x).strip()],
            raw_subscription_url=sub_url or None,
        )

    async def get_account_usage(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        ctx: PanelLogContext | None = None,
    ) -> PanelUsageResult:
        token, err = await self._get_token(panel, ctx)
        if err or not token:
            return PanelUsageResult(ok=False, error_code=PanelErrorCode.INVALID_CREDENTIALS, message=err)
        st, body = await self._request(panel, "GET", f"/user/{panel_username}", token=token, ctx=ctx)
        if st != 200 or not isinstance(body, dict):
            code, msg = _map_err(st, body)
            return PanelUsageResult(ok=False, error_code=code, message=msg)
        used = int(body.get("used_traffic") or 0)
        return PanelUsageResult(ok=True, upload_bytes=0, download_bytes=0, total_used_bytes=used)

    async def disable_account(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        client_id: str | None,
        ctx: PanelLogContext | None = None,
    ) -> PanelActionResult:
        return await self._modify_status(panel, panel_username, "disabled", ctx)

    async def enable_account(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        client_id: str | None,
        ctx: PanelLogContext | None = None,
    ) -> PanelActionResult:
        return await self._modify_status(panel, panel_username, "active", ctx)

    async def _modify_status(
        self, panel: Panel, username: str, status: str, ctx: PanelLogContext | None
    ) -> PanelActionResult:
        token, err = await self._get_token(panel, ctx)
        if err or not token:
            return PanelActionResult(ok=False, error_code=PanelErrorCode.INVALID_CREDENTIALS, message=err)
        st, body = await self._request(
            panel,
            "PUT",
            f"/user/{username}",
            json_body={"status": status},
            token=token,
            ctx=ctx,
        )
        if st not in (200, 201):
            code, msg = _map_err(st, body)
            return PanelActionResult(ok=False, error_code=code, message=msg)
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
        token, err = await self._get_token(panel, ctx)
        if err or not token:
            return PanelActionResult(ok=False, error_code=PanelErrorCode.INVALID_CREDENTIALS, message=err)
        st, body = await self._request(panel, "DELETE", f"/user/{panel_username}", token=token, ctx=ctx)
        if st not in (200, 204) and st != 404:
            code, msg = _map_err(st, body)
            return PanelActionResult(ok=False, error_code=code, message=msg)
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
        token, err = await self._get_token(panel, ctx)
        if err or not token:
            return PanelActionResult(ok=False, error_code=PanelErrorCode.INVALID_CREDENTIALS, message=err)
        st, body = await self._request(
            panel,
            "PUT",
            f"/user/{panel_username}",
            json_body={"data_limit": int(quota_bytes)},
            token=token,
            ctx=ctx,
        )
        if st not in (200, 201):
            code, msg = _map_err(st, body)
            return PanelActionResult(ok=False, error_code=code, message=msg)
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
        token, err = await self._get_token(panel, ctx)
        if err or not token:
            return PanelActionResult(ok=False, error_code=PanelErrorCode.INVALID_CREDENTIALS, message=err)
        ts = 0 if expire_at is None else int(expire_at.astimezone(UTC).timestamp())
        st, body = await self._request(
            panel,
            "PUT",
            f"/user/{panel_username}",
            json_body={"expire": ts},
            token=token,
            ctx=ctx,
        )
        if st not in (200, 201):
            code, msg = _map_err(st, body)
            return PanelActionResult(ok=False, error_code=code, message=msg)
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
        token, err = await self._get_token(panel, ctx)
        if err or not token:
            return [], err
        st, body = await self._request(panel, "GET", f"/user/{panel_username}", token=token, ctx=ctx)
        if st != 200 or not isinstance(body, dict):
            return [], str(body)[:200]
        links = body.get("links") or []
        if not isinstance(links, list):
            links = []
        sub = str(body.get("subscription_url") or "") or None
        return [str(x) for x in links if str(x).strip()], sub
