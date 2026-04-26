"""Mocked API purchase saga (no real panel)."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from bot_app.db.models import Panel, Plan, Server, User
from bot_app.providers.base import ProviderResult
from bot_app.security.crypto import encrypt_secret
from bot_app.services.api_purchase import execute_api_purchase_saga


@pytest.mark.asyncio
async def test_purchase_saga_wallet_unchanged_on_panel_fail(session, session_factory):
    from bot_app.config import get_settings

    key = get_settings().panel_credential_encryption_key
    session.add(User(telegram_id=222, wallet_balance=100_000))
    await session.flush()
    u = (await session.execute(select(User).where(User.telegram_id == 222))).scalar_one()
    session.add(
        Panel(
            name="PB",
            type="marzban",
            base_url="https://x.example",
            username="admin",
            password_encrypted=encrypt_secret(key, "secret"),
            verify_ssl=True,
        )
    )
    await session.flush()
    p = (await session.execute(select(Panel))).scalar_one()
    session.add(
        Server(
            name="Loc",
            location_label="X",
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
            display_name="10GB",
            volume_gb=10,
            total_quota_bytes=10 * 1024**3,
            duration_days=30,
            price=50_000,
            is_active=True,
            is_visible_to_users=True,
        )
    )
    await session.flush()
    pl = (await session.execute(select(Plan))).scalar_one()
    await session.commit()

    class _Settings:
        panel_credential_encryption_key = key
        multi_backend_active = False
        public_base_url = "https://example.com"

    fake = AsyncMock()
    fake.create_account = AsyncMock(return_value=ProviderResult(ok=False, error_code=None))
    fake.get_config_links = AsyncMock()
    fake.disable_account = AsyncMock()
    fake.delete_account = AsyncMock()

    with patch("bot_app.services.api_purchase.get_provider_for_panel", return_value=fake):
        ok, key, _ = await execute_api_purchase_saga(
            session,
            settings=_Settings(),
            user=u,
            plan=pl,
            server=srv,
            panel=p,
            custom_service_name="My",
            price=50_000,
            request_id="rid1",
        )
    assert not ok
    await session.rollback()
    async with session_factory() as s2:
        u2 = (await s2.execute(select(User).where(User.telegram_id == 222))).scalar_one()
        assert int(u2.wallet_balance) == 100_000
