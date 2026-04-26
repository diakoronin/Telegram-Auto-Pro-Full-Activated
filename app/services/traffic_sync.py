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
    User,
    UserService,
    UserServiceStatus,
)
from app.panel.factory import get_provider
from app.panel.types import PanelLogContext
from app.services.app_settings import get_setting, set_setting
from app.services.owner_notify import notify_owner_text
from app.services.quota import consumed_from_account, recompute_user_service_traffic
from app.structured_log import new_request_id, reset_request_id, set_request_id
from app.db.models import TrafficUsageSnapshot

log = logging.getLogger("app.traffic_sync")

CURSOR_KEY = "traffic_sync_cursor_id"
FAIL_ALERT_STREAK = 3


async def run_traffic_sync_cycle(
    session: AsyncSession,
    settings: Settings,
    *,
    bot: Bot | None,
    batch_size: int | None = None,
) -> dict[str, int]:
    """
    One pagination cycle: never loads all services — keyset by user_service.id.
    Returns counts: processed, errors.
    """
    bs = batch_size if batch_size is not None else settings.traffic_sync_batch_size
    cycle_rid = new_request_id()
    rid_tok = set_request_id(cycle_rid)
    processed = 0
    errors = 0
    try:
        raw = await get_setting(session, CURSOR_KEY)
        cursor = int(raw or "0")

        q1 = (
            select(UserService.id)
            .where(
                UserService.status.in_(
                    (UserServiceStatus.ACTIVE, UserServiceStatus.MIGRATING)
                )
            )
            .where(UserService.id > cursor)
            .order_by(UserService.id.asc())
            .limit(bs)
        )
        r1 = await session.execute(q1)
        ids = [row[0] for row in r1.all()]
        if not ids:
            cursor = 0
            r2 = await session.execute(
                select(UserService.id)
                .where(
                    UserService.status.in_(
                        (UserServiceStatus.ACTIVE, UserServiceStatus.MIGRATING)
                    )
                )
                .where(UserService.id > cursor)
                .order_by(UserService.id.asc())
                .limit(bs)
            )
            ids = [row[0] for row in r2.all()]

        new_cursor = cursor
        for us_id in ids:
            new_cursor = us_id
            try:
                await _sync_one_by_id(session, settings, us_id, bot)
                processed += 1
            except Exception:
                errors += 1
                log.exception("traffic_sync rid=%s us_id=%s", cycle_rid, us_id)

        await set_setting(session, CURSOR_KEY, str(new_cursor))
        log.info(
            "traffic_sync_cycle rid=%s processed=%s errors=%s cursor=%s",
            cycle_rid,
            processed,
            errors,
            new_cursor,
        )
        return {"processed": processed, "errors": errors}
    finally:
        reset_request_id(rid_tok)


async def sync_active_services_batch(
    session: AsyncSession,
    settings: Settings,
    *,
    bot: Bot | None,
    batch_size: int = 50,
) -> int:
    """Backward-compatible: returns processed count."""
    r = await run_traffic_sync_cycle(session, settings, bot=bot, batch_size=batch_size)
    return int(r.get("processed", 0))


async def _sync_one_by_id(
    session: AsyncSession, settings: Settings, us_id: int, bot: Bot | None
) -> None:
    us = await session.get(UserService, us_id)
    if us is None:
        return
    rid = new_request_id()
    rid_ctx = set_request_id(rid)
    try:
        await _sync_one_body(session, settings, us, bot, rid)
    finally:
        reset_request_id(rid_ctx)


