"""Main Telegram bot module.

Run with:
    python -m xui_bot.bot

Environment variables (see .env.example):
    TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_IDS, XUI_BASE_URL, XUI_USERNAME,
    XUI_PASSWORD, XUI_WEB_BASE_PATH, XUI_INSECURE_TLS, DEFAULT_SEND_CHAT_ID.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

from .config import config
from .link_builder import build_link
from .store import Store
from .utils import (
    days_from_now_ms,
    format_expiry,
    human_bytes,
    sanitize_prefix,
    server_host_from_base,
)
from .xui_client import XUIClient, XUIError, build_client_object

log = logging.getLogger(__name__)


(
    STATE_BULK_PICK_INBOUND,
    STATE_BULK_COUNT,
    STATE_BULK_GB,
    STATE_BULK_DAYS,
    STATE_BULK_PREFIX,
    STATE_BULK_TARGET,
    STATE_BULK_CONFIRM,
) = range(7)


STATE_SINGLE_PICK_INBOUND = 100
STATE_SINGLE_EMAIL = 101
STATE_SINGLE_GB = 102
STATE_SINGLE_DAYS = 103


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    return user.id in set(config.telegram_admin_ids)


async def _deny(update: Update) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "⛔ دسترسی ندارید. آیدی عددی شما باید در TELEGRAM_ADMIN_IDS قرار بگیرد."
        )


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 لیست اینباندها", callback_data="inbounds")],
            [InlineKeyboardButton("➕ ساخت تکی", callback_data="single_new")],
            [InlineKeyboardButton("🧰 ساخت انبوه (۱ تا ۲۰۰)", callback_data="bulk_new")],
            [InlineKeyboardButton("🗂 کارهای اخیر", callback_data="jobs")],
            [InlineKeyboardButton("ℹ️ راهنما", callback_data="help")],
        ]
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return await _deny(update)
    user = update.effective_user
    await update.effective_message.reply_text(
        f"👋 سلام {user.first_name or ''}\n"
        "به ربات مدیریت x-ui خوش آمدید. یکی از گزینه‌های زیر را انتخاب کنید:",
        reply_markup=main_menu_kb(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return await _deny(update)
    text = (
        "<b>راهنما</b>\n\n"
        "• <b>ساخت انبوه</b>: بین ۱ تا ۲۰۰ سرویس روی یک اینباند می‌سازد. حجم بر اساس <b>GB</b> "
        "و مدت بر حسب <b>روز</b> از لحظهٔ ساخت محاسبه می‌شود.\n"
        "• وارد کردن <b>0</b> برای حجم یا روز یعنی <b>نامحدود</b>.\n"
        "• در انتها می‌توانید لینک‌ها را برای یک گروه/کانال تلگرامی ارسال کنید؛ "
        "ربات باید در آن گروه/کانال ادمین باشد.\n\n"
        "<b>فرمت تارگت گروه</b>: آیدی عددی مثل <code>-1001234567890</code> یا یوزرنیم مثل <code>@my_channel</code>.\n\n"
        "<b>دستورات</b>:\n"
        "/start — منوی اصلی\n"
        "/inbounds — لیست اینباندها\n"
        "/bulk — شروع ساخت انبوه\n"
        "/new — ساخت تکی\n"
        "/cancel — لغو گفت‌وگوی فعلی"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def _get_xui(context: ContextTypes.DEFAULT_TYPE) -> XUIClient:
    client: Optional[XUIClient] = context.application.bot_data.get("xui")
    if client is None:
        client = XUIClient(
            base_url=config.xui_base_url,
            username=config.xui_username,
            password=config.xui_password,
            web_base_path=config.web_base_path,
            verify_tls=not config.xui_insecure_tls,
        )
        await client.login()
        context.application.bot_data["xui"] = client
    return client


def _store(context: ContextTypes.DEFAULT_TYPE) -> Store:
    store: Optional[Store] = context.application.bot_data.get("store")
    if store is None:
        store = Store(config.db_path)
        context.application.bot_data["store"] = store
    return store


async def cmd_inbounds(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return await _deny(update)
    try:
        xui = await _get_xui(context)
        inbounds = await xui.list_inbounds()
    except XUIError as exc:
        await update.effective_message.reply_text(f"❌ خطا در پنل: {exc}")
        return
    if not inbounds:
        await update.effective_message.reply_text("اینباندی پیدا نشد.")
        return
    lines: List[str] = ["<b>📋 اینباندها</b>"]
    for ib in inbounds:
        clients = 0
        settings = ib.get("settings")
        if isinstance(settings, str):
            try:
                import json

                clients = len((json.loads(settings) or {}).get("clients") or [])
            except Exception:
                clients = 0
        lines.append(
            f"• <b>#{ib.get('id')}</b> | {ib.get('remark') or '-'} | "
            f"<code>{ib.get('protocol')}</code> | پورت <code>{ib.get('port')}</code> | "
            f"کاربران: {clients}"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def _inbound_keyboard(context: ContextTypes.DEFAULT_TYPE, prefix: str) -> Optional[InlineKeyboardMarkup]:
    try:
        xui = await _get_xui(context)
        inbounds = await xui.list_inbounds()
    except XUIError:
        return None
    if not inbounds:
        return None
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for ib in inbounds:
        label = f"#{ib.get('id')} {ib.get('remark') or ib.get('protocol')}"
        row.append(InlineKeyboardButton(label[:32], callback_data=f"{prefix}:{ib.get('id')}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ لغو", callback_data=f"{prefix}:cancel")])
    return InlineKeyboardMarkup(rows)


async def bulk_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        await _deny(update)
        return ConversationHandler.END
    kb = await _inbound_keyboard(context, "bulkib")
    if kb is None:
        await update.effective_message.reply_text("❌ نمی‌توانم اینباندها را از پنل بگیرم.")
        return ConversationHandler.END
    await update.effective_message.reply_text(
        "🧰 <b>ساخت انبوه</b>\nاینباندی که روی آن می‌سازیم را انتخاب کنید:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
    context.user_data["bulk"] = {}
    return STATE_BULK_PICK_INBOUND


async def bulk_ib_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data.endswith(":cancel"):
        await query.edit_message_text("❌ لغو شد.")
        return ConversationHandler.END
    _, ib_id = data.split(":", 1)
    context.user_data["bulk"]["inbound_id"] = int(ib_id)
    await query.edit_message_text(
        f"✅ اینباند #{ib_id} انتخاب شد.\n\n"
        "چند سرویس ساخته شود؟ (عدد بین <b>1</b> تا <b>200</b>)",
        parse_mode=ParseMode.HTML,
    )
    return STATE_BULK_COUNT


async def bulk_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    try:
        n = int(text)
    except ValueError:
        await update.effective_message.reply_text("لطفاً یک عدد صحیح بین 1 تا 200 بفرستید.")
        return STATE_BULK_COUNT
    if n < 1 or n > 200:
        await update.effective_message.reply_text("خارج از بازه. عدد بین 1 تا 200 باشد.")
        return STATE_BULK_COUNT
    context.user_data["bulk"]["count"] = n
    await update.effective_message.reply_text(
        "حجم هر سرویس چقدر باشد؟ (به <b>GB</b>)\n"
        "<b>0</b> یعنی حجم نامحدود.",
        parse_mode=ParseMode.HTML,
    )
    return STATE_BULK_GB


async def bulk_gb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    try:
        gb = int(text)
    except ValueError:
        await update.effective_message.reply_text("لطفاً یک عدد صحیح بفرستید. (0 برای نامحدود)")
        return STATE_BULK_GB
    if gb < 0:
        await update.effective_message.reply_text("عدد مثبت یا صفر بفرستید.")
        return STATE_BULK_GB
    context.user_data["bulk"]["total_gb"] = gb
    await update.effective_message.reply_text(
        "مدت زمان هر سرویس چند روز باشد؟\n"
        "<b>0</b> یعنی زمان نامحدود.",
        parse_mode=ParseMode.HTML,
    )
    return STATE_BULK_DAYS


async def bulk_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    try:
        days = int(text)
    except ValueError:
        await update.effective_message.reply_text("لطفاً یک عدد صحیح بفرستید. (0 برای نامحدود)")
        return STATE_BULK_DAYS
    if days < 0:
        await update.effective_message.reply_text("عدد مثبت یا صفر بفرستید.")
        return STATE_BULK_DAYS
    context.user_data["bulk"]["days"] = days
    await update.effective_message.reply_text(
        "پیشوند نام کاربر (prefix) را بفرستید. مثال: <code>vip</code>\n"
        "برای رد شدن، یک خط تیره بفرستید: <code>-</code>",
        parse_mode=ParseMode.HTML,
    )
    return STATE_BULK_PREFIX


async def bulk_prefix(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.effective_message.text or "").strip()
    if raw == "-" or not raw:
        prefix = "user"
    else:
        prefix = sanitize_prefix(raw)
    context.user_data["bulk"]["prefix"] = prefix

    default = config.default_send_chat_id or "-"
    await update.effective_message.reply_text(
        "لینک‌ها به کدام چت تلگرامی ارسال شوند؟\n"
        "آیدی عددی یا یوزرنیم گروه/کانال را بفرستید (مثل <code>-1001234567890</code> یا <code>@my_ch</code>).\n"
        "برای ارسال در همین چت از <code>here</code> استفاده کنید.\n"
        "برای استفاده از مقدار پیش‌فرض، <code>default</code> بفرستید.\n"
        "برای عدم ارسال به جای چتی خاص (فقط همین جا)، <code>skip</code> بفرستید.\n\n"
        f"پیش‌فرض فعلی: <code>{default}</code>",
        parse_mode=ParseMode.HTML,
    )
    return STATE_BULK_TARGET


async def bulk_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.effective_message.text or "").strip()
    bulk = context.user_data["bulk"]
    target: Optional[str]
    if raw.lower() in {"here", "اینجا"}:
        target = str(update.effective_chat.id)
    elif raw.lower() in {"default", "پیش‌فرض", "پیشفرض"}:
        target = config.default_send_chat_id or str(update.effective_chat.id)
    elif raw.lower() in {"skip", "رد", "بدون"}:
        target = None
    else:
        target = raw
    bulk["target_chat"] = target

    gb_text = "نامحدود" if bulk["total_gb"] == 0 else f"{bulk['total_gb']} GB"
    days_text = "نامحدود" if bulk["days"] == 0 else f"{bulk['days']} روز"
    summary = (
        "<b>تأیید می‌کنید؟</b>\n"
        f"• اینباند: <code>#{bulk['inbound_id']}</code>\n"
        f"• تعداد: <b>{bulk['count']}</b>\n"
        f"• حجم هر سرویس: {gb_text}\n"
        f"• مدت: {days_text}\n"
        f"• پیشوند: <code>{bulk['prefix']}</code>\n"
        f"• ارسال به: <code>{target or 'عدم ارسال'}</code>"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ شروع ساخت", callback_data="bulk_go"),
                InlineKeyboardButton("❌ لغو", callback_data="bulk_cancel"),
            ]
        ]
    )
    await update.effective_message.reply_text(summary, parse_mode=ParseMode.HTML, reply_markup=kb)
    return STATE_BULK_CONFIRM


async def bulk_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "bulk_cancel":
        await query.edit_message_text("❌ لغو شد.")
        context.user_data.pop("bulk", None)
        return ConversationHandler.END

    bulk = context.user_data.get("bulk") or {}
    await query.edit_message_text("⏳ در حال ساخت...")

    admin_id = update.effective_user.id
    store = _store(context)
    job_id = store.create_job(
        admin_id=admin_id,
        inbound_id=int(bulk["inbound_id"]),
        count=int(bulk["count"]),
        total_gb=int(bulk["total_gb"]),
        expiry_days=int(bulk["days"]),
        prefix=bulk["prefix"],
        target_chat=bulk.get("target_chat"),
    )

    asyncio.create_task(_run_bulk_job(update, context, job_id, bulk))
    return ConversationHandler.END


async def _run_bulk_job(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    job_id: int,
    bulk: Dict[str, Any],
) -> None:
    store = _store(context)
    chat_id = update.effective_chat.id
    progress_msg = await context.bot.send_message(chat_id, f"🚀 شروع Job #{job_id}")

    try:
        xui = await _get_xui(context)
        inbound = await xui.get_inbound(int(bulk["inbound_id"]))
    except XUIError as exc:
        await progress_msg.edit_text(f"❌ خطا در دریافت اینباند: {exc}")
        store.update_job(job_id, status="failed", log=str(exc))
        return

    protocol = (inbound.get("protocol") or "").lower()
    server_host = server_host_from_base(config.xui_base_url)

    count = int(bulk["count"])
    total_gb = int(bulk["total_gb"])
    days = int(bulk["days"])
    prefix = sanitize_prefix(bulk["prefix"])
    target_chat = bulk.get("target_chat")

    expiry_ms = days_from_now_ms(days)
    timestamp = int(time.time())

    success = 0
    failed = 0
    links: List[str] = []
    fail_log: List[str] = []

    for i in range(1, count + 1):
        email = f"{prefix}-{timestamp}-{i:03d}"
        try:
            client_obj = build_client_object(
                protocol=protocol,
                email=email,
                total_gb=total_gb,
                expiry_time_ms=expiry_ms,
            )
            await xui.add_client(int(bulk["inbound_id"]), client_obj)
            link = build_link(inbound, client_obj, server_host=server_host, remark=email)
            links.append(link)
            store.record_client(
                job_id=job_id,
                inbound_id=int(bulk["inbound_id"]),
                email=email,
                client_uuid=str(client_obj.get("id") or client_obj.get("password") or ""),
                link=link,
            )
            success += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            fail_log.append(f"{email}: {exc}")
            log.exception("failed to create %s", email)

        if i % 5 == 0 or i == count:
            try:
                await progress_msg.edit_text(
                    f"🚀 Job #{job_id}\n"
                    f"ساخته شد: <b>{success}</b> / {count}\n"
                    f"خطا: <b>{failed}</b>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

    store.update_job(
        job_id,
        status="done" if failed == 0 else "partial",
        success_count=success,
        fail_count=failed,
        log="\n".join(fail_log)[:4000] if fail_log else None,
    )

    summary = (
        f"✅ تمام شد. موفق: <b>{success}</b> | ناموفق: <b>{failed}</b>\n"
        f"اینباند: <code>#{bulk['inbound_id']}</code> | پروتکل: <code>{protocol}</code>\n"
        f"حجم: {'نامحدود' if total_gb==0 else f'{total_gb} GB'} | مدت: {'نامحدود' if days==0 else f'{days} روز'}"
    )
    await context.bot.send_message(chat_id, summary, parse_mode=ParseMode.HTML)

    if target_chat:
        try:
            await _send_links_batch(context, target_chat, job_id, links, prefix)
            await context.bot.send_message(chat_id, f"📤 ارسال به <code>{target_chat}</code> انجام شد.", parse_mode=ParseMode.HTML)
        except Exception as exc:  # noqa: BLE001
            await context.bot.send_message(
                chat_id,
                f"⚠️ ارسال به <code>{target_chat}</code> خطا داد: {exc}",
                parse_mode=ParseMode.HTML,
            )
    else:
        if links:
            await _send_links_batch(context, chat_id, job_id, links, prefix)


async def _send_links_batch(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: str | int,
    job_id: int,
    links: List[str],
    prefix: str,
) -> None:
    """Send links to a chat. Each message stays under Telegram's 4096-char limit.

    Each config is wrapped in a <code> block on its own line so the user can
    tap-and-copy on mobile clients.
    """
    if not links:
        return
    header = f"🆕 کانفیگ‌های Job #{job_id} ({prefix}) — {len(links)} عدد"
    await context.bot.send_message(chat_id, header)

    buf: List[str] = []
    buf_len = 0
    limit = 3500

    async def flush():
        nonlocal buf, buf_len
        if not buf:
            return
        text = "\n".join(buf)
        await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        buf = []
        buf_len = 0

    for link in links:
        block = f"<code>{_escape_html(link)}</code>"
        if buf_len + len(block) + 1 > limit:
            await flush()
        buf.append(block)
        buf_len += len(block) + 1
    await flush()


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


async def single_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        await _deny(update)
        return ConversationHandler.END
    kb = await _inbound_keyboard(context, "singleib")
    if kb is None:
        await update.effective_message.reply_text("❌ اینباندی موجود نیست یا پنل در دسترس نیست.")
        return ConversationHandler.END
    await update.effective_message.reply_text(
        "➕ <b>ساخت تکی</b>\nاینباند را انتخاب کنید:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
    context.user_data["single"] = {}
    return STATE_SINGLE_PICK_INBOUND


async def single_ib_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data.endswith(":cancel"):
        await query.edit_message_text("❌ لغو شد.")
        return ConversationHandler.END
    _, ib_id = data.split(":", 1)
    context.user_data["single"]["inbound_id"] = int(ib_id)
    await query.edit_message_text(
        f"✅ اینباند #{ib_id}\nنام کاربر (email) را بفرستید. مثال: <code>ali-vip</code>",
        parse_mode=ParseMode.HTML,
    )
    return STATE_SINGLE_EMAIL


async def single_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.effective_message.text or "").strip()
    email = sanitize_prefix(raw, fallback=f"user-{int(time.time())}")
    context.user_data["single"]["email"] = email
    await update.effective_message.reply_text(
        "حجم (GB)؟ <b>0</b> یعنی نامحدود.", parse_mode=ParseMode.HTML
    )
    return STATE_SINGLE_GB


async def single_gb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        gb = int((update.effective_message.text or "").strip())
    except ValueError:
        await update.effective_message.reply_text("عدد بفرستید (0 = نامحدود).")
        return STATE_SINGLE_GB
    if gb < 0:
        await update.effective_message.reply_text("عدد مثبت یا صفر بفرستید.")
        return STATE_SINGLE_GB
    context.user_data["single"]["total_gb"] = gb
    await update.effective_message.reply_text(
        "مدت (روز)؟ <b>0</b> یعنی نامحدود.", parse_mode=ParseMode.HTML
    )
    return STATE_SINGLE_DAYS


async def single_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        days = int((update.effective_message.text or "").strip())
    except ValueError:
        await update.effective_message.reply_text("عدد بفرستید (0 = نامحدود).")
        return STATE_SINGLE_DAYS
    if days < 0:
        await update.effective_message.reply_text("عدد مثبت یا صفر بفرستید.")
        return STATE_SINGLE_DAYS
    data = context.user_data["single"]
    data["days"] = days
    try:
        xui = await _get_xui(context)
        inbound = await xui.get_inbound(int(data["inbound_id"]))
        protocol = (inbound.get("protocol") or "").lower()
        client_obj = build_client_object(
            protocol=protocol,
            email=data["email"],
            total_gb=int(data["total_gb"]),
            expiry_time_ms=days_from_now_ms(int(days)),
        )
        await xui.add_client(int(data["inbound_id"]), client_obj)
        link = build_link(
            inbound,
            client_obj,
            server_host=server_host_from_base(config.xui_base_url),
            remark=data["email"],
        )
        _store(context).record_client(
            job_id=None,
            inbound_id=int(data["inbound_id"]),
            email=data["email"],
            client_uuid=str(client_obj.get("id") or client_obj.get("password") or ""),
            link=link,
        )
        await update.effective_message.reply_text(
            "✅ ساخته شد.\n\n<code>" + _escape_html(link) + "</code>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except XUIError as exc:
        await update.effective_message.reply_text(f"❌ خطا: {exc}")
    context.user_data.pop("single", None)
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text("❌ لغو شد.")
    context.user_data.pop("bulk", None)
    context.user_data.pop("single", None)
    return ConversationHandler.END


async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return await _deny(update)
    rows = _store(context).recent_jobs(10)
    if not rows:
        await update.effective_message.reply_text("هیچ Job ثبت نشده است.")
        return
    lines = ["<b>🗂 ۱۰ Job اخیر</b>"]
    for r in rows:
        lines.append(
            f"• #{r['id']} | inbound={r['inbound_id']} | count={r['count']} | "
            f"ok={r['success_count']} fail={r['fail_count']} | {r['status']}"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def on_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "inbounds":
        await cmd_inbounds(update, context)
    elif data == "help":
        await cmd_help(update, context)
    elif data == "jobs":
        await cmd_jobs(update, context)
    elif data == "single_new":
        await single_entry(update, context)
    elif data == "bulk_new":
        await bulk_entry(update, context)


async def on_startup(app: Application) -> None:
    log.info("Bot started. Admins: %s", config.telegram_admin_ids)


async def on_shutdown(app: Application) -> None:
    xui: Optional[XUIClient] = app.bot_data.get("xui")
    if xui is not None:
        await xui.close()


def build_application() -> Application:
    config.validate()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = Application.builder().token(config.telegram_bot_token).post_init(on_startup).post_shutdown(on_shutdown).build()

    bulk_conv = ConversationHandler(
        entry_points=[
            CommandHandler("bulk", bulk_entry),
            CallbackQueryHandler(bulk_entry, pattern="^bulk_new$"),
        ],
        states={
            STATE_BULK_PICK_INBOUND: [CallbackQueryHandler(bulk_ib_chosen, pattern="^bulkib:")],
            STATE_BULK_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bulk_count)],
            STATE_BULK_GB: [MessageHandler(filters.TEXT & ~filters.COMMAND, bulk_gb)],
            STATE_BULK_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, bulk_days)],
            STATE_BULK_PREFIX: [MessageHandler(filters.TEXT & ~filters.COMMAND, bulk_prefix)],
            STATE_BULK_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, bulk_target)],
            STATE_BULK_CONFIRM: [CallbackQueryHandler(bulk_confirm, pattern="^bulk_(go|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        name="bulk_conv",
    )

    single_conv = ConversationHandler(
        entry_points=[
            CommandHandler("new", single_entry),
            CallbackQueryHandler(single_entry, pattern="^single_new$"),
        ],
        states={
            STATE_SINGLE_PICK_INBOUND: [CallbackQueryHandler(single_ib_chosen, pattern="^singleib:")],
            STATE_SINGLE_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, single_email)],
            STATE_SINGLE_GB: [MessageHandler(filters.TEXT & ~filters.COMMAND, single_gb)],
            STATE_SINGLE_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, single_days)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        name="single_conv",
    )

    app.add_handler(bulk_conv)
    app.add_handler(single_conv)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("inbounds", cmd_inbounds))
    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_menu_callback))
    return app


def main() -> None:
    app = build_application()
    app.run_polling(close_loop=False, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
