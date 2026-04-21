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
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Awaitable, Callable, Optional
from urllib.parse import quote_plus

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import RetryAfter, TelegramError
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
SEND_DELAY_SECONDS = float(os.getenv("SEND_DELAY_SECONDS", "3.0"))
MIN_SEND_GAP_SECONDS = max(float(os.getenv("MIN_SEND_GAP_SECONDS", "2.2")), 0.0)
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
MAX_LINKS_TEXT_BYTES = 5 * 1024 * 1024
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS send_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    chat_id INTEGER,
                    link TEXT,
                    status TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

    def delete_group(self, chat_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM groups WHERE chat_id = ?", (chat_id,))
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

    def add_send_log(
        self,
        job_key: str,
        title: str,
        chat_id: Optional[int],
        link: Optional[str],
        status: str,
        detail: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO send_logs(job_key, title, chat_id, link, status, detail)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (job_key, title, chat_id, link, status, detail[:1000]),
            )
            conn.commit()

    def list_send_logs(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, job_key, title, chat_id, link, status, detail, created_at
                FROM send_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        return list(rows)

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
        # Accept common subscription formats (http/https, vmess, vless, trojan, ss, etc.)
        if "://" not in line and not line.lower().startswith(("vmess://", "vless://", "trojan://", "ss://", "ssr://", "hysteria://", "tuic://", "wireguard://")):
            invalid_lines.append(line)
            continue
        if line not in seen:
            valid_links.append(line)
            seen.add(line)
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


def parse_manual_group_input(raw_text: str) -> tuple[Optional[int], str]:
    text = raw_text.strip()
    if not text:
        return None, ""
    if "|" in text:
        left, right = text.split("|", 1)
        try:
            return int(left.strip()), right.strip()
        except ValueError:
            return None, ""
    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        return None, ""
    try:
        return int(parts[0]), parts[1].strip()
    except ValueError:
        return None, ""


def _is_txt_document(file_name: str, mime_type: str) -> bool:
    lower_name = file_name.lower()
    lower_mime = mime_type.lower()
    return lower_name.endswith(".txt") or lower_mime.startswith("text/")


def _decode_text_payload(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1256"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


class BroadcastManager:
    def __init__(self, application: Application, storage: Storage, max_concurrent: int) -> None:
        self.application = application
        self.storage = storage
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.running_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._progress: dict[str, object] | None = None
        self._last_failure_notify_at: float = 0.0
        self._send_spacing_lock = asyncio.Lock()
        self._next_send_at: float = 0.0

    def running_keys(self) -> list[str]:
        return sorted([key for key, task in self.running_tasks.items() if not task.done()])

    def is_running(self, key: str) -> bool:
        task = self.running_tasks.get(key)
        return bool(task and not task.done())

    def get_progress(self) -> Optional[dict[str, object]]:
        return dict(self._progress) if self._progress else None

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
        group_map = {group.chat_id: group for group in self.storage.list_groups()}
        self._last_failure_notify_at = 0.0
        self._progress = {
            "key": key,
            "title": title,
            "total": result.total,
            "sent_ok": 0,
            "sent_fail": 0,
            "current_group": None,
        }

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
                    if self._progress is not None:
                        self._progress["current_group"] = group_id
                    known_group = group_map.get(group_id)
                    if known_group and (not known_group.is_active or not known_group.is_admin):
                        reason_parts: list[str] = []
                        if not known_group.is_active:
                            reason_parts.append("غیرفعال است")
                        if not known_group.is_admin:
                            reason_parts.append("ربات ادمین نیست")
                        reason = " و ".join(reason_parts)
                        fail_message = f"{group_id}: ارسال رد شد ({reason})"
                        result.failures.append(fail_message)
                        for link in links:
                            result.sent_fail += 1
                            self.storage.add_send_log(
                                job_key=key,
                                title=title,
                                chat_id=group_id,
                                link=link,
                                status="blocked",
                                detail=reason,
                            )
                            if self._progress is not None:
                                self._progress["sent_fail"] = result.sent_fail
                        if owner_chat_id:
                            try:
                                await self.application.bot.send_message(
                                    chat_id=owner_chat_id,
                                    text=(
                                        f"⚠️ گروه {known_group.title} ({group_id}) قابل ارسال نیست: {reason}\n"
                                        "برای توقف فوری /stop را بزن."
                                    ),
                                )
                            except TelegramError:
                                pass
                        continue
                    for link in links:
                        sent = await self._send_with_retry(
                            key=key,
                            title=title,
                            chat_id=group_id,
                            text=link,
                            owner_chat_id=owner_chat_id,
                            result=result,
                        )
                        if sent:
                            result.sent_ok += 1
                        else:
                            result.sent_fail += 1
                        if self._progress is not None:
                            self._progress["sent_ok"] = result.sent_ok
                            self._progress["sent_fail"] = result.sent_fail
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
            LOGGER.exception("خطای غیرمنتظره در ارسال", exc_info=exc)
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
                    LOGGER.exception("اجرای on_finish ناموفق بود", exc_info=exc)

            async with self._lock:
                self.running_tasks.pop(key, None)
                self._progress = None

    async def _send_with_retry(
        self,
        key: str,
        title: str,
        chat_id: int,
        text: str,
        owner_chat_id: Optional[int],
        result: BroadcastResult,
        max_retry: int = 6,
    ) -> bool:
        attempt = 0
        while attempt < max_retry:
            try:
                await self._acquire_send_slot()
                await self.application.bot.send_message(chat_id=chat_id, text=text)
                self.storage.add_send_log(
                    job_key=key,
                    title=title,
                    chat_id=chat_id,
                    link=text,
                    status="ok",
                    detail=f"ok (attempt={attempt + 1})",
                )
                return True
            except RetryAfter as exc:
                wait_seconds = max(int(exc.retry_after), 1)
                attempt += 1
                self.storage.add_send_log(
                    job_key=key,
                    title=title,
                    chat_id=chat_id,
                    link=text,
                    status="retry",
                    detail=f"retry_after={wait_seconds}s attempt={attempt}/{max_retry}",
                )
                if owner_chat_id:
                    try:
                        await self.application.bot.send_message(
                            chat_id=owner_chat_id,
                            text=(
                                f"محدودیت تلگرام فعال شد برای گروه {chat_id}.\n"
                                f"{wait_seconds} ثانیه صبر می‌کنم و دوباره تلاش می‌کنم "
                                f"(تلاش {attempt}/{max_retry})."
                            ),
                        )
                    except TelegramError:
                        pass
                await asyncio.sleep(wait_seconds + 1)
                continue
            except TelegramError as exc:
                fail_message = f"{chat_id}: {exc}"
                result.failures.append(fail_message)
                self.storage.add_send_log(
                    job_key=key,
                    title=title,
                    chat_id=chat_id,
                    link=text,
                    status="fail",
                    detail=str(exc),
                )
                if owner_chat_id:
                    now = time.monotonic()
                    if now - self._last_failure_notify_at >= 8:
                        self._last_failure_notify_at = now
                        try:
                            await self.application.bot.send_message(
                                chat_id=owner_chat_id,
                                text=(
                                    f"⚠️ هشدار ارسال\n"
                                    f"گروه: {chat_id}\n"
                                    f"خطا: {str(exc)[:300]}\n"
                                    "برای توقف فوری /stop را بزن."
                                ),
                            )
                        except TelegramError:
                            pass
                return False

        fail_message = f"{chat_id}: تلاش مجدد بیش از حد مجاز شد (FloodControl)"
        result.failures.append(fail_message)
        self.storage.add_send_log(
            job_key=key,
            title=title,
            chat_id=chat_id,
            link=text,
            status="fail",
            detail="retry-limit-exceeded",
        )
        return False

    async def _acquire_send_slot(self) -> None:
        min_gap = max(SEND_DELAY_SECONDS, MIN_SEND_GAP_SECONDS)
        if min_gap <= 0:
            return
        async with self._send_spacing_lock:
            now = time.monotonic()
            slot_time = max(now, self._next_send_at)
            self._next_send_at = slot_time + min_gap
        wait_seconds = max(0.0, slot_time - time.monotonic())
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)


def get_storage(application: Application) -> Storage:
    return application.bot_data["storage"]


def get_manager(application: Application) -> BroadcastManager:
    return application.bot_data["manager"]


def get_owner_id(application: Application) -> Optional[int]:
    return get_storage(application).get_owner_id()


def get_ui_message_id(application: Application) -> Optional[int]:
    raw_value = get_storage(application).get_setting("ui_message_id")
    if raw_value is None:
        return None
    try:
        return int(raw_value)
    except ValueError:
        return None


def set_ui_message_id(application: Application, message_id: int) -> None:
    get_storage(application).set_setting("ui_message_id", str(message_id))


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


def build_group_selector(groups: list[GroupItem], selected: set[int], single_choice: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for group in groups:
        checked = "✅" if group.chat_id in selected else "⬜"
        admin_mark = "ادمین" if group.is_admin else "غیرادمین"
        label = f"{checked} {group.title} ({admin_mark})"
        rows.append([InlineKeyboardButton(label[:64], callback_data=f"sel:toggle:{group.chat_id}")])

    if not single_choice:
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


def build_wizard_group_selector(groups: list[GroupItem], selected_group_id: Optional[int]) -> InlineKeyboardMarkup:
    selected = {selected_group_id} if selected_group_id is not None else set()
    rows: list[list[InlineKeyboardButton]] = []
    for group in groups:
        checked = "✅" if group.chat_id in selected else "⬜"
        label = f"{checked} {group.title}"
        rows.append([InlineKeyboardButton(label[:64], callback_data=f"wiz:pick:{group.chat_id}")])
    rows.append(
        [
            InlineKeyboardButton("ارسال به گروه انتخابی", callback_data="wiz:send"),
            InlineKeyboardButton("لغو", callback_data="wiz:cancel"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _shorten_text(value: str, limit: int = 90) -> str:
    cleaned = value.replace("\n", " ").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if STRICT_OWNER_ONLY and not await owner_required(update, context):
        return
    if update.effective_chat and update.effective_chat.type == ChatType.PRIVATE:
        await send_main_menu(update, context, text="پنل ربات آماده است. از دکمه‌ها استفاده کن.")
        return
    await update.effective_message.reply_text("بعد از افزودن و ادمین کردن ربات، در همین گروه /register بزن.")


async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await open_main_menu(update, context)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if STRICT_OWNER_ONLY and not await owner_required(update, context):
        return
    text = (
        "دستورات خصوصی:\n"
        "/claim - ثبت مالک (فقط بار اول)\n"
        "/whoami - نمایش آیدی تلگرام شما\n"
        "/setlinks - ثبت لینک‌ها (هر خط یک لینک)\n"
        "/setlinksfile - حالت دریافت فایل txt لینک\n"
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


def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("شروع ارسال مرحله‌ای", callback_data="menu:start_wizard"),
                InlineKeyboardButton("نمایش گروه‌ها", callback_data="menu:show_groups"),
            ],
            [
                InlineKeyboardButton("نمایش لینک‌ها", callback_data="menu:show_links"),
                InlineKeyboardButton("وضعیت ارسال", callback_data="menu:status"),
            ],
            [
                InlineKeyboardButton("ثبت لینک با فایل txt", callback_data="menu:setlinks_file"),
            ],
            [
                InlineKeyboardButton("بررسی قابلیت ارسال", callback_data="menu:precheck"),
                InlineKeyboardButton("تغییر وضعیت گروه", callback_data="menu:toggle_group"),
            ],
            [
                InlineKeyboardButton("افزودن گروه", callback_data="menu:add_group"),
                InlineKeyboardButton("حذف گروه", callback_data="menu:remove_group"),
            ],
            [
                InlineKeyboardButton("لاگ ارسال", callback_data="menu:logs"),
            ],
            [
                InlineKeyboardButton("توقف ارسال", callback_data="menu:stop"),
                InlineKeyboardButton("لغو/بستن", callback_data="menu:close"),
            ],
        ]
    )


def _clear_wizard_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("wizard_mode", None)
    context.user_data.pop("awaiting_wizard_links", None)
    context.user_data.pop("wizard_links", None)
    context.user_data.pop("wizard_group_id", None)


async def open_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    text = (
        "پنل سریع ربات\n"
        "از دکمه‌ها استفاده کن تا مرحله‌به‌مرحله ارسال انجام شود."
    )
    await update.effective_message.reply_text(text, reply_markup=build_main_menu_keyboard())


async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await open_main_menu(update, context)


async def send_main_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str = "پنل ربات آماده است.",
) -> None:
    if not await owner_required(update, context):
        return
    await update.effective_message.reply_text(text, reply_markup=build_main_menu_keyboard())


def _build_progress_text(progress: dict[str, object]) -> str:
    total = int(progress.get("total", 0))
    sent_ok = int(progress.get("sent_ok", 0))
    sent_fail = int(progress.get("sent_fail", 0))
    processed = sent_ok + sent_fail
    percent = int((processed * 100) / total) if total > 0 else 0
    title = str(progress.get("title", "manual-broadcast"))
    key = str(progress.get("key", "-"))
    current_group = progress.get("current_group")
    current_group_text = str(current_group) if current_group is not None else "-"
    return (
        f"پیشرفت ارسال: {percent}%\n"
        f"عنوان: {title}\n"
        f"شناسه کار: {key}\n"
        f"موفق: {sent_ok}\n"
        f"ناموفق: {sent_fail}\n"
        f"پردازش‌شده: {processed}/{total}\n"
        f"گروه فعلی: {current_group_text}"
    )


def _group_send_capability_summary(groups: list[GroupItem]) -> tuple[list[str], list[str]]:
    sendable: list[str] = []
    blocked: list[str] = []
    for group in groups:
        line = f"{group.title} ({group.chat_id})"
        if group.is_active and group.is_admin:
            sendable.append(line)
        else:
            reason_parts: list[str] = []
            if not group.is_active:
                reason_parts.append("غیرفعال")
            if not group.is_admin:
                reason_parts.append("ربات ادمین نیست")
            reason = "، ".join(reason_parts) if reason_parts else "نامشخص"
            blocked.append(f"{line} -> {reason}")
    return sendable, blocked


def _build_group_manage_keyboard(groups: list[GroupItem], mode: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for group in groups[:80]:
        status = "فعال" if group.is_active else "غیرفعال"
        admin = "ادمین" if group.is_admin else "غیرادمین"
        if mode == "toggle":
            action = "غیرفعال کن" if group.is_active else "فعال کن"
            button_text = f"{action} | {group.title} ({admin})"
            callback = f"manage:toggle:{group.chat_id}"
        else:
            button_text = f"حذف | {group.title} ({status}/{admin})"
            callback = f"manage:delete:{group.chat_id}"
        rows.append([InlineKeyboardButton(button_text[:64], callback_data=callback)])

    rows.append([InlineKeyboardButton("بازگشت به پنل", callback_data="menu:back")])
    return InlineKeyboardMarkup(rows)


def _build_group_preview_text(groups: list[GroupItem], links_count: int) -> str:
    sendable, blocked = _group_send_capability_summary(groups)
    lines = [
        "پیش‌نمایش ارسال قبل از شروع:",
        f"تعداد لینک: {links_count}",
        f"گروه قابل ارسال: {len(sendable)}",
        f"گروه غیرقابل ارسال: {len(blocked)}",
    ]
    if sendable:
        lines.append("\n✅ قابل ارسال:")
        lines.extend(f"- {line}" for line in sendable[:10])
    if blocked:
        lines.append("\n⛔ غیرقابل ارسال:")
        lines.extend(f"- {line}" for line in blocked[:15])
        if len(blocked) > 15:
            lines.append(f"... و {len(blocked) - 15} مورد دیگر")
    return "\n".join(lines)


def _clear_group_manage_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("awaiting_manual_group_add", None)


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    if not await owner_required(update, context):
        await query.answer("شما دسترسی ندارید", show_alert=True)
        return

    storage = get_storage(context.application)
    manager = get_manager(context.application)
    data = query.data

    if data == "menu:start_wizard":
        _clear_wizard_state(context)
        context.user_data["wizard_mode"] = True
        context.user_data["awaiting_wizard_links"] = True
        await query.edit_message_text(
            "ویزارد شروع شد.\nمرحله 1/3: لیست لینک‌ها را بفرست (هر خط یک لینک).\nبرای لغو: /cancel"
        )
        return

    if data == "menu:show_groups":
        groups = storage.list_groups()
        if not groups:
            await query.answer("گروهی ثبت نشده.", show_alert=True)
            return
        lines = []
        for index, group in enumerate(groups, start=1):
            status = "فعال" if group.is_active else "غیرفعال"
            admin = "ادمین" if group.is_admin else "غیرادمین"
            lines.append(f"{index}. {group.title} | {group.chat_id} | {status} | {admin}")
        await query.edit_message_text(
            "گروه‌ها:\n" + "\n".join(lines),
            reply_markup=build_main_menu_keyboard(),
        )
        return

    if data == "menu:setlinks_file":
        context.user_data["wizard_mode"] = True
        context.user_data["awaiting_wizard_links"] = True
        context.user_data["awaiting_links_file"] = True
        context.user_data["awaiting_links"] = True
        await query.edit_message_text(
            "حالت ثبت لینک با فایل فعال شد.\n"
            "الان یک فایل txt بفرست (هر خط یک لینک).\n"
            "برای لغو: /cancel",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("بازگشت به پنل", callback_data="menu:back")]]
            ),
        )
        return

    if data == "menu:add_group":
        _clear_group_manage_state(context)
        context.user_data["awaiting_manual_group_add"] = True
        await query.edit_message_text(
            "فرمت افزودن گروه:\n"
            "<chat_id> | <title>\n\n"
            "مثال:\n-1001234567890 | گروه تست\n\n"
            "بعد از ارسال، گروه به لیست اضافه می‌شود.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("بازگشت به پنل", callback_data="menu:back")]]
            ),
        )
        return

    if data == "menu:remove_group":
        groups = storage.list_groups()
        if not groups:
            await query.answer("گروهی ثبت نشده.", show_alert=True)
            return
        _clear_group_manage_state(context)
        await query.edit_message_text(
            "حذف گروه: یکی از گروه‌ها را انتخاب کن.",
            reply_markup=_build_group_manage_keyboard(groups, mode="delete"),
        )
        return

    if data == "menu:precheck":
        groups = storage.list_groups()
        links = storage.list_links()
        if not groups:
            await query.answer("گروهی ثبت نشده.", show_alert=True)
            return
        await query.edit_message_text(
            _build_group_preview_text(groups, links_count=len(links)),
            reply_markup=build_main_menu_keyboard(),
        )
        return

    if data == "menu:toggle_group":
        groups = storage.list_groups()
        if not groups:
            await query.answer("گروهی ثبت نشده.", show_alert=True)
            return
        _clear_group_manage_state(context)
        await query.edit_message_text(
            "تغییر وضعیت گروه: روی گروه مورد نظر بزن (فعال/غیرفعال).",
            reply_markup=_build_group_manage_keyboard(groups, mode="toggle"),
        )
        return

    if data == "menu:show_links":
        links = storage.list_links()
        if not links:
            await query.answer("لینکی ثبت نشده.", show_alert=True)
            return
        lines = [f"{idx + 1}. {link}" for idx, link in enumerate(links[:50])]
        await query.edit_message_text(
            "لینک‌ها:\n" + "\n".join(lines),
            reply_markup=build_main_menu_keyboard(),
        )
        return

    if data == "menu:status":
        progress = manager.get_progress()
        if not progress:
            await query.answer("ارسال فعالی وجود ندارد.", show_alert=True)
            return
        await query.edit_message_text(
            _build_progress_text(progress),
            reply_markup=build_main_menu_keyboard(),
        )
        return

    if data == "menu:logs":
        rows = storage.list_send_logs(limit=15)
        if not rows:
            await query.answer("لاگ ارسالی ثبت نشده.", show_alert=True)
            return
        lines: list[str] = []
        for row in rows:
            status_mark = "✅" if row["status"] == "ok" else ("⏳" if row["status"] == "retry" else "❌")
            group_val = row["chat_id"]
            group_txt = "-" if group_val is None else str(group_val)
            lines.append(
                f"{status_mark} {row['created_at']} | {row['job_key']} | g:{group_txt} | "
                f"{row['status']} | {row['detail']}"
            )
        text = "آخرین لاگ‌ها:\n" + "\n".join(lines)
        await query.edit_message_text(text[:4000], reply_markup=build_main_menu_keyboard())
        return

    if data == "menu:stop":
        cancelled = await manager.cancel_all()
        await query.edit_message_text(
            f"درخواست توقف برای {cancelled} کار ارسال شد.",
            reply_markup=build_main_menu_keyboard(),
        )
        return

    if data == "menu:close":
        await query.edit_message_text("پنل بسته شد. برای باز کردن دوباره /panel را بزن.")
        return

    if data == "menu:back":
        _clear_group_manage_state(context)
        await query.edit_message_text("پنل اصلی", reply_markup=build_main_menu_keyboard())
        return

    if data.startswith("manage:delete:"):
        try:
            chat_id = int(data.split(":")[2])
        except (ValueError, IndexError):
            await query.answer("شناسه نامعتبر", show_alert=True)
            return
        storage.delete_group(chat_id)
        groups = storage.list_groups()
        if not groups:
            await query.edit_message_text("گروهی باقی نمانده.", reply_markup=build_main_menu_keyboard())
            return
        await query.edit_message_text(
            f"گروه {chat_id} حذف شد.",
            reply_markup=_build_group_manage_keyboard(groups, mode="delete"),
        )
        return

    if data.startswith("manage:toggle:"):
        try:
            chat_id = int(data.split(":")[2])
        except (ValueError, IndexError):
            await query.answer("شناسه نامعتبر", show_alert=True)
            return
        groups = storage.list_groups()
        group = next((item for item in groups if item.chat_id == chat_id), None)
        if not group:
            await query.answer("گروه پیدا نشد", show_alert=True)
            return
        new_active = not group.is_active
        storage.set_group_active(chat_id, is_active=new_active)
        groups = storage.list_groups()
        await query.edit_message_text(
            f"وضعیت گروه {chat_id} به {'فعال' if new_active else 'غیرفعال'} تغییر کرد.",
            reply_markup=_build_group_manage_keyboard(groups, mode='toggle'),
        )
        return

    await query.answer("عملیات نامشخص")


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
            "چند خط نامعتبر شناسایی شد.\n"
            "فرمت‌های قابل قبول: http/https و همچنین vmess/vless/trojan/ss/ssr و مشابه.\n"
            f"{invalid_preview}"
        )
        return
    if not links:
        await update.effective_message.reply_text("هیچ لینک معتبری پیدا نشد.")
        return

    storage = get_storage(context.application)
    storage.replace_links(links)
    await update.effective_message.reply_text(f"{len(links)} لینک با موفقیت ذخیره شد.")


