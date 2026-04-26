"""Public invoice card picker must exclude incomplete full numbers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models import PaymentCard
from app.services.cards import pick_public_card_for_invoice


@pytest.mark.asyncio
async def test_pick_public_card_skips_incomplete_full() -> None:
    bad = PaymentCard(
        id=1,
        card_number_masked="1234****5678",
        card_number_full=None,
        card_holder="A",
        bank_name="B",
        is_active=True,
        is_public=True,
    )
    good = PaymentCard(
        id=2,
        card_number_masked="6012****3456",
        card_number_full="6012345678901234",
        card_holder="A",
        bank_name="B",
        is_active=True,
        is_public=True,
    )
    session = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=good)
    session.execute = AsyncMock(return_value=res)
    out = await pick_public_card_for_invoice(session)
    assert out is good
