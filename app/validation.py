from __future__ import annotations

import re
from dataclasses import dataclass

from app import texts_fa as T

MAX_SERVER_NAME = 120
MAX_PLAN_NAME = 120
MAX_LINK_TEXT = 4096
MAX_CUSTOMER_INFO = 500
MAX_CARD_HOLDER = 120
MAX_BANK_NAME = 120


@dataclass
class ValidationError(Exception):
    message_fa: str


def validate_server_name(name: str) -> str:
    s = name.strip()
    if not s or len(s) > MAX_SERVER_NAME:
        raise ValidationError(T.VALIDATION_SERVER_NAME)
    return s


def validate_plan_name(name: str) -> str:
    s = name.strip()
    if not s or len(s) > MAX_PLAN_NAME:
        raise ValidationError(T.VALIDATION_PLAN_NAME)
    return s


def validate_plan_price(raw: str) -> int:
    try:
        v = int(str(raw).strip().replace(",", ""))
    except ValueError as e:
        raise ValidationError(T.VALIDATION_PLAN_PRICE) from e
    if v <= 0:
        raise ValidationError(T.VALIDATION_PLAN_PRICE)
    return v


def validate_charge_amount(raw: str, min_amt: int, max_amt: int) -> int:
    try:
        v = int(str(raw).strip().replace(",", ""))
    except ValueError as e:
        raise ValidationError(T.INVALID_AMOUNT) from e
    if v < min_amt or v > max_amt:
        raise ValidationError(T.INVALID_AMOUNT)
    return v


_CARD_RE = re.compile(r"^\d{16}$")


def validate_card_number(raw: str) -> str:
    digits = re.sub(r"\D", "", raw.strip())
    if not _CARD_RE.match(digits):
        raise ValidationError(T.VALIDATION_CARD)
    return digits


def validate_card_holder(name: str) -> str:
    s = name.strip()
    if not s or len(s) > MAX_CARD_HOLDER:
        raise ValidationError(T.VALIDATION_CARD_HOLDER)
    return s


def validate_bank_name(name: str) -> str:
    s = name.strip()
    if not s or len(s) > MAX_BANK_NAME:
        raise ValidationError(T.VALIDATION_BANK)
    return s


def validate_link_text(text: str) -> str:
    s = text.strip()
    if not s or len(s) > MAX_LINK_TEXT:
        raise ValidationError(T.VALIDATION_LINK)
    return s


def validate_customer_info(text: str) -> str:
    s = text.strip()
    if len(s) > MAX_CUSTOMER_INFO:
        raise ValidationError(T.VALIDATION_CUSTOMER)
    return s


def validate_reason(text: str) -> str:
    s = text.strip()
    if not s:
        raise ValidationError(T.VALIDATION_REASON)
    if len(s) > 2000:
        raise ValidationError(T.VALIDATION_REASON)
    return s


def is_allowed_receipt_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    ct = content_type.lower()
    if ct == "image/jpeg" or ct == "image/png" or ct == "image/webp":
        return True
    return ct.startswith("image/")
