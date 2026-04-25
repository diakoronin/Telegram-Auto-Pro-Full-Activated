# -*- coding: utf-8 -*-
"""لینک‌های آموزش V2Ray و WireGuard برای بخش «آموزش» ربات تلگرام.

استفاده:
    from pathlib import Path
    from vpn_tutorial import load_tutorial_links, tutorial_caption, url_buttons_for_protocol

    data = load_tutorial_links(Path(__file__).parent / "tutorial_vpn_links.json")
    # در handler دکمه «آموزش V2Ray»:
    await query.message.reply_text(
        tutorial_caption("v2ray"),
        reply_markup=url_buttons_for_protocol(data, "v2ray"),
    )
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

Protocol = Literal["v2ray", "wireguard"]

_LABELS_FA = {
    "android": "اندروید",
    "ios": "iOS",
    "windows": "ویندوز",
    "mac": "مک",
}


def load_tutorial_links(json_path: Path | str) -> dict[str, dict[str, str]]:
    path = Path(json_path)
    with path.open(encoding="utf-8") as f:
        raw: Any = json.load(f)
    for key in ("v2ray", "wireguard"):
        if key not in raw or not isinstance(raw[key], dict):
            raise ValueError(f"کلید '{key}' در JSON موجود یا معتبر نیست.")
    return raw


def tutorial_caption(protocol: Protocol) -> str:
    if protocol == "v2ray":
        name = "V2Ray"
    else:
        name = "WireGuard"
    return (
        f"📚 آموزش {name}\n\n"
        "پلتفرم خود را انتخاب کنید؛ پست کانال تلگرام با راهنمای نصب و تنظیم باز می‌شود.\n"
        "پس از نصب اپلیکیشن، طبق همان پست کانفیگ را وارد یا ایمپورت کنید."
    )


def url_buttons_for_protocol(
    data: dict[str, dict[str, str]],
    protocol: Protocol,
) -> list[list[tuple[str, str]]]:
    """برای python-telegram-bot: هر tuple (متن_دکمه، url)."""
    plat = data[protocol]
    order = ("android", "ios", "windows", "mac")
    row: list[tuple[str, str]] = []
    rows: list[list[tuple[str, str]]] = []
    for key in order:
        if key not in plat:
            continue
        label = _LABELS_FA.get(key, key)
        row.append((label, plat[key]))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def build_inline_keyboard_markup(data: dict[str, dict[str, str]], protocol: Protocol):
    """اگر کتابخانه telegram نصب باشد، همان InlineKeyboardMarkup را برمی‌گرداند."""
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    except ImportError:
        raise ImportError(
            "برای build_inline_keyboard_markup بسته python-telegram-bot را نصب کنید: pip install python-telegram-bot"
        ) from None
    rows = url_buttons_for_protocol(data, protocol)
    keyboard = [
        [InlineKeyboardButton(text, url=url) for text, url in r] for r in rows
    ]
    return InlineKeyboardMarkup(keyboard)
