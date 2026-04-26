from __future__ import annotations

import io
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts_fa as T
from app.message_format import (
    format_copyable_code,
    format_jalali_datetime,
    format_message,
    format_money_toman,
)
from app.bot.filters import IsAdmin, IsManagerOrOwner, IsOwner
from app.bot.states import AdminStates
from app.config import Settings
from app.db.models import (
    Admin,
    AdminRole,
    Link,
    LinkStatus,
    LocationChangeRequest,
    LocationChangeRequestStatus,
    PaymentCard,
    Plan,
    Purchase,
    Server,
    SupportTicket,
    SupportTicketStatus,
    User,
    UserService,
)
from app.services.audit import write_audit
from app.services.backup import export_full_backup_bytes, write_temp_backup_file
from app.services.confirmations import create_confirmation, take_confirmation_if_valid
from app.services.delete_unused import delete_unused_links
from app.services.import_links import bulk_import_links_detailed
from app.services.links import admin_manual_deliver, return_link
from app.services.plan_display import plan_display_label
from app.services.rate_limit import consume_rate
from app.services.payments import list_pending_for_review, reject_payment_request
from app.services.sales_report import (
    aggregate_sales,
    window_this_month,
    window_today,
    window_yesterday,
)
from app.services.stock import get_all_servers_stock_summary, get_server_stock_summary
from app.services.users import get_admin_by_telegram
from app.services.cards import card_display_number
from app.services.stock_alerts import run_stock_check_after_commit
from app.services.admin_traffic_status import build_traffic_sync_status_text
from app.services.app_settings import KEY_LEGACY_LINK_ADMIN, get_setting
from app.services.owner_backup import send_backup_to_owner
from app.services.reports_aggregate import (
    aggregate_payments_approved,
    count_user_services_by_status,
)
from app.services.sales_csv import (
    export_daily_report_csv,
    export_payments_csv,
    export_purchases_csv,
    export_services_csv,
)
from app.services.traffic_sync import run_traffic_sync_cycle
from app.services.wallet import deduct_location_change_fee, manual_adjust_wallet, refund_purchase
from app.validation import (
    ValidationError,
    validate_bank_name,
    validate_card_holder,
    validate_card_number,
    validate_customer_info,
    validate_plan_name,
    validate_plan_display_name,
    validate_plan_price,
    validate_reason,
    validate_server_name,
)

logger = logging.getLogger(__name__)

# Bulk .txt import: keep below Telegram's upload limits and memory-safe.
_MAX_IMPORT_FILE_BYTES = 5 * 1024 * 1024

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _afmt(settings: Settings, text: str) -> str:
    return format_message(settings, text)


LEGACY_DISABLED_MSG = "این بخش در نسخه جدید غیرفعال است."


async def _admin_root_kb_async(
    session: AsyncSession, admin: Admin, settings: Settings
) -> InlineKeyboardMarkup:
    leg = await _legacy_manual_allowed(session, settings)
    return _admin_root_kb(admin, legacy_manual=leg)


async def _legacy_manual_allowed(session: AsyncSession, settings: Settings) -> bool:
    if settings.legacy_manual_mode:
        return True
    v = await get_setting(session, KEY_LEGACY_LINK_ADMIN, "")
    return v.strip().lower() in ("1", "true", "yes", "on")


def _admin_root_kb(admin: Admin, *, legacy_manual: bool = False) -> InlineKeyboardMarkup:
    if admin.role == AdminRole.SELLER:
        if legacy_manual:
            return InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🛒 تحویل دستی لینک", callback_data="adm:manual")],
                ]
            )
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🛒 تحویل دستی (غیرفعال)",
                        callback_data="adm:legacy_blocked",
                    )
                ],
            ]
        )
    rows = [
        [InlineKeyboardButton(text=T.ADM_CAT_SALES, callback_data="adm:cat_sales")],
        [InlineKeyboardButton(text=T.ADM_CAT_USERS, callback_data="adm:cat_users")],
    ]
    if admin.role in (AdminRole.OWNER, AdminRole.MANAGER):
        rows.append(
            [InlineKeyboardButton(text=T.ADM_CAT_MGMT, callback_data="adm:cat_mgmt")]
        )
    rows.append([InlineKeyboardButton(text=T.ADM_HOME_MAIN, callback_data="adm:goto_user_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_cat_sales(*, legacy_manual: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=T.ADM_SRV_PLANS, callback_data="adm:plans")],
    ]
    if legacy_manual:
        rows.extend(
            [
                [InlineKeyboardButton(text=T.ADM_LINK_MGMT, callback_data="adm:link_menu")],
                [InlineKeyboardButton(text=T.ADM_MANUAL, callback_data="adm:manual")],
                [InlineKeyboardButton(text=T.ADM_STOCK_ALL, callback_data="adm:stock_all")],
            ]
        )
    rows.append([InlineKeyboardButton(text=T.ADM_BACK, callback_data="admin_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_cat_users(admin: Admin) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=T.ADM_USERS, callback_data="adm:users")],
        [InlineKeyboardButton(text=T.ADM_PAY_REQS, callback_data="adm:pay_reqs")],
        [InlineKeyboardButton(text="🎫 تیکت‌های پشتیبانی", callback_data="adm:tickets")],
    ]
    if admin.role == AdminRole.OWNER:
        rows.insert(1, [InlineKeyboardButton(text=T.ADM_CARDS, callback_data="adm:cards")])
    elif admin.role == AdminRole.MANAGER:
        rows.insert(1, [InlineKeyboardButton(text=T.ADM_CARDS, callback_data="adm:cards_mgr")])
    if admin.role in (AdminRole.OWNER, AdminRole.MANAGER):
        rows.append([InlineKeyboardButton(text=T.ADM_WALLET, callback_data="adm:wallet")])
        rows.append(
            [
                InlineKeyboardButton(
                    text=T.ADM_CARD_USER_ACCESS, callback_data="adm:card_access_menu"
                )
            ]
        )
    rows.append([InlineKeyboardButton(text=T.ADM_BACK, callback_data="admin_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_cat_mgmt(admin: Admin) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="📊 گزارش فروش امروز", callback_data="adm:sales_report:today"
            )
        ],
        [
            InlineKeyboardButton(
                text="📡 وضعیت سینک مصرف", callback_data="adm:traffic_sync_status"
            )
        ],
        [
            InlineKeyboardButton(
                text="🌍 درخواست‌های لوکیشن", callback_data="adm:locreqs"
            )
        ],
        [InlineKeyboardButton(text=T.ADM_REPORTS, callback_data="adm:backup_menu")],
    ]
    if admin.role == AdminRole.OWNER:
        rows.append([InlineKeyboardButton(text=T.ADM_ADMINS, callback_data="adm:admins")])
        rows.append([InlineKeyboardButton(text="💸 بازپرداخت خرید", callback_data="adm:refund")])
        rows.append([InlineKeyboardButton(text="🗑 حذف ادمین", callback_data="adm:remove_admin")])
        rows.append(
            [InlineKeyboardButton(text="💾 ارسال بکاپ الان", callback_data="adm:owner_backup")]
        )
    rows.append(
        [InlineKeyboardButton(text=T.ADM_SETTINGS, callback_data="adm:settings_stub")]
    )
    rows.append([InlineKeyboardButton(text=T.ADM_BACK, callback_data="admin_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_link_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📥 ایمپورت لینک", callback_data="adm:import_menu")],
            [
                InlineKeyboardButton(
                    text="🗑 حذف لینک‌های بلااستفاده", callback_data="adm:del_unused_menu"
                )
            ],
            [InlineKeyboardButton(text="↩️ بازگرداندن لینک", callback_data="adm:ret_link_start")],
            [InlineKeyboardButton(text=T.ADM_BACK, callback_data="adm:cat_sales")],
        ]
    )


@router.callback_query(F.data == "adm:goto_user_menu")
async def cb_goto_user_menu(
    callback: CallbackQuery, admin: Admin, db_user: User | None = None, **kwargs: Any
) -> None:
    from app import texts_fa as Tfa

    allow = bool(db_user and db_user.card_view_allowed)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=Tfa.BTN_SHOP, callback_data="shop")],
            [
                InlineKeyboardButton(text=Tfa.BTN_WALLET, callback_data="wallet"),
                InlineKeyboardButton(text=Tfa.BTN_CHARGE, callback_data="charge"),
            ],
            [
                InlineKeyboardButton(text=Tfa.BTN_HISTORY, callback_data="hist_purchases"),
                InlineKeyboardButton(text=Tfa.BTN_PAYMENT_HISTORY, callback_data="hist_payments"),
            ],
            *(
                [[InlineKeyboardButton(text=Tfa.BTN_CARDS, callback_data="show_cards")]]
                if allow
                else []
            ),
            [InlineKeyboardButton(text=Tfa.BTN_SUPPORT, callback_data="support")],
        ]
    )
    await callback.message.answer(
        "منوی کاربران (برای بازگشت به پنل /admin بزنید):",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data == "adm:cat_sales", IsManagerOrOwner())
