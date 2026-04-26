#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات تلگرام: مدیریت لیست گروه، بررسی ادمین بودن ربات، انتخاب مقصد،
ارسال تک‌تک لینک‌های ساب به گروه انتخاب‌شده.

نکته: API تلگرام اجازه نمی‌دهد ربات خودش را ادمین کند؛ شما باید ربات را به گروه اضافه
و از تنظیمات گروه ادمین کنید. این ربات فقط وضعیت ادمین بودن را گزارش می‌دهد.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("group_sub_bot")

DATA_FILE = Path(os.environ.get("GROUP_SUB_BOT_DATA_FILE", "./data/groups.json"))


def _admin_ids() -> set[int]:
    raw = os.environ.get("TELEGRAM_ADMIN_IDS", "").strip()
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
            out.add(int(part))
    return out


ADMIN_IDS = _admin_ids()


def is_admin(user_id: int | None) -> bool:
    if user_id is None:
        return False
    if not ADMIN_IDS:
        logger.warning("TELEGRAM_ADMIN_IDS خالی است؛ هیچ کس نمی‌تواند ربات را کنترل کند.")
        return False
    return user_id in ADMIN_IDS


def load_store() -> dict[str, Any]:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        return {"groups": []}
    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if "groups" not in data:
            data["groups"] = []
        groups = data["groups"]
        deduped, changed = dedupe_groups(groups)
        if changed:
            data["groups"] = deduped
            save_store(data)
            logger.info(
                "حذف تکرار از لیست گروه‌ها: %s رکورد → %s رکورد یکتا",
                len(groups),
                len(deduped),
            )
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.error("خطا در خواندن فایل داده: %s", e)
        return {"groups": []}


