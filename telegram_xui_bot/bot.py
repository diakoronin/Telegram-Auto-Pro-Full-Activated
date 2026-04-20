"""Telegram bot: bulk-create 3x-ui clients and post results to a group."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import time
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from telegram_xui_bot.xui_client import PanelError, XuiPanelClient

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

(
    ASK_GROUP,
    ASK_INBOUND,
    ASK_COUNT,
    ASK_GB,
    ASK_DAYS,
) = range(5)


def _admin_ids() -> set[int]:
    raw = os.environ.get("TELEGRAM_ADMIN_IDS", "").strip()
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.replace(" ", "").split(","):
        if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
            out.add(int(part))
    return out


def _is_admin(user_id: int | None) -> bool:
    admins = _admin_ids()
    if not admins:
        log.warning("TELEGRAM_ADMIN_IDS خالی است؛ هیچ‌کس نمی‌تواند از ربات استفاده کند.")
        return False
    return user_id is not None and user_id in admins


def _panel_from_env() -> tuple[str, str, str, str, bool]:
    base = os.environ.get("XUI_BASE_URL", "").strip().rstrip("/")
    user = os.environ.get("XUI_USERNAME", "").strip()
    password = os.environ.get("XUI_PASSWORD", "").strip()
    tfa = os.environ.get("XUI_2FA_CODE", "").strip()
    verify = os.environ.get("XUI_VERIFY_TLS", "true").lower() not in ("0", "false", "no")
    if not base or not user or not password:
        raise RuntimeError("متغیرهای محیطی XUI_BASE_URL، XUI_USERNAME و XUI_PASSWORD الزامی‌اند.")
    return base, user, password, tfa, verify


def _chunk_text(s: str, limit: int = 4000) -> list[str]:
    if len(s) <= limit:
        return [s]
    lines = s.splitlines()
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > limit and buf:
            chunks.append("\n".join(buf))
            buf = []
            size = 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def _format_report_html(
    *,
    inbound_id: int,
    count: int,
    total_bytes: int,
    gb: float,
    days: int,
    expiry_ms: int,
    created: list[dict[str, Any]],
) -> str:
    vol = "نامحدود" if total_bytes <= 0 else f"{html.escape(str(gb))} GB"
    exp = "نامحدود" if expiry_ms <= 0 else f"{days} روز از الان"
    lines = [
        f"<b>ساخت انجام شد</b>: {count} کاربر روی inbound <code>{inbound_id}</code>",
        f"حجم هر کاربر: {vol}",
        f"انقضا: {exp}",
        "",
    ]
    sub_tpl = os.environ.get("XUI_SUB_URL_TEMPLATE", "").strip()
    for row in created:
        em = html.escape(row["email"])
        parts = [em]
        cid = row.get("id")
        if cid:
            parts.append(f"uuid/id: <code>{html.escape(str(cid))}</code>")
        pw = row.get("password")
        if pw:
            parts.append(f"pass: <code>{html.escape(str(pw))}</code>")
        sid = row.get("sub_id")
        if sub_tpl and sid:
            try:
                sub_url = sub_tpl.format(sub_id=sid, email=row["email"])
            except (KeyError, ValueError):
                sub_url = ""
            if sub_url:
                parts.append(f'<a href="{html.escape(sub_url, quote=True)}">ساب</a>')
        lines.append(" | ".join(parts))
    return "\n".join(lines)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id):
        if update.message:
            await update.message.reply_text("دسترسی ندارید.")
        return
    await update.message.reply_text(
        "سلام.\n\n"
        "برای ساخت گروهی کاربر روی یک inbound پنل 3x-ui، دستور /bulk را بزنید.\n"
        "مراحل: آیدی گروه → شماره inbound → تعداد (۱ تا ۲۰۰) → حجم (گیگ، ۰ نامحدود) → "
        "مدت انقضا (روز از الان، ۰ نامحدود).\n\n"
        "لیست inboundها: /inbounds\n"
        "ربات باید در آن گروه عضو و اجازهٔ ارسال پیام داشته باشد.\n"
        "/cancel برای لغو."
    )


async def inbounds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی ندارید.")
        return
    base, user, password, tfa, verify = _panel_from_env()

    def _work() -> list[dict[str, Any]]:
        with XuiPanelClient(base, user, password, two_factor_code=tfa, verify_tls=verify) as cli:
            cli.login()
            return cli.list_inbounds()

    try:
        rows = await asyncio.to_thread(_work)
    except PanelError as e:
        await update.message.reply_text(f"خطای پنل: {e}")
        return
    except Exception as e:  # noqa: BLE001
        log.exception("inbounds")
        await update.message.reply_text(f"خطا: {e}")
        return
    if not rows:
        await update.message.reply_text("هیچ inboundای یافت نشد.")
        return
    lines = ["<b>Inbounds</b> (id — پروتکل — remark):", ""]
    for ib in rows[:80]:
        iid = ib.get("id")
        pr = html.escape(str(ib.get("protocol", "")))
        rm = html.escape(str(ib.get("remark", ""))[:60])
        lines.append(f"<code>{iid}</code> — {pr} — {rm}")
    if len(rows) > 80:
        lines.append(f"\n… و {len(rows) - 80} مورد دیگر")
    text = "\n".join(lines)
    for part in _chunk_text(text, 3900):
        await update.message.reply_text(part, parse_mode=ParseMode.HTML)


async def bulk_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_user or not _is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی ندارید.")
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        "آیدی عددی گروه مقصد را بفرستید (مثلاً -1001234567890).\n"
        "این عدد را از @userinfobot یا تنظیمات گروه می‌توانید بگیرید."
    )
    return ASK_GROUP


async def receive_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    try:
        gid = int(text)
    except ValueError:
        await update.message.reply_text("فقط عدد آیدی گروه را بفرستید.")
        return ASK_GROUP
    context.user_data["target_chat"] = gid
    await update.message.reply_text("شمارهٔ inbound در پنل (همان id عددی) را بفرستید:")
    return ASK_INBOUND


async def receive_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    try:
        iid = int(text)
    except ValueError:
        await update.message.reply_text("یک عدد صحیح برای inbound وارد کنید.")
        return ASK_INBOUND
    if iid <= 0:
        await update.message.reply_text("باید مثبت باشد.")
        return ASK_INBOUND
    context.user_data["inbound_id"] = iid
    await update.message.reply_text("تعداد کاربر جدید (۱ تا ۲۰۰):")
    return ASK_COUNT


async def receive_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    try:
        n = int(text)
    except ValueError:
        await update.message.reply_text("یک عدد صحیح بفرستید.")
        return ASK_COUNT
    if not 1 <= n <= 200:
        await update.message.reply_text("فقط بین ۱ تا ۲۰۰ مجاز است.")
        return ASK_COUNT
    context.user_data["count"] = n
    await update.message.reply_text(
        "حجم سقف هر کاربر به گیگابایت (عدد اعشاری مجاز). ۰ یعنی نامحدود ترافیک."
    )
    return ASK_GB


async def receive_gb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip().replace(",", ".")
    try:
        gb = float(text)
    except ValueError:
        await update.message.reply_text("یک عدد معتبر بفرستید.")
        return ASK_GB
    if gb < 0:
        await update.message.reply_text("نمی‌تواند منفی باشد.")
        return ASK_GB
    context.user_data["gb"] = gb
    await update.message.reply_text(
        "تعداد روز اعتبار از الان (عدد صحیح). ۰ یعنی بدون محدودیت زمانی."
    )
    return ASK_DAYS


async def receive_days_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    try:
        days = int(text)
    except ValueError:
        await update.message.reply_text("یک عدد صحیح بفرستید.")
        return ASK_DAYS
    if days < 0:
        await update.message.reply_text("نمی‌تواند منفی باشد.")
        return ASK_DAYS

    ud = context.user_data
    target_chat = int(ud["target_chat"])
    inbound_id = int(ud["inbound_id"])
    count = int(ud["count"])
    gb = float(ud["gb"])

    total_bytes = 0 if gb <= 0 else int(gb * 1024**3)
    expiry_ms = 0 if days <= 0 else int(time.time() * 1000) + days * 86400 * 1000

    prefix = os.environ.get("XUI_EMAIL_PREFIX", "tg").strip() or "tg"
    await update.message.reply_text("در حال اتصال به پنل و ساخت کاربرها…")

    base, user, password, tfa, verify = _panel_from_env()

    def _work() -> list[dict[str, Any]]:
        with XuiPanelClient(base, user, password, two_factor_code=tfa, verify_tls=verify) as cli:
            cli.login()
            return cli.add_clients_bulk(
                inbound_id,
                count,
                total_bytes=total_bytes,
                expiry_time_ms=expiry_ms,
                email_prefix=prefix,
            )

    try:
        created = await asyncio.to_thread(_work)
    except PanelError as e:
        await update.message.reply_text(f"خطای پنل: {e}")
        return ConversationHandler.END
    except Exception as e:  # noqa: BLE001
        log.exception("panel")
        await update.message.reply_text(f"خطا: {e}")
        return ConversationHandler.END

    body = _format_report_html(
        inbound_id=inbound_id,
        count=len(created),
        total_bytes=total_bytes,
        gb=gb,
        days=days,
        expiry_ms=expiry_ms,
        created=created,
    )
    header_lines = body.splitlines()[:5]
    header = "\n".join(header_lines)

    try:
        for part in _chunk_text(body, 3900):
            await context.bot.send_message(
                chat_id=target_chat,
                text=part,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(
            f"کاربرها ساخته شدند اما ارسال به گروه ناموفق بود: {e}\n"
            f"خلاصه:\n{header}"
        )
        return ConversationHandler.END

    await update.message.reply_text("انجام شد و به گروه ارسال شد.")
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("لغو شد.")
    return ConversationHandler.END


def build_application(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("inbounds", inbounds_cmd))
    conv = ConversationHandler(
        entry_points=[CommandHandler("bulk", bulk_entry)],
        states={
            ASK_GROUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_group)],
            ASK_INBOUND: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_inbound)],
            ASK_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_count)],
            ASK_GB: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_gb)],
            ASK_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_days_run)],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )
    app.add_handler(conv)
    return app


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN را تنظیم کنید.")
    if not _admin_ids():
        raise SystemExit("TELEGRAM_ADMIN_IDS را با آیدی عددی تلگرام خود تنظیم کنید (جدا با ویرگول).")
    app = build_application(token)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