async def cb_cat_sales(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    leg = await _legacy_manual_allowed(session, settings)
    await callback.message.edit_text(
        _afmt(settings, "📦 فروش و موجودی"),
        reply_markup=_kb_cat_sales(legacy_manual=leg),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:legacy_blocked", IsAdmin())
async def cb_legacy_blocked(callback: CallbackQuery) -> None:
    await callback.answer(LEGACY_DISABLED_MSG, show_alert=True)


@router.callback_query(F.data == "adm:link_menu", IsManagerOrOwner())
async def cb_link_menu(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if not await _legacy_manual_allowed(session, settings):
        await callback.answer(LEGACY_DISABLED_MSG, show_alert=True)
        return
    await callback.message.edit_text(
        _afmt(settings, "🔗 مدیریت لینک‌ها"),
        reply_markup=_kb_link_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:cat_users", IsManagerOrOwner())
async def cb_cat_users(callback: CallbackQuery, settings: Settings, admin: Admin) -> None:
    await callback.message.edit_text(
        _afmt(settings, "👥 کاربران و پرداخت"),
        reply_markup=_kb_cat_users(admin),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:cat_mgmt", IsManagerOrOwner())
async def cb_cat_mgmt(callback: CallbackQuery, settings: Settings, admin: Admin) -> None:
    await callback.message.edit_text(
        _afmt(settings, "📊 مدیریت"),
        reply_markup=_kb_cat_mgmt(admin),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:settings_stub", IsManagerOrOwner())
async def cb_settings_stub(callback: CallbackQuery, settings: Settings) -> None:
    await callback.answer(
        "تنظیمات اصلی ربات از فایل .env روی سرور انجام می‌شود.",
        show_alert=True,
    )


@router.callback_query(F.data == "adm:stock_all", IsManagerOrOwner())
async def cb_stock_all(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not await _legacy_manual_allowed(session, settings):
        await callback.answer(LEGACY_DISABLED_MSG, show_alert=True)
        return
    summary = await get_all_servers_stock_summary(session)
    if not summary:
        text = T.EMPTY_NO_SERVERS
    else:
        blocks = [T.ADM_STOCK_ALL + "\n"]
        for block in summary:
            lines = [f"\n🌐 {block.server_name}"]
            tot = 0
            for p in block.plans:
                tot += p.unused_count
                st = "✅" if p.unused_count > 0 else "❌"
                lines.append(f"▫️ {p.plan_name}: {p.unused_count} {st}")
            lines.append(f"📌 مجموع: {tot}")
            blocks.append("\n".join(lines))
        text = "\n".join(blocks)
    await callback.message.edit_text(
        _afmt(settings, text),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=T.ADM_BACK, callback_data="adm:cat_sales")]]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:pay_reqs", IsManagerOrOwner())
async def cb_pay_reqs(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    pending = await list_pending_for_review(session, limit=15)
    if not pending:
        body = "🧾 درخواست پرداخت در انتظار بررسی وجود ندارد."
    else:
        lines = ["🧾 درخواست‌های در انتظار:\n"]
        for pr in pending:
            u = await session.get(User, pr.user_id)
            tid = u.telegram_id if u else 0
            un = f"@{u.username}" if u and u.username else "—"
            lines.append(
                f"\n🧾 #{pr.id} | 💵 {format_money_toman(pr.amount)} تومان\n"
                f"🆔 {tid} | {un}"
            )
        body = "\n".join(lines)
    kb = [[InlineKeyboardButton(text=T.ADM_BACK, callback_data="adm:cat_users")]]
    await callback.message.edit_text(_afmt(settings, body), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()


@router.callback_query(F.data == "adm:cards_mgr", IsManagerOrOwner())
async def cb_cards_manager_info(callback: CallbackQuery, settings: Settings) -> None:
    await callback.message.edit_text(
        _afmt(settings, "مدیریت کارت‌ها فقط برای مالک در دسترس است."),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=T.ADM_BACK, callback_data="adm:cat_users")]]
        ),
    )
    await callback.answer()


@router.message(Command("admin"))
async def cmd_admin(message: Message, session: AsyncSession, admin: Admin, settings: Settings) -> None:
    await write_audit(
        session,
        actor_telegram_id=message.from_user.id if message.from_user else None,
        actor_role=admin.role.value,
        action="admin_panel_open",
        target_type="admin",
        target_id=str(admin.id),
    )
    intro = f"{T.ADMIN_PANEL_TITLE}\n\n{T.ADMIN_PANEL_INTRO}"
    await message.answer(
        _afmt(settings, intro),
        reply_markup=await _admin_root_kb_async(session, admin, settings),
    )


@router.callback_query(F.data == "adm:owner_backup", IsOwner())
async def cb_owner_backup_now(
    callback: CallbackQuery, settings: Settings, after_commit: list, **kwargs
) -> None:
    bot = callback.bot

    async def _send() -> None:
        ok, err = await send_backup_to_owner(bot, settings)
        if ok:
            await callback.message.answer(_afmt(settings, "✅ بکاپ برای مالک ارسال شد."))
        else:
            await callback.message.answer(_afmt(settings, f"❌ ارسال بکاپ ناموفق: {err}"))

    after_commit.append(_send)
    await callback.answer()


@router.callback_query(F.data == "admin_home")
async def cb_admin_home(
    callback: CallbackQuery, session: AsyncSession, admin: Admin, settings: Settings
) -> None:
    intro = f"{T.ADMIN_PANEL_TITLE}\n\n{T.ADMIN_PANEL_INTRO}"
    await callback.message.edit_text(
        _afmt(settings, intro),
        reply_markup=await _admin_root_kb_async(session, admin, settings),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_cancel_fsm")
async def cb_admin_cancel_fsm(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
    settings: Settings,
) -> None:
    await state.clear()
    intro = f"{T.ADMIN_PANEL_TITLE}\n\n{T.ADMIN_PANEL_INTRO}"
    await callback.message.answer(
        _afmt(settings, intro),
        reply_markup=await _admin_root_kb_async(session, admin, settings),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:plans", IsManagerOrOwner())
async def cb_plans(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    rows = (await session.execute(select(Server).order_by(Server.id))).scalars().all()
    if not rows:
        text = T.EMPTY_NO_SERVERS
        kb: list[list[InlineKeyboardButton]] = []
    else:
        text = "🌐 سرور را انتخاب کنید:"
        kb = [
            [InlineKeyboardButton(text=f"🌐 {s.name}", callback_data=f"adm:srv:{s.id}")]
            for s in rows
        ]
    leg = await _legacy_manual_allowed(session, settings)
    extra = [
        [InlineKeyboardButton(text="➕ افزودن سرور", callback_data="adm:add_srv")],
        [InlineKeyboardButton(text="➕ افزودن پلن", callback_data="adm:add_plan_menu")],
        [
            InlineKeyboardButton(
                text="🚫 غیرفعال‌سازی پلن", callback_data="adm:deact_plan_menu"
            )
        ],
        [
            InlineKeyboardButton(
                text="🚫 غیرفعال‌سازی سرور", callback_data="adm:deact_srv_menu"
            )
        ],
    ]
    if leg:
        extra.insert(
            2,
            [InlineKeyboardButton(text="📥 افزودن لینک", callback_data="adm:import_menu")],
        )
    extra.append([InlineKeyboardButton(text=T.ADM_BACK, callback_data="adm:cat_sales")])
    kb.extend(extra)
    await callback.message.edit_text(
        _afmt(settings, text), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:srv:"), IsManagerOrOwner())
async def cb_server_detail(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    sid = int(callback.data.split(":")[-1])
    srv = await session.get(Server, sid)
    if srv is None:
        await callback.answer("سرور یافت نشد.", show_alert=True)
        return
    st = "فعال ✅" if srv.is_active else "غیرفعال ❌"
    plans = await get_server_stock_summary(session, server_id=sid, only_active_plans=False)
    plan_lines = ["\n📊 موجودی پلن‌ها:"]
    sum_unused = 0
    for p in plans:
        sum_unused += p.unused_count
        if p.unused_count > 0:
            plan_lines.append(f"▫️ {p.plan_name}: {p.unused_count} عدد ✅")
        else:
            plan_lines.append(f"▫️ {p.plan_name}: 0 ❌")
    body = (
        f"📦 مدیریت سرور\n\n🌐 سرور: {srv.name}\nوضعیت: {st}\n"
        + "\n".join(plan_lines)
        + f"\n\n📌 مجموع موجودی این سرور: {sum_unused} عدد"
    )
    leg = await _legacy_manual_allowed(session, settings)
    kb = [[InlineKeyboardButton(text="➕ افزودن پلن", callback_data=f"adm:pick_srv:{sid}")]]
    if leg:
        kb.append([InlineKeyboardButton(text="📥 افزودن لینک", callback_data="adm:import_menu")])
    kb.extend(
        [
            [
                InlineKeyboardButton(
                    text="📊 گزارش کامل موجودی", callback_data=f"adm:srv_stock_rpt:{sid}"
                )
            ],
            [InlineKeyboardButton(text=T.ADM_BACK, callback_data="adm:plans")],
        ]
    )
    await callback.message.edit_text(
        _afmt(settings, body), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:srv_stock_rpt:"), IsManagerOrOwner())
async def cb_server_stock_report(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    sid = int(callback.data.split(":")[-1])
    srv = await session.get(Server, sid)
    if srv is None:
        await callback.answer("سرور یافت نشد.", show_alert=True)
        return
    plans = await get_server_stock_summary(session, server_id=sid, only_active_plans=False)
    blocks = [f"📊 گزارش موجودی سرور {srv.name}\n"]
    for p in plans:
        price_s = format_money_toman(p.price)
        blocks.append(
            f"\n▫️ پلن {p.plan_name}\n"
            f"💵 قیمت: {price_s} تومان\n"
            f"✅ آماده فروش: {p.unused_count}\n"
            f"📦 فروخته‌شده: {p.used_count}\n"
            f"🗑 غیرفعال/برگشتی: {p.inactive_count}\n"
            f"📌 کل: {p.total_count}"
        )
    await callback.message.edit_text(
        _afmt(settings, "\n".join(blocks)),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=T.ADM_BACK, callback_data=f"adm:srv:{sid}")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:deact_plan_menu", IsManagerOrOwner())
async def cb_deact_plan_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    rows = (
        await session.execute(
            select(Server, Plan)
            .join(Plan, Plan.server_id == Server.id)
            .where(Plan.is_active.is_(True))
            .order_by(Server.id, Plan.id)
        )
    ).all()
    if not rows:
        await callback.answer("پلنی نیست.", show_alert=True)
        return
    kb = [
        [
            InlineKeyboardButton(
                text=f"{srv.name}/{pl.name}",
                callback_data=f"adm:deact_p:{pl.id}",
            )
        ]
        for srv, pl in rows
    ]
    kb.append([InlineKeyboardButton(text="بازگشت", callback_data="adm:plans")])
    await callback.message.edit_text(
        "پلن را برای غیرفعال‌سازی انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:deact_p:"), IsManagerOrOwner())
async def cb_deact_plan_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    pid = int(callback.data.split(":")[-1])
    cid = await create_confirmation(
        session,
        admin_telegram_id=callback.from_user.id,
        action_type="deactivate_plan",
        payload={"action": "deactivate_plan", "plan_id": pid},
    )
    await callback.message.answer(
        f"غیرفعال‌سازی پلن #{pid}؟ " + T.CONFIRM_PROMPT,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="تأیید", callback_data=f"acf:{cid}"),
                    InlineKeyboardButton(text="لغو", callback_data="noop"),
                ]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:deact_srv_menu", IsManagerOrOwner())
async def cb_deact_srv_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    rows = (await session.execute(select(Server).where(Server.is_active.is_(True)))).scalars().all()
    if not rows:
        await callback.answer("سروری نیست.", show_alert=True)
        return
    kb = [
        [InlineKeyboardButton(text=s.name, callback_data=f"adm:deact_s:{s.id}")]
        for s in rows
    ]
    kb.append([InlineKeyboardButton(text="بازگشت", callback_data="adm:plans")])
    await callback.message.edit_text(
        "سرور را برای غیرفعال‌سازی انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:deact_s:"), IsManagerOrOwner())
async def cb_deact_srv_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    sid = int(callback.data.split(":")[-1])
    cid = await create_confirmation(
        session,
        admin_telegram_id=callback.from_user.id,
        action_type="deactivate_server",
        payload={"action": "deactivate_server", "server_id": sid},
    )
    await callback.message.answer(
        f"غیرفعال‌سازی سرور #{sid}؟ " + T.CONFIRM_PROMPT,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="تأیید", callback_data=f"acf:{cid}"),
                    InlineKeyboardButton(text="لغو", callback_data="noop"),
                ]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:del_unused_menu", IsManagerOrOwner())
async def cb_del_unused_menu(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if not await _legacy_manual_allowed(session, settings):
        await callback.answer(LEGACY_DISABLED_MSG, show_alert=True)
        return
    rows = (
        await session.execute(
            select(Server, Plan)
            .join(Plan, Plan.server_id == Server.id)
            .order_by(Server.id, Plan.id)
        )
    ).all()
    if not rows:
        await callback.answer("پلنی نیست.", show_alert=True)
        return
    kb = [
        [
            InlineKeyboardButton(
                text=f"{srv.name}/{pl.name}",
                callback_data=f"adm:del_unused:{srv.id}:{pl.id}",
            )
        ]
        for srv, pl in rows
    ]
    kb.append([InlineKeyboardButton(text="بازگشت", callback_data="adm:plans")])
    await callback.message.edit_text(
        "پلن را برای حذف لینک‌های استفاده‌نشده انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:del_unused:"), IsManagerOrOwner())
async def cb_del_unused_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _legacy_manual_allowed(session, settings):
        await callback.answer(LEGACY_DISABLED_MSG, show_alert=True)
        return
    _, _, sid_s, pid_s = callback.data.split(":")
    sid, pid = int(sid_s), int(pid_s)
    cid = await create_confirmation(
        session,
        admin_telegram_id=callback.from_user.id,
        action_type="delete_unused_links",
        payload={
            "action": "delete_unused_links",
            "server_id": sid,
            "plan_id": pid,
        },
    )
    await callback.message.answer(
        "حذف همه لینک‌های استفاده‌نشده این پلن؟ " + T.CONFIRM_PROMPT,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="تأیید حذف", callback_data=f"acf:{cid}"),
                    InlineKeyboardButton(text="لغو", callback_data="noop"),
                ]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:ret_link_start", IsManagerOrOwner())
async def cb_ret_link_start(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    if not await _legacy_manual_allowed(session, settings):
        await callback.answer(LEGACY_DISABLED_MSG, show_alert=True)
        return
    await state.set_state(AdminStates.return_link_id)
    await callback.message.answer(
        "شناسه لینک (link id) را بفرستید:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.message(AdminStates.return_link_id, F.text)
async def msg_return_link_id(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
) -> None:
    try:
        lid = int((message.text or "").strip())
    except ValueError:
        await message.answer("شناسه نامعتبر است.")
        return
    cid = await create_confirmation(
        session,
        admin_telegram_id=message.from_user.id,
        action_type="return_link",
        payload={"action": "return_link", "link_id": lid},
    )
    await state.clear()
    await message.answer(
        "بازگرداندن لینک؟ " + T.CONFIRM_PROMPT,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="تأیید", callback_data=f"acf:{cid}"),
                    InlineKeyboardButton(text="لغو", callback_data="noop"),
                ]
            ]
        ),
    )


@router.callback_query(F.data == "adm:remove_admin", IsOwner())
async def cb_remove_admin_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.remove_admin_tid)
    await callback.message.answer(
        "شناسه تلگرام ادمین برای حذف:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.message(AdminStates.remove_admin_tid, F.text)
async def msg_remove_admin_tid(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    try:
        tid = int((message.text or "").strip())
    except ValueError:
        await message.answer("شناسه نامعتبر است.")
        return
    if tid == settings.owner_telegram_id:
        await message.answer("نمی‌توان مالک را حذف کرد.")
        await state.clear()
        return
    cid = await create_confirmation(
        session,
        admin_telegram_id=message.from_user.id,
        action_type="remove_admin",
        payload={"action": "remove_admin", "telegram_id": tid},
    )
    await state.clear()
    await message.answer(
        "حذف این ادمین؟ " + T.CONFIRM_PROMPT,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="تأیید حذف", callback_data=f"acf:{cid}"),
                    InlineKeyboardButton(text="لغو", callback_data="noop"),
                ]
            ]
        ),
    )


