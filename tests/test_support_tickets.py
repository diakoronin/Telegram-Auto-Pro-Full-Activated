import pytest
from sqlalchemy import select

from bot_app.db.models import SupportTicket, User


@pytest.mark.asyncio
async def test_ticket_reply_flow(session):
    session.add(User(telegram_id=555, wallet_balance=0))
    await session.flush()
    u = (await session.execute(select(User).where(User.telegram_id == 555))).scalar_one()
    session.add(SupportTicket(user_id=u.id, status="open", message="سلام"))
    await session.flush()
    t = (await session.execute(select(SupportTicket))).scalar_one()
    t.admin_reply = "پاسخ ادمین"
    t.status = "answered"
    await session.commit()
    t2 = (await session.execute(select(SupportTicket).where(SupportTicket.id == t.id))).scalar_one()
    assert t2.admin_reply == "پاسخ ادمین"
    assert t2.status == "answered"
