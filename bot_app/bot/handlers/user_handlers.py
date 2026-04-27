"""User-facing handlers."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot_app.bot.handlers.common import get_admin, get_or_create_user
from bot_app.bot.keyboards import back_kb, main_user_kb
from bot_app.bot.states import PurchaseStates, SupportStates, WalletStates
from bot_app.config import get_settings
from bot_app.db.models import (
    ManualDelivery,
    ManualPlan,
    ManualServer,
    Panel,
    Plan,
    Server,
    SupportTicket,
    UserService,
)
from bot_app.services.api_purchase import execute_api_purchase_saga
from bot_app.utils.jalali_format import format_copyable_code, format_gb, format_jalali_datetime, format_message, format_money

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, session):
    settings = get_settings()
    u = await get_or_create_user(session, message.from_user)
    await session.commit()
    text = (
        f"به {settings.brand_name} خوش آمدید.\n\n"
        "از منوی زیر گزینه مورد نظر را انتخاب کنید."
    )
    await message.answer(format_message(text), reply_markup=main_user_kb())


@router.message(F.text == "🏠 منوی اصلی")
async def home(message: Message):
    await message.answer(format_message("منوی اصلی"), reply_markup=main_user_kb())


@router.message(F.text == "🛒 خرید سرویس")
async def shop_start(message: Message, session, state: FSMContext):
    settings = get_settings()
    if not settings.api_products_enabled:
        await message.answer(format_message("خرید سرویس در حال حاضر غیرفعال است."))
        return
    q = await session.execute(
        select(Server)
        .where(Server.is_active.is_(True), Server.is_visible_to_users.is_(True))
        .order_by(Server.id.asc())
    )
    servers = q.scalars().all()
    if not servers:
        await message.answer(format_message("در حال حاضر لوکیشنی برای فروش وجود ندارد."))
        return
    lines = ["لوکیشن مورد نظر را انتخاب کنید (شماره بفرستید):"]
    for i, s in enumerate(servers, 1):
        lines.append(f"{i}) {s.name} — {s.location_label}")
    await state.set_state(PurchaseStates.choosing_server)
    await state.update_data(servers=[s.id for s in servers])
    await message.answer(format_message("\n".join(lines)), reply_markup=back_kb())


@router.message(PurchaseStates.choosing_server, F.text.regexp(r"^\d+$"))
async def shop_server(message: Message, session, state: FSMContext):
    data = await state.get_data()
    ids = data.get("servers") or []
    idx = int(message.text) - 1
    if idx < 0 or idx >= len(ids):
        await message.answer(format_message("شماره نامعتبر است."))
        return
    server_id = ids[idx]
    server = (await session.execute(select(Server).where(Server.id == server_id))).scalar_one()
    q = await session.execute(
        select(Plan)
        .where(
            Plan.server_id == server_id,
            Plan.is_active.is_(True),
            Plan.is_visible_to_users.is_(True),
        )
        .order_by(Plan.id.asc())
    )
    plans = q.scalars().all()
    if not plans:
        await message.answer(format_message("پلنی برای این لوکیشن وجود ندارد."))
        return
    lines = ["پلن را با شماره انتخاب کنید:"]
    for i, p in enumerate(plans, 1):
        lines.append(f"{i}) {p.display_name} — {format_money(int(p.price))} تومان")
    await state.update_data(server_id=server_id, plans=[p.id for p in plans])
    await state.set_state(PurchaseStates.choosing_plan)
    await message.answer(format_message("\n".join(lines)))


@router.message(PurchaseStates.choosing_plan, F.text.regexp(r"^\d+$"))
async def shop_plan(message: Message, session, state: FSMContext):
    data = await state.get_data()
    ids = data.get("plans") or []
    idx = int(message.text) - 1
    if idx < 0 or idx >= len(ids):
        await message.answer(format_message("شماره نامعتبر است."))
        return
    plan_id = ids[idx]
    await state.update_data(plan_id=plan_id)
    await state.set_state(PurchaseStates.custom_name)
    await message.answer(format_message("نام دلخواه برای این سرویس را بنویسید (حداکثر ۶۰ کاراکتر):"))


@router.message(PurchaseStates.custom_name)
async def shop_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()[:60]
    if len(name) < 2:
        await message.answer(format_message("نام کوتاه است."))
        return
    await state.update_data(custom_name=name)
    data = await state.get_data()
    await state.set_state(PurchaseStates.confirm)
    await message.answer(
        format_message(
            "تأیید خرید:\n"
            f"نام سرویس: {name}\n"
            "برای تأیید «بله» و برای انصراف «خیر» بفرستید."
        )
    )


@router.message(PurchaseStates.confirm, F.text.in_({"بله", "خیر"}))
async def shop_confirm(message: Message, session, state: FSMContext):
    if message.text == "خیر":
        await state.clear()
        await message.answer(format_message("خرید لغو شد."), reply_markup=main_user_kb())
        return
    settings = get_settings()
    data = await state.get_data()
    plan = (await session.execute(select(Plan).where(Plan.id == data["plan_id"]))).scalar_one()
    server = (await session.execute(select(Server).where(Server.id == data["server_id"]))).scalar_one()
    panel = (await session.execute(select(Panel).where(Panel.id == server.panel_id))).scalar_one()
    user = await get_or_create_user(session, message.from_user)
    rid = str(uuid.uuid4())[:12]
    ok, key, extra = await execute_api_purchase_saga(
        session,
        settings=settings,
        user=user,
        plan=plan,
        server=server,
        panel=panel,
        custom_service_name=data["custom_name"],
        price=int(plan.price),
        request_id=rid,
    )
    if not ok:
        await session.rollback()
        await message.answer(format_message("خرید انجام نشد. لطفاً با پشتیبانی تماس بگیرید."))
        return
    await session.commit()
    await state.clear()
    url = extra["stable_subscription_url"]
    body = (
        "✅ سرویس شما فعال شد\n\n"
        f"📝 نام سرویس: {extra['custom_service_name']}\n"
        f"🆔 کد سرویس: {format_copyable_code(extra['public_service_code'])}\n"
        f"📦 سرویس: {extra['plan_display_name']}\n"
        f"🌐 لوکیشن: {extra['server_name']}\n"
        f"💵 مبلغ پرداختی: {format_money(int(extra['price']))} تومان\n"
        f"🧾 شماره سفارش: #{extra['purchase_id']}\n"
        f"🕒 زمان خرید: {format_jalali_datetime()}\n\n"
        "🔗 لینک اشتراک ثابت:\n"
        f"{format_copyable_code(url)}\n\n"
        "📌 این لینک همیشه ثابت است.\n"
        "اگر لوکیشن تغییر کرد، فقط داخل برنامه Update Subscription بزنید."
    )
    await message.answer(format_message(body), reply_markup=main_user_kb())


@router.message(F.text == "📦 سرویس‌های من")
async def my_services(message: Message, session):
    u = await get_or_create_user(session, message.from_user)
    api_rows = (
        await session.execute(select(UserService).where(UserService.user_id == u.id).order_by(UserService.id.desc()))
    ).scalars().all()
    manual_rows = (
        await session.execute(
            select(ManualDelivery)
            .where(ManualDelivery.user_id == u.id, ManualDelivery.status == "delivered")
            .order_by(ManualDelivery.id.desc())
        )
    ).scalars().all()

    parts = ["📦 سرویس‌های من\n", "🟢 سرویس‌های اشتراکی\n"]
    for i, us in enumerate(api_rows[:10], 1):
        srv = (await session.execute(select(Server).where(Server.id == us.current_server_id))).scalar_one()
        pln = (await session.execute(select(Plan).where(Plan.id == us.plan_id))).scalar_one()
        parts.append(
            f"{i}) 🟢 {us.custom_service_name}\n"
            f"🆔 کد: {format_copyable_code(us.public_service_code)}\n"
            f"📦 {pln.display_name}\n"
            f"🌐 {srv.name}\n"
            f"✅ باقی‌مانده: {format_gb(int(us.remaining_traffic_bytes))} گیگ\n"
        )
    parts.append("\n⚪ سرویس‌های دستی\n")
    for i, d in enumerate(manual_rows[:10], 1):
        ms = (await session.execute(select(ManualServer).where(ManualServer.id == d.manual_server_id))).scalar_one()
        mp = (await session.execute(select(ManualPlan).where(ManualPlan.id == d.manual_plan_id))).scalar_one()
        parts.append(
            f"{i}) ⚪ {mp.display_name}\n"
            f"🧾 تحویل: #{d.id}\n"
            f"🌐 {ms.name}\n"
            f"📅 تاریخ: {format_jalali_datetime(d.delivered_at)}\n"
        )
    await message.answer(format_message("\n".join(parts)))


@router.message(F.text == "💳 کیف پول من")
async def wallet_menu(message: Message, session):
    u = await get_or_create_user(session, message.from_user)
    await message.answer(
        format_message(
            f"موجودی کیف پول شما:\n{format_money(int(u.wallet_balance))} تومان\n\n"
            "برای شارژ، مبلغ را به تومان بنویسید."
        )
    )


@router.message(F.text == "🎫 پشتیبانی")
async def support_menu(message: Message):
    await message.answer(format_message("پیام خود را برای پشتیبانی بنویسید."))
    # state could be set here for rate limiting in production


async def _open_admin_panel(message: Message, session) -> None:
    a = await get_admin(session, message.from_user.id)
    if not a:
        await message.answer(format_message("دسترسی ندارید."))
        return
    from bot_app.bot.keyboards import admin_panel_kb

    s = get_settings()
    lines = [
        "پنل مدیریت",
        "",
        "با دکمهٔ «باز کردن پنل مدیریت» وارد داشبورد شوید.",
        "منوی دکمه‌های زیر همان پنل قبلی (متنی) است.",
    ]
    if s.admin_webapp_enabled and s.webapp_entry_url and s.webapp_entry_url.startswith("https://"):
        ikb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="باز کردن پنل مدیریت", web_app=WebAppInfo(url=s.webapp_entry_url))],
            ]
        )
        await message.answer(
            format_message("\n".join(lines)),
            reply_markup=ikb,
        )
        await message.answer(
            format_message("یا از منوی زیر استفاده کنید:"),
            reply_markup=admin_panel_kb(s.manual_mode_enabled),
        )
    elif s.admin_webapp_enabled and s.webapp_entry_url and not s.webapp_entry_url.startswith("https://"):
        await message.answer(
            format_message(
                "پنل مدیریت (وب)\n\n"
                "تنظیم لازم: فقط با آدرس HTTPS (برای مینی‌اپ) کار می‌کند.\n"
                "مقدار PUBLIC_BASE_URL یا WEBAPP_PUBLIC_BASE_URL را روی https://... تنظیم کنید و nginx را به این سرور وصل کنید."
            ),
            reply_markup=admin_panel_kb(s.manual_mode_enabled),
        )
    elif s.admin_webapp_enabled and not s.webapp_entry_url:
        await message.answer(
            format_message(
                "پنل مدیریت (وب) فعال است اما آدرس عمومی تنظیم نشده.\n"
                "در .env مقدار PUBLIC_BASE_URL (یا WEBAPP_PUBLIC_BASE_URL) را بگذارید."
            ),
            reply_markup=admin_panel_kb(s.manual_mode_enabled),
        )
    else:
        await message.answer(
            format_message("پنل مدیریت (منوی دکمه‌ای)"),
            reply_markup=admin_panel_kb(s.manual_mode_enabled),
        )


@router.message(Command("admin"))
async def admin_command(message: Message, session):
    """Open admin panel (same as button). Owner is auto-added to admins on first bot start."""
    await _open_admin_panel(message, session)


@router.message(F.text == "🛠 پنل مدیریت")
async def admin_gate(message: Message, session):
    await _open_admin_panel(message, session)
