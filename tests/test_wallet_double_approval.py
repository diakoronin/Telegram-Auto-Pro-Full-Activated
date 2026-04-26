import pytest
from sqlalchemy import select

from bot_app.db.models import Admin, PaymentCard, PaymentRequest, User
from bot_app.services.wallet import try_approve_payment


@pytest.mark.asyncio
async def test_double_approval_impossible(session_factory):
    async with session_factory() as session:
        session.add(Admin(telegram_id=9001, role="owner", is_active=True))
        session.add(User(telegram_id=9002, wallet_balance=0))
        await session.flush()
        aid = (await session.execute(select(Admin.id).where(Admin.telegram_id == 9001))).scalar_one()
        uid = (await session.execute(select(User.id).where(User.telegram_id == 9002))).scalar_one()
        session.add(
            PaymentCard(card_number="6037701573119390", card_holder_name="T", bank_name="B", is_active=True)
        )
        await session.flush()
        cid = (await session.execute(select(PaymentCard.id))).scalar_one()
        session.add(
            PaymentRequest(
                user_id=uid,
                amount=50_000,
                card_id=cid,
                status="pending",
            )
        )
        await session.commit()

    async with session_factory() as s1:
        async with s1.begin():
            st1, _ = await try_approve_payment(s1, payment_request_id=1, admin_db_id=aid, request_id="w1")
            assert st1 == "approved"

    async with session_factory() as s2:
        async with s2.begin():
            st2, _ = await try_approve_payment(s2, payment_request_id=1, admin_db_id=aid, request_id="w2")
            assert st2 == "already"

    async with session_factory() as s3:
        u = (await s3.execute(select(User).where(User.id == uid))).scalar_one()
        assert int(u.wallet_balance) == 50_000