async def set_links_from_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    file_name: str,
    file_bytes: bytes,
) -> None:
    if len(file_bytes) > MAX_LINKS_TEXT_BYTES:
        await update.effective_message.reply_text("فایل خیلی بزرگ است. حداکثر 5 مگابایت مجاز است.")
        return
    raw_text = _decode_text_payload(file_bytes)
    await set_links_from_text(update, context, raw_text)
    await update.effective_message.reply_text(f"منبع لینک‌ها: فایل {file_name}")

    # Continue automatically to group-selection step after txt upload.
    if context.user_data.get("wizard_mode") or context.user_data.get("awaiting_links_file"):
        context.user_data["wizard_mode"] = True
        links, _ = parse_links(raw_text)
        if links:
            context.user_data["wizard_links"] = links
            context.user_data["awaiting_wizard_links"] = False
            groups = get_storage(context.application).list_groups(only_active=True)
            if groups:
                first_group = groups[0].chat_id
                context.user_data["wizard_group_id"] = first_group
                if update.effective_message:
                    await update.effective_message.reply_text(
                        "مرحله 2/2: گروه مقصد را انتخاب کن، سپس روی «ارسال به گروه انتخابی» بزن.",
                        reply_markup=build_wizard_group_selector(groups, first_group),
                    )
            elif update.effective_message:
                await update.effective_message.reply_text("هیچ گروه فعالی ندارید. اول گروه ثبت کن.")


