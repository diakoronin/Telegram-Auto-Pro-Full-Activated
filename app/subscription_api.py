"""
HTTP subscription: GET /sub/{subscription_token}, GET /health.

Rate-limited by token hash and client IP (DB buckets).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, Request, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.base import get_session_factory
from app.db.models import PanelAccount, User, UserService, UserServiceStatus
from app.services.rate_limit import consume_rate
from app.structured_log import mask_subscription_token

logger = logging.getLogger("app.subscription")


def create_subscription_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Subscription", docs_url=None, redoc_url=None)
    factory = get_session_factory(settings.database_url)

    def empty_response() -> Response:
        return Response(
            content="",
            status_code=404,
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    async def get_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    @app.get("/health")
    async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
        rid = uuid.uuid4().hex[:12]
        try:
            await session.execute(text("SELECT 1"))
            return {"status": "ok", "database": "ok", "request_id": rid}
        except Exception as e:
            logger.error("health DB fail rid=%s err=%s", rid, e)
            return {"status": "degraded", "database": "error", "request_id": rid}

    @app.get("/sub/{subscription_token}")
    async def subscription(
        request: Request,
        subscription_token: str,
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        rid = uuid.uuid4().hex[:12]
        tok = (subscription_token or "").strip()
        tok_masked = mask_subscription_token(tok)
        client_ip = request.client.host if request.client else "unknown"

        if not tok or len(tok) < 16:
            logger.info("[SUBSCRIPTION] rid=%s token=%s invalid", rid, tok_masked)
            return empty_response()

        tok_hash = hashlib.sha256(tok.encode("utf-8")).hexdigest()[:40]
        ok_tok = await consume_rate(
            session,
            key=f"sub:tok:{tok_hash}",
            window_seconds=60,
            max_count=settings.sub_rate_limit_per_minute,
        )
        ok_ip = await consume_rate(
            session,
            key=f"sub:ip:{client_ip}",
            window_seconds=60,
            max_count=settings.sub_ip_rate_limit_per_minute,
        )
        if not ok_tok or not ok_ip:
            logger.warning("[SUBSCRIPTION] rid=%s rate_limited ip=%s", rid, client_ip)
            return empty_response()

        r = await session.execute(
            select(UserService, User)
            .join(User, User.id == UserService.user_id)
            .where(UserService.subscription_token == tok)
            .limit(1)
        )
        row = r.one_or_none()
        if row is None:
            logger.info("[SUBSCRIPTION] rid=%s token=%s not_found", rid, tok_masked)
            return empty_response()
        us, u = row

        if u.is_blocked or not us.subscription_enabled:
            logger.info("[SUBSCRIPTION] rid=%s blocked_or_disabled user=%s", rid, u.id)
            return empty_response()

        if us.status not in (UserServiceStatus.ACTIVE, UserServiceStatus.MIGRATING):
            logger.info(
                "[SUBSCRIPTION] rid=%s status=%s svc=%s",
                rid,
                us.status.value,
                us.public_service_code,
            )
            return empty_response()

        pa_r = await session.execute(
            select(PanelAccount)
            .where(
                PanelAccount.user_service_id == us.id,
                PanelAccount.is_active.is_(True),
            )
            .order_by(PanelAccount.id.desc())
        )
        actives = list(pa_r.scalars().all())
        if len(actives) > 1 and not settings.multi_backend_active:
            logger.error(
                "[SUBSCRIPTION] rid=%s CRITICAL multiple_active user_service_id=%s count=%s",
                rid,
                us.id,
                len(actives),
            )
            actives = [max(actives, key=lambda x: x.id)]
        pa = actives[0] if actives else None
        if pa is None:
            return empty_response()

        links = pa.config_links_json or []
        if not isinstance(links, list):
            links = []
        body_lines = [str(x).strip() for x in links if str(x).strip()]
        body = "\n".join(body_lines)

        if settings.sub_base64_enabled and body:
            body = base64.b64encode(body.encode("utf-8")).decode("ascii")

        upload = int(pa.upload_bytes or 0)
        download = int(pa.download_bytes or 0)
        total = int(us.total_quota_bytes or 0)
        expire_ts = 0
        if us.expire_at is not None:
            expire_ts = int(us.expire_at.timestamp())

        logger.info(
            "[SUBSCRIPTION] rid=%s ok svc=%s token=%s",
            rid,
            us.public_service_code,
            tok_masked,
        )
        return Response(
            content=body or "",
            status_code=200,
            media_type="text/plain; charset=utf-8",
            headers={
                "Cache-Control": "no-store",
                "Subscription-Userinfo": (
                    f"upload={upload}; download={download}; total={total}; expire={expire_ts}"
                ),
            },
        )

    return app
