from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from bot_app.db.models import Panel, Plan, Server, User
from bot_app.providers.base import ProviderResult
from bot_app.security.crypto import encrypt_secret
from bot_app.services.api_purchase import execute_api_purchase_saga


@pytest.mark.asyncio
async def test_3xui_wallet_unchanged_when_no_config(session, session_factory):
    from bot_app.config import get_settings

    key = get_settings().panel_credential_encryption_key
    session.add(User(telegram_id=666, wallet_balance=200_000))
    await session.flush()
    u = (await session.execute(select(User).where(User.telegram_id == 666))).scalar_one()
    session.add(
        Panel(
            name="XUI",
            type="sanaei_3xui",
            base_url="https://ui.example",
            web_base_path="",
            username="admin",
            password_encrypted=encrypt_secret(key, "pw"),
            verify_ssl=True,
            inbound_id=1,
        )
    )
    await session.flush()
    p = (await session.execute(select(Panel))).scalar_one()
    session.add(
        Server(
            name="S",
            location_label="L",
            panel_id=p.id,
            panel_type="sanaei_3xui",
            inbound_id=1,
            is_active=True,
            is_visible_to_users=True,
        )
    )
    await session.flush()
    srv = (await session.execute(select(Server))).scalar_one()
    session.add(
        Plan(
            server_id=srv.id,
            display_name="5GB",
            volume_gb=5,
            total_quota_bytes=5 * 1024**3,
            duration_days=7,
            price=10_000,
            is_active=True,
            is_visible_to_users=True,
        )
    )
    await session.flush()
    pl = (await session.execute(select(Plan))).scalar_one()
    await session.commit()

    class _S:
        panel_credential_encryption_key = key
        multi_backend_active = False
        public_base_url = "https://example.com"

    fake = AsyncMock()
    fake.create_account = AsyncMock(return_value=ProviderResult(ok=True, data={"client_uuid": "uuid"}))
    fake.get_config_links = AsyncMock(return_value=ProviderResult(ok=True, data={"links": []}))
    fake.disable_account = AsyncMock()
    fake.delete_account = AsyncMock()

    with patch("bot_app.services.api_purchase.get_provider_for_panel", return_value=fake):
        ok, _, _ = await execute_api_purchase_saga(
            session,
            settings=_S(),
            user=u,
            plan=pl,
            server=srv,
            panel=p,
            custom_service_name="N",
            price=10_000,
            request_id="x1",
        )
    assert not ok
    await session.rollback()
    async with session_factory() as s2:
        u2 = (await s2.execute(select(User).where(User.telegram_id == 666))).scalar_one()
        assert int(u2.wallet_balance) == 200_000