@router.callback_query(F.data == "adm:add_srv", IsManagerOrOwner())
async def cb_add_srv(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.add_server_name)
    await callback.message.answer(
        "نام سرور را وارد کنید:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.message(AdminStates.add_server_name, F.text)
async def msg_add_server(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
    settings: Settings,
) -> None:
    try:
        name = validate_server_name(message.text or "")
    except ValidationError as e:
        await message.answer(e.message_fa)
        return
    s = Server(name=name, is_active=True)
    session.add(s)
    await session.flush()
    await write_audit(
        session,
        actor_telegram_id=message.from_user.id,
        actor_role=admin.role.value,
        action="server_created",
        target_type="server",
        target_id=str(s.id),
    )
    await state.clear()
    await message.answer(
        f"سرور #{s.id} ایجاد شد.",
        reply_markup=await _admin_root_kb_async(session, admin, settings),
    )


@router.callback_query(F.data == "adm:add_plan_menu", IsManagerOrOwner())
async def cb_add_plan_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    srvs = (await session.execute(select(Server).where(Server.is_active.is_(True)))).scalars().all()
    if not srvs:
        await callback.answer("ابتدا سرور بسازید.", show_alert=True)
        return
    kb = [
        [InlineKeyboardButton(text=s.name, callback_data=f"adm:pick_srv:{s.id}")]
        for s in srvs
    ]
    kb.append([InlineKeyboardButton(text="بازگشت", callback_data="adm:plans")])
    await callback.message.edit_text("سرور را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:pick_srv:"), IsManagerOrOwner())
async def cb_pick_srv(callback: CallbackQuery, state: FSMContext) -> None:
    sid = int(callback.data.split(":")[-1])
    await state.update_data(plan_server_id=sid)
    await state.set_state(AdminStates.add_plan_name)
    await callback.message.answer(
        "نام داخلی پلن را وارد کنید (برای مدیریت و ایمپورت):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.message(AdminStates.add_plan_name, F.text)
async def msg_plan_name(message: Message, state: FSMContext) -> None:
    try:
        name = validate_plan_name(message.text or "")
    except ValidationError as e:
        await message.answer(e.message_fa)
        return
    await state.update_data(plan_name=name)
    await state.set_state(AdminStates.add_plan_display_name)
    await message.answer(
        "نام نمایشی برای کاربر (کوتاه، مثلاً «یک گیگ»):\n"
        "برای استفاده از همان نام داخلی، خط تیره «-» بفرستید."
    )


@router.message(AdminStates.add_plan_display_name, F.text)
async def msg_plan_display_name(message: Message, state: FSMContext) -> None:
    try:
        disp = validate_plan_display_name(message.text or "")
    except ValidationError as e:
        await message.answer(e.message_fa)
        return
    await state.update_data(plan_display_name=disp)
    await state.set_state(AdminStates.add_plan_price)
    await message.answer("قیمت پلن را به تومان وارد کنید (عدد مثبت):")


@router.message(AdminStates.add_plan_price, F.text)
async def msg_plan_price(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
    settings: Settings,
) -> None:
    data = await state.get_data()
    sid = int(data.get("plan_server_id") or 0)
    name = str(data.get("plan_name") or "")
    disp = data.get("plan_display_name")
    try:
        price = validate_plan_price(message.text or "")
        pname = validate_plan_name(name)
    except ValidationError as e:
        await message.answer(e.message_fa)
        return
    p = Plan(
        server_id=sid,
        name=pname,
        display_name=disp,
        price=price,
        is_active=True,
    )
    session.add(p)
    await session.flush()
    await write_audit(
        session,
        actor_telegram_id=message.from_user.id,
        actor_role=admin.role.value,
        action="plan_created",
        target_type="plan",
        target_id=str(p.id),
    )
    await state.clear()
    await message.answer(
        f"پلن #{p.id} ایجاد شد.",
        reply_markup=await _admin_root_kb_async(session, admin, settings),
    )


@router.callback_query(F.data == "adm:manual")
async def cb_manual(
    callback: CallbackQuery,
    session: AsyncSession,
    admin: Admin,
    settings: Settings,
) -> None:
    if not await _legacy_manual_allowed(session, settings):
        await callback.answer(LEGACY_DISABLED_MSG, show_alert=True)
        return
    rows = (
        await session.execute(
            select(Server, Plan)
            .join(Plan, Plan.server_id == Server.id)
            .where(Server.is_active.is_(True), Plan.is_active.is_(True))
            .order_by(Server.id, Plan.id)
        )
    ).all()
    if not rows:
        await callback.answer("پلنی نیست.", show_alert=True)
        return
    kb = []
    for srv, pl in rows:
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"{srv.name}/{pl.name}",
                    callback_data=f"adm:md:{srv.id}:{pl.id}",
                )
            ]
        )
    kb.append([InlineKeyboardButton(text="بازگشت", callback_data="admin_home")])
    await callback.message.edit_text(
        f"{T.ADM_MANUAL_INTRO}\n\nپلن را برای تحویل دستی انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:md:"))
