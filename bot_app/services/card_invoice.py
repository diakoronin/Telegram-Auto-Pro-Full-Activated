"""Payment card invoice formatting (full card for user)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from bot_app.utils.jalali_format import format_copyable_code


@dataclass
class CardInvoice:
    full_card_number: str
    card_holder_name: str
    bank_name: str


def is_full_card_number(card: str) -> bool:
    digits = re.sub(r"\D", "", card or "")
    return len(digits) in (16, 19) and digits.isdigit()


def build_user_invoice(
    *,
    card_number: str,
    card_holder_name: str,
    bank_name: str,
    amount: int,
    payment_request_id: int,
    expire_minutes: int,
    show_full: bool = True,
    debug_card_logging: bool = False,
) -> tuple[bool, str]:
    """
    Returns (ok, html_body_or_error_key).
    If card is masked or incomplete, ok=False (admin must repair).
    """
    if not show_full:
        return False, "disabled"
    if not is_full_card_number(card_number):
        return False, "incomplete_card"
    if "*" in card_number:
        return False, "masked_card"
    num = format_copyable_code(re.sub(r"\D", "", card_number))
    body = (
        "☑️ پیش‌فاکتور شما آماده شد\n\n"
        f"💳 شماره کارت:\n{num}\n\n"
        f"🙎🏻‍♂️ {card_holder_name}\n"
        f"🏦 {bank_name}\n\n"
        f"💵 مبلغ: {amount:,}\n"
        f"🧾 فاکتور: #{payment_request_id}\n"
        f"⏳ مهلت پرداخت: {expire_minutes} دقیقه\n\n"
        "📌 بعد از پرداخت، «ارسال رسید» بزنید."
    )
    if debug_card_logging:
        body += "\n<!-- debug card -->"
    return True, body
