"""Admin WebApp: HTML, static files, API with Telegram initData verification."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot_app.config import get_settings
from bot_app.db.models import Purchase, Server, User, UserService
from bot_app.webapp.telegram_webapp_auth import (
    get_telegram_user_id_from_verified,
    verify_telegram_webapp_init_data,
)

logger = logging.getLogger(__name__)

# ---- Template / static resolution (package dir or cwd) ----
_PACKAGE_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _PACKAGE_DIR / "static"
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"

bearer_scheme = HTTPBearer(auto_error=False)


async def _is_admin_in_db(
    session_factory: async_sessionmaker[AsyncSession], telegram_id: int
) -> bool:
    from bot_app.db.models import Admin

    s = get_settings()
    if telegram_id == s.owner_id:
        return True
    async with session_factory() as session:
        r = await session.execute(
            select(Admin).where(Admin.telegram_id == telegram_id, Admin.is_active.is_(True))
        )
        return r.scalar_one_or_none() is not None


def mount_admin_routes(
    app,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    s = get_settings()
    if not s.admin_webapp_enabled:
        logger.info("[WEBAPP] Admin WebApp disabled (ADMIN_WEBAPP_ENABLED=false)")
        return

    # Lazy load Jinja2
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError as e:  # pragma: no cover
        logger.error("[WEBAPP] jinja2 required: pip install jinja2 — %s", e)
        return

    jinja_env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    if _STATIC_DIR.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount(
            "/admin-wa/static",
            StaticFiles(directory=str(_STATIC_DIR)),
            name="admin_wa_static",
        )

    router = APIRouter(prefix="/admin-wa", tags=["admin-webapp"])

    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def admin_spa() -> str:
        tpl = jinja_env.get_template("admin_webapp.html")
        base = s.webapp_public_base_url or s.public_base_url_normalized
        return tpl.render(
            brand_name=s.brand_name,
            public_base=base,
            cdn_telegram=True,
        )

    async def require_init_data(
        request: Request,
        creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    ) -> int:
        """Return verified Telegram user id or 401."""
        if creds is None or creds.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid Authorization"
            )
        token = s.bot_token
        init_data = creds.credentials
        if len(init_data) > 1_000_000:
            raise HTTPException(status_code=400, detail="initData too large")
        verified = verify_telegram_webapp_init_data(init_data, token)
        if not verified:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid initData")
        uid = get_telegram_user_id_from_verified(verified)
        if uid is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No user in initData")
        if not await _is_admin_in_db(session_factory, uid):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not an admin")
        return uid

    @router.get("/api/health", include_in_schema=False)
    async def wa_health() -> dict:
        return {"ok": True, "webapp": s.admin_webapp_enabled}

    @router.get("/api/summary", include_in_schema=False)
    async def api_summary(telegram_id: int = Depends(require_init_data)) -> Any:
        """Dashboard stats; real SQL aggregates where available."""
        # Today sales window in Asia/Tehran (matches business day in Iran)
        tz = ZoneInfo(get_settings().timezone or "Asia/Tehran")
        now_local = datetime.now(tz)
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=1)
        start = start_local.astimezone(timezone.utc)
        end = end_local.astimezone(timezone.utc)

        async with session_factory() as session:
            n_users = (
                await session.execute(
                    select(func.count()).select_from(User).where(User.is_blocked.is_(False))
                )
            ).scalar_one()
            n_active = (
                await session.execute(
                    select(func.count()).select_from(UserService).where(UserService.status == "active")
                )
            ).scalar_one()
            n_purch_today = (
                await session.execute(
                    select(func.count())
                    .select_from(Purchase)
                    .where(
                        Purchase.status == "completed",
                        Purchase.created_at >= start,
                        Purchase.created_at < end,
                    )
                )
            ).scalar_one()
            n_servers = (
                await session.execute(
                    select(func.count()).select_from(Server).where(Server.is_active.is_(True))
                )
            ).scalar_one()

        return {
            "total_users": int(n_users or 0),
            "active_subscriptions": int(n_active or 0),
            "today_completed_sales": int(n_purch_today or 0),
            "active_servers": int(n_servers or 0),
            "telegram_id": telegram_id,
        }

    @router.post("/api/placeholder/{action_id}", include_in_schema=False)
    async def api_placeholder(
        action_id: str, telegram_id: int = Depends(require_init_data)
    ) -> Any:
        """Stub actions — replace with real handlers; always JSON."""
        return {
            "ok": True,
            "action": action_id,
            "message": "این بخش به‌زودی کامل می‌شود.",
            "by": telegram_id,
        }

    app.include_router(router)
    logger.info(
        "[WEBAPP] Admin panel mounted at %s/admin-wa/ (set WEBAPP_PUBLIC_BASE_URL in BotFather for Mini App URL)",
        s.public_base_url_normalized,
    )