async def cb_manual_pick(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, sid_s, pid_s = callback.data.split(":")
    await state.update_data(md_server_id=int(sid_s), md_plan_id=int(pid_s))
    await state.set_state(AdminStates.manual_deliver_customer)
    await callback.message.answer(
        "اطلاعات مشتری (اختیاری، حداکثر ۵۰۰ نویسه) یا '-' برای رد:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.message(AdminStates.manual_deliver_customer, F.text)
async def msg_manual_customer(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
    settings: Settings,
    after_commit: list,
) -> None:
    if not await _legacy_manual_allowed(session, settings):
        await state.clear()
        await message.answer(LEGACY_DISABLED_MSG)
        return
    data = await state.get_data()
    sid = int(data["md_server_id"])
    pid = int(data["md_plan_id"])
    raw = (message.text or "").strip()
    if raw in ("", "-"):
        info = None
    else:
        try:
            info = validate_customer_info(raw)
        except ValidationError as e:
            await message.answer(e.message_fa)
            return
    ok, link, err, delivery_id = await admin_manual_deliver(
        session,
        admin_id=admin.id,
        server_id=sid,
        plan_id=pid,
        customer_info=info,
    )
    if not ok:
        await message.answer(err or T.GENERIC_ERROR)
        return
    await write_audit(
        session,
        actor_telegram_id=message.from_user.id,
        actor_role=admin.role.value,
        action="link_delivered_manual",
        target_type="link",
        metadata={"plan_id": pid},
    )
    plan_id_for_stock = int(data["md_plan_id"])
    await state.clear()
    srv = await session.get(Server, sid)
    pl = await session.get(Plan, pid)
    srv_n = srv.name if srv else "—"

    dt_s = format_jalali_datetime(settings, datetime.now(tz=UTC))
    body = (
        "✅ لینک آماده تحویل\n\n"
        f"📦 {plan_display_label(pl) if pl else '—'}\n"
        f"🌐 {srv_n}\n"
        f"🧾 #{delivery_id or 0}\n"
        f"🕒 {dt_s}\n\n"
        "🔗 لینک:\n"
        f"{format_copyable_code(link or '')}"
        f"{T.ADM_MANUAL_DELIVERED_FOOTNOTE}"
    )
    await message.answer(
        format_message(settings, body),
        parse_mode="HTML",
        reply_markup=await _admin_root_kb_async(session, admin, settings),
    )
    bot = message.bot

    async def _stock() -> None:
        await run_stock_check_after_commit(
            settings.database_url, settings, bot, plan_id=plan_id_for_stock
        )

    after_commit.append(_stock)


@router.callback_query(F.data == "adm:import_menu", IsManagerOrOwner())
async def cb_import_menu(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if not await _legacy_manual_allowed(session, settings):
        await callback.answer(LEGACY_DISABLED_MSG, show_alert=True)
        return
    rows = (
        await session.execute(
            select(Server, Plan)
            .join(Plan, Plan.server_id == Server.id)
            .where(Server.is_active.is_(True), Plan.is_active.is_(True))
        )
    ).all()
    if not rows:
        await callback.answer("پلنی نیست.", show_alert=True)
        return
    kb = []
    for srv, pl in rows:
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"{srv.name}/{pl.name}",
                    callback_data=f"adm:im:{srv.id}:{pl.id}",
                )
            ]
        )
    kb.append([InlineKeyboardButton(text="بازگشت", callback_data="admin_home")])
    await callback.message.edit_text("پلن مقصد ایمپورت را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:im:"), IsManagerOrOwner())
async def cb_import_pick(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    if not await _legacy_manual_allowed(session, settings):
        await callback.answer(LEGACY_DISABLED_MSG, show_alert=True)
        return
    _, _, sid_s, pid_s = callback.data.split(":")
    await state.update_data(import_server_id=int(sid_s), import_plan_id=int(pid_s))
    await state.set_state(AdminStates.import_links_paste)
    await callback.message.answer(
        T.IMPORT_FILE_HELP,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


def _decode_import_file(raw: bytes) -> tuple[str | None, str | None]:
    """Return (text, error_fa)."""
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return None, T.IMPORT_FILE_DECODE_ERROR


async def _finalize_link_import(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
    settings: Settings,
    after_commit: list,
    lines: list[str],
    *,
    source: str,
) -> None:
    if not await _legacy_manual_allowed(session, settings):
        await state.clear()
        await message.answer(LEGACY_DISABLED_MSG)
        return
    ok = await consume_rate(
        session,
        key=f"admin_import:{admin.telegram_id}",
        window_seconds=60,
        max_count=settings.rate_limit_admin_import_minute,
    )
    if not ok:
        await message.answer(_afmt(settings, T.RATE_LIMIT))
        return
    data = await state.get_data()
    sid = int(data["import_server_id"])
    pid = int(data["import_plan_id"])
    added, dup_b, dup_db, invalid, total_in, err = await bulk_import_links_detailed(
        session,
        server_id=sid,
        plan_id=pid,
        lines=lines,
        max_lines=settings.max_import_links,
        max_link_len=4096,
    )
    if err:
        await message.answer(_afmt(settings, err))
        return
    await write_audit(
        session,
        actor_telegram_id=message.from_user.id,
        actor_role=admin.role.value,
        action="links_imported",
        target_type="plan",
        target_id=str(pid),
        metadata={
            "added": added,
            "dup_batch": dup_b,
            "dup_db": dup_db,
            "source": source,
        },
    )
    await state.clear()
    body = T.IMPORT_DONE_UX.format(
        total=total_in,
        added=added,
        dup_file=dup_b,
        dup_db=dup_db,
        invalid=invalid,
    )
    kb_imp = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=T.BTN_ADD_LINK_AGAIN, callback_data="adm:import_menu")],
            [InlineKeyboardButton(text=T.BTN_VIEW_STOCK, callback_data="adm:stock_all")],
            [InlineKeyboardButton(text=T.ADM_BACK, callback_data="adm:cat_sales")],
        ]
    )
    await message.answer(_afmt(settings, body), reply_markup=kb_imp)
    bot = message.bot

    async def _stock() -> None:
        await run_stock_check_after_commit(
            settings.database_url, settings, bot, plan_id=pid
        )

    after_commit.append(_stock)


@router.message(AdminStates.import_links_paste, F.text)
async def msg_import_links(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
    settings: Settings,
    after_commit: list,
) -> None:
    lines = (message.text or "").splitlines()
    await _finalize_link_import(
        message,
        state,
        session,
        admin,
        settings,
        after_commit,
        lines,
        source="paste",
    )


@router.message(AdminStates.import_links_paste, F.document)
async def msg_import_links_document(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
    settings: Settings,
    after_commit: list,
) -> None:
    doc = message.document
    if doc is None:
        return
    fname = (doc.file_name or "").lower()
    mime = (doc.mime_type or "").lower()
    looks_txt = fname.endswith(".txt")
    looks_plain = mime.startswith("text/plain") or mime == "application/octet-stream"
    if not looks_txt and not looks_plain:
        await message.answer(T.IMPORT_FILE_NOT_TEXT)
        return
    if doc.file_size is not None and doc.file_size > _MAX_IMPORT_FILE_BYTES:
        await message.answer(T.IMPORT_FILE_TOO_LARGE)
        return
    buf = io.BytesIO()
    await message.bot.download(doc, destination=buf, timeout=120)
    raw = buf.getvalue()
    if len(raw) > _MAX_IMPORT_FILE_BYTES:
        await message.answer(T.IMPORT_FILE_TOO_LARGE)
        return
    text, dec_err = _decode_import_file(raw)
    if dec_err or text is None:
        await message.answer(dec_err or T.IMPORT_FILE_DECODE_ERROR)
        return
    lines = text.splitlines()
    if not any(s.strip() for s in lines):
        await message.answer(T.IMPORT_FILE_EMPTY)
        return
    await _finalize_link_import(
        message,
        state,
        session,
        admin,
        settings,
        after_commit,
        lines,
        source="file",
    )


