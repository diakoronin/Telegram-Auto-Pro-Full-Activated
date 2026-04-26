"""Append Jalali date/time footer to user-facing bot messages."""

from __future__ import annotations

import html
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


def format_money(amount: int) -> str:
    """Alias for format_money_toman."""
    return format_money_toman(amount)


def format_purchase_datetime(settings: Settings, dt: datetime | None) -> str:
    """Jalali date + local time for purchase rows."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    local = dt.astimezone(ZoneInfo(settings.timezone))
    jd = jdatetime.datetime.fromgregorian(datetime=local)
    date_s = jd.strftime("%Y/%m/%d")
    time_s = local.strftime("%H:%M")
    return f"{date_s} - {time_s}"


def format_jalali_datetime(settings: Settings, dt: datetime | None) -> str:
    """Full Jalali date + local time."""
    return format_purchase_datetime(settings, dt)


def format_jalali_date_only(settings: Settings, dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    local = dt.astimezone(ZoneInfo(settings.timezone))
    jd = jdatetime.datetime.fromgregorian(datetime=local)
    return jd.strftime("%Y/%m/%d")


def format_copyable_code(text: str) -> str:
    """Telegram HTML: tap-to-copy single block."""
    return f"<code>{html.escape(text, quote=False)}</code>"
