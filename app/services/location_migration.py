"""Change server/location for a user_service: same subscription_token, new panel_account with remaining quota."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    Panel,
    PanelAccount,
    PanelAccountStatus,
    Plan,
    Server,
    User,
    UserService,
    UserServiceStatus,
)
from app.panel.factory import get_provider
from app.panel.types import PanelLogContext
from app.services.quota import recompute_user_service_traffic
from app.services.user_service_codes import backend_panel_username
from app.structured_log import new_request_id

log = logging.getLogger(__name__)


async def migrate_user_service_location(
    session: AsyncSession,
    *,
    settings: Settings,
    us: UserService,
    target_server: Server,
    request_id: str | None = None,
) -> tuple[bool, str | None]:
    rid = request_id or new_request_id()
    ctx = PanelLogContext(request_id=rid, service_code=us.public_service_code, user_id=str(us.user_id))

    if not settings.location_change_enabled:
        return False, "تغییر لوکیشن غیرفعال است."
    if not us.location_change_enabled:
        return False, "برای این سرویس تغییر لوکیشن غیرفعال است."
    if us.status not in (UserServiceStatus.ACTIVE,):
        return False, "سرویس در وضعیت مناسب نیست."
    if us.expire_at and us.expire_at <= datetime.now(tz=UTC):
        return False, "سرویس منقضی شده است."
    u = await session.get(User, us.user_id)
    if u and u.is_blocked:
        return False, "حساب مسدود است."
    if not target_server.is_active or not target_server.is_visible_to_users:
        return False, "سرور مقصد فعال نیست."
    if not target_server.supports_location_change:
        return False, "این سرور تغییر لوکیشن را پشتیبانی نمی‌کند."
    if target_server.id == us.current_server_id:
        return False, "همان لوکیشن فعلی است."

    if us.last_location_change_at:
        delta = datetime.now(tz=UTC) - us.last_location_change_at
        need = timedelta(hours=max(1, settings.location_change_cooldown_hours))
        if delta < need:
            return False, "زمان استراحت تغییر لوکیشن هنوز به پایان نرسیده است."
    month_key = datetime.now(tz=UTC).strftime("%Y-%m")
    if (us.location_change_month_key or "") != month_key:
        us.location_change_month_key = month_key
        us.location_change_month_count = 0
    if int(us.location_change_month_count or 0) >= max(
        1, settings.location_change_max_per_month
    ):
        return False, "حداکثر تغییر لوکیشن ماهانه انجام شده است."

    if target_server.panel_id is None:
        return False, "سرور مقصد به پنل متصل نیست."

    await session.execute(select(UserService).where(UserService.id == us.id).with_for_update())
    await recompute_user_service_traffic(session, us)
    remaining = max(0, int(us.remaining_traffic_bytes))

    old_pa_r = await session.execute(
        select(PanelAccount).where(
            PanelAccount.user_service_id == us.id,
            PanelAccount.is_active.is_(True),
        )
    )
    old_accounts = list(old_pa_r.scalars().all())
    if not old_accounts:
        return False, "اکانت پنل فعال یافت نشد."
    old_pa = max(old_accounts, key=lambda x: x.id)
    old_panel = await session.get(Panel, old_pa.panel_id)
    if old_panel is None:
        return False, "پنل قبلی یافت نشد."

    new_panel = await session.get(Panel, target_server.panel_id)
    if new_panel is None:
        return False, "پنل مقصد یافت نشد."

    plan = await session.get(Plan, us.plan_id)
    if plan is None:
        return False, "پلن یافت نشد."

    backend_user = backend_panel_username(
        telegram_id=int(us.user_telegram_id),
        public_service_code=us.public_service_code,
        volume_gb=max(1, int(remaining // (1024**3)) or 1),
    )[:32]

    new_pa = PanelAccount(
        user_service_id=us.id,
        panel_id=new_panel.id,
        server_id=target_server.id,
        panel_type=new_panel.type.value,
        panel_account_id=None,
        username=backend_user,
        config_links_json=[],
        raw_subscription_url=None,
        quota_bytes_assigned=remaining,
        usage_baseline_bytes=0,
        is_active=False,
        status=PanelAccountStatus.ACTIVE,
    )
    session.add(new_pa)
    await session.flush()

    new_provider = get_provider(new_panel, settings)
    cr = await new_provider.create_account(
        user_service=us,
        panel=new_panel,
        server=target_server,
        plan=plan,
        quota_bytes=remaining,
        expire_at=us.expire_at,
        backend_username=backend_user,
        ctx=ctx,
    )
    if not cr.ok or not (cr.config_links or []):
        await session.delete(new_pa)
        await session.flush()
        return False, cr.message or "ایجاد اکانت در سرور جدید ناموفق بود."

    links = list(cr.config_links or [])
    if not links:
        links2, _ = await new_provider.get_config_links(
            panel=new_panel,
            panel_username=cr.username or backend_user,
            inbound_id=target_server.inbound_id,
            client_id=cr.panel_account_id,
            ctx=ctx,
        )
        links = [x for x in links2 if str(x).strip()]
    if not links:
        await new_provider.delete_account(
            panel=new_panel,
            panel_username=backend_user,
            inbound_id=target_server.inbound_id,
            client_id=cr.panel_account_id,
            ctx=ctx,
        )
        await session.delete(new_pa)
        await session.flush()
        return False, "کانفیگ از سرور جدید دریافت نشد."

    new_pa.panel_account_id = cr.panel_account_id or backend_user
    new_pa.config_links_json = links
    new_pa.raw_subscription_url = cr.raw_subscription_url
    new_pa.is_active = True
    new_pa.activated_at = datetime.now(tz=UTC)

    old_provider = get_provider(old_panel, settings)
    now = datetime.now(tz=UTC)
    old_srv = await session.get(Server, old_pa.server_id)
    old_inbound = old_srv.inbound_id if old_srv else None
    await old_provider.disable_account(
        panel=old_panel,
        panel_username=old_pa.username,
        inbound_id=old_inbound,
        client_id=old_pa.panel_account_id,
        ctx=ctx,
    )
    old_pa.is_active = False
    old_pa.status = PanelAccountStatus.MIGRATED
    old_pa.disabled_at = now
    from app.services.quota import consumed_from_account

    old_pa.final_used_bytes = consumed_from_account(old_pa)

    us.current_server_id = target_server.id
    us.location_change_count = int(us.location_change_count or 0) + 1
    us.location_change_month_count = int(us.location_change_month_count or 0) + 1
    us.last_location_change_at = now
    us.status = UserServiceStatus.ACTIVE
    await recompute_user_service_traffic(session, us)
    await session.flush()
    return True, None
