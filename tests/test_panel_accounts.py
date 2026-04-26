import pytest
from sqlalchemy import select

from bot_app.db.models import Panel, PanelAccount, Plan, Server, User, UserService
from bot_app.security.crypto import encrypt_secret
from bot_app.services.panel_accounts import count_active_accounts, has_duplicate_active


@pytest.mark.asyncio
async def test_duplicate_active_detection(session):
    from bot_app.config import get_settings
    from datetime import datetime, timedelta, timezone

    key = get_settings().panel_credential_encryption_key
    session.add(User(telegram_id=888, wallet_balance=0))
    await session.flush()
    user = (await session.execute(select(User).where(User.telegram_id == 888))).scalar_one()
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
    exp = datetime.now(timezone.utc) + timedelta(days=1)
    session.add(
        UserService(
            public_service_code="SVC888001",
            user_id=user.id,
            user_telegram_id=888,
            plan_id=plan.id,
            current_server_id=srv.id,
            custom_service_name="X",
            total_quota_bytes=1024**3,
            used_traffic_bytes=0,
            remaining_traffic_bytes=1024**3,
            expire_at=exp,
            status="active",
            subscription_token="t-dup",
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
            username="a1",
            quota_bytes_assigned=1024**3,
            is_active=True,
            status="active",
        )
    )
    session.add(
        PanelAccount(
            user_service_id=us.id,
            panel_id=p.id,
            server_id=srv.id,
            panel_type="marzban",
            username="a2",
            quota_bytes_assigned=1024**3,
            is_active=True,
            status="active",
        )
    )
    await session.flush()
    assert await count_active_accounts(session, us.id) == 2
    assert await has_duplicate_active(session, us.id)
