"""Background traffic sync: panel usage -> central user_service + snapshots."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    Panel,
    PanelAccount,
    PanelAccountStatus,
    Server,
    TrafficUsageSnapshot,
    User,
    UserService,
    UserServiceStatus,
)
from app.panel.factory import get_provider
from app.panel.types import PanelLogContext
from app.services.quota import consumed_from_account, recompute_user_service_traffic
from app.structured_log import new_request_id, set_request_id, reset_request_id

log = logging.getLogger("app.traffic_sync")


async def sync_active_services_batch(
    session: AsyncSession,
    settings: Settings,
    *,
    bot: Bot | None,
    batch_size: int = 50,
) -> int:
    """Returns number of services processed."""
    rid_tok = set_request_id(new_request_id())
    try:
        q = await session.execute(
            select(UserService)
            .where(
                UserService.status.in_(
                    (UserServiceStatus.ACTIVE, UserServiceStatus.MIGRATING)
                )
            )
            .order_by(UserService.id.asc())
            .limit(batch_size)
        )
        services = list(q.scalars().all())
        n = 0
        for us in services:
            await _sync_one(session, settings, us, bot)
            n += 1
        return n
    finally:
        reset_request_id(rid_tok)


async def _sync_one(
    session: AsyncSession, settings: Settings, us: UserService, bot: Bot | None
) -> None:
    rid = new_request_id()
    ctx = PanelLogContext(request_id=rid, service_code=us.public_service_code, user_id=str(us.user_id))
    now = datetime.now(tz=UTC)
    if us.expire_at and us.expire_at <= now and us.status == UserServiceStatus.ACTIVE:
        us.status = UserServiceStatus.EXPIRED
        us.subscription_enabled = False
        await _disable_active_panel(session, settings, us, ctx)
        await session.flush()
        return

    pa_r = await session.execute(
        select(PanelAccount).where(
            PanelAccount.user_service_id == us.id,
            PanelAccount.is_active.is_(True),
        )
    )
    active_list = list(pa_r.scalars().all())
    if len(active_list) > 1 and not settings.multi_backend_active:
        log.error(
            "traffic_sync: multiple active panel_accounts for user_service_id=%s — using newest",
            us.id,
        )
        active_list.sort(key=lambda x: x.id)
        for extra in active_list[:-1]:
            extra.is_active = False
            extra.status = PanelAccountStatus.DISABLED
            extra.disabled_at = now
        active_list = active_list[-1:]
    if not active_list:
        return
    pa = active_list[0]
    panel = await session.get(Panel, pa.panel_id)
    if panel is None:
        return
    server = await session.get(Server, pa.server_id)
    inbound_id = server.inbound_id if server else None

    provider = get_provider(panel, settings)
    usage = await provider.get_account_usage(
        panel=panel, panel_username=pa.username, inbound_id=inbound_id, ctx=ctx
    )
    if not usage.ok:
        log.warning("traffic_sync usage fail us=%s msg=%s", us.id, usage.message)
        return

    pa.upload_bytes = usage.upload_bytes
    pa.download_bytes = usage.download_bytes
    pa.total_used_bytes = usage.total_used_bytes
    pa.last_synced_at = now

    await recompute_user_service_traffic(session, us)
    session.add(
        TrafficUsageSnapshot(
            user_service_id=us.id,
            panel_account_id=pa.id,
            upload_bytes=pa.upload_bytes,
            download_bytes=pa.download_bytes,
            total_used_bytes=pa.total_used_bytes,
            calculated_service_used_bytes=us.used_traffic_bytes,
            remaining_traffic_bytes=us.remaining_traffic_bytes,
            source_panel=panel.type.value,
        )
    )

    buffer_bytes = max(0, int(settings.traffic_safety_buffer_mb)) * 1024 * 1024
    if us.remaining_traffic_bytes <= buffer_bytes:
        await provider.update_quota(
            panel=panel,
            panel_username=pa.username,
            inbound_id=inbound_id,
            client_id=pa.panel_account_id,
            quota_bytes=max(0, us.remaining_traffic_bytes),
            ctx=ctx,
        )

    if us.remaining_traffic_bytes <= 0:
        us.status = UserServiceStatus.LIMITED
        us.subscription_enabled = False
        pa.is_active = False
        pa.status = PanelAccountStatus.DISABLED
        pa.final_used_bytes = consumed_from_account(pa)
        pa.disabled_at = now
        await provider.disable_account(
            panel=panel,
            panel_username=pa.username,
            inbound_id=inbound_id,
            client_id=pa.panel_account_id,
            ctx=ctx,
        )
        if bot:
            u = await session.get(User, us.user_id)
            if u:
                try:
                    await bot.send_message(
                        int(u.telegram_id),
                        "⚠️ حجم سرویس شما به پایان رسید.\n"
                        f"کد سرویس: {us.public_service_code}\n"
                        "برای ادامه، سرویس جدید تهیه کنید.",
                    )
                except Exception:
                    log.exception("notify user quota end failed")

    await session.flush()


async def _disable_active_panel(
    session: AsyncSession, settings: Settings, us: UserService, ctx: PanelLogContext
) -> None:
    pa_r = await session.execute(
        select(PanelAccount).where(
            PanelAccount.user_service_id == us.id,
            PanelAccount.is_active.is_(True),
        )
    )
    for pa in pa_r.scalars().all():
        panel = await session.get(Panel, pa.panel_id)
        if not panel:
            continue
        srv = await session.get(Server, pa.server_id)
        inbound_id = srv.inbound_id if srv else None
        provider = get_provider(panel, settings)
        await provider.disable_account(
            panel=panel,
            panel_username=pa.username,
            inbound_id=inbound_id,
            client_id=pa.panel_account_id,
            ctx=ctx,
        )
        pa.is_active = False
        pa.status = PanelAccountStatus.DISABLED
        pa.disabled_at = datetime.now(tz=UTC)