@router.callback_query(F.data == "adm:cards", IsOwner())
async def cb_cards(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    cards = (await session.execute(select(PaymentCard).order_by(PaymentCard.id))).scalars().all()
    if not cards:
        text = T.EMPTY_NO_CARDS
    else:
        blocks = ["💳 مدیریت کارت‌ها\n"]
        for c in cards:
            pub = "بله ✅" if c.is_public else "خیر ❌"
            act = "فعال ✅" if c.is_active else "غیرفعال ❌"
            blocks.append(
                f"\n💳 {c.card_number_masked}\n"
                f"🙎🏻‍♂️ {c.card_holder}\n"
                f"🏦 {c.bank_name}\n"
                f"وضعیت: {act}\n"
                f"نمایش به کاربران: {pub}"
            )
        text = "\n".join(blocks)
    kb = [
        [InlineKeyboardButton(text="➕ افزودن کارت", callback_data="adm:add_card")],
    ]
    for c in cards:
        if c.is_active:
            kb.append(
                [
                    InlineKeyboardButton(
                        text=f"کارت #{c.id}",
                        callback_data=f"adm:card:{c.id}",
                    )
                ]
            )
    kb.append([InlineKeyboardButton(text=T.ADM_BACK, callback_data="adm:cat_users")])
    await callback.message.edit_text(
        _afmt(settings, text), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )
    await callback.answer()


def _payment_card_detail_markup(c: PaymentCard) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    full_ok = bool((c.card_number_full or "").strip()) and len((c.card_number_full or "").strip()) >= 16
    if c.is_active:
        rows.append(
            [
                InlineKeyboardButton(
                    text="غیرفعال‌سازی",
                    callback_data=f"adm:card_deact_ask:{c.id}",
                ),
                InlineKeyboardButton(
                    text="ویرایش نام/بانک",
                    callback_data=f"adm:card_edit:{c.id}",
                ),
            ]
        )
        if not full_ok:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="✏️ تکمیل شماره کارت",
                        callback_data=f"adm:card_repair:{c.id}",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text="👁 تغییر نمایش عمومی",
                    callback_data=f"adm:card_toggle_pub:{c.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="بازگشت به لیست", callback_data="adm:cards")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _payment_card_detail_text(c: PaymentCard) -> str:
    num = card_display_number(c)
    warn = ""
    full = (c.card_number_full or "").strip()
    if not full or len(full) < 16 or "*" in num:
        warn = T.CARD_NUMBER_INCOMPLETE_ADMIN
    base = (
        f"کارت #{c.id}\n"
        f"شماره: {num}\n"
        f"صاحب: {c.card_holder}\n"
        f"بانک: {c.bank_name}\n"
        f"وضعیت: {'فعال ✅' if c.is_active else 'غیرفعال ❌'}\n"
        f"نمایش به کاربران: {'بله ✅' if c.is_public else 'خیر ❌'}"
    )
    if warn:
        return base + "\n\n" + warn
    return base + T.CARD_ADMIN_DETAIL_NOTE


@router.callback_query(F.data.startswith("adm:card:"), IsOwner())
async def cb_card_detail(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    cid = int(callback.data.split(":")[-1])
    c = await session.get(PaymentCard, cid)
    if c is None:
        await callback.answer("کارت یافت نشد.", show_alert=True)
        return
    await callback.message.edit_text(
        _afmt(settings, _payment_card_detail_text(c)),
        reply_markup=_payment_card_detail_markup(c),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:card_toggle_pub:"), IsOwner())
async def cb_card_toggle_pub(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    cid = int(callback.data.split(":")[-1])
    c = await session.get(PaymentCard, cid)
    if c is None:
        await callback.answer("کارت یافت نشد.", show_alert=True)
        return
    c.is_public = not c.is_public
    await session.flush()
    await callback.message.edit_text(
        _afmt(settings, _payment_card_detail_text(c)),
        reply_markup=_payment_card_detail_markup(c),
    )
    await callback.answer("ذخیره شد ✅")


@router.callback_query(F.data.startswith("adm:card_deact_ask:"), IsOwner())
async def cb_card_deact_ask(callback: CallbackQuery, session: AsyncSession) -> None:
    cid = int(callback.data.split(":")[-1])
    c = await session.get(PaymentCard, cid)
    if c is None or not c.is_active:
        await callback.answer("کارت نامعتبر است.", show_alert=True)
        return
    conf_id = await create_confirmation(
        session,
        admin_telegram_id=callback.from_user.id,
        action_type="deactivate_card",
        payload={"action": "deactivate_payment_card", "card_id": cid},
    )
    await callback.message.answer(
        "غیرفعال‌سازی این کارت؟ " + T.CONFIRM_PROMPT,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="تأیید", callback_data=f"acf:{conf_id}"),
                    InlineKeyboardButton(text="لغو", callback_data="noop"),
                ]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:card_repair:"), IsOwner())
async def cb_card_repair_start(callback: CallbackQuery, state: FSMContext) -> None:
    cid = int(callback.data.split(":")[-1])
    await state.update_data(repair_card_id=cid)
    await state.set_state(AdminStates.repair_card_number)
    await callback.message.answer(
        "شماره کارت کامل ۱۶ رقمی را وارد کنید:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.message(AdminStates.repair_card_number, F.text)
async def msg_repair_card_number(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
    settings: Settings,
) -> None:
    try:
        num = validate_card_number(message.text or "")
    except ValidationError as e:
        await message.answer(e.message_fa)
        return
    data = await state.get_data()
    cid = int(data.get("repair_card_id") or 0)
    c = await session.get(PaymentCard, cid)
    if c is None or not c.is_active:
        await message.answer("کارت نامعتبر است.")
        await state.clear()
        return
    c.card_number_full = num
    c.card_number_masked = num[:4] + "****" + num[-4:]
    await write_audit(
        session,
        actor_telegram_id=message.from_user.id,
        actor_role=admin.role.value,
        action="card_number_repaired",
        target_type="payment_card",
        target_id=str(cid),
    )
    await state.clear()
    await message.answer(
        "شماره کارت ذخیره شد.",
        reply_markup=await _admin_root_kb_async(session, admin, settings),
    )


@router.callback_query(F.data.startswith("adm:card_edit:"), IsOwner())
async def cb_card_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    cid = int(callback.data.split(":")[-1])
    await state.update_data(edit_card_id=cid)
    await state.set_state(AdminStates.edit_card_holder)
    await callback.message.answer(
        "نام جدید صاحب کارت:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.message(AdminStates.edit_card_holder, F.text)
async def msg_edit_card_holder(message: Message, state: FSMContext) -> None:
    try:
        h = validate_card_holder(message.text or "")
    except ValidationError as e:
        await message.answer(e.message_fa)
        return
    await state.update_data(edit_card_holder=h)
    await state.set_state(AdminStates.edit_card_bank)
    await message.answer("نام جدید بانک:")


@router.message(AdminStates.edit_card_bank, F.text)
async def msg_edit_card_bank(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
    settings: Settings,
) -> None:
    try:
        b = validate_bank_name(message.text or "")
    except ValidationError as e:
        await message.answer(e.message_fa)
        return
    data = await state.get_data()
    cid = int(data["edit_card_id"])
    c = await session.get(PaymentCard, cid)
    if c is None or not c.is_active:
        await message.answer("کارت نامعتبر است.")
        await state.clear()
        return
    c.card_holder = str(data["edit_card_holder"])
    c.bank_name = b
    await write_audit(
        session,
        actor_telegram_id=message.from_user.id,
        actor_role=admin.role.value,
        action="card_edited",
        target_type="payment_card",
        target_id=str(cid),
    )
    await state.clear()
    await message.answer(
        "کارت به‌روزرسانی شد.",
        reply_markup=await _admin_root_kb_async(session, admin, settings),
    )


@router.callback_query(F.data == "adm:add_card", IsOwner())
async def cb_add_card(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.add_card_number)
    await callback.message.answer(
        "شماره کارت ۱۶ رقمی:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.message(AdminStates.add_card_number, F.text)
async def msg_card_number(message: Message, state: FSMContext) -> None:
    try:
        num = validate_card_number(message.text or "")
    except ValidationError as e:
        await message.answer(e.message_fa)
        return
    await state.update_data(card_num=num)
    await state.set_state(AdminStates.add_card_holder)
    await message.answer("نام صاحب کارت:")


@router.message(AdminStates.add_card_holder, F.text)
async def msg_card_holder(message: Message, state: FSMContext) -> None:
    try:
        h = validate_card_holder(message.text or "")
    except ValidationError as e:
        await message.answer(e.message_fa)
        return
    await state.update_data(card_holder=h)
    await state.set_state(AdminStates.add_card_bank)
    await message.answer("نام بانک:")


@router.message(AdminStates.add_card_bank, F.text)
async def msg_card_bank(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
    settings: Settings,
) -> None:
    try:
        b = validate_bank_name(message.text or "")
    except ValidationError as e:
        await message.answer(e.message_fa)
        return
    data = await state.get_data()
    num: str = data["card_num"]
    masked = num[:4] + "****" + num[-4:]
    c = PaymentCard(
        card_number_masked=masked,
        card_number_full=num,
        card_holder=data["card_holder"],
        bank_name=b,
        is_public=True,
    )
    session.add(c)
    await session.flush()
    await write_audit(
        session,
        actor_telegram_id=message.from_user.id,
        actor_role=admin.role.value,
        action="card_added",
        target_type="payment_card",
        target_id=str(c.id),
    )
    await state.clear()
    await message.answer(
        "کارت ثبت شد.",
        reply_markup=await _admin_root_kb_async(session, admin, settings),
    )


@router.callback_query(F.data == "adm:users", IsManagerOrOwner())
async def cb_users(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "عملیات کاربر:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="بلاک با آیدی عددی تلگرام", callback_data="adm:block_ask")],
                [InlineKeyboardButton(text="رفع مسدودیت با آیدی تلگرام", callback_data="adm:unblock_ask")],
                [InlineKeyboardButton(text="بازگشت", callback_data="admin_home")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:block_ask", IsManagerOrOwner())
async def cb_block_ask(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.wallet_user_id)
    await state.update_data(block_mode=True)
    await callback.message.answer(
        "شناسه عددی تلگرام کاربر را بفرستید:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:unblock_ask", IsManagerOrOwner())
async def cb_unblock_ask(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.unblock_user_tid)
    await callback.message.answer(
        "شناسه عددی تلگرام کاربر برای رفع مسدودیت:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.message(AdminStates.unblock_user_tid, F.text)
async def msg_unblock_user_tid(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    try:
        tid = int((message.text or "").strip())
    except ValueError:
        await message.answer("شناسه نامعتبر است.")
        return
    cid = await create_confirmation(
        session,
        admin_telegram_id=message.from_user.id,
        action_type="unblock_user",
        payload={"action": "unblock_user", "telegram_id": tid},
    )
    await state.clear()
    await message.answer(
        T.CONFIRM_PROMPT,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="رفع مسدودیت", callback_data=f"acf:{cid}"),
                    InlineKeyboardButton(text="لغو", callback_data="noop"),
                ]
            ]
        ),
    )


@router.message(AdminStates.wallet_user_id, F.text)
async def msg_wallet_user_id(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
) -> None:
    data = await state.get_data()
    if data.get("block_mode"):
        try:
            tid = int((message.text or "").strip())
        except ValueError:
            await message.answer("شناسه نامعتبر است.")
            return
        cid = await create_confirmation(
            session,
            admin_telegram_id=message.from_user.id,
            action_type="block_user",
            payload={"action": "block_user", "telegram_id": tid},
        )
        await state.clear()
        await message.answer(
            T.CONFIRM_PROMPT,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="بلاک کن", callback_data=f"acf:{cid}"),
                        InlineKeyboardButton(text="لغو", callback_data="noop"),
                    ]
                ]
            ),
        )
        return

    try:
        tid = int((message.text or "").strip())
    except ValueError:
        await message.answer("شناسه نامعتبر است.")
        return
    u = (await session.execute(select(User).where(User.telegram_id == tid))).scalar_one_or_none()
    if u is None:
        await message.answer("کاربر یافت نشد.")
        await state.clear()
        return
    await state.update_data(wallet_user_db_id=u.id)
    await state.set_state(AdminStates.wallet_amount)
    await message.answer("مبلغ تغییر (مثبت یا منفی) به تومان:")


@router.message(AdminStates.wallet_amount, F.text)
async def msg_wallet_amount(message: Message, state: FSMContext) -> None:
    try:
        delta = int((message.text or "").strip().replace(",", ""))
    except ValueError:
        await message.answer("مبلغ نامعتبر است.")
        return
    await state.update_data(wallet_delta=delta)
    await state.set_state(AdminStates.wallet_reason)
    await message.answer("دلیل (الزامی):")


@router.message(AdminStates.wallet_reason, F.text)
async def msg_wallet_reason(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
    settings: Settings,
) -> None:
    try:
        reason = validate_reason(message.text or "")
    except ValidationError as e:
        await message.answer(e.message_fa)
        return
    data = await state.get_data()
    uid = int(data["wallet_user_db_id"])
    delta = int(data["wallet_delta"])
    cid = await create_confirmation(
        session,
        admin_telegram_id=message.from_user.id,
        action_type="wallet_adjust",
        payload={
            "action": "wallet_adjust",
            "user_id": uid,
            "delta": delta,
            "reason": reason,
        },
    )
    await state.clear()
    await message.answer(
        T.CONFIRM_PROMPT,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="اعمال", callback_data=f"acf:{cid}"),
                    InlineKeyboardButton(text="لغو", callback_data="noop"),
                ]
            ]
        ),
    )


