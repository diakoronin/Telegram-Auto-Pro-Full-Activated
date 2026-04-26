"""Payment card helpers for invoices and display."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PaymentCard


async def pick_public_card_for_invoice(session: AsyncSession) -> PaymentCard | None:
    r = await session.execute(
        select(PaymentCard)
        .where(
            PaymentCard.is_active.is_(True),
            PaymentCard.is_public.is_(True),
            PaymentCard.card_number_full.is_not(None),
            func.length(func.trim(PaymentCard.card_number_full)) >= 16,
        )
        .order_by(PaymentCard.id.asc())
        .limit(1)
    )
    return r.scalar_one_or_none()


def card_display_number(card: PaymentCard) -> str:
    """Admin list: prefer full if present else masked."""
    if card.card_number_full:
        return card.card_number_full
    return card.card_number_masked


def invoice_card_number_for_user(card: PaymentCard, *, show_full: bool) -> str:
    """User invoice: full digits when configured and stored; else masked (admin must fix DB)."""
    full = (card.card_number_full or "").strip()
    if show_full and full and len(full) >= 10:
        return full
    return card.card_number_masked
