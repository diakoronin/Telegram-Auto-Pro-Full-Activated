#!/usr/bin/env python3
from __future__ import annotations

import asyncio
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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import TelegramError
from telegram.ext import (
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
WEB_PANEL_PORT = int(os.getenv("WEB_PANEL_PORT", "8080"))
WEB_PANEL_TOKEN = os.getenv("WEB_PANEL_TOKEN", "").strip()
SERVICE_NOTIFY_OWNER = os.getenv("SERVICE_NOTIFY_OWNER", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LINK_PATTERN = re.compile(r"^https?://\S+$", re.IGNORECASE)


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
                        text=f"Job started: {title}\nKey: {key}",
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
                            text=f"{title}: group {group_index}/{len(group_ids)} finished ({group_id})",
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
                    f"Job finished: {title}\n"
                    f"Key: {key}\n"
                    f"Success: {result.sent_ok}\n"
                    f"Failed: {result.sent_fail}\n"
                    f"Total: {result.total}"
                )
                if result.stopped:
                    summary += "\nStatus: stopped"
                if result.error:
                    summary += f"\nError: {result.error}"
                if result.failures:
                    summary += "\nFirst failures:\n" + "\n".join(result.failures[:8])
                try:
                    await self.application.bot.send_message(chat_id=owner_chat_id, text=summary)
                except TelegramError:
                    LOGGER.warning("Could not send summary to owner chat %s", owner_chat_id)

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


async def owner_required(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    owner_id = get_owner_id(context.application)
    user = update.effective_user
    chat = update.effective_chat

    if user is None:
        return False

    if owner_id is None:
        if chat and chat.type == ChatType.PRIVATE and update.effective_message:
            await update.effective_message.reply_text("Owner is not set yet. Send /claim in private chat first.")
        return False

    if user.id != owner_id:
        if chat and chat.type == ChatType.PRIVATE and update.effective_message:
            await update.effective_message.reply_text("Only owner can run this command.")
        return False

    return True


def build_group_selector(groups: list[GroupItem], selected: set[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for group in groups:
        checked = "✅" if group.chat_id in selected else "⬜"
        admin_mark = "admin" if group.is_admin else "not-admin"
        label = f"{checked} {group.title} ({admin_mark})"
        rows.append([InlineKeyboardButton(label[:64], callback_data=f"sel:toggle:{group.chat_id}")])

    rows.append(
        [
            InlineKeyboardButton("Select all", callback_data="sel:all"),
            InlineKeyboardButton("Clear", callback_data="sel:none"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton("Start send", callback_data="sel:send"),
            InlineKeyboardButton("Cancel", callback_data="sel:cancel"),
        ]
    )
    return InlineKeyboardMarkup(rows)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat and update.effective_chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text(
            "Bot is running.\n"
            "Use /help to see commands.\n"
            "Use /whoami to get your Telegram user ID."
        )
        return
    await update.effective_message.reply_text("Use /register in this group after adding and promoting the bot.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Commands (private):\n"
        "/claim - set owner (only first time)\n"
        "/whoami - show your user ID\n"
        "/setlinks - paste links one per line\n"
        "/links - show saved links\n"
        "/groups - show saved groups\n"
        "/addgroup <chat_id> <title> - add group manually\n"
        "/removegroup <chat_id> - mark group inactive\n"
        "/refreshadmins - re-check admin status in groups\n"
        "/sendlinks - choose groups and broadcast links\n"
        "/services - list scheduled services\n"
        "/runsvc <id> - schedule one service immediately\n"
        "/enablesvc <id> - enable service\n"
        "/disablesvc <id> - disable service\n"
        "/jobs - list active jobs\n"
        "/stop - stop all active jobs\n"
        "/cancel - cancel pending text input\n\n"
        "Group command:\n"
        "/register - save this group into bot list"
    )
    await update.effective_message.reply_text(text)


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    await update.effective_message.reply_text(f"Your user ID: {user.id}")


async def claim_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text("Run this command in private chat.")
        return
    if not user:
        return

    storage = get_storage(context.application)
    current_owner = storage.get_owner_id()
    if current_owner is None:
        storage.set_owner_id(user.id)
        await update.effective_message.reply_text(f"Owner set to user id: {user.id}")
        return
    if current_owner == user.id:
        await update.effective_message.reply_text("You are already owner.")
        return
    await update.effective_message.reply_text("Owner already set. You are not allowed.")


async def set_links_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str) -> None:
    links, invalid_lines = parse_links(raw_text)
    if invalid_lines:
        invalid_preview = "\n".join(invalid_lines[:10])
        await update.effective_message.reply_text(
            "Invalid lines detected. Only http/https links are accepted.\n" f"{invalid_preview}"
        )
        return
    if not links:
        await update.effective_message.reply_text("No valid links found.")
        return

    storage = get_storage(context.application)
    storage.replace_links(links)
    await update.effective_message.reply_text(f"{len(links)} links saved successfully.")


async def setlinks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    payload = (update.effective_message.text or "").split(maxsplit=1)
    if len(payload) > 1 and payload[1].strip():
        await set_links_from_text(update, context, payload[1].strip())
        return
    context.user_data["awaiting_links"] = True
    await update.effective_message.reply_text(
        "Send links now, one per line.\nExample:\nhttps://t.me/channel1\nhttps://t.me/channel2"
    )


async def links_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    links = get_storage(context.application).list_links()
    if not links:
        await update.effective_message.reply_text("No links saved yet.")
        return
    lines = [f"{index + 1}. {link}" for index, link in enumerate(links)]
    await update.effective_message.reply_text("Saved links:\n" + "\n".join(lines))


async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    groups = get_storage(context.application).list_groups()
    if not groups:
        await update.effective_message.reply_text("No groups saved yet.")
        return
    lines = []
    for index, group in enumerate(groups, start=1):
        status = "active" if group.is_active else "inactive"
        admin = "admin" if group.is_admin else "not-admin"
        lines.append(f"{index}. {group.title} | {group.chat_id} | {status} | {admin}")
    await update.effective_message.reply_text("Saved groups:\n" + "\n".join(lines))


async def addgroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    parts = (update.effective_message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await update.effective_message.reply_text("Usage: /addgroup <chat_id> <title>")
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await update.effective_message.reply_text("chat_id must be an integer.")
        return
    title = parts[2].strip()
    if not title:
        await update.effective_message.reply_text("Group title cannot be empty.")
        return
    get_storage(context.application).upsert_group(chat_id=chat_id, title=title, is_active=True, is_admin=False)
    await update.effective_message.reply_text("Group saved.")


async def removegroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    parts = (update.effective_message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.effective_message.reply_text("Usage: /removegroup <chat_id>")
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await update.effective_message.reply_text("chat_id must be an integer.")
        return
    get_storage(context.application).set_group_active(chat_id, is_active=False)
    await update.effective_message.reply_text("Group marked as inactive.")


async def register_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await update.effective_message.reply_text("Use this command in a group.")
        return
    if not user:
        return
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            await update.effective_message.reply_text("Only group admins can register this group.")
            return
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        is_bot_admin = bot_member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
        get_storage(context.application).upsert_group(
            chat_id=chat.id,
            title=chat.title or str(chat.id),
            is_active=True,
            is_admin=is_bot_admin,
        )
        await update.effective_message.reply_text("Group registered.")
    except TelegramError as exc:
        await update.effective_message.reply_text(f"Register failed: {exc}")


async def refresh_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    storage = get_storage(context.application)
    groups = storage.list_groups(only_active=True)
    if not groups:
        await update.effective_message.reply_text("No active groups to refresh.")
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
    await update.effective_message.reply_text(f"Checked {len(groups)} groups. Refreshed: {updated_count}.")


async def sendlinks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    storage = get_storage(context.application)
    groups = storage.list_groups(only_active=True)
    if not groups:
        await update.effective_message.reply_text("No active groups available.")
        return
    links = storage.list_links()
    if not links:
        await update.effective_message.reply_text("No links saved. Use /setlinks first.")
        return
    selected = {group.chat_id for group in groups}
    context.user_data["selected_groups"] = selected
    await update.effective_message.reply_text(
        f"Choose target groups ({len(selected)}/{len(groups)} selected):",
        reply_markup=build_group_selector(groups, selected),
    )


async def selector_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    if not await owner_required(update, context):
        await query.answer("Unauthorized", show_alert=True)
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
        await query.edit_message_text("Selection canceled.")
        return
    elif data == "sel:send":
        if not selected:
            await query.answer("No groups selected.", show_alert=True)
            return
        links = storage.list_links()
        if not links:
            await query.answer("No links saved.", show_alert=True)
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
            await query.answer("Could not start job", show_alert=True)
            return
        await query.edit_message_text(
            f"Manual job queued.\nKey: {key}\nGroups: {len(selected)}\nLinks: {len(links)}"
        )
        return
    elif data.startswith("sel:toggle:"):
        try:
            chat_id = int(data.split(":")[2])
        except (IndexError, ValueError):
            await query.answer("Invalid selection")
            return
        if chat_id in selected:
            selected.remove(chat_id)
        else:
            selected.add(chat_id)
    else:
        await query.answer("Unknown action")
        return

    context.user_data["selected_groups"] = selected
    await query.edit_message_text(
        f"Choose target groups ({len(selected)}/{len(groups)} selected):",
        reply_markup=build_group_selector(groups, selected),
    )


async def services_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    services = get_storage(context.application).list_services()
    if not services:
        await update.effective_message.reply_text("No services found.")
        return
    lines = []
    for service in services:
        state = "enabled" if service.is_enabled else "disabled"
        next_run = dt_to_str(service.next_run_at)
        last_run = dt_to_str(service.last_run_at) if service.last_run_at else "-"
        lines.append(
            f"{service.id}. {service.name} | every {service.interval_minutes}m | "
            f"{state} | next:{next_run} | last:{last_run}"
        )
    await update.effective_message.reply_text("Services:\n" + "\n".join(lines))


async def _service_control(update: Update, context: ContextTypes.DEFAULT_TYPE, enable: Optional[bool]) -> None:
    if not await owner_required(update, context):
        return
    parts = (update.effective_message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        usage = "/runsvc <id>" if enable is None else ("/enablesvc <id>" if enable else "/disablesvc <id>")
        await update.effective_message.reply_text(f"Usage: {usage}")
        return
    try:
        service_id = int(parts[1].strip())
    except ValueError:
        await update.effective_message.reply_text("Service id must be integer.")
        return

    storage = get_storage(context.application)
    service = storage.get_service(service_id)
    if not service:
        await update.effective_message.reply_text("Service not found.")
        return

    if enable is None:
        storage.schedule_service_now(service_id)
        await update.effective_message.reply_text("Service scheduled to run now.")
    else:
        storage.set_service_enabled(service_id, enable)
        if enable:
            storage.schedule_service_now(service_id)
        state = "enabled" if enable else "disabled"
        await update.effective_message.reply_text(f"Service {state}.")


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
        await update.effective_message.reply_text("No active jobs.")
        return
    await update.effective_message.reply_text("Active jobs:\n" + "\n".join(f"- {key}" for key in running))


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    cancelled = await get_manager(context.application).cancel_all()
    await update.effective_message.reply_text(f"Stop signal sent to {cancelled} job(s).")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("awaiting_links", None)
    await update.effective_message.reply_text("Canceled.")


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
    app = FastAPI(title="Telegram Sender Web Panel")

    def _validate_token(request: Request) -> str:
        if not WEB_PANEL_TOKEN:
            return ""
        supplied = request.query_params.get("token") or request.headers.get("x-panel-token", "")
        if supplied != WEB_PANEL_TOKEN:
            raise HTTPException(status_code=401, detail="Invalid panel token")
        return supplied

    def _render_page(token: str, message: str = "") -> str:
        groups = storage.list_groups()
        links = storage.list_links()
        services = storage.list_services()
        token_suffix = f"?token={escape(token)}" if token else ""
        token_input = f'<input type="hidden" name="token" value="{escape(token)}" />' if token else ""

        group_rows = "".join(
            f"<tr><td>{escape(g.title)}</td><td>{g.chat_id}</td>"
            f"<td>{'active' if g.is_active else 'inactive'}</td>"
            f"<td>{'admin' if g.is_admin else 'not-admin'}</td></tr>"
            for g in groups
        )
        if not group_rows:
            group_rows = "<tr><td colspan='4'>No groups</td></tr>"

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
            group_checkbox = "<p>No active groups available.</p>"

        service_rows = ""
        for svc in services:
            state = "enabled" if svc.is_enabled else "disabled"
            service_rows += (
                "<tr>"
                f"<td>{svc.id}</td>"
                f"<td>{escape(svc.name)}</td>"
                f"<td>{svc.interval_minutes}m</td>"
                f"<td>{state}</td>"
                f"<td>{dt_to_str(svc.next_run_at)}</td>"
                f"<td>{dt_to_str(svc.last_run_at) if svc.last_run_at else '-'}</td>"
                f"<td>{escape(svc.last_status)}</td>"
                f"<td>{len(svc.group_ids)}</td>"
                f"<td>{len(svc.links)}</td>"
                "<td>"
                f"<form method='post' action='/services/{svc.id}/run{token_suffix}' style='display:inline;'>"
                f"{token_input}<button type='submit'>Run now</button></form> "
                f"<form method='post' action='/services/{svc.id}/toggle{token_suffix}' style='display:inline;'>"
                f"{token_input}<button type='submit'>{'Disable' if svc.is_enabled else 'Enable'}</button></form> "
                f"<form method='post' action='/services/{svc.id}/delete{token_suffix}' style='display:inline;'>"
                f"{token_input}<button type='submit'>Delete</button></form>"
                "</td>"
                "</tr>"
            )
        if not service_rows:
            service_rows = "<tr><td colspan='10'>No services</td></tr>"

        message_html = f"<p style='color:#0a6'>{escape(message)}</p>" if message else ""
        warning = (
            "<p style='color:#a33;'>WARNING: WEB_PANEL_TOKEN is empty. Panel is open without auth.</p>"
            if not WEB_PANEL_TOKEN
            else ""
        )

        return f"""
<!doctype html>
<html>
<head>
    <meta charset="utf-8" />
    <title>Telegram Sender Panel</title>
    <style>
      body {{ font-family: Arial, sans-serif; margin: 20px; }}
      textarea {{ width: 100%; min-height: 120px; }}
      table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
      th, td {{ border: 1px solid #ddd; padding: 6px; text-align: left; }}
      .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 12px; margin-bottom: 16px; }}
      .group-box {{ max-height: 220px; overflow-y: auto; border: 1px solid #ddd; padding: 8px; }}
    </style>
</head>
<body>
    <h2>Telegram Sender Web Panel</h2>
    {warning}
    {message_html}

    <div class="card">
      <h3>Groups</h3>
      <form method="post" action="/groups/add{token_suffix}">
        {token_input}
        <label>Chat ID</label><br />
        <input type="text" name="chat_id" required />
        <br />
        <label>Title</label><br />
        <input type="text" name="title" required />
        <br /><br />
        <button type="submit">Add group</button>
      </form>
      <table>
        <thead><tr><th>Title</th><th>Chat ID</th><th>Status</th><th>Bot Admin</th></tr></thead>
        <tbody>{group_rows}</tbody>
      </table>
    </div>

    <div class="card">
      <h3>Global Links (used by /sendlinks)</h3>
      <form method="post" action="/links/update{token_suffix}">
        {token_input}
        <textarea name="links_text" placeholder="one link per line">{escape(links_text)}</textarea>
        <br /><button type="submit">Save links</button>
      </form>
    </div>

    <div class="card">
      <h3>Create Scheduled Service</h3>
      <form method="post" action="/services/add{token_suffix}">
        {token_input}
        <label>Name</label><br />
        <input type="text" name="name" required />
        <br /><br />
        <label>Interval Minutes</label><br />
        <input type="number" min="1" name="interval_minutes" value="30" required />
        <br /><br />
        <label><input type="checkbox" name="is_enabled" checked /> Enabled</label>
        <label style="margin-left:12px;"><input type="checkbox" name="run_now" checked /> Run immediately</label>
        <br /><br />
        <label>Select target groups</label>
        <div class="group-box">{group_checkbox}</div>
        <br />
        <label>Links for this service (leave empty to use global links)</label>
        <textarea name="service_links_text" placeholder="one link per line"></textarea>
        <br /><button type="submit">Create service</button>
      </form>
    </div>

    <div class="card">
      <h3>Services</h3>
      <table>
        <thead>
          <tr>
            <th>ID</th><th>Name</th><th>Interval</th><th>Status</th><th>Next Run</th>
            <th>Last Run</th><th>Last Status</th><th>Groups</th><th>Links</th><th>Actions</th>
          </tr>
        </thead>
        <tbody>{service_rows}</tbody>
      </table>
    </div>
</body>
</html>
"""

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, msg: str = "") -> HTMLResponse:
        token = _validate_token(request)
        return HTMLResponse(_render_page(token=token, message=msg))

    @app.post("/groups/add")
    async def add_group(request: Request) -> RedirectResponse:
        token = _validate_token(request)
        form = await request.form()
        try:
            chat_id = int(str(form.get("chat_id", "")).strip())
        except ValueError:
            return RedirectResponse(url=f"/?msg=invalid+chat_id&token={token}" if token else "/?msg=invalid+chat_id", status_code=303)
        title = str(form.get("title", "")).strip()
        if not title:
            return RedirectResponse(url=f"/?msg=title+is+required&token={token}" if token else "/?msg=title+is+required", status_code=303)
        storage.upsert_group(chat_id=chat_id, title=title, is_active=True, is_admin=False)
        return RedirectResponse(url=f"/?msg=group+saved&token={token}" if token else "/?msg=group+saved", status_code=303)

    @app.post("/links/update")
    async def update_links(request: Request) -> RedirectResponse:
        token = _validate_token(request)
        form = await request.form()
        links, invalid = parse_links(str(form.get("links_text", "")))
        if invalid:
            return RedirectResponse(
                url=f"/?msg=invalid+lines+in+links&token={token}" if token else "/?msg=invalid+lines+in+links",
                status_code=303,
            )
        storage.replace_links(links)
        return RedirectResponse(url=f"/?msg=links+saved&token={token}" if token else "/?msg=links+saved", status_code=303)

    @app.post("/services/add")
    async def add_service(request: Request) -> RedirectResponse:
        token = _validate_token(request)
        form = await request.form()
        name = str(form.get("name", "")).strip()
        if not name:
            return RedirectResponse(url=f"/?msg=name+required&token={token}" if token else "/?msg=name+required", status_code=303)
        try:
            interval_minutes = int(str(form.get("interval_minutes", "0")).strip())
        except ValueError:
            interval_minutes = 0
        if interval_minutes <= 0:
            return RedirectResponse(
                url=f"/?msg=interval+must+be+positive&token={token}" if token else "/?msg=interval+must+be+positive",
                status_code=303,
            )

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
            return RedirectResponse(
                url=f"/?msg=select+at+least+one+group&token={token}" if token else "/?msg=select+at+least+one+group",
                status_code=303,
            )

        service_links_text = str(form.get("service_links_text", ""))
        if service_links_text.strip():
            links, invalid = parse_links(service_links_text)
            if invalid or not links:
                return RedirectResponse(
                    url=f"/?msg=invalid+service+links&token={token}" if token else "/?msg=invalid+service+links",
                    status_code=303,
                )
        else:
            links = storage.list_links()
            if not links:
                return RedirectResponse(
                    url=f"/?msg=no+global+links+found&token={token}" if token else "/?msg=no+global+links+found",
                    status_code=303,
                )

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
            return RedirectResponse(
                url=f"/?msg=service+name+exists&token={token}" if token else "/?msg=service+name+exists",
                status_code=303,
            )
        return RedirectResponse(
            url=f"/?msg=service+created&token={token}" if token else "/?msg=service+created",
            status_code=303,
        )

    @app.post("/services/{service_id}/toggle")
    async def toggle_service(service_id: int, request: Request) -> RedirectResponse:
        token = _validate_token(request)
        service = storage.get_service(service_id)
        if not service:
            return RedirectResponse(url=f"/?msg=service+not+found&token={token}" if token else "/?msg=service+not+found", status_code=303)
        new_value = not service.is_enabled
        storage.set_service_enabled(service_id, new_value)
        if new_value:
            storage.schedule_service_now(service_id)
        return RedirectResponse(url=f"/?msg=service+updated&token={token}" if token else "/?msg=service+updated", status_code=303)

    @app.post("/services/{service_id}/run")
    async def run_service_now(service_id: int, request: Request) -> RedirectResponse:
        token = _validate_token(request)
        if not storage.get_service(service_id):
            return RedirectResponse(url=f"/?msg=service+not+found&token={token}" if token else "/?msg=service+not+found", status_code=303)
        storage.schedule_service_now(service_id)
        return RedirectResponse(url=f"/?msg=service+queued&token={token}" if token else "/?msg=service+queued", status_code=303)

    @app.post("/services/{service_id}/delete")
    async def delete_service(service_id: int, request: Request) -> RedirectResponse:
        token = _validate_token(request)
        storage.delete_service(service_id)
        return RedirectResponse(url=f"/?msg=service+deleted&token={token}" if token else "/?msg=service+deleted", status_code=303)

    return app


def run_web_panel(storage: Storage) -> None:
    app = create_web_app(storage)
    uvicorn.run(app, host=WEB_PANEL_HOST, port=WEB_PANEL_PORT, log_level="info")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.exception("Unhandled error while processing update", exc_info=context.error)


def build_application(storage: Storage) -> Application:
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

    if WEB_PANEL_ENABLED:
        web_thread = threading.Thread(target=run_web_panel, args=(storage,), daemon=True, name="web-panel")
        web_thread.start()
        LOGGER.info("Web panel started at http://%s:%s", WEB_PANEL_HOST, WEB_PANEL_PORT)

    app = build_application(storage)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
