import uuid

import pytest
from sqlalchemy import text

from bot_app.db.models import Admin, ManualLink, ManualPlan, ManualServer, User
from bot_app.services.manual_links import bulk_import_links, deliver_one_link, parse_import_lines


@pytest.mark.asyncio
async def test_parse_and_import(session):
    session.add(Admin(telegram_id=999001, role="owner", is_active=True))
    await session.flush()
    aid = (await session.execute(text("SELECT id FROM admins WHERE telegram_id=999001"))).scalar_one()
    session.add(ManualServer(name="S1", is_active=True))
    await session.flush()
    sid = (await session.execute(text("SELECT id FROM manual_servers LIMIT 1"))).scalar_one()
    session.add(ManualPlan(manual_server_id=sid, display_name="P1", volume_label="30GB", is_active=True))
    await session.flush()
    pid = (await session.execute(text("SELECT id FROM manual_plans LIMIT 1"))).scalar_one()

    lines = ["vless://a@b:1", "vless://a@b:1", "short", "vless://c@d:2"]
    stats = await bulk_import_links(
        session,
        lines=lines,
        manual_server_id=sid,
        manual_plan_id=pid,
        admin_db_id=aid,
        max_links=1000,
        max_link_length=2000,
        request_id="t1",
    )
    assert stats["added"] == 2
    assert stats["duplicate_in_file"] == 1
    assert stats["invalid"] == 1
    await session.commit()

    stats2 = await bulk_import_links(
        session,
        lines=["vless://a@b:1"],
        manual_server_id=sid,
        manual_plan_id=pid,
        admin_db_id=aid,
        max_links=1000,
        max_link_length=2000,
        request_id="t2",
    )
    assert stats2["duplicate_in_db"] == 1


@pytest.mark.asyncio
async def test_delivery_no_reuse(session):
    session.add(Admin(telegram_id=999002, role="owner", is_active=True))
    await session.flush()
    aid = (await session.execute(text("SELECT id FROM admins WHERE telegram_id=999002"))).scalar_one()
    session.add(ManualServer(name="S2", is_active=True))
    await session.flush()
    sid = (await session.execute(text("SELECT id FROM manual_servers ORDER BY id DESC LIMIT 1"))).scalar_one()
    session.add(ManualPlan(manual_server_id=sid, display_name="P2", volume_label="10GB", is_active=True))
    await session.flush()
    pid = (await session.execute(text("SELECT id FROM manual_plans ORDER BY id DESC LIMIT 1"))).scalar_one()
    session.add(
        ManualLink(
            manual_server_id=sid,
            manual_plan_id=pid,
            link_text="vless://unique@test",
            status="unused",
            imported_by_admin_id=aid,
        )
    )
    await session.flush()
    ok, _, data = await deliver_one_link(
        session,
        manual_server_id=sid,
        manual_plan_id=pid,
        admin_db_id=aid,
        user_telegram_id=None,
        customer_info=None,
        request_id="d1",
    )
    assert ok and data
    ok2, key, _ = await deliver_one_link(
        session,
        manual_server_id=sid,
        manual_plan_id=pid,
        admin_db_id=aid,
        user_telegram_id=None,
        customer_info=None,
        request_id="d2",
    )
    assert not ok2 and key == "no_stock"
