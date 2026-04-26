"""Append Jalali date/time footer to user-facing bot messages."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import jdatetime

from app.config import Settings

# Avoid "_" (Markdown) in footers when messages use parse_mode.
_FOOTER_LINE = "────────────────"


def _now_local(settings: Settings) -> datetime:
    tz = ZoneInfo(settings.timezone)
    return datetime.now(tz=tz)


def format_jalali_footer(settings: Settings) -> str:
    """Single footer line: 🕒 YYYY/MM/DD - HH:mm (Jalali calendar)."""
    now = _now_local(settings)
    jd = jdatetime.datetime.fromgregorian(datetime=now)
    date_s = jd.strftime("%Y/%m/%d")
    time_s = now.strftime("%H:%M")
    return f"{_FOOTER_LINE}\n🕒 {date_s} - {time_s}"


def format_message(settings: Settings, text: str) -> str:
    body = (text or "").rstrip()
    if not settings.footer_enabled:
        return body
    foot = format_jalali_footer(settings)
    if not body:
        return foot
    return f"{body}\n\n{foot}"


def format_money_toman(amount: int) -> str:
    """e.g. 200,000 for display in Persian UI."""
    return f"{int(amount):,}"
