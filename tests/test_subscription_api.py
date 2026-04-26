from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot_app.db.models import Panel, PanelAccount, Plan, Server, User, UserService
from bot_app.security.crypto import encrypt_secret
from bot_app.subscription_api.app import create_subscription_app


@pytest.mark.asyncio
async def test_sub_returns_config_and_blocks_blocked_user(engine):
    from bot_app.config import get_settings

    key = get_settings().panel_credential_encryption_key
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(User(telegram_id=111, wallet_balance=0, is_blocked=False))
        await s.flush()
        uid = (await s.execute(select(User.id))).scalar_one()
        s.add(
            Panel(
                name="P1",
                type="marzban",
                base_url="https://p.example",
                username="u",
                password_encrypted=encrypt_secret(key, "pw"),
                verify_ssl=True,
            )
        )
        await s.flush()
        pid = (await s.execute(select(Panel.id))).scalar_one()
        s.add(
            Server(
                name="S1",
                location_label="DE",
                panel_id=pid,
                panel_type="marzban",
                is_active=True,
                is_visible_to_users=True,
            )
        )
        await s.flush()
        sid = (await s.execute(select(Server.id))).scalar_one()
        s.add(
            Plan(
                server_id=sid,
                display_name="30GB",
                volume_gb=30,
                total_quota_bytes=30 * 1024**3,
                duration_days=30,
                price=1000,
                is_active=True,
                is_visible_to_users=True,
            )
        )
        await s.flush()
        plid = (await s.execute(select(Plan.id))).scalar_one()
        exp = datetime.now(timezone.utc) + timedelta(days=10)
        s.add(
            UserService(
                public_service_code="SVC000001",
                user_id=uid,
                user_telegram_id=111,
                plan_id=plid,
                current_server_id=sid,
                custom_service_name="T",
                total_quota_bytes=30 * 1024**3,
                used_traffic_bytes=0,
                remaining_traffic_bytes=30 * 1024**3,
                expire_at=exp,
                status="active",
                subscription_token="secrettok123",
                subscription_enabled=True,
            )
        )
        await s.flush()
        usid = (await s.execute(select(UserService.id))).scalar_one()
        s.add(
            PanelAccount(
                user_service_id=usid,
                panel_id=pid,
                server_id=sid,
                panel_type="marzban",
                username="u1",
                config_links_json={"links": ["vless://a", "vmess://b"]},
                quota_bytes_assigned=30 * 1024**3,
                is_active=True,
                status="active",
            )
        )
        await s.commit()

    app = create_subscription_app(factory, sub_base64_enabled=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/sub/secrettok123")
        assert r.status_code == 200
        assert "vless://a" in r.text
        h = r.headers.get("subscription-userinfo", "")
        assert "upload=" in h.lower()

    async with factory() as s2:
        u = (await s2.execute(select(User).where(User.id == uid))).scalar_one()
        u.is_blocked = True
        await s2.commit()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r2 = await client.get("/sub/secrettok123")
        assert r2.status_code == 404
