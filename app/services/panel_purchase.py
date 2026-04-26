"""
API-based purchase (saga): wallet deduction only after successful panel account + config.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    Panel,
    PanelAccount,
    PanelAccountStatus,
    Plan,
    Purchase,
    PurchaseStatus,
    Server,
    User,
    UserService,
    UserServiceStatus,
    WalletTransaction,
    WalletTransactionType,
)
from app.panel.factory import get_provider
from app.panel.types import PanelLogContext
from app.services.audit import write_audit
from app.services.user_service_codes import allocate_public_service_code, backend_panel_username
from app.services.user_service_tokens import generate_subscription_token

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


async def purchase_service_via_panel(
    session: AsyncSession,
    *,
    settings: Settings,
    user: User,
    server: Server,
    plan: Plan,
    custom_service_name: str,
    request_id: str,
) -> tuple[bool, str | None, int | None, int | None]:
    """
    Returns (ok, error_fa, purchase_id, user_service_id).
    """
    if user.is_blocked:
        return False, "حساب مسدود است.", None, None
    if server.panel_id is None:
        return False, "سرور به پنل متصل نیست. لطفاً با پشتیبانی تماس بگیرید.", None, None
    panel = await session.get(Panel, server.panel_id)
    if panel is None or not panel.is_active:
        return False, "پنل فعال نیست.", None, None
    if not plan.is_active or not server.is_active:
        return False, "پلن یا سرور غیرفعال است.", None, None

    await session.execute(select(Plan).where(Plan.id == plan.id).with_for_update())
    u_row = await session.execute(select(User).where(User.id == user.id).with_for_update())
    locked_user = u_row.scalar_one()
    price = int(plan.price)
    if int(locked_user.wallet_balance) < price:
        return False, "موجودی کافی نیست.", None, None

    quota_bytes = max(1, int(plan.volume_gb)) * 1024 * 1024 * 1024
    duration_days = max(1, int(plan.duration_days or 30))
    expire_at = datetime.now(tz=UTC) + timedelta(days=duration_days)

    purchase = Purchase(
        user_id=locked_user.id,
        server_id=server.id,
        plan_id=plan.id,
        link_id=None,
        user_service_id=None,
        custom_service_name=custom_service_name.strip()[:120],
        amount_paid=price,
        status=PurchaseStatus.PENDING,
    )
    session.add(purchase)
    await session.flush()

    public_code = await allocate_public_service_code(session)
    sub_token = generate_subscription_token()
    backend_user = backend_panel_username(
        telegram_id=int(locked_user.telegram_id),
        public_service_code=public_code,
        volume_gb=int(plan.volume_gb or 1),
    )

    us = UserService(
        public_service_code=public_code,
        user_id=locked_user.id,
        user_telegram_id=int(locked_user.telegram_id),
        purchase_id=purchase.id,
        plan_id=plan.id,
        custom_service_name=custom_service_name.strip()[:120],
        total_quota_bytes=quota_bytes,
        used_traffic_bytes=0,
        remaining_traffic_bytes=quota_bytes,
        expire_at=expire_at,
        status=UserServiceStatus.ACTIVE,
        subscription_token=sub_token,
        subscription_enabled=True,
        current_server_id=server.id,
    )
    session.add(us)
    await session.flush()
    purchase.user_service_id = us.id
    await session.flush()

    ctx = PanelLogContext(request_id=request_id, service_code=public_code, user_id=str(locked_user.id))
    provider = get_provider(panel, settings)

    pa = PanelAccount(
        user_service_id=us.id,
        panel_id=panel.id,
        server_id=server.id,
        panel_type=panel.type.value,
        panel_account_id=None,
        username=backend_user,
        config_links_json=[],
        raw_subscription_url=None,
        quota_bytes_assigned=quota_bytes,
        usage_baseline_bytes=0,
        upload_bytes=0,
        download_bytes=0,
        total_used_bytes=0,
        is_active=True,
        status=PanelAccountStatus.ACTIVE,
        activated_at=datetime.now(tz=UTC),
    )
    session.add(pa)
    await session.flush()

    try:
        cr = await provider.create_account(
            user_service=us,
            panel=panel,
            server=server,
            plan=plan,
            quota_bytes=quota_bytes,
            expire_at=expire_at,
            backend_username=backend_user,
            ctx=ctx,
        )
    except Exception as e:
        log.exception("panel create_account exception rid=%s", request_id)
        await _fail_cleanup(session, purchase, us, pa, panel, provider, backend_user, server.inbound_id, None)
        return False, f"خطای پنل: {e}", purchase.id, us.id

    if not cr.ok:
        await _fail_cleanup(session, purchase, us, pa, panel, provider, backend_user, server.inbound_id, cr.panel_account_id)
        return False, cr.message or "ایجاد اکانت در پنل ناموفق بود.", purchase.id, us.id

    links = [x for x in (cr.config_links or []) if str(x).strip()]
    if not links:
        links2, _raw = await provider.get_config_links(
            panel=panel,
            panel_username=cr.username or backend_user,
            inbound_id=server.inbound_id,
            client_id=cr.panel_account_id,
            ctx=ctx,
        )
        links = [x for x in links2 if str(x).strip()]

    if not links:
        await _fail_cleanup(session, purchase, us, pa, panel, provider, backend_user, server.inbound_id, cr.panel_account_id)
        return False, "هیچ کانفیگی از پنل دریافت نشد. خرید لغو شد.", purchase.id, us.id

    pa.panel_account_id = cr.panel_account_id or backend_user
    pa.config_links_json = links
    pa.raw_subscription_url = cr.raw_subscription_url

    usage = await provider.get_account_usage(
        panel=panel,
        panel_username=pa.username,
        inbound_id=server.inbound_id,
        ctx=ctx,
    )
    if usage.ok:
        pa.upload_bytes = usage.upload_bytes
        pa.download_bytes = usage.download_bytes
        pa.total_used_bytes = usage.total_used_bytes
        pa.usage_baseline_bytes = int(usage.total_used_bytes or 0)

    before = int(locked_user.wallet_balance)
    after = before - price
    locked_user.wallet_balance = after
    purchase.status = PurchaseStatus.COMPLETED
    session.add(
        WalletTransaction(
            user_id=locked_user.id,
            type=WalletTransactionType.PURCHASE,
            amount_delta=-price,
            balance_before=before,
            balance_after=after,
            reason="panel_purchase",
            related_purchase_id=purchase.id,
        )
    )
    await session.flush()

    await write_audit(
        session,
        actor_telegram_id=locked_user.telegram_id,
        actor_role="user",
        action="panel_purchase_completed",
        target_type="user_service",
        target_id=str(us.id),
        metadata={"purchase_id": purchase.id, "request_id": request_id, "public_service_code": public_code},
    )
    return True, None, purchase.id, us.id


async def _fail_cleanup(
    session: AsyncSession,
    purchase: Purchase,
    us: UserService,
    pa: PanelAccount,
    panel: Panel,
    provider,
    backend_username: str,
    inbound_id: int | None,
    client_id: str | None,
) -> None:
    purchase.status = PurchaseStatus.FAILED
    us.status = UserServiceStatus.ERROR
    pa.is_active = False
    pa.status = PanelAccountStatus.FAILED
    ctx = PanelLogContext(request_id="cleanup", service_code=us.public_service_code, user_id=str(us.user_id))
    try:
        await provider.delete_account(
            panel=panel,
            panel_username=backend_username,
            inbound_id=inbound_id,
            client_id=client_id or backend_username,
            ctx=ctx,
        )
    except Exception:
        log.exception("cleanup delete_account failed")
    await session.flush()
