"""Jalali date/time and Persian message formatting (Asia/Tehran)."""

from __future__ import annotations

import html
from datetime import datetime
from typing import Optional

import jdatetime
import pytz

from bot_app.config import get_settings


def format_jalali_datetime(dt: Optional[datetime] = None) -> str:
    if dt is None:
        dt = datetime.now(pytz.UTC)
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)
    tz = pytz.timezone(get_settings().timezone)
    local = dt.astimezone(tz)
    jd = jdatetime.datetime.fromgregorian(datetime=local.replace(tzinfo=None))
    return jd.strftime("%Y/%m/%d %H:%M")


def format_message(body: str, include_footer: Optional[bool] = None) -> str:
    settings = get_settings()
    footer_on = settings.footer_enabled if include_footer is None else include_footer
    text = body.rstrip()
    if footer_on:
        ts = format_jalali_datetime()
        text += f"\n\n────────────────\n🕒 {ts}"
    return text


def format_money(amount: int) -> str:
    return f"{amount:,}".replace(",", "٬")


def format_gb(bytes_or_gb: int, *, is_bytes: bool = True) -> str:
    if is_bytes:
        gb = bytes_or_gb / (1024**3)
    else:
        gb = float(bytes_or_gb)
    if gb >= 100:
        return f"{gb:.0f}"
    if gb >= 10:
        return f"{gb:.1f}".rstrip("0").rstrip(".")
    return f"{gb:.2f}".rstrip("0").rstrip(".")


def format_copyable_code(text: str) -> str:
    safe = html.escape(str(text), quote=True)
    return f"<code>{safe}</code>"
