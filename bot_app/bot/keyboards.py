"""Reply and inline keyboards."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

import bot_app.bot.admin_texts as T


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


def admin_root_menu_kb(*, manual_enabled: bool) -> ReplyKeyboardMarkup:
    """Main chat admin menu: every button maps to a handler (no empty submenu)."""
    _ = manual_enabled
    r1 = [KeyboardButton(text=T.BTN_ADD_PANEL), KeyboardButton(text=T.BTN_USER_MGMT)]
    r2 = [KeyboardButton(text=T.BTN_FINANCE), KeyboardButton(text=T.BTN_EDUCATION)]
    r3 = [KeyboardButton(text=T.BTN_SALES), KeyboardButton(text=T.BTN_REPORTS)]
    r4 = [KeyboardButton(text=T.BTN_SYSTEM), KeyboardButton(text=T.BTN_BACK_MAIN)]
    return ReplyKeyboardMarkup(
        keyboard=[r1, r2, r3, r4],
        resize_keyboard=True,
    )


def admin_add_panel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=T.BTN_TYPE_MARZBAN), KeyboardButton(text=T.BTN_TYPE_3XUI)],
            [KeyboardButton(text=T.BTN_BACK_ADMIN)],
        ],
        resize_keyboard=True,
    )


def admin_user_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=T.BTN_USER_SEARCH), KeyboardButton(text=T.BTN_USER_LIST)],
            [KeyboardButton(text=T.BTN_USER_BULK_CREDIT), KeyboardButton(text=T.BTN_USER_BROADCAST)],
            [KeyboardButton(text=T.BTN_BACK_ADMIN)],
        ],
        resize_keyboard=True,
    )


def admin_user_list_nav_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=T.BTN_PAGE_PREV), KeyboardButton(text=T.BTN_PAGE_NEXT)],
            [KeyboardButton(text=T.BTN_BACK_ADMIN)],
        ],
        resize_keyboard=True,
    )


def admin_finance_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=T.BTN_CARDS), KeyboardButton(text=T.BTN_ADD_CARD)],
            [KeyboardButton(text=T.BTN_C2C_TEXT), KeyboardButton(text=T.BTN_PENDING_PAY)],
            [KeyboardButton(text=T.BTN_BACK_ADMIN)],
        ],
        resize_keyboard=True,
    )


def admin_education_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=T.BTN_EDU_ADD), KeyboardButton(text=T.BTN_EDU_EDIT)],
            [KeyboardButton(text=T.BTN_EDU_DEL), KeyboardButton(text=T.BTN_EDU_LIST)],
            [KeyboardButton(text=T.BTN_BACK_ADMIN)],
        ],
        resize_keyboard=True,
    )


def admin_sales_menu_kb(*, manual_enabled: bool) -> ReplyKeyboardMarkup:
    if manual_enabled:
        rows: list = [
            [KeyboardButton(text=T.BTN_LIST_PANELS), KeyboardButton(text=T.BTN_IMPORT)],
            [KeyboardButton(text=T.BTN_DELIVER), KeyboardButton(text=T.BTN_LINK_STOCK)],
            [KeyboardButton(text=T.BTN_BACK_ADMIN)],
        ]
    else:
        rows = [
            [KeyboardButton(text=T.BTN_LIST_PANELS)],
            [KeyboardButton(text=T.BTN_BACK_ADMIN)],
        ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def admin_system_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=T.BTN_SYNC), KeyboardButton(text=T.BTN_DB_HEALTH)],
            [KeyboardButton(text=T.BTN_BACK_ADMIN)],
        ],
        resize_keyboard=True,
    )


def admin_cancel_row_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=T.BTN_CANCEL)]],
        resize_keyboard=True,
    )


def admin_panel_kb(manual_enabled: bool) -> ReplyKeyboardMarkup:
    """Alias for the main admin reply keyboard (compatibility)."""
    return admin_root_menu_kb(manual_enabled=manual_enabled)


def back_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 بازگشت")]],
        resize_keyboard=True,
    )
