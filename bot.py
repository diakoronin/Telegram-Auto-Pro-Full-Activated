#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Awaitable, Callable, Optional
from urllib.parse import quote_plus

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationHandlerStop,
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import uvicorn


LOGGER = logging.getLogger(__name__)
DB_PATH = os.getenv("DB_PATH", "bot_data.sqlite3")
SEND_DELAY_SECONDS = float(os.getenv("SEND_DELAY_SECONDS", "1.0"))
SCHEDULER_POLL_SECONDS = max(float(os.getenv("SCHEDULER_POLL_SECONDS", "5.0")), 1.0)
MAX_CONCURRENT_BROADCASTS = max(int(os.getenv("MAX_CONCURRENT_BROADCASTS", "4")), 1)
WEB_PANEL_ENABLED = os.getenv("WEB_PANEL_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
WEB_PANEL_HOST = os.getenv("WEB_PANEL_HOST", "0.0.0.0")
WEB_PANEL_PORT = int(os.getenv("WEB_PANEL_PORT", "18080"))
WEB_PANEL_PATH = os.getenv("WEB_PANEL_PATH", "panel").strip().strip("/")
WEB_PANEL_USERNAME = os.getenv("WEB_PANEL_USERNAME", "admin").strip()
WEB_PANEL_PASSWORD = os.getenv("WEB_PANEL_PASSWORD", "").strip()
WEB_PANEL_REQUIRE_LOGIN = bool(WEB_PANEL_USERNAME and WEB_PANEL_PASSWORD)
WEB_PANEL_SESSION_SECRET = os.getenv("WEB_PANEL_SESSION_SECRET", "").strip()
OWNER_ID_ENV = os.getenv("OWNER_ID", "").strip()
STRICT_OWNER_ONLY = os.getenv("STRICT_OWNER_ONLY", "true").lower() in {"1", "true", "yes", "on"}
SERVICE_NOTIFY_OWNER = os.getenv("SERVICE_NOTIFY_OWNER", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LINK_PATTERN = re.compile(r"^https?://\S+$", re.IGNORECASE)
WEB_PANEL_PATH = re.sub(r"[^a-zA-Z0-9_-]", "", WEB_PANEL_PATH) or "panel"


@dataclass
class GroupItem:
    chat_id: int
    title: str
    is_active: bool
    is_admin: bool


@dataclass
class ServiceItem:
    id: int
    name: str
    interval_minutes: int
    is_enabled: bool
    group_ids: list[int]
    links: list[str]
    next_run_at: datetime
    last_run_at: Optional[datetime]
    last_status: str


@dataclass
class BroadcastResult:
    sent_ok: int
    sent_fail: int
    total: int
    failures: list[str]
    stopped: bool = False
    error: Optional[str] = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def dt_to_str(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def dt_from_str(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS groups (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS services (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    interval_minutes INTEGER NOT NULL,
                    is_enabled INTEGER NOT NULL DEFAULT 1,
                    group_ids_json TEXT NOT NULL,
                    links_json TEXT NOT NULL,
                    next_run_at TEXT NOT NULL,
                    last_run_at TEXT,
                    last_status TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            conn.commit()

    def get_setting(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_owner_id(self, owner_id: int) -> None:
        self.set_setting("owner_id", str(owner_id))

    def get_owner_id(self) -> Optional[int]:
        raw_value = self.get_setting("owner_id")
        if raw_value is None:
            return None
        try:
            return int(raw_value)
        except ValueError:
            return None

    def upsert_group(
        self,
        chat_id: int,
        title: str,
        is_active: bool,
        is_admin: bool,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO groups(chat_id, title, is_active, is_admin, updated_at)
                VALUES(?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title = excluded.title,
                    is_active = excluded.is_active,
                    is_admin = excluded.is_admin,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, title, int(is_active), int(is_admin)),
            )
            conn.commit()

    def set_group_active(self, chat_id: int, is_active: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE groups
                SET is_active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE chat_id = ?
                """,
                (int(is_active), chat_id),
            )
            conn.commit()

    def set_group_admin(self, chat_id: int, is_admin: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE groups
                SET is_admin = ?, updated_at = CURRENT_TIMESTAMP
                WHERE chat_id = ?
                """,
                (int(is_admin), chat_id),
            )
            conn.commit()

    def list_groups(self, only_active: bool = False) -> list[GroupItem]:
        query = """
            SELECT chat_id, title, is_active, is_admin
            FROM groups
        """
        if only_active:
            query += " WHERE is_active = 1"
        query += " ORDER BY title COLLATE NOCASE"

        with self._connect() as conn:
            rows = conn.execute(query).fetchall()

        return [
            GroupItem(
                chat_id=row["chat_id"],
                title=row["title"],
                is_active=bool(row["is_active"]),
                is_admin=bool(row["is_admin"]),
            )
            for row in rows
        ]

    def replace_links(self, links: list[str]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM links")
            conn.executemany("INSERT INTO links(url) VALUES(?)", [(url,) for url in links])
            conn.commit()

    def list_links(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT url FROM links ORDER BY id").fetchall()
        return [row["url"] for row in rows]

    def add_service(
        self,
        name: str,
        interval_minutes: int,
        group_ids: list[int],
        links: list[str],
        is_enabled: bool,
        run_now: bool = True,
    ) -> None:
        now = utc_now()
        next_run = now if run_now else now + timedelta(minutes=interval_minutes)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO services(
                    name, interval_minutes, is_enabled, group_ids_json, links_json,
                    next_run_at, last_run_at, last_status, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, NULL, '', CURRENT_TIMESTAMP)
                """,
                (
                    name,
                    interval_minutes,
                    int(is_enabled),
                    json.dumps(group_ids, ensure_ascii=True),
                    json.dumps(links, ensure_ascii=True),
                    dt_to_str(next_run),
                ),
            )
            conn.commit()

    def delete_service(self, service_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM services WHERE id = ?", (service_id,))
            conn.commit()

    def set_service_enabled(self, service_id: int, is_enabled: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE services
                SET is_enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (int(is_enabled), service_id),
            )
            conn.commit()

    def schedule_service_now(self, service_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE services
                SET next_run_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (dt_to_str(utc_now()), service_id),
            )
            conn.commit()

    def reserve_service_next_run(self, service_id: int, next_run_at: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE services
                SET next_run_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (dt_to_str(next_run_at), service_id),
            )
            conn.commit()

    def finish_service_run(self, service_id: int, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE services
                SET last_run_at = ?, last_status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (dt_to_str(utc_now()), status[:500], service_id),
            )
            conn.commit()

    def get_service(self, service_id: int) -> Optional[ServiceItem]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    id, name, interval_minutes, is_enabled,
                    group_ids_json, links_json, next_run_at, last_run_at, last_status
                FROM services
                WHERE id = ?
                """,
                (service_id,),
            ).fetchone()
        return self._row_to_service(row) if row else None

    def list_services(self) -> list[ServiceItem]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id, name, interval_minutes, is_enabled,
                    group_ids_json, links_json, next_run_at, last_run_at, last_status
                FROM services
                ORDER BY id
                """
            ).fetchall()
        return [self._row_to_service(row) for row in rows]

    def list_due_services(self, now: datetime) -> list[ServiceItem]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id, name, interval_minutes, is_enabled,
                    group_ids_json, links_json, next_run_at, last_run_at, last_status
                FROM services
                WHERE is_enabled = 1 AND next_run_at <= ?
                ORDER BY next_run_at
                """,
                (dt_to_str(now),),
            ).fetchall()
        return [self._row_to_service(row) for row in rows]

    def _row_to_service(self, row: sqlite3.Row) -> ServiceItem:
        return ServiceItem(
            id=row["id"],
            name=row["name"],
            interval_minutes=int(row["interval_minutes"]),
            is_enabled=bool(row["is_enabled"]),
            group_ids=[int(item) for item in json.loads(row["group_ids_json"])],
            links=[str(item) for item in json.loads(row["links_json"])],
            next_run_at=dt_from_str(row["next_run_at"]),
            last_run_at=dt_from_str(row["last_run_at"]) if row["last_run_at"] else None,
            last_status=row["last_status"] or "",
        )


def parse_links(raw_text: str) -> tuple[list[str], list[str]]:
    valid_links: list[str] = []
    invalid_lines: list[str] = []
    seen: set[str] = set()

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if LINK_PATTERN.match(line):
            if line not in seen:
                valid_links.append(line)
                seen.add(line)
        else:
            invalid_lines.append(line)
    return valid_links, invalid_lines


def parse_group_ids(raw_text: str) -> tuple[list[int], list[str]]:
    tokens = re.split(r"[\s,;]+", raw_text.strip())
    group_ids: list[int] = []
    invalid_tokens: list[str] = []
    seen: set[int] = set()

    for token in tokens:
        if not token:
            continue
        try:
            chat_id = int(token)
            if chat_id not in seen:
                group_ids.append(chat_id)
                seen.add(chat_id)
        except ValueError:
            invalid_tokens.append(token)
    return group_ids, invalid_tokens


class BroadcastManager:
    def __init__(self, application: Application, storage: Storage, max_concurrent: int) -> None:
        self.application = application
        self.storage = storage
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.running_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def running_keys(self) -> list[str]:
        return sorted([key for key, task in self.running_tasks.items() if not task.done()])

    def is_running(self, key: str) -> bool:
        task = self.running_tasks.get(key)
        return bool(task and not task.done())

    async def cancel_all(self) -> int:
        async with self._lock:
            tasks = [task for task in self.running_tasks.values() if not task.done()]
            for task in tasks:
                task.cancel()
        return len(tasks)

    async def start_job(
        self,
        key: str,
        group_ids: list[int],
        links: list[str],
        owner_chat_id: Optional[int],
        title: str,
        on_finish: Optional[Callable[[BroadcastResult], Awaitable[None]]] = None,
    ) -> bool:
        async with self._lock:
            existing = self.running_tasks.get(key)
            if existing and not existing.done():
                return False

            task = asyncio.create_task(
                self._run_job(
                    key=key,
                    group_ids=group_ids,
                    links=links,
                    owner_chat_id=owner_chat_id,
                    title=title,
                    on_finish=on_finish,
                )
            )
            self.running_tasks[key] = task
            return True

    async def _run_job(
        self,
        key: str,
        group_ids: list[int],
        links: list[str],
        owner_chat_id: Optional[int],
        title: str,
        on_finish: Optional[Callable[[BroadcastResult], Awaitable[None]]],
    ) -> None:
        result = BroadcastResult(sent_ok=0, sent_fail=0, total=len(group_ids) * len(links), failures=[])

        try:
            async with self.semaphore:
                if owner_chat_id:
                    await self.application.bot.send_message(
                        chat_id=owner_chat_id,
                        text=f"شروع ارسال: {title}\nشناسه کار: {key}",
                    )
                group_index = 0
                for group_id in group_ids:
                    group_index += 1
                    for link in links:
                        try:
                            await self.application.bot.send_message(chat_id=group_id, text=link)
                            result.sent_ok += 1
                        except TelegramError as exc:
                            result.sent_fail += 1
                            result.failures.append(f"{group_id}: {exc}")
                        if SEND_DELAY_SECONDS > 0:
                            await asyncio.sleep(SEND_DELAY_SECONDS)
                    if owner_chat_id:
                        await self.application.bot.send_message(
                            chat_id=owner_chat_id,
                            text=f"{title}: گروه {group_index}/{len(group_ids)} تمام شد ({group_id})",
                        )
        except asyncio.CancelledError:
            result.stopped = True
            raise
        except Exception as exc:  # pragma: no cover
            LOGGER.exception("Unexpected error in broadcast task", exc_info=exc)
            result.error = str(exc)
        finally:
            if owner_chat_id:
                summary = (
                    f"ارسال تمام شد: {title}\n"
                    f"شناسه کار: {key}\n"
                    f"موفق: {result.sent_ok}\n"
                    f"ناموفق: {result.sent_fail}\n"
                    f"کل: {result.total}"
                )
                if result.stopped:
                    summary += "\nوضعیت: متوقف شد"
                if result.error:
                    summary += f"\nخطا: {result.error}"
                if result.failures:
                    summary += "\nچند خطای اول:\n" + "\n".join(result.failures[:8])
                try:
                    await self.application.bot.send_message(chat_id=owner_chat_id, text=summary)
                except TelegramError:
                    LOGGER.warning("ارسال گزارش نهایی به مالک ناموفق بود: %s", owner_chat_id)

            if on_finish:
                try:
                    await on_finish(result)
                except Exception as exc:  # pragma: no cover
                    LOGGER.exception("on_finish callback failed", exc_info=exc)

            async with self._lock:
                self.running_tasks.pop(key, None)


def get_storage(application: Application) -> Storage:
    return application.bot_data["storage"]


def get_manager(application: Application) -> BroadcastManager:
    return application.bot_data["manager"]


def get_owner_id(application: Application) -> Optional[int]:
    return get_storage(application).get_owner_id()


def parse_owner_id_env() -> Optional[int]:
    if not OWNER_ID_ENV:
        return None
    try:
        return int(OWNER_ID_ENV)
    except ValueError:
        LOGGER.error("OWNER_ID must be an integer. Ignoring invalid OWNER_ID env value.")
        return None


def sync_owner_from_env(storage: Storage) -> Optional[int]:
    configured_owner = parse_owner_id_env()
    if configured_owner is None:
        return None
    current_owner = storage.get_owner_id()
    if current_owner != configured_owner:
        storage.set_owner_id(configured_owner)
        LOGGER.info("Owner ID synchronized from OWNER_ID env: %s", configured_owner)
    return configured_owner


async def owner_required(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    owner_id = get_owner_id(context.application)
    configured_owner = context.application.bot_data.get("configured_owner_id")
    user = update.effective_user
    chat = update.effective_chat

    if user is None:
        return False

    if configured_owner and owner_id != configured_owner:
        get_storage(context.application).set_owner_id(configured_owner)
        owner_id = configured_owner

    if owner_id is None:
        if chat and chat.type == ChatType.PRIVATE and update.effective_message:
            await update.effective_message.reply_text("مالک هنوز تنظیم نشده است. ابتدا در پی‌وی /claim را بزن.")
        return False

    if user.id != owner_id:
        if chat and chat.type == ChatType.PRIVATE and update.effective_message:
            await update.effective_message.reply_text("فقط مالک ربات اجازه اجرای این دستور را دارد.")
        return False

    return True


def build_group_selector(groups: list[GroupItem], selected: set[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for group in groups:
        checked = "✅" if group.chat_id in selected else "⬜"
        admin_mark = "ادمین" if group.is_admin else "غیرادمین"
        label = f"{checked} {group.title} ({admin_mark})"
        rows.append([InlineKeyboardButton(label[:64], callback_data=f"sel:toggle:{group.chat_id}")])

    rows.append(
        [
            InlineKeyboardButton("انتخاب همه", callback_data="sel:all"),
            InlineKeyboardButton("پاک کردن", callback_data="sel:none"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton("شروع ارسال", callback_data="sel:send"),
            InlineKeyboardButton("لغو", callback_data="sel:cancel"),
        ]
    )
    return InlineKeyboardMarkup(rows)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if STRICT_OWNER_ONLY and not await owner_required(update, context):
        return
    if update.effective_chat and update.effective_chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text(
            "ربات فعال است.\n"
            "برای دیدن دستورات از /help استفاده کن.\n"
            "برای دیدن آیدی خودت از /whoami استفاده کن."
        )
        return
    await update.effective_message.reply_text("بعد از افزودن و ادمین کردن ربات، در همین گروه /register بزن.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if STRICT_OWNER_ONLY and not await owner_required(update, context):
        return
    text = (
        "دستورات خصوصی:\n"
        "/claim - ثبت مالک (فقط بار اول)\n"
        "/whoami - نمایش آیدی تلگرام شما\n"
        "/setlinks - ثبت لینک‌ها (هر خط یک لینک)\n"
        "/links - نمایش لینک‌های ذخیره‌شده\n"
        "/groups - نمایش گروه‌های ذخیره‌شده\n"
        "/addgroup <chat_id> <title> - افزودن دستی گروه\n"
        "/removegroup <chat_id> - غیرفعال کردن گروه\n"
        "/refreshadmins - بروزرسانی وضعیت ادمین بودن ربات\n"
        "/sendlinks - انتخاب گروه‌ها و ارسال لینک‌ها\n"
        "/services - نمایش سرویس‌های زمان‌بندی‌شده\n"
        "/runsvc <id> - اجرای فوری یک سرویس\n"
        "/enablesvc <id> - فعال‌سازی سرویس\n"
        "/disablesvc <id> - غیرفعال‌سازی سرویس\n"
        "/jobs - نمایش کارهای در حال اجرا\n"
        "/stop - توقف همه کارهای فعال\n"
        "/cancel - لغو حالت انتظار\n\n"
        "دستور گروه:\n"
        "/register - ثبت همین گروه در لیست"
    )
    await update.effective_message.reply_text(text)


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if STRICT_OWNER_ONLY and not await owner_required(update, context):
        return
    user = update.effective_user
    if not user:
        return
    await update.effective_message.reply_text(f"آیدی عددی شما: {user.id}")


async def claim_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text("این دستور را در پی‌وی ربات اجرا کن.")
        return
    if not user:
        return

    storage = get_storage(context.application)
    configured_owner = context.application.bot_data.get("configured_owner_id")
    if configured_owner and user.id != configured_owner:
        await update.effective_message.reply_text("این ربات خصوصی است و شما دسترسی ندارید.")
        return

    current_owner = storage.get_owner_id()
    if current_owner is None:
        storage.set_owner_id(user.id)
        await update.effective_message.reply_text(f"مالک روی آیدی {user.id} ثبت شد.")
        return
    if current_owner == user.id:
        await update.effective_message.reply_text("شما از قبل مالک هستید.")
        return
    await update.effective_message.reply_text("مالک قبلا ثبت شده و شما دسترسی ندارید.")


async def set_links_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str) -> None:
    links, invalid_lines = parse_links(raw_text)
    if invalid_lines:
        invalid_preview = "\n".join(invalid_lines[:10])
        await update.effective_message.reply_text(
            "چند خط نامعتبر شناسایی شد. فقط لینک‌های http/https مجاز هستند.\n" f"{invalid_preview}"
        )
        return
    if not links:
        await update.effective_message.reply_text("هیچ لینک معتبری پیدا نشد.")
        return

    storage = get_storage(context.application)
    storage.replace_links(links)
    await update.effective_message.reply_text(f"{len(links)} لینک با موفقیت ذخیره شد.")


async def setlinks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    payload = (update.effective_message.text or "").split(maxsplit=1)
    if len(payload) > 1 and payload[1].strip():
        await set_links_from_text(update, context, payload[1].strip())
        return
    context.user_data["awaiting_links"] = True
    await update.effective_message.reply_text(
        "الان لینک‌ها را بفرست (هر خط یک لینک).\nمثال:\nhttps://t.me/channel1\nhttps://t.me/channel2"
    )


async def links_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    links = get_storage(context.application).list_links()
    if not links:
        await update.effective_message.reply_text("هنوز لینکی ذخیره نشده.")
        return
    lines = [f"{index + 1}. {link}" for index, link in enumerate(links)]
    await update.effective_message.reply_text("لینک‌های ذخیره‌شده:\n" + "\n".join(lines))


async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    groups = get_storage(context.application).list_groups()
    if not groups:
        await update.effective_message.reply_text("هنوز گروهی ذخیره نشده.")
        return
    lines = []
    for index, group in enumerate(groups, start=1):
        status = "فعال" if group.is_active else "غیرفعال"
        admin = "ادمین" if group.is_admin else "غیرادمین"
        lines.append(f"{index}. {group.title} | {group.chat_id} | {status} | {admin}")
    await update.effective_message.reply_text("گروه‌های ذخیره‌شده:\n" + "\n".join(lines))


async def addgroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    parts = (update.effective_message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await update.effective_message.reply_text("نحوه استفاده: /addgroup <chat_id> <title>")
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await update.effective_message.reply_text("شناسه chat_id باید عدد صحیح باشد.")
        return
    title = parts[2].strip()
    if not title:
        await update.effective_message.reply_text("عنوان گروه نمی‌تواند خالی باشد.")
        return
    get_storage(context.application).upsert_group(chat_id=chat_id, title=title, is_active=True, is_admin=False)
    await update.effective_message.reply_text("گروه ذخیره شد.")


async def removegroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    parts = (update.effective_message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.effective_message.reply_text("نحوه استفاده: /removegroup <chat_id>")
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await update.effective_message.reply_text("شناسه chat_id باید عدد صحیح باشد.")
        return
    get_storage(context.application).set_group_active(chat_id, is_active=False)
    await update.effective_message.reply_text("گروه غیرفعال شد.")


async def register_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if STRICT_OWNER_ONLY and not await owner_required(update, context):
        return
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await update.effective_message.reply_text("این دستور باید داخل گروه اجرا شود.")
        return
    if not user:
        return
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            await update.effective_message.reply_text("فقط ادمین‌های گروه می‌توانند این گروه را ثبت کنند.")
            return
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        is_bot_admin = bot_member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
        get_storage(context.application).upsert_group(
            chat_id=chat.id,
            title=chat.title or str(chat.id),
            is_active=True,
            is_admin=is_bot_admin,
        )
        await update.effective_message.reply_text("گروه ثبت شد.")
    except TelegramError as exc:
        await update.effective_message.reply_text(f"ثبت گروه ناموفق بود: {exc}")


async def refresh_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    storage = get_storage(context.application)
    groups = storage.list_groups(only_active=True)
    if not groups:
        await update.effective_message.reply_text("گروه فعال برای بررسی وجود ندارد.")
        return

    updated_count = 0
    for group in groups:
        try:
            bot_member = await context.bot.get_chat_member(group.chat_id, context.bot.id)
            is_admin = bot_member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
            storage.set_group_admin(group.chat_id, is_admin=is_admin)
            updated_count += 1
        except TelegramError:
            storage.set_group_active(group.chat_id, is_active=False)
    await update.effective_message.reply_text(
        f"{len(groups)} گروه بررسی شد. وضعیت {updated_count} گروه بروزرسانی شد."
    )


async def sendlinks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    storage = get_storage(context.application)
    groups = storage.list_groups(only_active=True)
    if not groups:
        await update.effective_message.reply_text("هیچ گروه فعالی موجود نیست.")
        return
    links = storage.list_links()
    if not links:
        await update.effective_message.reply_text("لینکی ذخیره نشده. اول /setlinks بزن.")
        return
    selected = {group.chat_id for group in groups}
    context.user_data["selected_groups"] = selected
    await update.effective_message.reply_text(
        f"گروه‌های هدف را انتخاب کن ({len(selected)}/{len(groups)} انتخاب شده):",
        reply_markup=build_group_selector(groups, selected),
    )


async def selector_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    if not await owner_required(update, context):
        await query.answer("شما دسترسی ندارید", show_alert=True)
        return

    storage = get_storage(context.application)
    groups = storage.list_groups(only_active=True)
    available_group_ids = {group.chat_id for group in groups}
    selected = context.user_data.get("selected_groups", set())
    if not isinstance(selected, set):
        selected = set()
    selected = selected & available_group_ids

    data = query.data
    if data == "sel:all":
        selected = set(available_group_ids)
    elif data == "sel:none":
        selected = set()
    elif data == "sel:cancel":
        context.user_data.pop("selected_groups", None)
        await query.edit_message_text("انتخاب لغو شد.")
        return
    elif data == "sel:send":
        if not selected:
            await query.answer("هیچ گروهی انتخاب نشده.", show_alert=True)
            return
        links = storage.list_links()
        if not links:
            await query.answer("هیچ لینکی ذخیره نشده.", show_alert=True)
            return
        manager = get_manager(context.application)
        owner_chat_id = update.effective_chat.id if update.effective_chat else None
        key = f"manual:{datetime.now().timestamp()}:{secrets.token_hex(3)}"
        started = await manager.start_job(
            key=key,
            group_ids=sorted(selected),
            links=links,
            owner_chat_id=owner_chat_id,
            title="manual-broadcast",
        )
        if not started:
            await query.answer("شروع ارسال ناموفق بود", show_alert=True)
            return
        await query.edit_message_text(
            f"ارسال دستی در صف اجرا قرار گرفت.\nشناسه: {key}\nتعداد گروه: {len(selected)}\nتعداد لینک: {len(links)}"
        )
        return
    elif data.startswith("sel:toggle:"):
        try:
            chat_id = int(data.split(":")[2])
        except (IndexError, ValueError):
            await query.answer("انتخاب نامعتبر")
            return
        if chat_id in selected:
            selected.remove(chat_id)
        else:
            selected.add(chat_id)
    else:
        await query.answer("عملیات نامشخص")
        return

    context.user_data["selected_groups"] = selected
    await query.edit_message_text(
        f"گروه‌های هدف را انتخاب کن ({len(selected)}/{len(groups)} انتخاب شده):",
        reply_markup=build_group_selector(groups, selected),
    )


async def services_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    services = get_storage(context.application).list_services()
    if not services:
        await update.effective_message.reply_text("هیچ سرویسی تعریف نشده.")
        return
    lines = []
    for service in services:
        state = "فعال" if service.is_enabled else "غیرفعال"
        next_run = dt_to_str(service.next_run_at)
        last_run = dt_to_str(service.last_run_at) if service.last_run_at else "-"
        lines.append(
            f"{service.id}. {service.name} | هر {service.interval_minutes} دقیقه | "
            f"{state} | اجرای بعدی: {next_run} | آخرین اجرا: {last_run}"
        )
    await update.effective_message.reply_text("سرویس‌ها:\n" + "\n".join(lines))


async def _service_control(update: Update, context: ContextTypes.DEFAULT_TYPE, enable: Optional[bool]) -> None:
    if not await owner_required(update, context):
        return
    parts = (update.effective_message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        usage = "/runsvc <id>" if enable is None else ("/enablesvc <id>" if enable else "/disablesvc <id>")
        await update.effective_message.reply_text(f"نحوه استفاده: {usage}")
        return
    try:
        service_id = int(parts[1].strip())
    except ValueError:
        await update.effective_message.reply_text("شناسه سرویس باید عدد صحیح باشد.")
        return

    storage = get_storage(context.application)
    service = storage.get_service(service_id)
    if not service:
        await update.effective_message.reply_text("سرویس پیدا نشد.")
        return

    if enable is None:
        storage.schedule_service_now(service_id)
        await update.effective_message.reply_text("سرویس برای اجرای فوری زمان‌بندی شد.")
    else:
        storage.set_service_enabled(service_id, enable)
        if enable:
            storage.schedule_service_now(service_id)
        state = "فعال شد" if enable else "غیرفعال شد"
        await update.effective_message.reply_text(f"سرویس {state}.")


async def runsvc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _service_control(update, context, enable=None)


async def enablesvc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _service_control(update, context, enable=True)


async def disablesvc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _service_control(update, context, enable=False)


async def jobs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    running = get_manager(context.application).running_keys()
    if not running:
        await update.effective_message.reply_text("در حال حاضر کاری در حال اجرا نیست.")
        return
    await update.effective_message.reply_text("کارهای در حال اجرا:\n" + "\n".join(f"- {key}" for key in running))


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    cancelled = await get_manager(context.application).cancel_all()
    await update.effective_message.reply_text(f"دستور توقف برای {cancelled} کار ارسال شد.")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("awaiting_links", None)
    await update.effective_message.reply_text("لغو شد.")


async def private_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE:
        return
    if not await owner_required(update, context):
        return
    if context.user_data.get("awaiting_links"):
        context.user_data["awaiting_links"] = False
        await set_links_from_text(update, context, update.effective_message.text or "")


async def my_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    status_update = update.my_chat_member
    if status_update is None:
        return
    chat = status_update.chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    new_status = status_update.new_chat_member.status
    is_active = new_status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    is_admin = new_status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    get_storage(context.application).upsert_group(
        chat_id=chat.id,
        title=chat.title or str(chat.id),
        is_active=is_active,
        is_admin=is_admin,
    )


async def scheduler_loop(application: Application) -> None:
    storage = get_storage(application)
    manager = get_manager(application)
    while True:
        try:
            now = utc_now()
            due_services = storage.list_due_services(now)
            if due_services:
                owner_id = storage.get_owner_id()
                for service in due_services:
                    key = f"service:{service.id}"
                    if manager.is_running(key):
                        continue
                    next_run = now + timedelta(minutes=service.interval_minutes)
                    storage.reserve_service_next_run(service.id, next_run)

                    async def on_finish(result: BroadcastResult, svc: ServiceItem = service) -> None:
                        status = (
                            f"ok:{result.sent_ok} fail:{result.sent_fail} total:{result.total}"
                            if not result.error
                            else f"error:{result.error}"
                        )
                        if result.stopped:
                            status = "stopped"
                        storage.finish_service_run(svc.id, status)

                    notify_owner = owner_id if SERVICE_NOTIFY_OWNER else None
                    await manager.start_job(
                        key=key,
                        group_ids=service.group_ids,
                        links=service.links,
                        owner_chat_id=notify_owner,
                        title=f"service:{service.name}",
                        on_finish=on_finish,
                    )
        except Exception as exc:  # pragma: no cover
            LOGGER.exception("Scheduler loop failed", exc_info=exc)

        await asyncio.sleep(SCHEDULER_POLL_SECONDS)


async def post_init(application: Application) -> None:
    application.bot_data["scheduler_task"] = asyncio.create_task(scheduler_loop(application))


async def post_shutdown(application: Application) -> None:
    scheduler_task = application.bot_data.get("scheduler_task")
    if scheduler_task and not scheduler_task.done():
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
    await get_manager(application).cancel_all()


def create_web_app(storage: Storage) -> FastAPI:
    app = FastAPI(title="پنل مدیریت ربات تلگرام")
    panel_prefix = "/" + WEB_PANEL_PATH
    panel_sessions: set[str] = set()

    def _panel_url(path: str = "", message: str = "") -> str:
        base = f"{panel_prefix}{path}"
        if message:
            return f"{base}?msg={quote_plus(message)}"
        return base

    def _is_authenticated(request: Request) -> bool:
        if not WEB_PANEL_REQUIRE_LOGIN:
            return True
        session_id = request.cookies.get("panel_session", "")
        return bool(session_id and session_id in panel_sessions)

    async def _require_auth(request: Request) -> Optional[RedirectResponse]:
        if _is_authenticated(request):
            return None
        return RedirectResponse(url=_panel_url("/login", "لطفا وارد شوید"), status_code=303)

    def _render_login_page(message: str = "") -> str:
        message_html = f"<p style='color:#b22;'>{escape(message)}</p>" if message else ""
        return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>ورود پنل ربات</title>
  <style>
    body {{ font-family: Tahoma, Arial, sans-serif; max-width: 420px; margin: 48px auto; }}
    .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 16px; }}
    input {{ width: 100%; padding: 8px; margin: 4px 0 10px 0; box-sizing: border-box; }}
    button {{ padding: 8px 14px; }}
  </style>
</head>
<body>
  <div class="card">
    <h3>ورود به پنل مدیریت</h3>
    {message_html}
    <form method="post" action="{panel_prefix}/login">
      <label>نام کاربری</label>
      <input type="text" name="username" required />
      <label>رمز عبور</label>
      <input type="password" name="password" required />
      <button type="submit">ورود</button>
    </form>
  </div>
</body>
</html>
"""

    def _render_page(message: str = "") -> str:
        groups = storage.list_groups()
        links = storage.list_links()
        services = storage.list_services()

        group_rows = "".join(
            f"<tr><td>{escape(g.title)}</td><td>{g.chat_id}</td>"
            f"<td>{'فعال' if g.is_active else 'غیرفعال'}</td>"
            f"<td>{'ادمین' if g.is_admin else 'غیرادمین'}</td></tr>"
            for g in groups
        )
        if not group_rows:
            group_rows = "<tr><td colspan='4'>گروهی ثبت نشده است</td></tr>"

        links_text = "\n".join(links)
        group_checkbox = "".join(
            "<label style='display:block;margin-bottom:4px;'>"
            f"<input type='checkbox' name='group_ids' value='{g.chat_id}'> "
            f"{escape(g.title)} ({g.chat_id})"
            "</label>"
            for g in groups
            if g.is_active
        )
        if not group_checkbox:
            group_checkbox = "<p>هیچ گروه فعالی وجود ندارد.</p>"

        service_rows = ""
        for svc in services:
            state = "فعال" if svc.is_enabled else "غیرفعال"
            service_rows += (
                "<tr>"
                f"<td>{svc.id}</td>"
                f"<td>{escape(svc.name)}</td>"
                f"<td>{svc.interval_minutes} دقیقه</td>"
                f"<td>{state}</td>"
                f"<td>{dt_to_str(svc.next_run_at)}</td>"
                f"<td>{dt_to_str(svc.last_run_at) if svc.last_run_at else '-'}</td>"
                f"<td>{escape(svc.last_status)}</td>"
                f"<td>{len(svc.group_ids)}</td>"
                f"<td>{len(svc.links)}</td>"
                "<td>"
                f"<form method='post' action='{panel_prefix}/services/{svc.id}/run' style='display:inline;'>"
                "<button type='submit'>اجرای فوری</button></form> "
                f"<form method='post' action='{panel_prefix}/services/{svc.id}/toggle' style='display:inline;'>"
                f"<button type='submit'>{'غیرفعال' if svc.is_enabled else 'فعال'}</button></form> "
                f"<form method='post' action='{panel_prefix}/services/{svc.id}/delete' style='display:inline;'>"
                "<button type='submit'>حذف</button></form>"
                "</td>"
                "</tr>"
            )
        if not service_rows:
            service_rows = "<tr><td colspan='10'>سرویسی ثبت نشده است</td></tr>"

        message_html = f"<p style='color:#0a6'>{escape(message)}</p>" if message else ""
        auth_status = (
            f"<p style='color:#0a6;'>ورود با نام کاربری فعال است: {escape(WEB_PANEL_USERNAME)}</p>"
            if WEB_PANEL_REQUIRE_LOGIN
            else "<p style='color:#b22;'>هشدار: پنل بدون لاگین اجرا شده است.</p>"
        )

        return f"""
<!doctype html>
<html>
<head>
    <meta charset="utf-8" />
    <title>پنل مدیریت ربات</title>
    <style>
      body {{ font-family: Tahoma, Arial, sans-serif; margin: 20px; }}
      textarea {{ width: 100%; min-height: 120px; }}
      table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
      th, td {{ border: 1px solid #ddd; padding: 6px; text-align: right; }}
      .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 12px; margin-bottom: 16px; }}
      .group-box {{ max-height: 220px; overflow-y: auto; border: 1px solid #ddd; padding: 8px; }}
    </style>
</head>
<body>
    <h2>پنل مدیریت ربات تلگرام</h2>
    {auth_status}
    {message_html}
    <p><a href="{panel_prefix}/logout">خروج از پنل</a></p>

    <div class="card">
      <h3>گروه‌ها</h3>
      <form method="post" action="{panel_prefix}/groups/add">
        <label>شناسه گروه (chat_id)</label><br />
        <input type="text" name="chat_id" required />
        <br />
        <label>عنوان گروه</label><br />
        <input type="text" name="title" required />
        <br /><br />
        <button type="submit">افزودن گروه</button>
      </form>
      <table>
        <thead><tr><th>عنوان</th><th>chat_id</th><th>وضعیت</th><th>وضعیت ادمین ربات</th></tr></thead>
        <tbody>{group_rows}</tbody>
      </table>
    </div>

    <div class="card">
      <h3>لینک‌های سراسری</h3>
      <form method="post" action="{panel_prefix}/links/update">
        <textarea name="links_text" placeholder="در هر خط یک لینک">{escape(links_text)}</textarea>
        <br /><button type="submit">ذخیره لینک‌ها</button>
      </form>
    </div>

    <div class="card">
      <h3>ساخت سرویس زمان‌بندی‌شده</h3>
      <form method="post" action="{panel_prefix}/services/add">
        <label>نام سرویس</label><br />
        <input type="text" name="name" required />
        <br /><br />
        <label>بازه اجرا (دقیقه)</label><br />
        <input type="number" min="1" name="interval_minutes" value="30" required />
        <br /><br />
        <label><input type="checkbox" name="is_enabled" checked /> فعال باشد</label>
        <label style="margin-right:12px;"><input type="checkbox" name="run_now" checked /> بلافاصله اجرا شود</label>
        <br /><br />
        <label>گروه‌های هدف</label>
        <div class="group-box">{group_checkbox}</div>
        <br />
        <label>لینک‌های اختصاصی این سرویس (در صورت خالی بودن، از لینک‌های سراسری استفاده می‌شود)</label>
        <textarea name="service_links_text" placeholder="در هر خط یک لینک"></textarea>
        <br /><button type="submit">ساخت سرویس</button>
      </form>
    </div>

    <div class="card">
      <h3>لیست سرویس‌ها</h3>
      <table>
        <thead>
          <tr>
            <th>شناسه</th><th>نام</th><th>بازه</th><th>وضعیت</th><th>اجرای بعدی</th>
            <th>آخرین اجرا</th><th>آخرین وضعیت</th><th>تعداد گروه</th><th>تعداد لینک</th><th>عملیات</th>
          </tr>
        </thead>
        <tbody>{service_rows}</tbody>
      </table>
    </div>
</body>
</html>
"""

    @app.get("/", response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        return HTMLResponse("Panel endpoint is hidden.", status_code=404)

    @app.get(f"{panel_prefix}/login", response_class=HTMLResponse)
    async def login_page(msg: str = "") -> HTMLResponse:
        if not WEB_PANEL_REQUIRE_LOGIN:
            return HTMLResponse("<h3>Login is disabled. Configure WEB_PANEL_USERNAME and WEB_PANEL_PASSWORD.</h3>")
        return HTMLResponse(_render_login_page(message=msg))

    @app.post(f"{panel_prefix}/login")
    async def login_submit(request: Request) -> RedirectResponse:
        if not WEB_PANEL_REQUIRE_LOGIN:
            return RedirectResponse(url=_panel_url("/"), status_code=303)
        form = await request.form()
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", "")).strip()
        if username != WEB_PANEL_USERNAME or password != WEB_PANEL_PASSWORD:
            return RedirectResponse(url=_panel_url("/login", "نام کاربری یا رمز عبور اشتباه است"), status_code=303)
        session_id = secrets.token_urlsafe(32)
        panel_sessions.add(session_id)
        response = RedirectResponse(url=_panel_url("/"), status_code=303)
        response.set_cookie("panel_session", session_id, httponly=True, samesite="lax", max_age=86400)
        return response

    @app.get(f"{panel_prefix}/logout")
    async def logout(request: Request) -> RedirectResponse:
        session_id = request.cookies.get("panel_session", "")
        if session_id in panel_sessions:
            panel_sessions.remove(session_id)
        response = RedirectResponse(url=_panel_url("/login", "خارج شدید"), status_code=303)
        response.delete_cookie("panel_session")
        return response

    @app.get(f"{panel_prefix}", response_class=HTMLResponse)
    @app.get(f"{panel_prefix}/", response_class=HTMLResponse)
    async def index(request: Request, msg: str = "") -> HTMLResponse | RedirectResponse:
        unauthorized = await _require_auth(request)
        if unauthorized:
            return unauthorized
        return HTMLResponse(_render_page(message=msg))

    @app.post(f"{panel_prefix}/groups/add")
    async def add_group(request: Request) -> RedirectResponse:
        unauthorized = await _require_auth(request)
        if unauthorized:
            return unauthorized
        form = await request.form()
        try:
            chat_id = int(str(form.get("chat_id", "")).strip())
        except ValueError:
            return RedirectResponse(url=_panel_url("/", "chat_id نامعتبر است"), status_code=303)
        title = str(form.get("title", "")).strip()
        if not title:
            return RedirectResponse(url=_panel_url("/", "عنوان گروه الزامی است"), status_code=303)
        storage.upsert_group(chat_id=chat_id, title=title, is_active=True, is_admin=False)
        return RedirectResponse(url=_panel_url("/", "گروه با موفقیت ذخیره شد"), status_code=303)

    @app.post(f"{panel_prefix}/links/update")
    async def update_links(request: Request) -> RedirectResponse:
        unauthorized = await _require_auth(request)
        if unauthorized:
            return unauthorized
        form = await request.form()
        links, invalid = parse_links(str(form.get("links_text", "")))
        if invalid:
            return RedirectResponse(url=_panel_url("/", "برخی خطوط لینک نامعتبر هستند"), status_code=303)
        storage.replace_links(links)
        return RedirectResponse(url=_panel_url("/", "لینک‌ها ذخیره شدند"), status_code=303)

    @app.post(f"{panel_prefix}/services/add")
    async def add_service(request: Request) -> RedirectResponse:
        unauthorized = await _require_auth(request)
        if unauthorized:
            return unauthorized
        form = await request.form()
        name = str(form.get("name", "")).strip()
        if not name:
            return RedirectResponse(url=_panel_url("/", "نام سرویس الزامی است"), status_code=303)
        try:
            interval_minutes = int(str(form.get("interval_minutes", "0")).strip())
        except ValueError:
            interval_minutes = 0
        if interval_minutes <= 0:
            return RedirectResponse(url=_panel_url("/", "بازه زمانی باید بیشتر از صفر باشد"), status_code=303)

        group_ids: list[int] = []
        for key, value in form.multi_items():
            if key != "group_ids":
                continue
            try:
                group_ids.append(int(str(value)))
            except ValueError:
                continue
        group_ids = sorted(set(group_ids))
        if not group_ids:
            return RedirectResponse(url=_panel_url("/", "حداقل یک گروه انتخاب کنید"), status_code=303)

        service_links_text = str(form.get("service_links_text", ""))
        if service_links_text.strip():
            links, invalid = parse_links(service_links_text)
            if invalid or not links:
                return RedirectResponse(url=_panel_url("/", "لینک‌های سرویس نامعتبر هستند"), status_code=303)
        else:
            links = storage.list_links()
            if not links:
                return RedirectResponse(url=_panel_url("/", "ابتدا لینک سراسری ثبت کنید"), status_code=303)

        is_enabled = "is_enabled" in form
        run_now = "run_now" in form
        try:
            storage.add_service(
                name=name,
                interval_minutes=interval_minutes,
                group_ids=group_ids,
                links=links,
                is_enabled=is_enabled,
                run_now=run_now,
            )
        except sqlite3.IntegrityError:
            return RedirectResponse(url=_panel_url("/", "نام سرویس تکراری است"), status_code=303)
        return RedirectResponse(url=_panel_url("/", "سرویس ایجاد شد"), status_code=303)

    @app.post(f"{panel_prefix}/services/{{service_id}}/toggle")
    async def toggle_service(service_id: int, request: Request) -> RedirectResponse:
        unauthorized = await _require_auth(request)
        if unauthorized:
            return unauthorized
        service = storage.get_service(service_id)
        if not service:
            return RedirectResponse(url=_panel_url("/", "سرویس پیدا نشد"), status_code=303)
        new_value = not service.is_enabled
        storage.set_service_enabled(service_id, new_value)
        if new_value:
            storage.schedule_service_now(service_id)
        return RedirectResponse(url=_panel_url("/", "وضعیت سرویس تغییر کرد"), status_code=303)

    @app.post(f"{panel_prefix}/services/{{service_id}}/run")
    async def run_service_now(service_id: int, request: Request) -> RedirectResponse:
        unauthorized = await _require_auth(request)
        if unauthorized:
            return unauthorized
        if not storage.get_service(service_id):
            return RedirectResponse(url=_panel_url("/", "سرویس پیدا نشد"), status_code=303)
        storage.schedule_service_now(service_id)
        return RedirectResponse(url=_panel_url("/", "سرویس برای اجرا صف‌بندی شد"), status_code=303)

    @app.post(f"{panel_prefix}/services/{{service_id}}/delete")
    async def delete_service(service_id: int, request: Request) -> RedirectResponse:
        unauthorized = await _require_auth(request)
        if unauthorized:
            return unauthorized
        storage.delete_service(service_id)
        return RedirectResponse(url=_panel_url("/", "سرویس حذف شد"), status_code=303)

    return app


def run_web_panel(storage: Storage) -> None:
    app = create_web_app(storage)
    uvicorn.run(app, host=WEB_PANEL_HOST, port=WEB_PANEL_PORT, log_level="info")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.exception("Unhandled error while processing update", exc_info=context.error)


def build_application(storage: Storage, configured_owner_id: Optional[int]) -> Application:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN environment variable is required.")

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.bot_data["storage"] = storage
    app.bot_data["manager"] = BroadcastManager(application=app, storage=storage, max_concurrent=MAX_CONCURRENT_BROADCASTS)
    app.bot_data["configured_owner_id"] = configured_owner_id

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("whoami", whoami_command))
    app.add_handler(CommandHandler("claim", claim_command))
    app.add_handler(CommandHandler("setlinks", setlinks_command))
    app.add_handler(CommandHandler("links", links_command))
    app.add_handler(CommandHandler("groups", groups_command))
    app.add_handler(CommandHandler("addgroup", addgroup_command))
    app.add_handler(CommandHandler("removegroup", removegroup_command))
    app.add_handler(CommandHandler("refreshadmins", refresh_admins_command))
    app.add_handler(CommandHandler("sendlinks", sendlinks_command))
    app.add_handler(CommandHandler("services", services_command))
    app.add_handler(CommandHandler("runsvc", runsvc_command))
    app.add_handler(CommandHandler("enablesvc", enablesvc_command))
    app.add_handler(CommandHandler("disablesvc", disablesvc_command))
    app.add_handler(CommandHandler("jobs", jobs_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("register", register_group_command))
    app.add_handler(CallbackQueryHandler(selector_callback, pattern=r"^sel:"))
    app.add_handler(ChatMemberHandler(my_chat_member_handler, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, private_text_handler))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    logging.basicConfig(format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO)

    storage = Storage(DB_PATH)
    storage.init()
    configured_owner_id = sync_owner_from_env(storage)

    if WEB_PANEL_ENABLED:
        web_thread = threading.Thread(target=run_web_panel, args=(storage,), daemon=True, name="web-panel")
        web_thread.start()
        LOGGER.info("Web panel started at http://%s:%s", WEB_PANEL_HOST, WEB_PANEL_PORT)

    app = build_application(storage, configured_owner_id=configured_owner_id)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
