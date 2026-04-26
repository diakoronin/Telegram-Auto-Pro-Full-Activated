"""FastAPI app for GET /sub/{token} and /health."""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot_app.db.models import PanelAccount, User, UserService

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)


def create_subscription_app(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    sub_base64_enabled: bool,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None)
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.state.session_factory = session_factory
    app.state.sub_base64_enabled = sub_base64_enabled

    @app.get("/health")
    async def health():
        try:
            async with session_factory() as s:
                await s.execute(select(User).limit(1))
            return {"ok": True, "db": True}
        except Exception as e:
            logger.exception("[SUBSCRIPTION] health failed")
            return Response(status_code=503, content=str(e))

    @app.get("/sub/{token}")
    @limiter.limit("120/minute")
    async def subscription(request: Request, token: str):  # noqa: ARG001 — required for SlowAPI
        rid = request.headers.get("X-Request-Id", "-")
        # Never log full token
        logger.info("[SUBSCRIPTION] fetch token_prefix=%s rid=%s", token[:6] + "…", rid)
        async with session_factory() as session:
            r = await session.execute(select(UserService).where(UserService.subscription_token == token))
            us = r.scalar_one_or_none()
            if not us:
                return Response(status_code=404, content="")
            user = (await session.execute(select(User).where(User.id == us.user_id))).scalar_one_or_none()
            if not user or user.is_blocked:
                return PlainTextResponse("", status_code=404)
            if not us.subscription_enabled:
                return PlainTextResponse("", status_code=404)
            if us.status in ("disabled", "refunded", "error"):
                return PlainTextResponse("", status_code=404)
            now = datetime.now(timezone.utc)
            exp = us.expire_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            else:
                exp = exp.astimezone(timezone.utc)
            if exp <= now or us.status in ("expired", "limited"):
                return PlainTextResponse("", status_code=404)

            pa_r = await session.execute(
                select(PanelAccount).where(
                    PanelAccount.user_service_id == us.id,
                    PanelAccount.is_active.is_(True),
                    PanelAccount.status == "active",
                )
            )
            pa = pa_r.scalar_one_or_none()
            if not pa or not pa.config_links_json:
                return PlainTextResponse("", status_code=404)

            links = []
            if isinstance(pa.config_links_json, dict):
                links = list(pa.config_links_json.get("links") or [])
            body = "\n".join(links) if links else ""
            if app.state.sub_base64_enabled and body:
                body = base64.b64encode(body.encode("utf-8")).decode("ascii")

            expire_ts = int(us.expire_at.timestamp()) if us.expire_at else 0
            upload = int(pa.upload_bytes or 0)
            download = int(pa.download_bytes or 0)
            total = int(us.total_quota_bytes or 0)
            headers = {
                "Subscription-Userinfo": (
                    f"upload={upload}; download={download}; total={total}; expire={expire_ts}"
                ),
                "Cache-Control": "no-store",
                "Content-Type": "text/plain; charset=utf-8",
            }
            return Response(content=body, media_type="text/plain; charset=utf-8", headers=headers)

    return app
