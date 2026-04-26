"""API location change: new panel account with remaining quota, same subscription token."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot_app.db.models import Panel, PanelAccount, Plan, Server, User, UserService
from bot_app.providers.factory import get_provider_for_panel
from bot_app.services.audit import audit_log
from bot_app.services.quota import remaining_bytes, total_service_used_bytes
from bot_app.services.wallet import adjust_balance

logger = logging.getLogger(__name__)


class SettingsLike(Protocol):
    panel_credential_encryption_key: str
    location_change_fee: int


async def migrate_service_to_server(
    session: AsyncSession,
    *,
    settings: SettingsLike,
    us: UserService,
    target_server: Server,
    target_panel: Panel,
    user: User,
    request_id: str,
) -> tuple[bool, str]:
    fee = int(settings.location_change_fee or 0)
    if fee > 0:
        ok, _, _ = await adjust_balance(
            session,
            user_id=user.id,
            delta=-fee,
            tx_type="location_change_fee",
            reference=f"loc:{us.public_service_code}",
            request_id=request_id,
        )
        if not ok:
            return False, "fee_failed"

    accounts = (
        await session.execute(select(PanelAccount).where(PanelAccount.user_service_id == us.id))
    ).scalars().all()
    used = total_service_used_bytes(accounts)
    total = int(us.total_quota_bytes)
    remaining = remaining_bytes(total, used)
    if remaining <= 0:
        if fee > 0:
            await adjust_balance(
                session,
                user_id=user.id,
                delta=fee,
                tx_type="location_change_refund",
                request_id=request_id,
            )
        return False, "no_remaining"

    old_active = [a for a in accounts if a.is_active and a.status == "active"]
    if not old_active:
        return False, "no_active_account"
    old_pa = old_active[0]
    old_panel = (await session.execute(select(Panel).where(Panel.id == old_pa.panel_id))).scalar_one()

    plan = (await session.execute(select(Plan).where(Plan.id == us.plan_id))).scalar_one()
    backend_user = old_pa.username

    prov_new = get_provider_for_panel(
        target_panel,
        request_id=request_id,
        encryption_key=settings.panel_credential_encryption_key,
    )
    expire_ms = int(us.expire_at.timestamp() * 1000)
    inbound_id = target_server.inbound_id or target_panel.inbound_id
    cr = await prov_new.create_account(backend_user, remaining, expire_ms, inbound_id=inbound_id)
    if not cr.ok:
        if fee > 0:
            await adjust_balance(
                session,
                user_id=user.id,
                delta=fee,
                tx_type="location_change_refund",
                request_id=request_id,
            )
        return False, "new_account_failed"

    cfg = await prov_new.get_config_links(backend_user)
    if not cfg.ok:
        await prov_new.delete_account(backend_user)
        if fee > 0:
            await adjust_balance(
                session,
                user_id=user.id,
                delta=fee,
                tx_type="location_change_refund",
                request_id=request_id,
            )
        return False, "new_config_failed"

    links = list((cfg.data or {}).get("links") or [])
    raw_sub = (cfg.data or {}).get("subscription_url")

    prov_old = get_provider_for_panel(
        old_panel,
        request_id=request_id,
        encryption_key=settings.panel_credential_encryption_key,
    )
    await prov_old.disable_account(old_pa.username)
    await prov_old.delete_account(old_pa.username)

    consumed_old = max(0, int(old_pa.total_used_bytes or 0) - int(old_pa.usage_baseline_bytes or 0))
    old_pa.is_active = False
    old_pa.status = "migrated"
    old_pa.final_used_bytes = consumed_old
    old_pa.disabled_at = datetime.now(timezone.utc)

    new_pa = PanelAccount(
        user_service_id=us.id,
        panel_id=target_panel.id,
        server_id=target_server.id,
        panel_type=target_panel.type,
        panel_account_id=(cr.data or {}).get("client_uuid"),
        username=backend_user,
        config_links_json={"links": links},
        raw_subscription_url=str(raw_sub) if raw_sub else None,
        quota_bytes_assigned=remaining,
        usage_baseline_bytes=0,
        is_active=True,
        status="active",
        activated_at=datetime.now(timezone.utc),
    )
    session.add(new_pa)

    us.current_server_id = target_server.id
    us.remaining_traffic_bytes = remaining
    us.used_traffic_bytes = used
    us.last_location_change_at = datetime.now(timezone.utc)
    us.location_change_count = int(us.location_change_count or 0) + 1
    now = datetime.now(timezone.utc)
    us.location_change_month_key = f"{now.year}-{now.month:02d}"

    await session.flush()
    await audit_log(
        session,
        action="api_migration",
        user_telegram_id=user.telegram_id,
        details=us.public_service_code,
        request_id=request_id,
    )
    logger.info("[MIGRATION] ok rid=%s code=%s", request_id, us.public_service_code)
    return True, "ok"
