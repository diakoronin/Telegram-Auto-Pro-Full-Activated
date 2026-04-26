"""Reply and inline keyboards."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_user_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛒 خرید سرویس"), KeyboardButton(text="📦 سرویس‌های من")],
            [KeyboardButton(text="💳 کیف پول من"), KeyboardButton(text="🎫 پشتیبانی")],
            [KeyboardButton(text="🏠 منوی اصلی")],
        ],
        resize_keyboard=True,
    )


def admin_root_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛠 پنل مدیریت")],
            [KeyboardButton(text="🏠 منوی اصلی")],
        ],
        resize_keyboard=True,
    )


def admin_panel_kb(manual_enabled: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📦 فروش و موجودی")],
        [KeyboardButton(text="👥 کاربران و پرداخت")],
        [KeyboardButton(text="📊 گزارش‌ها و مدیریت")],
        [KeyboardButton(text="🧪 تست و دیباگ")],
        [KeyboardButton(text="🏠 منوی اصلی")],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def back_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 بازگشت")]],
        resize_keyboard=True,
    )
