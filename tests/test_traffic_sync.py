from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from bot_app.db.models import Panel, PanelAccount, Plan, Server, User, UserService
from bot_app.providers.base import AccountUsage, ProviderResult
from bot_app.security.crypto import encrypt_secret
from bot_app.services.traffic_sync import sync_one_service


@pytest.mark.asyncio
async def test_sync_sets_limited_at_quota_end(session):
    from bot_app.config import get_settings

    key = get_settings().panel_credential_encryption_key
    session.add(User(telegram_id=777, wallet_balance=0))
    await session.flush()
    user = (await session.execute(select(User).where(User.telegram_id == 777))).scalar_one()
    session.add(
        Panel(
            name="P",
            type="marzban",
            base_url="https://p.example",
            username="a",
            password_encrypted=encrypt_secret(key, "pw"),
            verify_ssl=True,
        )
    )
    await session.flush()
    p = (await session.execute(select(Panel))).scalar_one()
    session.add(
        Server(
            name="S",
            location_label="L",
            panel_id=p.id,
            panel_type="marzban",
            is_active=True,
            is_visible_to_users=True,
        )
    )
    await session.flush()
    srv = (await session.execute(select(Server))).scalar_one()
    session.add(
        Plan(
            server_id=srv.id,
            display_name="1GB",
            volume_gb=1,
            total_quota_bytes=1024**3,
            duration_days=30,
            price=1,
            is_active=True,
            is_visible_to_users=True,
        )
    )
    await session.flush()
    plan = (await session.execute(select(Plan))).scalar_one()
    exp = datetime.now(timezone.utc) + timedelta(days=10)
    session.add(
        UserService(
            public_service_code="SVC777001",
            user_id=user.id,
            user_telegram_id=777,
            plan_id=plan.id,
            current_server_id=srv.id,
            custom_service_name="T",
            total_quota_bytes=1024**3,
            used_traffic_bytes=0,
            remaining_traffic_bytes=1024**3,
            expire_at=exp,
            status="active",
            subscription_token="tok-sync",
        )
    )
    await session.flush()
    us = (await session.execute(select(UserService))).scalar_one()
    session.add(
        PanelAccount(
            user_service_id=us.id,
            panel_id=p.id,
            server_id=srv.id,
            panel_type="marzban",
            username="u77",
            quota_bytes_assigned=1024**3,
            total_used_bytes=1024**3 + 500 * 1024**2,
            is_active=True,
            status="active",
        )
    )
    await session.commit()

    class _S:
        panel_credential_encryption_key = key
        traffic_safety_buffer_mb = 0

    fake = AsyncMock()
    over = int(1.5 * 1024**3)
    fake.get_account_usage = AsyncMock(
        return_value=ProviderResult(
            ok=True,
            data={"usage": AccountUsage(upload_bytes=0, download_bytes=over, total_used_bytes=over)},
        )
    )
    fake.disable_account = AsyncMock(return_value=ProviderResult(ok=True))

    with patch("bot_app.services.traffic_sync.get_provider_for_panel", return_value=fake):
        async with session.begin():
            uu = (await session.execute(select(UserService).where(UserService.id == us.id))).scalar_one()
            await sync_one_service(session, settings=_S(), us=uu, request_id="sync1")

    u2 = (await session.execute(select(UserService).where(UserService.id == us.id))).scalar_one()
    assert u2.status == "limited"
    fake.disable_account.assert_called()