@router.callback_query(F.data == "adm:wallet", IsManagerOrOwner())
async def cb_wallet_adjust(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(block_mode=False)
    await state.set_state(AdminStates.wallet_user_id)
    await callback.message.answer(
        "شناسه عددی تلگرام کاربر:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:backup_menu", IsManagerOrOwner())
async def cb_backup_menu(callback: CallbackQuery, admin: Admin) -> None:
    rows = [
        [
            InlineKeyboardButton(
                text="📊 گزارش فروش امروز", callback_data="adm:sales_report:today"
            )
        ],
    ]
    if admin.role == AdminRole.OWNER:
        rows.append(
            [InlineKeyboardButton(text="پشتیبان کامل (مالک)", callback_data="adm:backup_full")]
        )
    if admin.role in (AdminRole.OWNER, AdminRole.MANAGER):
        rows.append(
            [InlineKeyboardButton(text="گزارش متنی", callback_data="adm:backup_report")]
        )
    rows.append([InlineKeyboardButton(text="بازگشت", callback_data="admin_home")])
    await callback.message.edit_text("انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


def _sales_report_keyboard(period: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=T.BTN_SALES_REFRESH,
                    callback_data=f"adm:sales_report:{period}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=T.BTN_SALES_YESTERDAY,
                    callback_data="adm:sales_report:yesterday",
                ),
                InlineKeyboardButton(
                    text=T.BTN_SALES_MONTH,
                    callback_data="adm:sales_report:month",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📤 CSV خریدها",
                    callback_data=f"adm:sales_csv_purchases:{period}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📤 CSV پرداخت‌ها",
                    callback_data=f"adm:sales_csv_payments:{period}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📤 CSV سرویس‌ها",
                    callback_data="adm:sales_csv_services",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📤 CSV خلاصه گزارش",
                    callback_data=f"adm:sales_csv_summary:{period}",
                )
            ],
            [InlineKeyboardButton(text=T.ADM_BACK, callback_data="adm:backup_menu")],
        ]
    )


def _render_sales_report_text(
    settings: Settings,
    agg,
    *,
    period: str,
    window_label: str,
    jalali_date: str,
    pay,
    svc,
) -> str:
    from app.message_format import format_money_toman

    if period == "today":
        title = "📊 گزارش فروش امروز"
    elif period == "yesterday":
        title = "📊 گزارش فروش دیروز"
    elif period == "month":
        title = "📊 گزارش فروش این ماه"
    else:
        title = f"📊 گزارش فروش ({window_label})"
    lines = [
        title,
        "",
        f"📅 {jalali_date}",
        "⏰ بازهٔ گزارش: " + window_label,
        "",
        f"📦 حجم فروخته‌شده: {agg.total_gb:g} گیگ",
        f"🛒 سفارش‌ها: {agg.total_orders}",
        f"💵 فروش: {format_money_toman(agg.total_revenue)} تومان",
        "",
    ]
    if agg.total_orders == 0:
        lines.append("(در این بازه سفارش تکمیل‌شده‌ای ثبت نشده است.)")
        lines.append("")
    else:
        lines.append("جزئیات پلن:")
        for lbl, (cnt, rev) in sorted(agg.by_plan_label.items()):
            lines.append(f"▫️ {lbl}: {cnt} سفارش | {format_money_toman(rev)} تومان")
        lines.append("")
        lines.append("🌐 لوکیشن‌ها:")
        for sname, (gb, cnt, rev) in sorted(agg.by_server.items()):
            lines.append(
                f"▫️ {sname}: {gb:g} گیگ | {cnt} سفارش | {format_money_toman(rev)} تومان"
            )
        lines.append("")
    lines.extend(
        [
            "",
            "💳 پرداخت‌های تاییدشده:",
            f"▫️ تعداد: {pay.approved_count}",
            f"▫️ مجموع شارژ کیف پول: {format_money_toman(pay.approved_amount)} تومان",
            "",
            "📦 وضعیت سرویس‌ها:",
            f"🟢 فعال: {svc.active}",
            f"🟡 محدود شده: {svc.limited}",
            f"🔴 منقضی: {svc.expired}",
            f"⚫ غیرفعال: {svc.disabled}",
            f"❌ خطادار: {svc.error}",
            "",
            "👤 کاربران:",
            f"▫️ خرید کاربران: {agg.user_channel_gb:g} گیگ | {agg.user_channel_orders} سفارش",
            f"▫️ تحویل ادمین‌ها: {agg.admin_channel_gb:g} گیگ | {agg.admin_channel_orders} مورد",
        ]
    )
    return "\n".join(lines)


def _sales_window_for_period(settings: Settings, period: str):
    if period == "yesterday":
        return window_yesterday(settings)
    if period == "month":
        return window_this_month(settings)
    return window_today(settings)