def save_store(data: dict[str, Any]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(DATA_FILE)


def _find_group(groups: list[dict[str, Any]], chat_id: int) -> dict[str, Any] | None:
    for g in groups:
        if g.get("chat_id") == chat_id:
            return g
    return None


def dedupe_groups(groups: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """
    یک گروه را فقط یک‌بار (بر اساس chat_id) نگه می‌دارد.
    اگر چند رکورد با یک chat_id بود، عنوان/یوزرنیم جدیدتر جایگزین می‌شود.
    """
    seen_order: list[int] = []
    by_id: dict[int, dict[str, Any]] = {}
    for g in groups:
        cid = g.get("chat_id")
        if cid is None:
            continue
        try:
            cid_int = int(cid)
        except (TypeError, ValueError):
            continue
        if cid_int not in by_id:
            seen_order.append(cid_int)
            by_id[cid_int] = dict(g)
            by_id[cid_int]["chat_id"] = cid_int
        else:
            cur = by_id[cid_int]
            if g.get("title"):
                cur["title"] = g["title"]
            if g.get("username") is not None:
                cur["username"] = g.get("username")
    out = [by_id[i] for i in seen_order]
    return out, len(out) != len(groups)


CHAT_ID_RE = re.compile(r"^-?\d+$")
# دکمه‌های جدید: g:<chat_id> — با pick:<n> قدیمی تداخل ندارد
GROUP_CB_PREFIX = "g:"
# دکمه‌های قدیمی: pick:1 (ایندکس ۱-based)
LEGACY_PICK_INDEX_RE = re.compile(r"^pick:(\d+)$")


def _remove_group_by_chat_id(store: dict[str, Any], chat_id: int) -> bool:
    groups: list[dict[str, Any]] = store.get("groups") or []
    before = len(groups)

    def _cid(x: dict[str, Any]) -> int | None:
        c = x.get("chat_id")
        if c is None:
            return None
        try:
            return int(c)
        except (TypeError, ValueError):
            return None

    store["groups"] = [g for g in groups if _cid(g) != chat_id]
    return len(store["groups"]) < before


async def sync_store_with_telegram(bot: Bot, store: dict[str, Any]) -> tuple[bool, int, int]:
    """
    عنوان و username هر گروه را از تلگرام می‌گیرد؛ اگر ربات دیگر به چت دسترسی ندارد، رکورد حذف می‌شود.
    برمی‌گرداند: (تغییر در فایل؟, تعداد به‌روزرسانی عنوان, تعداد حذف‌شده).
    """
    groups: list[dict[str, Any]] = list(store.get("groups") or [])
    if not groups:
        return False, 0, 0
    updated = 0
    removed = 0
    new_list: list[dict[str, Any]] = []
    for g in groups:
        cid = g.get("chat_id")
        if cid is None:
            continue
        try:
            cid_int = int(cid)
        except (TypeError, ValueError):
            continue
        try:
            chat = await bot.get_chat(cid_int)
        except Forbidden:
            removed += 1
            logger.info("حذف از لیست (بدون دسترسی / اخراج): chat_id=%s", cid_int)
            continue
        except BadRequest as e:
            err = str(e).lower()
            if "not found" in err or "chat not found" in err or "chat not exist" in err:
                removed += 1
                logger.info("حذف از لیست (چت پیدا نشد): chat_id=%s", cid_int)
                continue
            new_list.append(g)
            logger.warning("get_chat ناموفح برای %s: %s", cid_int, e)
            continue
        except TelegramError as e:
            new_list.append(g)
            logger.warning("get_chat خطا برای %s: %s", cid_int, e)
            continue
        new_title = chat.title or str(cid_int)
        new_username = getattr(chat, "username", None)
        if g.get("title") != new_title or g.get("username") != new_username:
            g["title"] = new_title
            g["username"] = new_username
            updated += 1
        new_list.append(g)
        await asyncio.sleep(0.03)
    changed = updated > 0 or removed > 0 or len(new_list) != len(groups)
    if changed:
        deduped, dup_fix = dedupe_groups(new_list)
        store["groups"] = deduped
        if dup_fix:
            changed = True
        save_store(store)
    return changed, updated, removed


async def cmd_syncgroups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not is_admin(uid):
        await update.message.reply_text("مجاز نیستید.")
        return
    store = load_store()
    ch, up, rm = await sync_store_with_telegram(context.bot, store)
    if ch:
        await update.message.reply_text(
            f"همگام شد. عنوان به‌روز: {up} گروه، حذف‌شده (بدون دسترسی): {rm} گروه."
        )
    else:
        await update.message.reply_text("تغییری نبود؛ لیست با تلگرام هم‌خوان است.")


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cmu = update.my_chat_member
    if not cmu or not cmu.chat:
        return
    chat = cmu.chat
    if chat.type not in ("group", "supergroup"):
        return
    chat_id = chat.id
    new_st = cmu.new_chat_member.status
    store = load_store()
    if new_st in ("left", "kicked"):
        if _remove_group_by_chat_id(store, chat_id):
            save_store(store)
            logger.info("ربات از گروه خارج شد؛ حذف از لیست: %s", chat_id)
        return
    if new_st in ("member", "administrator", "restricted", "creator"):
        g = _find_group(store.get("groups") or [], chat_id)
        if g is None:
            return
        title = chat.title or str(chat_id)
        un = getattr(chat, "username", None)
        if g.get("title") != title or g.get("username") != un:
            g["title"] = title
            g["username"] = un
            save_store(store)
            logger.info("عنوان گروه از my_chat_member به‌روز شد: %s", chat_id)


async def on_new_chat_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.chat:
        return
    if msg.chat.type not in ("group", "supergroup"):
        return
    chat_id = msg.chat.id
    new_title = msg.new_chat_title
    if not new_title:
        return
    store = load_store()
    g = _find_group(store.get("groups") or [], chat_id)
    if g is None:
        return
    if g.get("title") != new_title:
        g["title"] = new_title
        save_store(store)
        logger.info("عنوان گروه از پیام new_chat_title به‌روز شد: %s", chat_id)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not is_admin(uid):
        await update.message.reply_text("شما مجاز به استفاده از این ربات نیستید.")
        return
    text = (
        "سلام. این ربات برای مدیریت گروه‌ها و ارسال لینک ساب است.\n\n"
        "**گروه‌ها را خودتان به ربات اضافه می‌کنید** (ربات را به گروه ببرید)، سپس اینجا ثبت کنید.\n\n"
        "دستورات:\n"
        "/addgroup <chat_id> — ثبت گروه با شناسه عددی (مثلاً `-1001234567890`)\n"
        "/register — در خود گروه بزنید تا همان گروه ثبت شود\n"
        "/mygroups — لیست شماره‌دار گروه‌های ذخیره‌شده\n"
        "/pick <شماره> — انتخاب گروه مقصد برای ارسال لینک‌ها\n"
        "/listgroups — دکمه‌های انتخاب گروه\n"
        "/dedupgroups — حذف دستی تکرارها از فایل (معمولاً خودکار انجام می‌شود)\n"
        "/syncgroups — همگام‌سازی دستی عنوان‌ها با تلگرام و حذف گروه‌های بدون دسترسی\n"
        "/admincheck — بررسی اینکه ربات در هر گروه ادمین است یا نه\n"
        "/subs_start — شروع جمع‌آوری لینک‌ها (هر خط یک لینک)\n"
        "/subs_done — پایان لیست لینک‌ها\n"
        "/send_subs — ارسال یکی‌یکی لینک‌های ذخیره‌شده به گروه انتخاب‌شده\n\n"
        "**مهم:** ربات نمی‌تواند خودش را ادمین کند؛ باید از تنظیمات گروه ادمینش کنید.\n"
        "ربات باید حداقل بتواند پیام بفرستد."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_addgroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not is_admin(uid):
        await update.message.reply_text("مجاز نیستید.")
        return
    if not context.args:
        await update.message.reply_text("استفاده: `/addgroup <chat_id>`", parse_mode="Markdown")
        return
    arg = context.args[0].strip()
    if not CHAT_ID_RE.match(arg):
        await update.message.reply_text("شناسه گروه باید یک عدد صحیح باشد (مثلاً `-100...`).")
        return
    chat_id = int(arg)
    bot = context.bot
    try:
        chat = await bot.get_chat(chat_id)
    except TelegramError as e:
        await update.message.reply_text(f"نمی‌توانم اطلاعات این چت را بگیرم: {e}")
        return
    title = chat.title or str(chat_id)
    username = chat.username
    store = load_store()
    groups: list[dict[str, Any]] = store["groups"]
    if _find_group(groups, chat_id):
        await update.message.reply_text(f"این گروه قبلاً ثبت شده: {title}")
        return
    groups.append({"chat_id": chat_id, "title": title, "username": username})
    save_store(store)
    await update.message.reply_text(f"گروه ثبت شد: {title} (`{chat_id}`)", parse_mode="Markdown")


async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not is_admin(uid):
        await update.message.reply_text("مجاز نیستید.")
        return
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.message.reply_text("این دستور را داخل یک گروه یا سوپرگروه بزنید.")
        return
    chat_id = chat.id
    title = chat.title or str(chat_id)
    username = getattr(chat, "username", None)
    store = load_store()
    groups = store["groups"]
    if _find_group(groups, chat_id):
        await update.message.reply_text("این گروه از قبل در لیست است.")
        return
    groups.append({"chat_id": chat_id, "title": title, "username": username})
    save_store(store)
    await update.message.reply_text(f"ثبت شد: {title}")


async def cmd_mygroups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not is_admin(uid):
        await update.message.reply_text("مجاز نیستید.")
        return
    store = load_store()
    _ch, up, rm = await sync_store_with_telegram(context.bot, store)
    groups = store["groups"]
    if not groups:
        await update.message.reply_text("هیچ گروهی ثبت نشده. از /addgroup یا /register استفاده کنید.")
        return
    lines = []
    if up or rm:
        lines.append(f"همگام با تلگرام: {up} عنوان به‌روز، {rm} گروه حذف شد.\n")
    for i, g in enumerate(groups, start=1):
        cid = g.get("chat_id")
        title = g.get("title", "?")
        lines.append(f"{i}. {title} — `{cid}`")
    lines.append("برای انتخاب مقصد ارسال: `/pick شماره`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not is_admin(uid):
        await update.message.reply_text("مجاز نیستید.")
        return
    if not context.args:
        await update.message.reply_text("استفاده: `/pick <شماره>` مثل `/pick 1`", parse_mode="Markdown")
        return
    try:
        n = int(context.args[0])
    except ValueError:
        await update.message.reply_text("شماره نامعتبر.")
        return
    store = load_store()
    await sync_store_with_telegram(context.bot, store)
    groups = store["groups"]
    if n < 1 or n > len(groups):
        await update.message.reply_text("شماره خارج از محدوده لیست است.")
        return
    g = groups[n - 1]
    context.user_data["target_chat_id"] = g["chat_id"]
    context.user_data["target_title"] = g.get("title", "")
    await update.message.reply_text(f"مقصد ارسال: {g.get('title')} (`{g['chat_id']}`)", parse_mode="Markdown")


async def cmd_listgroups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not is_admin(uid):
        await update.message.reply_text("مجاز نیستید.")
        return
    store = load_store()
    ch, up, rm = await sync_store_with_telegram(context.bot, store)
    groups = store["groups"]
    if not groups:
        await update.message.reply_text("لیست خالی است.")
        return
    rows = []
    row: list[InlineKeyboardButton] = []
    for i, g in enumerate(groups, start=1):
        cid = g.get("chat_id")
        if cid is None:
            continue
        label = f"{i}. {(g.get('title') or '?')[:20]}"
        row.append(InlineKeyboardButton(label, callback_data=f"{GROUP_CB_PREFIX}{cid}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    hint = ""
    if ch and (up or rm):
        hint = f"\n(لیست با تلگرام همگام شد: {up} عنوان به‌روز، {rm} گروه حذف شد)"
    await update.message.reply_text(
        "یک گروه را انتخاب کنید:" + hint,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def on_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    uid = q.from_user.id if q.from_user else None
    if not is_admin(uid):
        await q.answer("مجاز نیستید.", show_alert=True)
        return
    data = q.data or ""
    store = load_store()
    await sync_store_with_telegram(context.bot, store)
    groups = store["groups"]
    g: dict[str, Any] | None = None
    if data.startswith(GROUP_CB_PREFIX):
        rest = data[len(GROUP_CB_PREFIX) :]
        if CHAT_ID_RE.match(rest):
            cid = int(rest)
            g = _find_group(groups, cid)
    if g is None:
        m = LEGACY_PICK_INDEX_RE.match(data)
        if m:
            try:
                n = int(m.group(1))
            except ValueError:
                n = 0
            if 1 <= n <= len(groups):
                g = groups[n - 1]
    if g is None:
        await q.answer("گروه در لیست نیست.", show_alert=True)
        return
    context.user_data["target_chat_id"] = g["chat_id"]
    context.user_data["target_title"] = g.get("title", "")
    await q.answer()
    await q.edit_message_text(f"مقصد انتخاب شد: {g.get('title')} (`{g['chat_id']}`)", parse_mode="Markdown")


async def cmd_dedupgroups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not is_admin(uid):
        await update.message.reply_text("مجاز نیستید.")
        return
    store = load_store()
    groups = store.get("groups") or []
    before = len(groups)
    deduped, changed = dedupe_groups(groups)
    if not changed:
        await update.message.reply_text("تکراری در فایل نبود؛ همه chat_idها یکتا هستند.")
        return
    store["groups"] = deduped
    save_store(store)
    await update.message.reply_text(
        f"انجام شد. قبل: {before} رکورد، بعد: {len(deduped)} گروه یکتا ({before - len(deduped)} تکرار حذف شد)."
    )


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not is_admin(uid):
        await update.message.reply_text("مجاز نیستید.")
        return
    if not context.args:
        await update.message.reply_text("استفاده: `/remove <شماره>`", parse_mode="Markdown")
        return
    try:
        n = int(context.args[0])
    except ValueError:
        await update.message.reply_text("شماره نامعتبر.")
        return
    store = load_store()
    groups = store["groups"]
    if n < 1 or n > len(groups):
        await update.message.reply_text("شماره خارج از محدوده.")
        return
    removed = groups.pop(n - 1)
    save_store(store)
    await update.message.reply_text(f"حذف شد: {removed.get('title')}")


async def cmd_admincheck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not is_admin(uid):
        await update.message.reply_text("مجاز نیستید.")
        return
    bot = context.bot
    me = await bot.get_me()
    store = load_store()
    ch, up, rm = await sync_store_with_telegram(bot, store)
    if ch and (up or rm):
        await update.message.reply_text(f"(قبل از بررسی ادمین: {up} عنوان به‌روز، {rm} گروه بدون دسترسی حذف شد)")
    groups = store["groups"]
    if not groups:
        await update.message.reply_text("لیست گروه خالی است.")
        return
    lines = []
    for g in groups:
        cid = g["chat_id"]
        title = g.get("title", "?")
        try:
            m = await bot.get_chat_member(cid, me.id)
            status = m.status
            ok = status in ("administrator", "creator")
            lines.append(f"{'✅' if ok else '❌'} {title}: وضعیت ربات = `{status}`")
        except TelegramError as e:
            lines.append(f"⚠️ {title}: خطا — `{e}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_subs_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not is_admin(uid):
        await update.message.reply_text("مجاز نیستید.")
        return
    context.user_data["collecting_subs"] = True
    context.user_data["pending_links"] = []
    await update.message.reply_text(
        "حالت جمع‌آوری لینک فعال شد.\n"
        "هر پیامی که بفرستید (غیر از دستور) خطوط آن به عنوان لینک اضافه می‌شود.\n"
        "وقتی تمام شد بزنید: /subs_done"
    )


async def cmd_subs_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not is_admin(uid):
        await update.message.reply_text("مجاز نیستید.")
        return
    context.user_data["collecting_subs"] = False
    links = context.user_data.get("pending_links") or []
    await update.message.reply_text(f"تعداد لینک ذخیره‌شده: {len(links)}. حالا با /pick گروه را انتخاب کنید و /send_subs بزنید.")


async def on_text_collect_subs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.get("collecting_subs"):
        return
    uid = update.effective_user.id if update.effective_user else None
    if not is_admin(uid):
        return
    text = update.message.text or ""
    added = 0
    for line in text.splitlines():
        s = line.strip()
        if s:
            context.user_data.setdefault("pending_links", []).append(s)
            added += 1
    if added:
        n = len(context.user_data.get("pending_links", []))
        await update.message.reply_text(f"{added} خط اضافه شد؛ مجموع: {n}. ادامه دهید یا /subs_done")


async def cmd_send_subs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if not is_admin(uid):
        await update.message.reply_text("مجاز نیستید.")
        return
    links = context.user_data.get("pending_links") or []
    if not links:
        await update.message.reply_text("ابتدا با /subs_start لینک‌ها را بفرستید و /subs_done بزنید.")
        return
    target = context.user_data.get("target_chat_id")
    if target is None:
        await update.message.reply_text("ابتدا گروه مقصد را با /pick یا دکمه‌های /listgroups انتخاب کنید.")
        return
    bot = context.bot
    total = len(links)
    await update.message.reply_text(f"شروع ارسال {total} لینک به `{target}` ...", parse_mode="Markdown")
    ok = 0
    for i, link in enumerate(links, start=1):
        try:
            await bot.send_chat_action(target, ChatAction.TYPING)
            await bot.send_message(chat_id=target, text=link, disable_web_page_preview=False)
            ok += 1
        except (BadRequest, Forbidden, TelegramError) as e:
            await update.message.reply_text(f"خطا در ارسال مورد {i}: {e}")
            break
        await asyncio.sleep(1.1)
    await update.message.reply_text(f"تمام شد. موفق: {ok} از {total}.")


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN را در محیط تنظیم کنید.")
    if not ADMIN_IDS:
        raise SystemExit("TELEGRAM_ADMIN_IDS را با آیدی عددی خودتان تنظیم کنید.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("addgroup", cmd_addgroup))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("mygroups", cmd_mygroups))
    app.add_handler(CommandHandler("pick", cmd_pick))
    app.add_handler(CommandHandler("listgroups", cmd_listgroups))
    app.add_handler(CommandHandler("dedupgroups", cmd_dedupgroups))
    app.add_handler(CommandHandler("syncgroups", cmd_syncgroups))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("admincheck", cmd_admincheck))
    app.add_handler(CommandHandler("subs_start", cmd_subs_start))
    app.add_handler(CommandHandler("subs_done", cmd_subs_done))
    app.add_handler(CommandHandler("send_subs", cmd_send_subs))
    app.add_handler(CallbackQueryHandler(on_pick_callback, pattern=r"^(g:-?\d+|pick:\d+)$"))
    app.add_handler(ChatMemberHandler(on_my_chat_member, chat_member_types=ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_TITLE, on_new_chat_title),
        group=0,
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_collect_subs),
        group=1,
    )

    logger.info("ربات در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