async def setlinks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    payload = (update.effective_message.text or "").split(maxsplit=1)
    if len(payload) > 1 and payload[1].strip():
        await set_links_from_text(update, context, payload[1].strip())
        return
    context.user_data["awaiting_links"] = True
    await update.effective_message.reply_text(
        "الان لینک‌ها را بفرست (هر خط یک لینک) یا یک فایل txt بفرست.\n"
        "مثال:\nhttps://t.me/channel1\nhttps://t.me/channel2"
    )


async def setlinksfile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    context.user_data["wizard_mode"] = True
    context.user_data["awaiting_wizard_links"] = True
    context.user_data["awaiting_links_file"] = True
    await update.effective_message.reply_text(
        "حالت دریافت فایل فعال شد (ارسال مرحله‌ای).\n"
        "الان یک فایل txt بفرست (هر خط یک لینک).\n"
        "برای لغو: /cancel"
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
    _clear_wizard_state(context)
    context.user_data["wizard_mode"] = True
    context.user_data["awaiting_wizard_links"] = True
    await update.effective_message.reply_text(
        "ویزارد ارسال مرحله‌ای شروع شد.\n"
        "مرحله 1/3: لیست لینک‌ها را بفرست (هر خط یک لینک).\n"
        "برای لغو: /cancel",
        reply_markup=build_main_menu_keyboard(),
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


async def wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    if not await owner_required(update, context):
        await query.answer("شما دسترسی ندارید", show_alert=True)
        return

    storage = get_storage(context.application)
    data = query.data

    if data == "wiz:cancel":
        context.user_data.pop("wizard_mode", None)
        context.user_data.pop("awaiting_wizard_links", None)
        context.user_data.pop("wizard_links", None)
        context.user_data.pop("wizard_group_id", None)
        await query.edit_message_text("ویزارد لغو شد.")
        return

    if data == "wiz:resend_links":
        context.user_data["awaiting_wizard_links"] = True
        context.user_data.pop("wizard_links", None)
        await query.edit_message_text(
            "مرحله 1/3: لیست لینک‌ها را دوباره بفرست (هر خط یک لینک)."
        )
        return

    if data == "wiz:confirm_links":
        links = context.user_data.get("wizard_links")
        if not isinstance(links, list) or not links:
            await query.answer("اول لیست لینک را ارسال کن.", show_alert=True)
            return
        groups = storage.list_groups(only_active=True)
        if not groups:
            await query.edit_message_text("هیچ گروه فعالی ندارید. ابتدا گروه‌ها را ثبت کن.")
            return
        selected = {groups[0].chat_id}
        context.user_data["wizard_group_id"] = groups[0].chat_id
        await query.edit_message_text(
            "مرحله 3/3: یک گروه را انتخاب کن تا ارسال انجام شود.",
            reply_markup=build_wizard_group_selector(groups, groups[0].chat_id),
        )
        return

    if data.startswith("wiz:pick:"):
        try:
            chat_id = int(data.split(":")[2])
        except (IndexError, ValueError):
            await query.answer("انتخاب نامعتبر")
            return
        links = context.user_data.get("wizard_links")
        if not isinstance(links, list) or not links:
            await query.answer("ابتدا مرحله لینک‌ها را انجام بده.", show_alert=True)
            return
        groups = storage.list_groups(only_active=True)
        group_ids = {group.chat_id for group in groups}
        if chat_id not in group_ids:
            await query.answer("گروه دیگر فعال نیست.", show_alert=True)
            return
        context.user_data["wizard_group_id"] = chat_id
        await query.edit_message_text(
            "مرحله 3/3: گروه انتخاب شد. برای ارسال، دکمه ارسال را بزن.",
            reply_markup=build_wizard_group_selector(groups, chat_id),
        )
        return

    if data == "wiz:send":
        links = context.user_data.get("wizard_links")
        wizard_group_id = context.user_data.get("wizard_group_id")
        if not isinstance(links, list) or not links:
            await query.answer("ابتدا مرحله لینک‌ها را انجام بده.", show_alert=True)
            return
        if not isinstance(wizard_group_id, int):
            await query.answer("ابتدا یک گروه انتخاب کن.", show_alert=True)
            return

        groups = storage.list_groups(only_active=True)
        group_ids = {group.chat_id for group in groups}
        if wizard_group_id not in group_ids:
            await query.answer("گروه انتخابی فعال نیست.", show_alert=True)
            return
        manager = get_manager(context.application)
        owner_chat_id = update.effective_chat.id if update.effective_chat else None
        key = f"wizard:{datetime.now().timestamp()}:{secrets.token_hex(3)}"
        started = await manager.start_job(
            key=key,
            group_ids=[wizard_group_id],
            links=links,
            owner_chat_id=owner_chat_id,
            title="wizard-broadcast",
        )
        if not started:
            await query.answer("شروع ارسال ناموفق بود", show_alert=True)
            return

        context.user_data.pop("wizard_mode", None)
        context.user_data.pop("awaiting_wizard_links", None)
        context.user_data.pop("wizard_links", None)
        context.user_data.pop("wizard_group_id", None)
        await query.edit_message_text(
            f"ارسال شروع شد.\nشناسه کار: {key}\nتعداد لینک: {len(links)}\nتعداد گروه: 1"
        )
        return

    await query.answer("عملیات نامشخص")


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


async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    storage = get_storage(context.application)
    rows = storage.list_send_logs(limit=20)
    if not rows:
        await update.effective_message.reply_text("لاگ ارسالی ثبت نشده است.")
        return

    lines: list[str] = []
    for row in rows:
        status_mark = "✅" if row["status"] == "ok" else ("⏳" if row["status"] == "retry" else "❌")
        group_id = row["chat_id"]
        if group_id is None:
            group_text = "-"
        else:
            group_text = str(group_id)
        lines.append(
            f"{status_mark} {row['created_at']} | {row['job_key']} | گروه:{group_text} | "
            f"{row['status']} | {row['detail']}"
        )

    text = "آخرین لاگ‌های ارسال:\n" + "\n".join(lines[:20])
    await update.effective_message.reply_text(text[:4000])


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    cancelled = await get_manager(context.application).cancel_all()
    await send_main_menu(update, context, text=f"دستور توقف برای {cancelled} کار ارسال شد.")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("awaiting_links", None)
    context.user_data.pop("wizard_mode", None)
    context.user_data.pop("awaiting_wizard_links", None)
    context.user_data.pop("wizard_links", None)
    context.user_data.pop("wizard_group_id", None)
    context.user_data.pop("sendlinks_mode", None)
    context.user_data.pop("pending_links", None)
    context.user_data.pop("pending_selected_groups", None)
    context.user_data.pop("awaiting_links_file", None)
    await send_main_menu(update, context, text="عملیات لغو شد. پنل آماده است.")


async def private_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE:
        return
    if not await owner_required(update, context):
        return
    if context.user_data.get("awaiting_manual_group_add"):
        chat_id, title = parse_manual_group_input(update.effective_message.text or "")
        if chat_id is None or not title:
            await update.effective_message.reply_text(
                "فرمت نامعتبر است.\n"
                "فرمت درست:\n"
                "<chat_id> | <title>\n\n"
                "مثال:\n-1001234567890 | گروه تست"
            )
            return
        get_storage(context.application).upsert_group(
            chat_id=chat_id,
            title=title,
            is_active=True,
            is_admin=False,
        )
        context.user_data.pop("awaiting_manual_group_add", None)
        await send_main_menu(
            update,
            context,
            text=f"گروه با موفقیت اضافه شد.\nchat_id: {chat_id}\ntitle: {title}",
        )
        return
    if context.user_data.get("awaiting_wizard_links"):
        raw_text = update.effective_message.text or ""
        links, invalid_lines = parse_links(raw_text)
        if invalid_lines:
            preview = "\n".join(invalid_lines[:10])
            await update.effective_message.reply_text(
                "چند خط نامعتبر دارید.\n"
                "فرمت‌های قابل قبول: http/https و همچنین vmess/vless/trojan/ss/ssr و مشابه.\n"
                f"{preview}\n\nدوباره لیست لینک را ارسال کن."
            )
            return
        if not links:
            await update.effective_message.reply_text(
                "هیچ لینک معتبری پیدا نشد. دوباره لیست لینک را بفرست."
            )
            return

        context.user_data["wizard_links"] = links
        context.user_data["awaiting_wizard_links"] = False

        confirm_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("تایید لینک‌ها", callback_data="wiz:confirm_links"),
                    InlineKeyboardButton("ارسال دوباره لیست", callback_data="wiz:resend_links"),
                ],
                [InlineKeyboardButton("لغو", callback_data="wiz:cancel")],
            ]
        )
        await update.effective_message.reply_text(
            f"{len(links)} لینک دریافت شد.\n"
            "مرحله 2/3: تایید کن تا لیست گروه‌ها باز شود.",
            reply_markup=confirm_keyboard,
        )
        return
    if context.user_data.get("awaiting_links"):
        context.user_data["awaiting_links"] = False
        await set_links_from_text(update, context, update.effective_message.text or "")


