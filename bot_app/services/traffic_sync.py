"""Background traffic sync from panel to central quota."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot_app.db.models import Panel, PanelAccount, TrafficUsageSnapshot, User, UserService
from bot_app.providers.factory import get_provider_for_panel
from bot_app.services.quota import remaining_bytes, total_service_used_bytes
from bot_app.services.audit import audit_log

logger = logging.getLogger(__name__)


class SettingsLike(Protocol):
    panel_credential_encryption_key: str
    traffic_sync_batch_size: int
    traffic_safety_buffer_mb: int


async def sync_one_service(
    session: AsyncSession,
    *,
    settings: SettingsLike,
    us: UserService,
    request_id: str,
) -> None:
    accounts = (
        await session.execute(select(PanelAccount).where(PanelAccount.user_service_id == us.id))
    ).scalars().all()
    active = [a for a in accounts if a.is_active and a.status == "active"]
    if not active:
        return
    pa = active[0]
    panel = (await session.execute(select(Panel).where(Panel.id == pa.panel_id))).scalar_one_or_none()
    if not panel:
        return
    prov = get_provider_for_panel(
        panel,
        request_id=request_id,
        encryption_key=settings.panel_credential_encryption_key,
    )
    usage_res = await prov.get_account_usage(pa.username)
    if not usage_res.ok:
        us.sync_failure_count = int(us.sync_failure_count or 0) + 1
        us.last_sync_error = str(usage_res.error_code)
        await session.flush()
        return
    usage = (usage_res.data or {}).get("usage")
    if usage is None:
        return
    up = int(getattr(usage, "upload_bytes", 0) or 0)
    down = int(getattr(usage, "download_bytes", 0) or 0)
    total = int(getattr(usage, "total_used_bytes", up + down) or 0)
    pa.upload_bytes = up
    pa.download_bytes = down
    pa.total_used_bytes = total
    pa.last_synced_at = datetime.now(timezone.utc)

    used_service = total_service_used_bytes(accounts)
    rem = remaining_bytes(int(us.total_quota_bytes), used_service)
    buffer = int(settings.traffic_safety_buffer_mb) * 1024 * 1024
    us.used_traffic_bytes = used_service
    us.remaining_traffic_bytes = max(0, rem - buffer)

    snap = TrafficUsageSnapshot(
        user_service_id=us.id,
        panel_account_id=pa.id,
        upload_bytes=up,
        download_bytes=down,
        total_used_bytes=total,
        calculated_service_used_bytes=used_service,
        remaining_traffic_bytes=us.remaining_traffic_bytes,
        source_panel=panel.name,
        request_id=request_id,
    )
    session.add(snap)

    now = datetime.now(timezone.utc)
    exp = us.expire_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    else:
        exp = exp.astimezone(timezone.utc)
    if exp <= now:
        us.status = "expired"
        pa.is_active = False
        pa.status = "disabled"
        pa.disabled_at = now
        await prov.disable_account(pa.username)
    elif us.remaining_traffic_bytes <= 0:
        us.status = "limited"
        pa.is_active = False
        pa.status = "disabled"
        pa.disabled_at = now
        await prov.disable_account(pa.username)
        await audit_log(
            session,
            action="traffic_limit_reached",
            user_telegram_id=us.user_telegram_id,
            details=us.public_service_code,
            request_id=request_id,
        )
    else:
        us.status = "active"
        us.sync_failure_count = 0
        us.last_sync_error = None

    await session.flush()


async def sync_batch(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: SettingsLike,
    owner_telegram_id: Optional[int] = None,
) -> int:
    """Process one batch; returns count processed."""
    rid = str(uuid.uuid4())[:12]
    logger.info("[TRAFFIC_SYNC] batch start rid=%s", rid)
    batch = int(settings.traffic_sync_batch_size or 100)
    processed = 0
    async with session_factory() as session:
        q = (
            select(UserService)
            .where(UserService.status.in_(["active", "limited"]))
            .order_by(UserService.id.asc())
            .limit(batch)
        )
        rows = (await session.execute(q)).scalars().all()
        for us in rows:
            await sync_one_service(session, settings=settings, us=us, request_id=rid)
            processed += 1
        await session.commit()
    logger.info("[TRAFFIC_SYNC] batch done rid=%s count=%s", rid, processed)
    return processed
