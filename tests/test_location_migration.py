from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from bot_app.db.models import Panel, PanelAccount, Plan, Server, User, UserService
from bot_app.providers.base import AccountUsage, ProviderResult
from bot_app.security.crypto import encrypt_secret
from bot_app.services.location_migration import migrate_service_to_server


@pytest.mark.asyncio
async def test_migration_uses_remaining_quota(session, session_factory):
    from bot_app.config import get_settings

    key = get_settings().panel_credential_encryption_key
    session.add(User(telegram_id=333, wallet_balance=0))
    await session.flush()
    user = (await session.execute(select(User).where(User.telegram_id == 333))).scalar_one()

    for i in range(2):
        session.add(
            Panel(
                name=f"P{i}",
                type="marzban",
                base_url=f"https://p{i}.example",
                username="a",
                password_encrypted=encrypt_secret(key, "pw"),
                verify_ssl=True,
            )
        )
    await session.flush()
    panels = (await session.execute(select(Panel))).scalars().all()
    p_old, p_new = panels[0], panels[1]

    session.add(
        Server(
            name="Old",
            location_label="A",
            panel_id=p_old.id,
            panel_type="marzban",
            is_active=True,
            is_visible_to_users=True,
        )
    )
    session.add(
        Server(
            name="New",
            location_label="B",
            panel_id=p_new.id,
            panel_type="marzban",
            is_active=True,
            is_visible_to_users=True,
        )
    )
    await session.flush()
    s_old = (await session.execute(select(Server).where(Server.name == "Old"))).scalar_one()
    s_new = (await session.execute(select(Server).where(Server.name == "New"))).scalar_one()

    session.add(
        Plan(
            server_id=s_old.id,
            display_name="30GB",
            volume_gb=30,
            total_quota_bytes=30 * 1024**3,
            duration_days=30,
            price=1,
            is_active=True,
            is_visible_to_users=True,
        )
    )
    await session.flush()
    plan = (await session.execute(select(Plan))).scalar_one()

    exp = datetime.now(timezone.utc) + timedelta(days=5)
    session.add(
        UserService(
            public_service_code="SVC900001",
            user_id=user.id,
            user_telegram_id=333,
            plan_id=plan.id,
            current_server_id=s_old.id,
            custom_service_name="X",
            total_quota_bytes=30 * 1024**3,
            used_traffic_bytes=10 * 1024**3,
            remaining_traffic_bytes=20 * 1024**3,
            expire_at=exp,
            status="active",
            subscription_token="tok-mig-1",
        )
    )
    await session.flush()
    us = (await session.execute(select(UserService))).scalar_one()
    session.add(
        PanelAccount(
            user_service_id=us.id,
            panel_id=p_old.id,
            server_id=s_old.id,
            panel_type="marzban",
            username="tg333_SVC900001_30gb",
            quota_bytes_assigned=30 * 1024**3,
            usage_baseline_bytes=0,
            total_used_bytes=10 * 1024**3,
            is_active=True,
            status="active",
        )
    )
    await session.commit()

    class _S:
        panel_credential_encryption_key = key
        location_change_fee = 0

    fake = AsyncMock()
    fake.create_account = AsyncMock(return_value=ProviderResult(ok=True, data={"client_uuid": "c1"}))
    fake.get_config_links = AsyncMock(
        return_value=ProviderResult(ok=True, data={"links": ["vless://new"]})
    )
    fake.disable_account = AsyncMock(return_value=ProviderResult(ok=True))
    fake.delete_account = AsyncMock(return_value=ProviderResult(ok=True))

    with patch("bot_app.services.location_migration.get_provider_for_panel", return_value=fake):
        async with session_factory() as s:
            uu = (await s.execute(select(UserService).where(UserService.id == us.id))).scalar_one()
            uusr = (await s.execute(select(User).where(User.id == user.id))).scalar_one()
            ok, msg = await migrate_service_to_server(
                s,
                settings=_S(),
                us=uu,
                target_server=s_new,
                target_panel=p_new,
                user=uusr,
                request_id="m1",
            )
            assert ok and msg == "ok"
            await s.commit()

    async with session_factory() as s2:
        uu2 = (await s2.execute(select(UserService).where(UserService.id == us.id))).scalar_one()
        assert uu2.current_server_id == s_new.id
        args = fake.create_account.call_args[0]
        assert args[1] == 20 * 1024**3  # remaining quota bytes
