"""Payment card helpers for invoices and display."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PaymentCard


async def pick_public_card_for_invoice(session: AsyncSession) -> PaymentCard | None:
    r = await session.execute(
        select(PaymentCard)
        .where(PaymentCard.is_active.is_(True), PaymentCard.is_public.is_(True))
        .order_by(PaymentCard.id.asc())
        .limit(1)
    )
    return r.scalar_one_or_none()


def card_display_number(card: PaymentCard) -> str:
    if card.card_number_full:
        return card.card_number_full
    return card.card_number_masked
