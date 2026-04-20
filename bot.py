#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot for 3x-ui Panel Management
========================================
Features:
  - Create 1-200 VLESS services at once
  - Set volume limit (GB) - 0 = unlimited
  - Set expiry (days) - 0 = never expires
  - Automatically send share links to a Telegram group/channel
  - View, delete, reset traffic for clients
  - Server status overview
"""

import asyncio
import io
import json
import logging
import os
import time
from typing import Optional

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

try:
    import qrcode
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False

from xui_api import XUIClient

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Config from environment
# ──────────────────────────────────────────────────────────────────────────────

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_IDS: list[int] = [
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
]
XUI_URL: str = os.getenv("XUI_URL", "http://localhost:54321")
XUI_USER: str = os.getenv("XUI_USER", "admin")
XUI_PASS: str = os.getenv("XUI_PASS", "admin")
XUI_INBOUND_ID: int = int(os.getenv("XUI_INBOUND_ID", "1"))
SERVER_HOST: str = os.getenv("SERVER_HOST", "")
TARGET_CHAT_ID: str = os.getenv("TARGET_CHAT_ID", "")
VERIFY_SSL: bool = os.getenv("VERIFY_SSL", "false").lower() == "true"

# ──────────────────────────────────────────────────────────────────────────────
# Conversation states
# ──────────────────────────────────────────────────────────────────────────────
(
    STATE_MAIN,
    STATE_CREATE_COUNT,
    STATE_CREATE_EMAIL_PREFIX,
    STATE_CREATE_GB,
    STATE_CREATE_DAYS,
    STATE_CREATE_CONFIRM,
    STATE_DELETE_EMAIL,
    STATE_RESET_EMAIL,
) = range(8)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_xui() -> XUIClient:
    return XUIClient(XUI_URL, XUI_USER, XUI_PASS, verify_ssl=VERIFY_SSL)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def bytes_to_human(b: int) -> str:
    if b == 0:
        return "∞ (نامحدود)"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def ts_to_date(ts_ms: int) -> str:
    if ts_ms == 0:
        return "هرگز (نامحدود)"
    return time.strftime("%Y-%m-%d", time.localtime(ts_ms / 1000))


def build_main_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("➕ ساخت سرویس", callback_data="create"),
            InlineKeyboardButton("📋 لیست کلاینت‌ها", callback_data="list"),
        ],
        [
            InlineKeyboardButton("🗑 حذف کلاینت", callback_data="delete"),
            InlineKeyboardButton("🔄 ریست ترافیک", callback_data="reset"),
        ],
        [
            InlineKeyboardButton("📊 وضعیت سرور", callback_data="status"),
            InlineKeyboardButton("📶 ترافیک کلاینت", callback_data="traffic"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str = "منو اصلی:"):
    kb = build_main_keyboard()
    if update.message:
        await update.message.reply_text(text, reply_markup=kb)
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)


# ──────────────────────────────────────────────────────────────────────────────
# Guard: admin only
# ──────────────────────────────────────────────────────────────────────────────

async def admin_guard(update: Update) -> bool:
    user = update.effective_user
    if not user or not is_admin(user.id):
        if update.message:
            await update.message.reply_text("⛔ دسترسی ندارید.")
        elif update.callback_query:
            await update.callback_query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return False
    return True


# ──────────────────────────────────────────────────────────────────────────────
# /start
# ──────────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return ConversationHandler.END
    context.user_data.clear()
    await send_main_menu(update, context, "👋 سلام! به ربات مدیریت x-ui خوش آمدید.")
    return STATE_MAIN


# ──────────────────────────────────────────────────────────────────────────────
# Main menu callback dispatch
# ──────────────────────────────────────────────────────────────────────────────

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await admin_guard(update):
        return STATE_MAIN

    action = query.data

    if action == "create":
        await query.edit_message_text(
            "🔢 چند تا سرویس می‌خواهید بسازید؟ (۱ تا ۲۰۰)\n\n"
            "مثال: <code>5</code>",
            parse_mode=ParseMode.HTML,
        )
        return STATE_CREATE_COUNT

    if action == "list":
        await handle_list(update, context)
        return STATE_MAIN

    if action == "delete":
        await query.edit_message_text(
            "📧 ایمیل (نام) کلاینتی که می‌خواهید حذف کنید را وارد کنید:\n"
            "(یا /cancel برای لغو)"
        )
        return STATE_DELETE_EMAIL

    if action == "reset":
        await query.edit_message_text(
            "📧 ایمیل (نام) کلاینتی که می‌خواهید ترافیکش را ریست کنید:\n"
            "(یا /cancel برای لغو)"
        )
        return STATE_RESET_EMAIL

    if action == "status":
        await handle_status(update, context)
        return STATE_MAIN

    if action == "traffic":
        await query.edit_message_text(
            "📧 ایمیل کلاینت را برای مشاهده ترافیک وارد کنید:\n"
            "(یا /cancel برای لغو)"
        )
        context.user_data["traffic_mode"] = True
        return STATE_DELETE_EMAIL  # reuse state for email input

    if action == "back":
        await send_main_menu(update, context)
        return STATE_MAIN

    return STATE_MAIN


# ──────────────────────────────────────────────────────────────────────────────
# Create flow
# ──────────────────────────────────────────────────────────────────────────────

async def create_get_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return ConversationHandler.END
    text = update.message.text.strip()
    if text == "/cancel":
        await send_main_menu(update, context, "❌ لغو شد.")
        return STATE_MAIN
    try:
        count = int(text)
        assert 1 <= count <= 200
    except Exception:
        await update.message.reply_text("❌ عدد بین ۱ تا ۲۰۰ وارد کنید:")
        return STATE_CREATE_COUNT
    context.user_data["count"] = count
    await update.message.reply_text(
        f"✅ {count} سرویس\n\n"
        "📝 پیشوند ایمیل (نام) سرویس‌ها را وارد کنید.\n"
        "مثال: <code>user</code>  →  user1, user2, ...\n"
        "(یا /cancel برای لغو)",
        parse_mode=ParseMode.HTML,
    )
    return STATE_CREATE_EMAIL_PREFIX


async def create_get_prefix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return ConversationHandler.END
    text = update.message.text.strip()
    if text == "/cancel":
        await send_main_menu(update, context, "❌ لغو شد.")
        return STATE_MAIN
    if not text or " " in text:
        await update.message.reply_text("❌ پیشوند معتبر نیست (فاصله نداشته باشد):")
        return STATE_CREATE_EMAIL_PREFIX
    context.user_data["prefix"] = text
    await update.message.reply_text(
        "📦 حجم هر سرویس را به گیگابایت وارد کنید.\n"
        "<b>0 = نامحدود</b>\n"
        "مثال: <code>10</code> یا <code>0</code>\n"
        "(یا /cancel برای لغو)",
        parse_mode=ParseMode.HTML,
    )
    return STATE_CREATE_GB


async def create_get_gb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return ConversationHandler.END
    text = update.message.text.strip()
    if text == "/cancel":
        await send_main_menu(update, context, "❌ لغو شد.")
        return STATE_MAIN
    try:
        gb = float(text)
        assert gb >= 0
    except Exception:
        await update.message.reply_text("❌ عدد معتبر وارد کنید (مثلاً 10 یا 0):")
        return STATE_CREATE_GB
    context.user_data["gb"] = gb
    await update.message.reply_text(
        "⏳ مدت اعتبار هر سرویس را به روز وارد کنید.\n"
        "<b>0 = نامحدود (بدون تاریخ انقضا)</b>\n"
        "مثال: <code>30</code> یا <code>0</code>\n"
        "(یا /cancel برای لغو)",
        parse_mode=ParseMode.HTML,
    )
    return STATE_CREATE_DAYS


async def create_get_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return ConversationHandler.END
    text = update.message.text.strip()
    if text == "/cancel":
        await send_main_menu(update, context, "❌ لغو شد.")
        return STATE_MAIN
    try:
        days = int(text)
        assert days >= 0
    except Exception:
        await update.message.reply_text("❌ عدد صحیح غیر منفی وارد کنید:")
        return STATE_CREATE_DAYS
    context.user_data["days"] = days

    count = context.user_data["count"]
    prefix = context.user_data["prefix"]
    gb = context.user_data["gb"]
    gb_label = "نامحدود" if gb == 0 else f"{gb} GB"
    days_label = "نامحدود" if days == 0 else f"{days} روز"

    summary = (
        f"📋 <b>خلاصه سفارش:</b>\n\n"
        f"🔢 تعداد: <b>{count}</b>\n"
        f"📝 پیشوند: <b>{prefix}</b>\n"
        f"📦 حجم: <b>{gb_label}</b>\n"
        f"⏳ مدت: <b>{days_label}</b>\n\n"
        f"آیا تایید می‌کنید؟"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ بله، بساز", callback_data="confirm_create"),
            InlineKeyboardButton("❌ لغو", callback_data="back"),
        ]
    ])
    await update.message.reply_text(summary, reply_markup=kb, parse_mode=ParseMode.HTML)
    return STATE_CREATE_CONFIRM


async def create_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await admin_guard(update):
        return STATE_MAIN

    if query.data != "confirm_create":
        await send_main_menu(update, context, "❌ لغو شد.")
        return STATE_MAIN

    count: int = context.user_data["count"]
    prefix: str = context.user_data["prefix"]
    gb: float = context.user_data["gb"]
    days: int = context.user_data["days"]

    await query.edit_message_text("⏳ در حال ساخت سرویس‌ها، لطفاً صبر کنید...")

    xui = get_xui()
    if not xui.login():
        await query.edit_message_text("❌ خطا در اتصال به x-ui. اطلاعات را بررسی کنید.")
        return STATE_MAIN

    clients = []
    for i in range(1, count + 1):
        email = f"{prefix}{i}"
        clients.append(XUIClient.build_vless_client(email, total_gb=gb, expire_days=days))

    # Add in batches of 10 to avoid potential payload limits
    batch_size = 10
    success_count = 0
    for batch_start in range(0, count, batch_size):
        batch = clients[batch_start: batch_start + batch_size]
        if xui.add_clients(XUI_INBOUND_ID, batch):
            success_count += len(batch)

    if success_count == 0:
        await query.edit_message_text("❌ ساخت سرویس‌ها با خطا مواجه شد.")
        return STATE_MAIN

    # Build share links
    inbound = xui.get_inbound(XUI_INBOUND_ID)
    links_text_parts = []
    for i in range(success_count):
        c = clients[i]
        link = xui.build_vless_link(inbound, c, SERVER_HOST, remark=c["email"]) if inbound else None
        gb_label = "نامحدود" if gb == 0 else f"{gb} GB"
        days_label = "نامحدود" if days == 0 else f"{days} روز"
        part = (
            f"🔑 <b>{c['email']}</b>\n"
            f"📦 حجم: {gb_label} | ⏳ مدت: {days_label}\n"
        )
        if link:
            part += f"<code>{link}</code>"
        links_text_parts.append(part)

    # Send confirmation to admin
    await query.edit_message_text(
        f"✅ <b>{success_count}</b> سرویس با موفقیت ساخته شد!\n\n"
        "در حال ارسال لینک‌ها...",
        parse_mode=ParseMode.HTML,
    )

    # Send to target group (if configured) in chunks of 5
    target = TARGET_CHAT_ID or context.user_data.get("target_chat")
    if target:
        chunk_size = 5
        for chunk_start in range(0, len(links_text_parts), chunk_size):
            chunk = links_text_parts[chunk_start: chunk_start + chunk_size]
            msg = "\n\n".join(chunk)
            try:
                await context.bot.send_message(
                    chat_id=target,
                    text=msg,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as exc:
                logger.error("Error sending to target chat: %s", exc)
            await asyncio.sleep(0.5)

    # Also send back to admin in chunks
    for chunk_start in range(0, len(links_text_parts), 5):
        chunk = links_text_parts[chunk_start: chunk_start + 5]
        msg = "\n\n".join(chunk)
        try:
            await query.message.reply_text(msg, parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.error("Error sending links to admin: %s", exc)
        await asyncio.sleep(0.3)

    await send_main_menu(update, context, "✅ عملیات کامل شد.")
    return STATE_MAIN


# ──────────────────────────────────────────────────────────────────────────────
# Delete client
# ──────────────────────────────────────────────────────────────────────────────

async def delete_get_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return ConversationHandler.END
    text = update.message.text.strip()
    if text == "/cancel":
        await send_main_menu(update, context, "❌ لغو شد.")
        return STATE_MAIN

    # Traffic mode reuse
    if context.user_data.get("traffic_mode"):
        context.user_data.pop("traffic_mode", None)
        xui = get_xui()
        if not xui.login():
            await update.message.reply_text("❌ خطا در اتصال به x-ui.")
            await send_main_menu(update, context)
            return STATE_MAIN
        info = xui.get_client_traffic(text)
        if not info:
            await update.message.reply_text(f"❌ کلاینت «{text}» یافت نشد.")
        else:
            up = bytes_to_human(info.get("up", 0))
            down = bytes_to_human(info.get("down", 0))
            total = bytes_to_human(info.get("total", 0))
            exp = ts_to_date(info.get("expiryTime", 0))
            en = "✅ فعال" if info.get("enable") else "❌ غیرفعال"
            await update.message.reply_text(
                f"📊 <b>ترافیک کلاینت: {text}</b>\n\n"
                f"⬆️ آپلود: {up}\n"
                f"⬇️ دانلود: {down}\n"
                f"📦 کل: {total}\n"
                f"📅 انقضا: {exp}\n"
                f"وضعیت: {en}",
                parse_mode=ParseMode.HTML,
            )
        await send_main_menu(update, context)
        return STATE_MAIN

    xui = get_xui()
    if not xui.login():
        await update.message.reply_text("❌ خطا در اتصال به x-ui.")
        await send_main_menu(update, context)
        return STATE_MAIN

    # Find client UUID
    clients = xui.list_clients(XUI_INBOUND_ID)
    client = next((c for c in clients if c.get("email") == text), None)
    if not client:
        await update.message.reply_text(f"❌ کلاینت «{text}» یافت نشد.")
        await send_main_menu(update, context)
        return STATE_MAIN

    ok = xui.delete_client(XUI_INBOUND_ID, client["id"])
    if ok:
        await update.message.reply_text(f"✅ کلاینت «{text}» با موفقیت حذف شد.")
    else:
        await update.message.reply_text(f"❌ خطا در حذف کلاینت «{text}».")
    await send_main_menu(update, context)
    return STATE_MAIN


# ──────────────────────────────────────────────────────────────────────────────
# Reset traffic
# ──────────────────────────────────────────────────────────────────────────────

async def reset_get_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return ConversationHandler.END
    text = update.message.text.strip()
    if text == "/cancel":
        await send_main_menu(update, context, "❌ لغو شد.")
        return STATE_MAIN
    xui = get_xui()
    if not xui.login():
        await update.message.reply_text("❌ خطا در اتصال به x-ui.")
        await send_main_menu(update, context)
        return STATE_MAIN
    ok = xui.reset_client_traffic(XUI_INBOUND_ID, text)
    if ok:
        await update.message.reply_text(f"✅ ترافیک کلاینت «{text}» ریست شد.")
    else:
        await update.message.reply_text(f"❌ خطا در ریست ترافیک «{text}».")
    await send_main_menu(update, context)
    return STATE_MAIN


# ──────────────────────────────────────────────────────────────────────────────
# List clients
# ──────────────────────────────────────────────────────────────────────────────

async def handle_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    xui = get_xui()
    if not xui.login():
        await query.edit_message_text("❌ خطا در اتصال به x-ui.")
        return
    clients = xui.list_clients(XUI_INBOUND_ID)
    if not clients:
        await query.edit_message_text("📭 هیچ کلاینتی یافت نشد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="back")]]))
        return
    lines = [f"📋 <b>کلاینت‌ها ({len(clients)} عدد):</b>\n"]
    for c in clients[:50]:  # cap at 50 in message
        en = "✅" if c.get("enable", True) else "❌"
        exp = ts_to_date(c.get("expiryTime", 0))
        total = bytes_to_human(c.get("totalGB", 0))
        lines.append(f"{en} <code>{c.get('email', '?')}</code> | {total} | {exp}")
    if len(clients) > 50:
        lines.append(f"\n... و {len(clients) - 50} کلاینت دیگر")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="back")]])
    await query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode=ParseMode.HTML)


# ──────────────────────────────────────────────────────────────────────────────
# Server status
# ──────────────────────────────────────────────────────────────────────────────

async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    xui = get_xui()
    if not xui.login():
        await query.edit_message_text("❌ خطا در اتصال به x-ui.")
        return
    status = xui.server_status()
    if not status:
        await query.edit_message_text(
            "❌ اطلاعات سرور در دسترس نیست.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="back")]]),
        )
        return

    cpu = status.get("cpu", 0)
    mem = status.get("mem", {})
    mem_used = bytes_to_human(mem.get("current", 0))
    mem_total = bytes_to_human(mem.get("total", 0))
    disk = status.get("disk", {})
    disk_used = bytes_to_human(disk.get("current", 0))
    disk_total = bytes_to_human(disk.get("total", 0))
    uptime = status.get("uptime", 0)
    uptime_h = uptime // 3600
    uptime_m = (uptime % 3600) // 60
    net_io = status.get("netIO", {})
    up_speed = bytes_to_human(net_io.get("up", 0))
    down_speed = bytes_to_human(net_io.get("down", 0))

    text = (
        f"📊 <b>وضعیت سرور</b>\n\n"
        f"🖥 CPU: <b>{cpu:.1f}%</b>\n"
        f"🧠 RAM: <b>{mem_used} / {mem_total}</b>\n"
        f"💾 دیسک: <b>{disk_used} / {disk_total}</b>\n"
        f"⏰ آپتایم: <b>{uptime_h}h {uptime_m}m</b>\n"
        f"⬆️ آپلود: <b>{up_speed}/s</b>\n"
        f"⬇️ دانلود: <b>{down_speed}/s</b>"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="back")]])
    await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


# ──────────────────────────────────────────────────────────────────────────────
# /cancel anywhere
# ──────────────────────────────────────────────────────────────────────────────

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await send_main_menu(update, context, "❌ لغو شد.")
    return STATE_MAIN


# ──────────────────────────────────────────────────────────────────────────────
# /settarget  - change target group at runtime
# ──────────────────────────────────────────────────────────────────────────────

async def cmd_set_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "استفاده: <code>/settarget -100xxxxxxxxx</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    context.user_data["target_chat"] = args[0]
    # Also update global for this process session
    global TARGET_CHAT_ID
    TARGET_CHAT_ID = args[0]
    await update.message.reply_text(f"✅ چت هدف تنظیم شد: <code>{args[0]}</code>", parse_mode=ParseMode.HTML)


# ──────────────────────────────────────────────────────────────────────────────
# /help
# ──────────────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return
    text = (
        "📖 <b>راهنمای ربات مدیریت x-ui</b>\n\n"
        "/start — منو اصلی\n"
        "/settarget &lt;chat_id&gt; — تنظیم گروه/کانال هدف برای ارسال لینک‌ها\n"
        "/cancel — لغو عملیات فعلی\n"
        "/help — این راهنما\n\n"
        "📌 قابلیت‌ها:\n"
        "• ساخت ۱ تا ۲۰۰ سرویس VLESS به صورت دسته‌ای\n"
        "• تنظیم حجم (۰ = نامحدود)\n"
        "• تنظیم مدت اعتبار (۰ = نامحدود)\n"
        "• ارسال خودکار لینک به گروه تلگرام\n"
        "• مشاهده لیست کلاینت‌ها\n"
        "• حذف کلاینت\n"
        "• ریست ترافیک\n"
        "• مشاهده ترافیک مصرفی\n"
        "• وضعیت سرور"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ──────────────────────────────────────────────────────────────────────────────
# Application setup
# ──────────────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN تنظیم نشده است. فایل .env را بررسی کنید.")
    if not ADMIN_IDS:
        raise SystemExit("❌ ADMIN_IDS تنظیم نشده است. شناسه ادمین را وارد کنید.")

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            STATE_MAIN: [
                CallbackQueryHandler(main_menu_callback),
            ],
            STATE_CREATE_COUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_get_count),
                CommandHandler("cancel", cmd_cancel),
            ],
            STATE_CREATE_EMAIL_PREFIX: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_get_prefix),
                CommandHandler("cancel", cmd_cancel),
            ],
            STATE_CREATE_GB: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_get_gb),
                CommandHandler("cancel", cmd_cancel),
            ],
            STATE_CREATE_DAYS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_get_days),
                CommandHandler("cancel", cmd_cancel),
            ],
            STATE_CREATE_CONFIRM: [
                CallbackQueryHandler(create_confirm, pattern="^confirm_create$"),
                CallbackQueryHandler(main_menu_callback, pattern="^back$"),
            ],
            STATE_DELETE_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, delete_get_email),
                CommandHandler("cancel", cmd_cancel),
            ],
            STATE_RESET_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reset_get_email),
                CommandHandler("cancel", cmd_cancel),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start", cmd_start),
        ],
        allow_reentry=True,
        per_message=False,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("settarget", cmd_set_target))
    app.add_handler(CommandHandler("help", cmd_help))

    logger.info("ربات شروع به کار کرد...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