@router.callback_query(
    F.data.in_(
        {
            "adm:sales_csv:today",
            "adm:sales_csv:yesterday",
            "adm:sales_csv:month",
        }
    ),
    IsManagerOrOwner(),
)
async def cb_sales_csv_legacy(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    period = callback.data.split(":")[-1]
    w = _sales_window_for_period(settings, period)
    csv_s = await export_purchases_csv(session, start_utc=w.start_utc, end_utc=w.end_utc)
    doc = BufferedInputFile(csv_s.encode("utf-8-sig"), filename=f"purchases_{period}.csv")
    await callback.message.answer_document(
        doc,
        caption=_afmt(settings, f"CSV خریدها — {w.label_fa}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:sales_csv_purchases:"), IsManagerOrOwner())
async def cb_sales_csv_purchases(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    period = callback.data.split(":")[-1]
    w = _sales_window_for_period(settings, period)
    csv_s = await export_purchases_csv(session, start_utc=w.start_utc, end_utc=w.end_utc)
    doc = BufferedInputFile(csv_s.encode("utf-8-sig"), filename=f"purchases_{period}.csv")
    await callback.message.answer_document(
        doc,
        caption=_afmt(settings, f"CSV خریدها — {w.label_fa}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:sales_csv_payments:"), IsManagerOrOwner())
async def cb_sales_csv_payments(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    period = callback.data.split(":")[-1]
    w = _sales_window_for_period(settings, period)
    csv_s = await export_payments_csv(session, start_utc=w.start_utc, end_utc=w.end_utc)
    doc = BufferedInputFile(csv_s.encode("utf-8-sig"), filename=f"payments_{period}.csv")
    await callback.message.answer_document(
        doc,
        caption=_afmt(settings, f"CSV پرداخت‌های تاییدشده — {w.label_fa}"),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:sales_csv_services", IsManagerOrOwner())
async def cb_sales_csv_services(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    csv_s = await export_services_csv(session)
    doc = BufferedInputFile(csv_s.encode("utf-8-sig"), filename="services_all.csv")
    await callback.message.answer_document(
        doc,
        caption=_afmt(settings, "CSV وضعیت سرویس‌ها"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:sales_csv_summary:"), IsManagerOrOwner())
async def cb_sales_csv_summary(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    from app.message_format import format_jalali_date_only

    period = callback.data.split(":")[-1]
    w = _sales_window_for_period(settings, period)
    if period == "yesterday":
        jd = format_jalali_date_only(settings, (datetime.now(tz=UTC) - timedelta(days=1)))
    else:
        jd = format_jalali_date_only(settings, datetime.now(tz=UTC))
    csv_s = await export_daily_report_csv(
        session,
        start_utc=w.start_utc,
        end_utc=w.end_utc,
        jalali_date=jd,
        window_label=w.label_fa,
    )
    doc = BufferedInputFile(csv_s.encode("utf-8-sig"), filename=f"report_summary_{period}.csv")
    await callback.message.answer_document(
        doc,
        caption=_afmt(settings, f"CSV خلاصه — {w.label_fa}"),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:traffic_sync_status", IsManagerOrOwner())
async def cb_traffic_sync_status(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    body = await build_traffic_sync_status_text(session)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 سینک دستی الان", callback_data="adm:traffic_sync_now")],
            [InlineKeyboardButton(text=T.ADM_BACK, callback_data="adm:cat_mgmt")],
        ]
    )
    await callback.message.edit_text(_afmt(settings, body), reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "adm:traffic_sync_now", IsManagerOrOwner())
async def cb_traffic_sync_now(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    after_commit: list,
) -> None:
    bot = callback.bot

    async def _run() -> None:
        from app.db.base import get_session_factory

        fac = get_session_factory(settings.database_url)
        async with fac() as s2:
            r = await run_traffic_sync_cycle(s2, settings, bot=bot)
            await s2.commit()
        await callback.message.answer(
            _afmt(
                settings,
                f"✅ سینک دستی انجام شد.\nپردازش‌شده: {r.get('processed', 0)}\nخطا: {r.get('errors', 0)}",
            )
        )

    after_commit.append(_run)
    await callback.answer("در حال اجرا…")


@router.callback_query(F.data.startswith("adm:sales_report:"), IsManagerOrOwner())
async def cb_sales_report(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    from app.message_format import format_jalali_date_only

    period = callback.data.split(":", 2)[-1]
    if period == "yesterday":
        w = window_yesterday(settings)
        jd = format_jalali_date_only(
            settings, (datetime.now(tz=UTC) - timedelta(days=1))
        )
    elif period == "month":
        w = window_this_month(settings)
        jd = format_jalali_date_only(settings, datetime.now(tz=UTC))
    else:
        period = "today"
        w = window_today(settings)
        jd = format_jalali_date_only(settings, datetime.now(tz=UTC))
    agg = await aggregate_sales(session, start_utc=w.start_utc, end_utc=w.end_utc)
    pay = await aggregate_payments_approved(session, start_utc=w.start_utc, end_utc=w.end_utc)
    svc = await count_user_services_by_status(session)
    body = _render_sales_report_text(
        settings,
        agg,
        period=period,
        window_label=w.label_fa,
        jalali_date=jd,
        pay=pay,
        svc=svc,
    )
    await callback.message.edit_text(
        _afmt(settings, body),
        reply_markup=_sales_report_keyboard(period),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:backup_report", IsManagerOrOwner())
async def cb_backup_report(
    callback: CallbackQuery,
    session: AsyncSession,
    admin: Admin,
    after_commit: list,
) -> None:
    users_c = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    pr_c = (await session.execute(select(func.count()).select_from(Purchase))).scalar_one()
    await write_audit(
        session,
        actor_telegram_id=callback.from_user.id,
        actor_role=admin.role.value,
        action="backup_report_exported",
        metadata={"users": int(users_c or 0), "purchases": int(pr_c or 0)},
    )
    text = f"گزارش: users={users_c} purchases={pr_c}"
    bot = callback.bot
    tid = callback.from_user.id

    async def _send() -> None:
        await bot.send_message(tid, text)

    after_commit.append(_send)
    await callback.answer("ارسال شد.")


@router.callback_query(F.data == "adm:backup_full", IsOwner())
async def cb_backup_full(
    callback: CallbackQuery,
    session: AsyncSession,
    admin: Admin,
    settings: Settings,
    after_commit: list,
) -> None:
    cid = await create_confirmation(
        session,
        admin_telegram_id=callback.from_user.id,
        action_type="backup_full",
        payload={"action": "backup_full"},
    )
    await callback.message.answer(
        "پشتیبان کامل حاوی داده حساس است. تأیید؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="تأیید", callback_data=f"acf:{cid}"),
                    InlineKeyboardButton(text="لغو", callback_data="noop"),
                ]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:admins", IsOwner())
async def cb_admins(callback: CallbackQuery, session: AsyncSession) -> None:
    rows = (await session.execute(select(Admin))).scalars().all()
    lines = [f"#{a.id} tid={a.telegram_id} role={a.role.value} active={a.is_active}" for a in rows]
    text = "ادمین‌ها:\n" + "\n".join(lines)
    kb = [
        [InlineKeyboardButton(text="افزودن ادمین", callback_data="adm:add_admin")],
        [InlineKeyboardButton(text="بازگشت", callback_data="admin_home")],
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()


@router.callback_query(F.data == "adm:refund", IsOwner())
async def cb_refund_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.refund_purchase_id)
    await callback.message.answer(
        "شناسه خرید (purchase id) را بفرستید:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.message(AdminStates.refund_purchase_id, F.text)
async def msg_refund_purchase_id(message: Message, state: FSMContext) -> None:
    try:
        pid = int((message.text or "").strip())
    except ValueError:
        await message.answer("شناسه نامعتبر است.")
        return
    await state.update_data(refund_purchase_id=pid)
    await state.set_state(AdminStates.refund_reason)
    await message.answer("دلیل بازپرداخت (الزامی):")


@router.message(AdminStates.refund_reason, F.text)
async def msg_refund_reason(message: Message, state: FSMContext) -> None:
    try:
        reason = validate_reason(message.text or "")
    except ValidationError as e:
        await message.answer(e.message_fa)
        return
    await state.update_data(refund_reason=reason)
    await state.set_state(AdminStates.refund_return)
    await message.answer("آیا لینک به حالت بازگشتی برگردد؟ پاسخ «بله» یا «خیر»:")


@router.message(AdminStates.refund_return, F.text)
async def msg_refund_return(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
) -> None:
    t = (message.text or "").strip()
    if t not in ("بله", "خیر"):
        await message.answer("فقط «بله» یا «خیر» وارد کنید.")
        return
    return_link = t == "بله"
    data = await state.get_data()
    pid = int(data["refund_purchase_id"])
    reason = str(data["refund_reason"])
    cid = await create_confirmation(
        session,
        admin_telegram_id=message.from_user.id,
        action_type="refund",
        payload={
            "action": "refund_purchase",
            "purchase_id": pid,
            "reason": reason,
            "return_link": return_link,
        },
    )
    await state.clear()
    await message.answer(
        T.CONFIRM_PROMPT,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="تأیید بازپرداخت", callback_data=f"acf:{cid}"),
                    InlineKeyboardButton(text="لغو", callback_data="noop"),
                ]
            ]
        ),
    )


@router.callback_query(F.data == "adm:add_admin", IsOwner())
async def cb_add_admin(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.add_admin_tid)
    await callback.message.answer(
        "شناسه تلگرام ادمین جدید:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.message(AdminStates.add_admin_tid, F.text)
async def msg_add_admin_tid(message: Message, state: FSMContext) -> None:
    try:
        tid = int((message.text or "").strip())
    except ValueError:
        await message.answer("شناسه نامعتبر است.")
        return
    await state.update_data(new_admin_tid=tid)
    await state.set_state(AdminStates.add_admin_role)
    await message.answer(
        "نقش را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="manager", callback_data="adm:role:manager"),
                    InlineKeyboardButton(text="seller", callback_data="adm:role:seller"),
                ]
            ]
        ),
    )


@router.callback_query(F.data.startswith("adm:role:"), IsOwner())
async def cb_add_admin_role(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
) -> None:
    role_s = callback.data.split(":")[-1]
    role = AdminRole.MANAGER if role_s == "manager" else AdminRole.SELLER
    data = await state.get_data()
    tid = int(data["new_admin_tid"])
    existing = await get_admin_by_telegram(session, tid)
    if existing:
        existing.role = role
        existing.is_active = True
    else:
        session.add(Admin(telegram_id=tid, role=role, is_active=True))
    await write_audit(
        session,
        actor_telegram_id=callback.from_user.id,
        actor_role=admin.role.value,
        action="admin_role_changed",
        target_type="admin",
        target_id=str(tid),
        metadata={"role": role.value},
    )
    await state.clear()
    await callback.message.answer("ثبت شد.", reply_markup=_admin_root_kb(admin))
    await callback.answer()


@router.message(AdminStates.reject_reason, F.text)
async def msg_reject_reason(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
) -> None:
    try:
        reason = validate_reason(message.text or "")
    except ValidationError as e:
        await message.answer(e.message_fa)
        return
    data = await state.get_data()
    pr_id = int(data.get("reject_pr_id") or 0)
    cid = await create_confirmation(
        session,
        admin_telegram_id=message.from_user.id,
        action_type="reject_payment",
        payload={"action": "reject_payment_final", "pr_id": pr_id, "reason": reason},
    )
    await state.clear()
    await message.answer(
        T.CONFIRM_PROMPT,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="تأیید رد", callback_data=f"acf:{cid}"),
                    InlineKeyboardButton(text="لغو", callback_data="noop"),
                ]
            ]
        ),
    )


admin_cf_router = Router(name="admin_cf")


@admin_cf_router.callback_query(F.data.startswith("acf:"), IsAdmin())
async def cb_confirm_admin_actions(
    callback: CallbackQuery,
    session: AsyncSession,
    admin: Admin,
    settings: Settings,
    after_commit: list,
) -> None:
    cid = int(callback.data.split(":", 1)[1])
    payload = await take_confirmation_if_valid(
        session,
        confirmation_id=cid,
        admin_telegram_id=callback.from_user.id,
    )
    if payload is None:
        await callback.answer(T.CONFIRM_EXPIRED, show_alert=True)
        return
    action = str(payload.get("action"))

    if action == "reject_payment_final":
        if admin.role not in (AdminRole.OWNER, AdminRole.MANAGER):
            await callback.answer(T.UNAUTHORIZED, show_alert=True)
            return
        pr_id = int(payload["pr_id"])
        reason = str(payload["reason"])
        pr, err = await reject_payment_request(
            session, request_id=pr_id, reviewer=admin, reason=reason
        )
        if err or pr is None:
            await callback.answer(err or T.GENERIC_ERROR, show_alert=True)
            return
        u = await session.get(User, pr.user_id)
        user_tid = u.telegram_id if u else None
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            actor_role=admin.role.value,
            action="payment_rejected",
            target_type="payment_request",
            target_id=str(pr_id),
            metadata={"reason": reason},
        )
        bot = callback.bot

        async def _notify() -> None:
            if user_tid:
                try:
                    await bot.send_message(
                        user_tid,
                        T.PAYMENT_REJECTED_USER.format(reason=reason),
                    )
                except Exception:
                    logger.exception("notify user reject failed")

        after_commit.append(_notify)
        await callback.answer("رد شد.")
        return

    if action == "block_user":
        if admin.role not in (AdminRole.OWNER, AdminRole.MANAGER):
            await callback.answer(T.UNAUTHORIZED, show_alert=True)
            return
        tid = int(payload["telegram_id"])
        if tid == settings.owner_telegram_id:
            await callback.answer("نمی‌توان مالک را مسدود کرد.", show_alert=True)
            return
        u = (await session.execute(select(User).where(User.telegram_id == tid))).scalar_one_or_none()
        if u:
            u.is_blocked = True
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            actor_role=admin.role.value,
            action="user_blocked",
            target_type="user",
            target_id=str(u.id) if u else str(tid),
        )
        await callback.answer("انجام شد.")
        return

    if action == "unblock_user":
        if admin.role not in (AdminRole.OWNER, AdminRole.MANAGER):
            await callback.answer(T.UNAUTHORIZED, show_alert=True)
            return
        tid = int(payload["telegram_id"])
        u = (await session.execute(select(User).where(User.telegram_id == tid))).scalar_one_or_none()
        if u:
            u.is_blocked = False
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            actor_role=admin.role.value,
            action="user_unblocked",
            target_type="user",
            target_id=str(u.id) if u else str(tid),
        )
        await callback.answer("رفع مسدودیت انجام شد.")
        return

    if action == "grant_card_view":
        if admin.role not in (AdminRole.OWNER, AdminRole.MANAGER):
            await callback.answer(T.UNAUTHORIZED, show_alert=True)
            return
        uid = int(payload["user_db_id"])
        u = await session.get(User, uid)
        if u is None:
            await callback.answer("کاربر یافت نشد.", show_alert=True)
            return
        if u.is_blocked:
            await callback.answer("کاربر مسدود است.", show_alert=True)
            return
        u.card_view_allowed = True
        u.card_payment_enabled = True
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            actor_role=admin.role.value,
            action="card_view_granted",
            target_type="user",
            target_id=str(u.id),
            metadata={"via": payload.get("via")},
        )
        bot = callback.bot
        user_tid = u.telegram_id

        async def _notify_grant() -> None:
            try:
                await bot.send_message(user_tid, T.CARD_ACCESS_GRANTED_USER)
            except Exception:
                logger.exception("notify grant card view failed")

        after_commit.append(_notify_grant)
        await callback.answer("دسترسی کارت فعال شد.")
        return

    if action == "revoke_card_view":
        if admin.role not in (AdminRole.OWNER, AdminRole.MANAGER):
            await callback.answer(T.UNAUTHORIZED, show_alert=True)
            return
        uid = int(payload["user_db_id"])
        u = await session.get(User, uid)
        if u is None:
            await callback.answer("کاربر یافت نشد.", show_alert=True)
            return
        u.card_view_allowed = False
        u.card_payment_enabled = False
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            actor_role=admin.role.value,
            action="card_view_revoked",
            target_type="user",
            target_id=str(u.id),
        )
        bot = callback.bot
        user_tid = u.telegram_id

        async def _notify_revoke() -> None:
            try:
                await bot.send_message(user_tid, T.CARD_ACCESS_REVOKED_USER)
            except Exception:
                logger.exception("notify revoke card view failed")

        after_commit.append(_notify_revoke)
        await callback.answer("دسترسی کارت قطع شد.")
        return

    if action == "deactivate_payment_card":
        if admin.role != AdminRole.OWNER:
            await callback.answer(T.UNAUTHORIZED, show_alert=True)
            return
        card_id = int(payload["card_id"])
        card = await session.get(PaymentCard, card_id)
        if card:
            card.is_active = False
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            actor_role=admin.role.value,
            action="card_deactivated",
            target_type="payment_card",
            target_id=str(card_id),
        )
        await callback.answer("کارت غیرفعال شد.")
        return

    if action == "wallet_adjust":
        if admin.role not in (AdminRole.OWNER, AdminRole.MANAGER):
            await callback.answer(T.UNAUTHORIZED, show_alert=True)
            return
        uid = int(payload["user_id"])
        delta = int(payload["delta"])
        reason = str(payload["reason"])
        u = await session.get(User, uid)
        if u is None:
            await callback.answer("کاربر یافت نشد.", show_alert=True)
            return
        ok, err = await manual_adjust_wallet(
            session,
            admin=admin,
            user=u,
            delta=delta,
            reason=reason,
            large_threshold=settings.large_wallet_adjustment_amount,
        )
        if not ok:
            await callback.answer(err or T.GENERIC_ERROR, show_alert=True)
            return
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            actor_role=admin.role.value,
            action="wallet_adjusted",
            target_type="user",
            target_id=str(uid),
            metadata={"delta": delta},
        )
        await callback.answer("اعمال شد.")
        return

    if action == "backup_full":
        if admin.role != AdminRole.OWNER:
            await callback.answer(T.BACKUP_OWNER_ONLY, show_alert=True)
            return
        content, fname, err = await export_full_backup_bytes(settings.database_url)
        if err:
            await callback.answer(err, show_alert=True)
            return
        path = write_temp_backup_file(content, os.path.splitext(fname)[1] or ".bin")
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            actor_role=admin.role.value,
            action="backup_exported",
            metadata={"filename": fname},
        )
        bot = callback.bot
        tid = callback.from_user.id

        async def _send_file() -> None:
            try:
                await bot.send_document(
                    tid,
                    document=FSInputFile(path, filename=fname),
                    caption=fname,
                )
            finally:
                try:
                    os.remove(path)
                except OSError:
                    logger.warning("temp backup remove failed")

        after_commit.append(_send_file)
        await callback.answer("ارسال می‌شود.")
        return

    if action == "refund_purchase":
        if admin.role != AdminRole.OWNER:
            await callback.answer(T.UNAUTHORIZED, show_alert=True)
            return
        pid = int(payload["purchase_id"])
        reason = str(payload["reason"])
        return_link = bool(payload.get("return_link"))
        ok, err = await refund_purchase(
            session,
            admin=admin,
            purchase_id=pid,
            reason=reason,
            return_link=return_link,
        )
        if not ok:
            await callback.answer(err or T.GENERIC_ERROR, show_alert=True)
            return
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            actor_role=admin.role.value,
            action="purchase_refunded",
            target_type="purchase",
            target_id=str(pid),
            metadata={"return_link": return_link},
        )
        await callback.answer(T.REFUND_OK)
        return

    if action == "deactivate_plan":
        if admin.role not in (AdminRole.OWNER, AdminRole.MANAGER):
            await callback.answer(T.UNAUTHORIZED, show_alert=True)
            return
        pid = int(payload["plan_id"])
        p = await session.get(Plan, pid)
        if p:
            p.is_active = False
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            actor_role=admin.role.value,
            action="plan_deactivated",
            target_type="plan",
            target_id=str(pid),
        )
        await callback.answer("پلن غیرفعال شد.")
        return

    if action == "deactivate_server":
        if admin.role not in (AdminRole.OWNER, AdminRole.MANAGER):
            await callback.answer(T.UNAUTHORIZED, show_alert=True)
            return
        sid = int(payload["server_id"])
        s = await session.get(Server, sid)
        if s:
            s.is_active = False
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            actor_role=admin.role.value,
            action="server_deactivated",
            target_type="server",
            target_id=str(sid),
        )
        await callback.answer("سرور غیرفعال شد.")
        return

    if action == "delete_unused_links":
        if admin.role not in (AdminRole.OWNER, AdminRole.MANAGER):
            await callback.answer(T.UNAUTHORIZED, show_alert=True)
            return
        sid = int(payload["server_id"])
        pid = int(payload["plan_id"])
        n = await delete_unused_links(session, server_id=sid, plan_id=pid)
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            actor_role=admin.role.value,
            action="links_deleted_unused",
            target_type="plan",
            target_id=str(pid),
            metadata={"count": n},
        )
        bot = callback.bot

        async def _stock_del() -> None:
            await run_stock_check_after_commit(
                settings.database_url, settings, bot, plan_id=pid
            )

        after_commit.append(_stock_del)
        await callback.answer(f"حذف شد: {n}")
        return

    if action == "return_link":
        if admin.role != AdminRole.OWNER:
            await callback.answer(T.UNAUTHORIZED, show_alert=True)
            return
        lid = int(payload["link_id"])
        lk = await session.get(Link, lid)
        plan_id_for_stock = lk.plan_id if lk else 0
        ok, err = await return_link(session, link_id=lid)
        if not ok:
            await callback.answer(err or T.GENERIC_ERROR, show_alert=True)
            return
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            actor_role=admin.role.value,
            action="link_returned",
            target_type="link",
            target_id=str(lid),
        )
        bot = callback.bot

        async def _stock_ret() -> None:
            if plan_id_for_stock:
                await run_stock_check_after_commit(
                    settings.database_url, settings, bot, plan_id=plan_id_for_stock
                )

        after_commit.append(_stock_ret)
        await callback.answer("لینک بازگردانده شد.")
        return

    if action == "remove_admin":
        if admin.role != AdminRole.OWNER:
            await callback.answer(T.UNAUTHORIZED, show_alert=True)
            return
        tid = int(payload["telegram_id"])
        if tid == settings.owner_telegram_id:
            await callback.answer("غیرمجاز.", show_alert=True)
            return
        a = (
            await session.execute(select(Admin).where(Admin.telegram_id == tid))
        ).scalar_one_or_none()
        if a:
            a.is_active = False
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            actor_role=admin.role.value,
            action="admin_removed",
            target_type="admin",
            target_id=str(tid),
        )
        await callback.answer("ادمین حذف شد.")
        return

    await callback.answer(T.CONFIRM_EXPIRED, show_alert=True)