async def _sync_one_body(
    session: AsyncSession,
    settings: Settings,
    us: UserService,
    bot: Bot | None,
    rid: str,
) -> None:
    ctx = PanelLogContext(request_id=rid, service_code=us.public_service_code, user_id=str(us.user_id))
    now = datetime.now(tz=UTC)

    us.last_traffic_sync_at = now

    if us.expire_at and us.expire_at <= now and us.status in (
        UserServiceStatus.ACTIVE,
        UserServiceStatus.MIGRATING,
    ):
        us.status = UserServiceStatus.EXPIRED
        us.subscription_enabled = False
        us.last_traffic_sync_ok = True
        us.last_traffic_sync_error = None
        us.traffic_sync_fail_streak = 0
        ok_d, err_d = await _disable_active_panel(session, settings, us, ctx, bot)
        if not ok_d:
            us.last_traffic_sync_ok = False
            us.last_traffic_sync_error = err_d
            us.traffic_sync_fail_streak = min(999, (us.traffic_sync_fail_streak or 0) + 1)
            await _maybe_alert_owner(
                bot,
                settings,
                f"⚠️ غیرفعال‌سازی پس از انقضا ناموفق\nrid={rid}\nسرویس={us.public_service_code}\n{err_d}",
            )
        if bot:
            u = await session.get(User, us.user_id)
            if u:
                try:
                    await bot.send_message(
                        int(u.telegram_id),
                        "⏳ سرویس شما منقضی شد.\n"
                        f"کد سرویس: {us.public_service_code}\n"
                        "برای ادامه، سرویس جدید تهیه کنید.",
                    )
                except Exception:
                    log.exception("notify user expire failed")
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
            "traffic_sync: multiple active panel_accounts for user_service_id=%s — disabling extras",
            us.id,
        )
        active_list.sort(key=lambda x: x.id)
        for extra in active_list[:-1]:
            extra.is_active = False
            extra.status = PanelAccountStatus.DISABLED
            extra.disabled_at = now
            extra.final_used_bytes = consumed_from_account(extra)
        active_list = active_list[-1:]
    if not active_list:
        us.last_traffic_sync_ok = True
        us.last_traffic_sync_error = None
        us.traffic_sync_fail_streak = 0
        await session.flush()
        return

    pa = active_list[0]
    panel = await session.get(Panel, pa.panel_id)
    if panel is None:
        us.last_traffic_sync_ok = False
        us.last_traffic_sync_error = "panel missing"
        us.traffic_sync_fail_streak = min(999, (us.traffic_sync_fail_streak or 0) + 1)
        await session.flush()
        return

    srv = await session.get(Server, pa.server_id)
    inbound_id = srv.inbound_id if srv else None

    provider = get_provider(panel, settings)
    usage = await provider.get_account_usage(
        panel=panel,
        panel_username=pa.username,
        inbound_id=inbound_id,
        ctx=ctx,
    )

    if not usage.ok:
        msg = usage.message or "usage failed"
        log.warning("traffic_sync usage fail us=%s rid=%s msg=%s", us.id, rid, msg)
        pa.last_sync_ok = False
        pa.last_sync_error = msg[:2000]
        pa.sync_fail_streak = int(pa.sync_fail_streak or 0) + 1
        us.last_traffic_sync_ok = False
        us.last_traffic_sync_error = msg[:2000]
        us.traffic_sync_fail_streak = int(us.traffic_sync_fail_streak or 0) + 1
        panel.last_traffic_sync_at = now
        panel.last_traffic_sync_ok = False
        panel.last_traffic_sync_error = msg[:2000]
        panel.traffic_sync_fail_streak = int(panel.traffic_sync_fail_streak or 0) + 1
        await _streak_owner_alert(bot, settings, "سینک مصرف از پنل", panel, pa, us, msg)
        await session.flush()
        return

    pa.last_sync_ok = True
    pa.last_sync_error = None
    pa.sync_fail_streak = 0
    us.last_traffic_sync_ok = True
    us.last_traffic_sync_error = None
    us.traffic_sync_fail_streak = 0
    panel.last_traffic_sync_at = now
    panel.last_traffic_sync_ok = True
    panel.last_traffic_sync_error = None
    panel.traffic_sync_fail_streak = 0

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
        uq = await provider.update_quota(
            panel=panel,
            panel_username=pa.username,
            inbound_id=inbound_id,
            client_id=pa.panel_account_id,
            quota_bytes=max(0, us.remaining_traffic_bytes),
            ctx=ctx,
        )
        if not uq.ok:
            log.warning("traffic_sync update_quota fail us=%s %s", us.id, uq.message)
            await _maybe_alert_owner(
                bot,
                settings,
                f"⚠️ اعمال سهمیه روی پنل ناموفق\nrid={rid}\nکد سرویس={us.public_service_code}\n{uq.message}",
            )

    if us.remaining_traffic_bytes <= 0:
        us.status = UserServiceStatus.LIMITED
        us.subscription_enabled = False
        pa.is_active = False
        pa.status = PanelAccountStatus.DISABLED
        pa.final_used_bytes = consumed_from_account(pa)
        pa.disabled_at = now
        dis = await provider.disable_account(
            panel=panel,
            panel_username=pa.username,
            inbound_id=inbound_id,
            client_id=pa.panel_account_id,
            ctx=ctx,
        )
        if not dis.ok:
            await _maybe_alert_owner(
                bot,
                settings,
                f"⚠️ غیرفعال‌سازی اکانت پس از اتمام حجم ناموفق\nrid={rid}\nکد سرویس={us.public_service_code}\n{dis.message}",
            )
            us.status = UserServiceStatus.ERROR
            us.last_traffic_sync_error = dis.message
        elif bot:
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


async def _streak_owner_alert(
    bot: Bot | None,
    settings: Settings,
    kind: str,
    panel: Panel,
    pa: PanelAccount,
    us: UserService,
    detail: str,
) -> None:
    if not bot:
        return
    if int(pa.sync_fail_streak or 0) >= FAIL_ALERT_STREAK:
        await notify_owner_text(
            bot,
            settings.owner_telegram_id,
            f"🚨 {kind}: {FAIL_ALERT_STREAK} خطای پی‌درپی\n"
            f"پنل #{panel.id} ({panel.name})\n"
            f"اکانت پنل pa_id={pa.id} user={pa.username}\n"
            f"سرویس {us.public_service_code}\n"
            f"جزئیات: {detail[:500]}",
        )
    elif int(panel.traffic_sync_fail_streak or 0) >= FAIL_ALERT_STREAK:
        await notify_owner_text(
            bot,
            settings.owner_telegram_id,
            f"🚨 {kind}: پنل #{panel.id} ({panel.name}) — {FAIL_ALERT_STREAK} خطای پی‌درپی\n{detail[:500]}",
        )


async def _maybe_alert_owner(bot: Bot | None, settings: Settings, text: str) -> None:
    if bot:
        await notify_owner_text(bot, settings.owner_telegram_id, text)


async def _disable_active_panel(
    session: AsyncSession,
    settings: Settings,
    us: UserService,
    ctx: PanelLogContext,
    bot: Bot | None,
) -> tuple[bool, str | None]:
    ok_all = True
    last_err: str | None = None
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
        dis = await provider.disable_account(
            panel=panel,
            panel_username=pa.username,
            inbound_id=inbound_id,
            client_id=pa.panel_account_id,
            ctx=ctx,
        )
        if not dis.ok:
            ok_all = False
            last_err = dis.message
            await _maybe_alert_owner(
                bot,
                settings,
                f"⚠️ غیرفعال‌سازی اکانت ناموفق\nrid={ctx.request_id}\n{dis.message}",
            )
        pa.is_active = False
        pa.status = PanelAccountStatus.DISABLED
        pa.disabled_at = datetime.now(tz=UTC)
        pa.final_used_bytes = consumed_from_account(pa)
    return ok_all, last_err
