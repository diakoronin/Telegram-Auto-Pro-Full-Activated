"""Admin handlers (subset wired to menus)."""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.types import Message

from sqlalchemy import func, select, text

from bot_app.bot.handlers.common import get_admin, is_owner_or_manager
from bot_app.bot.keyboards import admin_panel_kb, main_user_kb
from bot_app.config import get_settings
from bot_app.db.models import ManualPlan, ManualServer, Purchase, User, UserService
from bot_app.services.manual_links import bulk_import_links, deliver_one_link, parse_import_lines
from bot_app.services.traffic_sync import sync_batch

router = Router()


@router.message(F.text == "📦 فروش و موجودی")
async def admin_sales(message: Message, session):
    if not await get_admin(session, message.from_user.id):
        return
    await message.answer(
        "زیرمنو:\n"
        "🌐 پنل‌ها و لوکیشن‌های API\n"
        "📦 پلن‌های API\n"
        "📦 فروش دستی و لینک‌ها\n"
        "از دکمه‌های قبلی یا پنل مدیریت استفاده کنید."
    )


@router.message(F.text == "🔄 وضعیت سینک مصرف")
async def admin_sync_status(message: Message, session):
    admin = await get_admin(session, message.from_user.id)
    if not admin or not is_owner_or_manager(admin):
        await message.answer("دسترسی ندارید.")
        return
    active = (
        await session.execute(select(func.count()).select_from(UserService).where(UserService.status == "active"))
    ).scalar_one()
    limited = (
        await session.execute(select(func.count()).select_from(UserService).where(UserService.status == "limited"))
    ).scalar_one()
    await message.answer(f"سرویس‌های فعال: {active}\nمحدود شده: {limited}")


@router.message(F.text == "📥 ایمپورت لینک TXT")
async def admin_import_prompt(message: Message, session):
    admin = await get_admin(session, message.from_user.id)
    if not admin:
        return
    settings = get_settings()
    if not settings.manual_mode_enabled:
        await message.answer("حالت دستی غیرفعال است.")
        return
    await message.answer(
        "متن لینک‌ها را بفرستید.\n"
        "خط اول: شناسه سرور دستی و پلن با کاما، مثال:\n"
        "3,5\n"
        "از خط دوم به بعد هر خط یک لینک."
    )


@router.message(F.text.regexp(r"^\d+,\d+\s*\n"))
async def admin_import_bulk(message: Message, session):
    admin = await get_admin(session, message.from_user.id)
    if not admin:
        return
    settings = get_settings()
    lines = message.text.splitlines()
    if not lines:
        return
    head = lines[0].replace(" ", "")
    try:
        sid_s, pid_s = head.split(",")
        sid, pid = int(sid_s), int(pid_s)
    except Exception:
        await message.answer("خط اول باید دو عدد با کاما باشد، مثال: 3,5")
        return
    body = "\n".join(lines[1:])
    parsed = parse_import_lines(body, settings.max_import_links)
    rid = str(uuid.uuid4())[:12]
    stats = await bulk_import_links(
        session,
        lines=parsed,
        manual_server_id=sid,
        manual_plan_id=pid,
        admin_db_id=admin.id,
        max_links=settings.max_import_links,
        max_link_length=4096,
        request_id=rid,
    )
    await session.commit()
    await message.answer(
        "✅ ایمپورت لینک‌ها انجام شد\n\n"
        f"📥 دریافت‌شده: {stats['total']}\n"
        f"✅ اضافه‌شده: {stats['added']}\n"
        f"🔁 تکراری در فایل: {stats['duplicate_in_file']}\n"
        f"🔁 تکراری در دیتابیس: {stats['duplicate_in_db']}\n"
        f"❌ نامعتبر: {stats['invalid']}"
    )


@router.message(F.text == "🛒 تحویل دستی")
async def admin_deliver_help(message: Message, session):
    admin = await get_admin(session, message.from_user.id)
    if not admin:
        return
    await message.answer("فرمت: تحویل server_id=X plan_id=Y tg=123456789 یا tg=0 برای فقط ادمین")


@router.message(F.text.startswith("تحویل "))
async def admin_deliver(message: Message, session):
    admin = await get_admin(session, message.from_user.id)
    if not admin:
        return
    parts = dict(p.split("=") for p in message.text.split()[1:])
    sid = int(parts.get("server_id", 0))
    pid = int(parts.get("plan_id", 0))
    tg = int(parts.get("tg", 0))
    rid = str(uuid.uuid4())[:12]
    ok, key, data = await deliver_one_link(
        session,
        manual_server_id=sid,
        manual_plan_id=pid,
        admin_db_id=admin.id,
        user_telegram_id=tg or None,
        customer_info=None,
        request_id=rid,
    )
    if not ok:
        await session.rollback()
        await message.answer("تحویل انجام نشد (موجودی یا خطا).")
        return
    await session.commit()
    await message.answer(
        "✅ سرویس دستی تحویل داده شد\n\n"
        f"📦 سرویس: {data['manual_plan_name']}\n"
        f"🌐 سرور: {data['manual_server_name']}\n"
        f"🧾 شماره تحویل: #{data['delivery_id']}\n\n"
        f"🔗 لینک:\n<code>{data['link']}</code>\n\n"
        "⚠️ لینک بالا را لمس کنید تا کپی شود.",
        parse_mode="HTML",
    )


@router.message(F.text == "🩺 Health Check")
async def admin_health(message: Message, session):
    admin = await get_admin(session, message.from_user.id)
    if not admin:
        return
    try:
        await session.execute(text("SELECT 1"))
        await message.answer("پایگاه داده: سالم")
    except Exception as e:
        await message.answer(f"خطا: {e}")