async def private_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE:
        return
    if not await owner_required(update, context):
        return
    document = update.effective_message.document if update.effective_message else None
    if not document:
        return
    file_name = document.file_name or "links.txt"
    mime_type = document.mime_type or ""
    if not _is_txt_document(file_name, mime_type):
        if context.user_data.get("awaiting_links") or context.user_data.get("awaiting_links_file"):
            await update.effective_message.reply_text("فقط فایل txt مجاز است.")
        return
    if update.effective_message:
        await update.effective_message.reply_text(
            f"فایل دریافت شد: {file_name}\nدر حال پردازش لینک‌ها..."
        )
    LOGGER.info("TXT document received from owner: %s (mime=%s)", file_name, mime_type or "-")
    telegram_file = await document.get_file()
    file_bytes = await telegram_file.download_as_bytearray()
    context.user_data["awaiting_links"] = False
    context.user_data["awaiting_links_file"] = False
    await set_links_from_document(update, context, file_name=file_name, file_bytes=bytes(file_bytes))


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
    app.add_handler(CommandHandler("setlinksfile", setlinksfile_command))
    app.add_handler(CommandHandler("links", links_command))
    app.add_handler(CommandHandler("groups", groups_command))
    app.add_handler(CommandHandler("addgroup", addgroup_command))
    app.add_handler(CommandHandler("removegroup", removegroup_command))
    app.add_handler(CommandHandler("refreshadmins", refresh_admins_command))
    app.add_handler(CommandHandler("sendlinks", sendlinks_command))
    app.add_handler(CommandHandler("panel", open_main_menu))
    app.add_handler(CommandHandler("services", services_command))
    app.add_handler(CommandHandler("runsvc", runsvc_command))
    app.add_handler(CommandHandler("enablesvc", enablesvc_command))
    app.add_handler(CommandHandler("disablesvc", disablesvc_command))
    app.add_handler(CommandHandler("jobs", jobs_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("register", register_group_command))
    app.add_handler(CallbackQueryHandler(selector_callback, pattern=r"^sel:"))
    app.add_handler(CallbackQueryHandler(wizard_callback, pattern=r"^wiz:"))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^manage:"))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu:"))
    app.add_handler(ChatMemberHandler(my_chat_member_handler, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Document.ALL, private_document_handler))
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
