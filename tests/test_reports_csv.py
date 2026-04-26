import csv
from io import StringIO

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_purchase_csv_stream(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from bot_app.migrations.runner import run_migrations

    await run_migrations(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO users (telegram_id, wallet_balance, is_blocked, card_payment_enabled) "
                "VALUES (444, 0, 0, 1)"
            )
        )
        uid = (await s.execute(text("SELECT id FROM users WHERE telegram_id=444"))).scalar_one()
        await s.execute(
            text(
                "INSERT INTO purchases (user_id, user_telegram_id, purchase_type, price, status) "
                "VALUES (:uid, 444, 'api', 1000, 'completed')"
            ),
            {"uid": uid},
        )
        await s.commit()

    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "purchase_id", "purchase_type", "price", "status"])
    async with factory() as s:
        cur = await s.execute(
            text(
                "SELECT created_at, id, purchase_type, price, status FROM purchases ORDER BY id ASC LIMIT 100"
            )
        )
        for row in cur.mappings().all():
            w.writerow([str(row["created_at"]), row["id"], row["purchase_type"], row["price"], row["status"]])
    out = buf.getvalue()
    assert "api" in out
    assert "completed" in out
