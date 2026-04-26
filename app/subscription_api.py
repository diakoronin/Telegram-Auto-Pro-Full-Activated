"""
HTTP subscription endpoint: GET /sub/{subscription_token}

Each **API-managed** purchased service has its own row in ``user_services`` and its own
``subscription_token``. This endpoint only reads those rows plus the active
``panel_account`` — it never includes **manual stock links** (admin manual delivery),
because those are not tied to ``user_services`` and the bot does not track their
traffic or expiry centrally. Manual delivery stays a separate admin workflow.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.base import get_session_factory
from app.db.models import (
    PanelAccount,
    User,
    UserService,
    UserServiceStatus,
)

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
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/sub/{subscription_token}")
    async def subscription(
        subscription_token: str,
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        tok = (subscription_token or "").strip()
        if not tok or len(tok) < 16:
            return empty_response()

        r = await session.execute(
            select(UserService, User)
            .join(User, User.id == UserService.user_id)
            .where(UserService.subscription_token == tok)
            .limit(1)
        )
        row = r.one_or_none()
        if row is None:
            return empty_response()
        us, u = row

        if (
            u.is_blocked
            or not us.subscription_enabled
            or us.status != UserServiceStatus.ACTIVE
        ):
            return empty_response()

        pa_r = await session.execute(
            select(PanelAccount)
            .where(
                PanelAccount.user_service_id == us.id,
                PanelAccount.is_active.is_(True),
            )
            .order_by(PanelAccount.id.desc())
            .limit(1)
        )
        pa = pa_r.scalar_one_or_none()
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
