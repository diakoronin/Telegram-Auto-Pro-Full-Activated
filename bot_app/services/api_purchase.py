"""API purchase saga (wallet + panel + user_service)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot_app.db.models import Panel, PanelAccount, Plan, Purchase, Server, User, UserService
from bot_app.providers.base import PanelErrorCode
from bot_app.providers.factory import get_provider_for_panel
from bot_app.services.audit import audit_log
from bot_app.services.codes import backend_username, generate_public_service_code, generate_subscription_token
from bot_app.services.wallet import adjust_balance

logger = logging.getLogger(__name__)


class SettingsLike(Protocol):
    panel_credential_encryption_key: str
    multi_backend_active: bool
    public_base_url: str


async def _cleanup_panel_user(
    panel: Panel,
    settings: SettingsLike,
    request_id: str,
    backend_user: str,
) -> None:
    try:
        prov = get_provider_for_panel(panel, request_id=request_id, encryption_key=settings.panel_credential_encryption_key)
        await prov.disable_account(backend_user)
        await prov.delete_account(backend_user)
    except Exception:
        logger.exception("[PURCHASE] cleanup_panel_failed rid=%s user=%s", request_id, backend_user)


async def execute_api_purchase_saga(
    session: AsyncSession,
    *,
    settings: SettingsLike,
    user: User,
    plan: Plan,
    server: Server,
    panel: Panel,
    custom_service_name: str,
    price: int,
    request_id: str,
) -> tuple[bool, str, Optional[dict]]:
    """
    Returns (success, message_key_or_error, extra_dict).
    On failure, external account is compensated when possible.
    """
    if user.is_blocked:
        return False, "blocked", None
    if int(user.wallet_balance) < int(price):
        return False, "insufficient_balance", None

    public_code = generate_public_service_code()
    sub_token = generate_subscription_token()
    backend_user = backend_username(user.telegram_id, public_code, plan.volume_gb)

    # Check duplicate active panel account (app-level)
    # (DB partial unique on PostgreSQL also)
    existing = await session.execute(
        select(PanelAccount).where(
            PanelAccount.username == backend_user,
            PanelAccount.is_active.is_(True),
        )
    )
    if existing.scalar_one_or_none():
        return False, "username_collision", None

    expire_at = datetime.now(timezone.utc) + timedelta(days=int(plan.duration_days))

    purchase = Purchase(
        user_id=user.id,
        user_telegram_id=user.telegram_id,
        purchase_type="api",
        server_id=server.id,
        plan_id=plan.id,
        price=int(price),
        status="pending",
    )
    session.add(purchase)
    await session.flush()

    user_service = UserService(
        public_service_code=public_code,
        user_id=user.id,
        user_telegram_id=user.telegram_id,
        purchase_id=purchase.id,
        plan_id=plan.id,
        current_server_id=server.id,
        custom_service_name=custom_service_name,
        total_quota_bytes=int(plan.total_quota_bytes),
        used_traffic_bytes=0,
        remaining_traffic_bytes=int(plan.total_quota_bytes),
        expire_at=expire_at,
        status="active",
        subscription_token=sub_token,
    )
    session.add(user_service)
    await session.flush()

    purchase.user_service_id = user_service.id
    user_service.purchase_id = purchase.id

    prov = get_provider_for_panel(
        panel,
        request_id=request_id,
        encryption_key=settings.panel_credential_encryption_key,
    )
    expire_ms = int(expire_at.timestamp() * 1000)
    inbound_id = server.inbound_id or panel.inbound_id

    logger.info("[PURCHASE] create_account rid=%s backend=%s", request_id, backend_user)
    create_res = await prov.create_account(
        backend_user,
        int(plan.total_quota_bytes),
        expire_ms,
        inbound_id=inbound_id,
        email=f"tg{user.telegram_id}",
    )
    if not create_res.ok:
        if create_res.error_code == PanelErrorCode.user_already_exists:
            exists_usage = await prov.get_account_usage(backend_user)
            if exists_usage.ok:
                await _cleanup_panel_user(panel, settings, request_id, backend_user)
        purchase.status = "failed"
        user_service.status = "error"
        await session.flush()
        await audit_log(
            session,
            action="api_purchase_failed",
            user_telegram_id=user.telegram_id,
            details=str(create_res.error_code),
            request_id=request_id,
        )
        return False, "panel_create_failed", {"code": str(create_res.error_code)}

    cfg = await prov.get_config_links(backend_user)
    if not cfg.ok or not (cfg.data or {}).get("links"):
        await _cleanup_panel_user(panel, settings, request_id, backend_user)
        purchase.status = "failed"
        user_service.status = "error"
        await session.flush()
        await audit_log(session, action="api_purchase_config_failed", user_telegram_id=user.telegram_id, request_id=request_id)
        return False, "config_fetch_failed", None

    links: List[str] = list((cfg.data or {}).get("links") or [])
    raw_sub = (cfg.data or {}).get("subscription_url")

    panel_account = PanelAccount(
        user_service_id=user_service.id,
        panel_id=panel.id,
        server_id=server.id,
        panel_type=panel.type,
        panel_account_id=(create_res.data or {}).get("client_uuid"),
        username=backend_user,
        config_links_json={"links": links},
        raw_subscription_url=str(raw_sub) if raw_sub else None,
        quota_bytes_assigned=int(plan.total_quota_bytes),
        usage_baseline_bytes=0,
        upload_bytes=0,
        download_bytes=0,
        total_used_bytes=0,
        is_active=True,
        status="active",
        activated_at=datetime.now(timezone.utc),
    )
    session.add(panel_account)
    await session.flush()

    ok_wallet, before, after = await adjust_balance(
        session,
        user_id=user.id,
        delta=-int(price),
        tx_type="purchase_api",
        reference=f"purchase:{purchase.id}",
        purchase_id=purchase.id,
        request_id=request_id,
    )
    if not ok_wallet:
        await _cleanup_panel_user(panel, settings, request_id, backend_user)
        purchase.status = "failed"
        user_service.status = "error"
        panel_account.is_active = False
        panel_account.status = "failed"
        await session.flush()
        await audit_log(session, action="api_purchase_wallet_failed", user_telegram_id=user.telegram_id, request_id=request_id)
        return False, "wallet_deduct_failed", None

    purchase.status = "completed"
    await session.flush()
    await audit_log(session, action="api_purchase", user_telegram_id=user.telegram_id, details=public_code, request_id=request_id)

    base = (settings.public_base_url or "").rstrip("/")
    stable_url = f"{base}/sub/{sub_token}"
    return True, "ok", {
        "purchase_id": purchase.id,
        "public_service_code": public_code,
        "subscription_token": sub_token,
        "stable_subscription_url": stable_url,
        "custom_service_name": custom_service_name,
        "plan_display_name": plan.display_name,
        "server_name": server.name,
        "price": price,
        "balance_after": after,
    }
