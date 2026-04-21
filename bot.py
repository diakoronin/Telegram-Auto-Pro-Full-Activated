#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import base64
import binascii
from collections import Counter
import io
import hmac
import json
import logging
import os
import re
import secrets
import ssl
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Awaitable, Callable, Optional
from urllib.parse import quote_plus, urlencode, urlparse
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request as UrlRequest, build_opener
from urllib.error import HTTPError, URLError
from http.cookiejar import CookieJar

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut
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
MIN_SEND_GAP_SECONDS = max(float(os.getenv("MIN_SEND_GAP_SECONDS", "3.0")), 0.0)
SCHEDULER_POLL_SECONDS = max(float(os.getenv("SCHEDULER_POLL_SECONDS", "5.0")), 1.0)
MAX_CONCURRENT_BROADCASTS = max(int(os.getenv("MAX_CONCURRENT_BROADCASTS", "4")), 1)
DB_TIMEOUT_SECONDS = max(float(os.getenv("DB_TIMEOUT_SECONDS", "20.0")), 1.0)
SEND_LOG_RETENTION = max(int(os.getenv("SEND_LOG_RETENTION", "30000")), 1000)
SEND_LOG_PRUNE_INTERVAL_SECONDS = max(
    float(os.getenv("SEND_LOG_PRUNE_INTERVAL_SECONDS", "900.0")),
    60.0,
)
XUI_HTTP_TIMEOUT_SECONDS = max(float(os.getenv("XUI_HTTP_TIMEOUT_SECONDS", "25.0")), 5.0)
XUI_DEFAULT_TLS_VERIFY = os.getenv("XUI_TLS_VERIFY", "true").lower() in {"1", "true", "yes", "on"}
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
WALLET_PAYMENT_INFO = os.getenv(
    "WALLET_PAYMENT_INFO",
    "برای شارژ کیف پول، مبلغ را به حساب شما واریز کنید و درخواست شارژ را ثبت کنید.",
).strip()
WALLET_TOPUP_MIN = max(int(os.getenv("WALLET_TOPUP_MIN", "10000")), 1000)
MAX_USER_ORDER_PREVIEW = max(int(os.getenv("MAX_USER_ORDER_PREVIEW", "8")), 3)
SERVICE_NOTIFY_OWNER = os.getenv("SERVICE_NOTIFY_OWNER", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
MAX_LINKS_TEXT_BYTES = 5 * 1024 * 1024
WEB_PANEL_PATH = re.sub(r"[^a-zA-Z0-9_-]", "", WEB_PANEL_PATH) or "panel"
XUI_PRESET_PLANS: list[tuple[str, int, int]] = [
    ("1GB | 30 روز", 1, 30),
    ("5GB | 30 روز", 5, 30),
    ("10GB | 30 روز", 10, 30),
    ("20GB | 30 روز", 20, 30),
]


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
class XuiServerItem:
    id: int
    name: str
    panel_url: str
    username: str
    password: str
    is_active: bool
    tls_verify: bool = True
    last_check_at: Optional[str] = None
    last_check_ok: Optional[bool] = None
    last_check_error: str = ""


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


def _is_access_lost_error(exc: TelegramError) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "chat not found",
            "forbidden",
            "bot was kicked",
            "kicked from the",
            "have no rights",
            "not enough rights",
            "member not found",
            "group chat was upgraded",
        )
    )


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=DB_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {int(DB_TIMEOUT_SECONDS * 1000)}")
        return conn

    def init(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS xui_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    panel_url TEXT NOT NULL,
                    username TEXT NOT NULL,
                    password TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    tls_verify INTEGER NOT NULL DEFAULT 1,
                    last_check_at TEXT,
                    last_check_ok INTEGER,
                    last_check_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(panel_url, username)
                )
                """
            )
            xui_columns = {row["name"] for row in conn.execute("PRAGMA table_info(xui_servers)").fetchall()}
            if "tls_verify" not in xui_columns:
                conn.execute("ALTER TABLE xui_servers ADD COLUMN tls_verify INTEGER NOT NULL DEFAULT 1")
            if "last_check_at" not in xui_columns:
                conn.execute("ALTER TABLE xui_servers ADD COLUMN last_check_at TEXT")
            if "last_check_ok" not in xui_columns:
                conn.execute("ALTER TABLE xui_servers ADD COLUMN last_check_ok INTEGER")
            if "last_check_error" not in xui_columns:
                conn.execute("ALTER TABLE xui_servers ADD COLUMN last_check_error TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS xui_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL,
                    inbound_id INTEGER NOT NULL,
                    requested_count INTEGER NOT NULL,
                    created_count INTEGER NOT NULL,
                    failed_count INTEGER NOT NULL,
                    total_gb INTEGER NOT NULL,
                    expire_days INTEGER NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    wallet_balance INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wallet_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    tx_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'done',
                    note TEXT NOT NULL DEFAULT '',
                    related_order_id INTEGER,
                    related_tx_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS catalog_services (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    volume_gb INTEGER NOT NULL,
                    duration_days INTEGER NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    service_id INTEGER,
                    order_type TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_services (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    service_id INTEGER,
                    order_id INTEGER,
                    server_id INTEGER,
                    inbound_id INTEGER,
                    client_email TEXT,
                    sub_link TEXT,
                    expires_at TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
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

    def list_admin_ids(self) -> list[int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT user_id FROM admins ORDER BY user_id").fetchall()
        return [int(row["user_id"]) for row in rows]

    def is_admin_id(self, user_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None

    def add_admin_id(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO admins(user_id) VALUES(?)",
                (int(user_id),),
            )
            conn.commit()

    def remove_admin_id(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM admins WHERE user_id = ?", (int(user_id),))
            conn.commit()

    def ensure_user(self, user_id: int, username: str = "", full_name: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users(user_id, username, full_name, wallet_balance, updated_at)
                VALUES(?, ?, ?, 0, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (int(user_id), username[:120], full_name[:120]),
            )
            conn.commit()

    def get_user_profile(self, user_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, username, full_name, wallet_balance, created_at, updated_at
                FROM users
                WHERE user_id = ?
                """,
                (int(user_id),),
            ).fetchone()
        return row

    def adjust_wallet_balance(
        self,
        user_id: int,
        delta: int,
        tx_type: str,
        note: str = "",
        related_order_id: Optional[int] = None,
        related_tx_id: Optional[int] = None,
    ) -> tuple[bool, int, int]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT wallet_balance FROM users WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
            if not row:
                conn.execute(
                    """
                    INSERT INTO users(user_id, username, full_name, wallet_balance, updated_at)
                    VALUES(?, '', '', 0, CURRENT_TIMESTAMP)
                    """,
                    (int(user_id),),
                )
                current_balance = 0
            else:
                current_balance = int(row["wallet_balance"])
            new_balance = current_balance + int(delta)
            if new_balance < 0:
                return False, current_balance, 0
            conn.execute(
                """
                UPDATE users
                SET wallet_balance = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (new_balance, int(user_id)),
            )
            cursor = conn.execute(
                """
                INSERT INTO wallet_transactions(
                    user_id, amount, tx_type, status, note, related_order_id, related_tx_id
                )
                VALUES(?, ?, ?, 'done', ?, ?, ?)
                """,
                (
                    int(user_id),
                    int(delta),
                    tx_type[:60],
                    note[:500],
                    related_order_id,
                    related_tx_id,
                ),
            )
            conn.commit()
            return True, new_balance, int(cursor.lastrowid or 0)

    def create_deposit_request(self, user_id: int, amount: int, note: str = "") -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO wallet_transactions(user_id, amount, tx_type, status, note)
                VALUES(?, ?, 'deposit_request', 'pending', ?)
                """,
                (int(user_id), int(amount), note[:500]),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)

    def get_wallet_transaction(self, tx_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, amount, tx_type, status, note, related_order_id, related_tx_id, created_at
                FROM wallet_transactions
                WHERE id = ?
                """,
                (int(tx_id),),
            ).fetchone()
        return row

    def approve_deposit_request(self, tx_id: int, admin_id: int) -> tuple[bool, str, Optional[int], int]:
        tx = self.get_wallet_transaction(tx_id)
        if not tx:
            return False, "درخواست یافت نشد.", None, 0
        if tx["tx_type"] != "deposit_request" or tx["status"] != "pending":
            return False, "این درخواست قبلا بررسی شده است.", int(tx["user_id"]), 0
        user_id = int(tx["user_id"])
        amount = int(tx["amount"])
        ok, new_balance, _ = self.adjust_wallet_balance(
            user_id=user_id,
            delta=amount,
            tx_type="deposit_approved",
            note=f"approved_by:{admin_id} request:{tx_id}",
            related_tx_id=tx_id,
        )
        if not ok:
            return False, "خطا در شارژ کیف پول.", user_id, 0
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE wallet_transactions
                SET status = 'approved',
                    note = substr(note || ' | approved_by:' || ?, 1, 500)
                WHERE id = ?
                """,
                (str(admin_id), int(tx_id)),
            )
            conn.commit()
        return True, "واریزی تایید و کیف پول شارژ شد.", user_id, new_balance

    def reject_deposit_request(self, tx_id: int, admin_id: int, reason: str = "") -> tuple[bool, str, Optional[int]]:
        tx = self.get_wallet_transaction(tx_id)
        if not tx:
            return False, "درخواست یافت نشد.", None
        if tx["tx_type"] != "deposit_request" or tx["status"] != "pending":
            return False, "این درخواست قبلا بررسی شده است.", int(tx["user_id"])
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE wallet_transactions
                SET status = 'rejected',
                    note = substr(note || ' | rejected_by:' || ? || ' ' || ?, 1, 500)
                WHERE id = ?
                """,
                (str(admin_id), reason[:120], int(tx_id)),
            )
            conn.commit()
        return True, "درخواست واریز رد شد.", int(tx["user_id"])

    def list_pending_deposit_requests(self, limit: int = 30) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, amount, status, note, created_at
                FROM wallet_transactions
                WHERE tx_type = 'deposit_request' AND status = 'pending'
                ORDER BY id ASC
                LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        return list(rows)

    def list_user_wallet_transactions(self, user_id: int, limit: int = 20) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, amount, tx_type, status, note, created_at
                FROM wallet_transactions
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(user_id), max(1, min(limit, 200))),
            ).fetchall()
        return list(rows)

    def add_catalog_service(self, title: str, price: int, volume_gb: int, duration_days: int) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO catalog_services(title, price, volume_gb, duration_days, is_active, updated_at)
                VALUES(?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                """,
                (title[:120], int(price), int(volume_gb), int(duration_days)),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)

    def list_catalog_services(self, only_active: bool = False) -> list[sqlite3.Row]:
        query = """
            SELECT id, title, price, volume_gb, duration_days, is_active, created_at, updated_at
            FROM catalog_services
        """
        if only_active:
            query += " WHERE is_active = 1"
        query += " ORDER BY id DESC"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return list(rows)

    def get_catalog_service(self, service_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, title, price, volume_gb, duration_days, is_active
                FROM catalog_services
                WHERE id = ?
                """,
                (int(service_id),),
            ).fetchone()
        return row

    def set_catalog_service_active(self, service_id: int, is_active: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE catalog_services
                SET is_active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (int(is_active), int(service_id)),
            )
            conn.commit()

    def create_order(
        self,
        user_id: int,
        service_id: Optional[int],
        order_type: str,
        amount: int,
        status: str,
        detail: str = "",
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO orders(user_id, service_id, order_type, amount, status, detail, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    int(user_id),
                    int(service_id) if service_id is not None else None,
                    order_type[:32],
                    int(amount),
                    status[:32],
                    detail[:1000],
                ),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)

    def update_order_status(self, order_id: int, status: str, detail: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET status = ?, detail = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status[:32], detail[:1000], int(order_id)),
            )
            conn.commit()

    def get_order(self, order_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, service_id, order_type, amount, status, detail, created_at, updated_at
                FROM orders
                WHERE id = ?
                """,
                (int(order_id),),
            ).fetchone()
        return row

    def list_orders(self, limit: int = 80) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, service_id, order_type, amount, status, detail, created_at, updated_at
                FROM orders
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
        return list(rows)

    def list_user_orders(self, user_id: int, limit: int = 20) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, service_id, order_type, amount, status, detail, created_at, updated_at
                FROM orders
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(user_id), max(1, min(limit, 200))),
            ).fetchall()
        return list(rows)

    def count_user_orders(self, user_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM orders WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
        return int(row["total"] if row else 0)

    def list_orders(self, limit: int = 50, user_id: Optional[int] = None) -> list[sqlite3.Row]:
        base_query = """
            SELECT id, user_id, service_id, order_type, amount, status, detail, created_at, updated_at
            FROM orders
        """
        params: list[object] = []
        if user_id is not None:
            base_query += " WHERE user_id = ?"
            params.append(int(user_id))
        base_query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        with self._connect() as conn:
            rows = conn.execute(base_query, tuple(params)).fetchall()
        return list(rows)

    def get_order(self, order_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, service_id, order_type, amount, status, detail, created_at, updated_at
                FROM orders
                WHERE id = ?
                """,
                (int(order_id),),
            ).fetchone()
        return row

    def list_order_anomalies(self, limit: int = 50) -> list[dict[str, object]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    o.id,
                    o.user_id,
                    o.order_type,
                    o.amount,
                    o.status,
                    o.detail,
                    o.created_at,
                    (
                        SELECT COUNT(1)
                        FROM wallet_transactions wt
                        WHERE wt.related_order_id = o.id
                          AND wt.tx_type = 'purchase_debit'
                          AND wt.status = 'done'
                    ) AS debit_count,
                    (
                        SELECT COUNT(1)
                        FROM wallet_transactions wt
                        WHERE wt.related_order_id = o.id
                          AND wt.tx_type = 'purchase_refund'
                          AND wt.status = 'done'
                    ) AS refund_count,
                    (
                        SELECT COUNT(1)
                        FROM user_services us
                        WHERE us.order_id = o.id
                    ) AS service_count
                FROM orders o
                WHERE o.order_type IN ('buy', 'renew')
                ORDER BY o.id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
        anomalies: list[dict[str, object]] = []
        for row in rows:
            status = str(row["status"] or "")
            debit_count = int(row["debit_count"] or 0)
            refund_count = int(row["refund_count"] or 0)
            service_count = int(row["service_count"] or 0)
            issue = ""
            if status == "delivered" and debit_count == 0:
                issue = "سرویس تحویل شده ولی برداشت کیف پول ثبت نشده"
            elif status == "delivered" and service_count == 0:
                issue = "سفارش تحویل‌شده است اما سرویس ثبت نشده"
            elif status.startswith("failed") and debit_count > refund_count:
                issue = "برداشت انجام شده ولی ریفاند کامل نشده"
            elif status in {"provisioning", "paid"} and debit_count == 0:
                issue = "وضعیت پرداخت نامعتبر (پرداخت ثبت نشده)"
            if issue:
                anomalies.append(
                    {
                        "order_id": int(row["id"]),
                        "user_id": int(row["user_id"]),
                        "order_type": str(row["order_type"]),
                        "amount": int(row["amount"]),
                        "status": status,
                        "issue": issue,
                        "detail": str(row["detail"] or ""),
                        "created_at": str(row["created_at"] or ""),
                    }
                )
        return anomalies

    def add_user_service(
        self,
        user_id: int,
        service_id: Optional[int],
        order_id: Optional[int],
        server_id: Optional[int],
        inbound_id: Optional[int],
        client_email: str,
        sub_link: str,
        expires_at: Optional[str],
        status: str = "active",
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO user_services(
                    user_id, service_id, order_id, server_id, inbound_id,
                    client_email, sub_link, expires_at, status
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
                    int(service_id) if service_id is not None else None,
                    int(order_id) if order_id is not None else None,
                    int(server_id) if server_id is not None else None,
                    int(inbound_id) if inbound_id is not None else None,
                    client_email[:120],
                    sub_link[:500],
                    (expires_at or "")[:64] or None,
                    status[:32],
                ),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)

    def list_user_services(self, user_id: int, limit: int = 30) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, service_id, order_id, server_id, inbound_id, client_email, sub_link, expires_at, status, created_at
                FROM user_services
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(user_id), max(1, min(limit, 200))),
            ).fetchall()
        return list(rows)

    def count_user_services(self, user_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM user_services WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
        return int(row["total"] if row else 0)

    def get_user_service(self, service_instance_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, service_id, order_id, server_id, inbound_id, client_email, sub_link, expires_at, status
                FROM user_services
                WHERE id = ?
                """,
                (int(service_instance_id),),
            ).fetchone()
        return row

    def list_recent_order_audit(self, limit: int = 120) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    o.id,
                    o.user_id,
                    o.order_type,
                    o.amount,
                    o.status,
                    o.created_at,
                    COALESCE(SUM(CASE WHEN wt.tx_type IN ('order_debit', 'order_purchase_debit', 'order_renew_debit')
                        THEN wt.amount ELSE 0 END), 0) AS debit_sum,
                    COALESCE(SUM(CASE WHEN wt.tx_type = 'order_refund' THEN wt.amount ELSE 0 END), 0) AS refund_sum,
                    (
                        SELECT COUNT(*) FROM user_services us
                        WHERE us.order_id = o.id
                    ) AS service_count
                FROM orders o
                LEFT JOIN wallet_transactions wt ON wt.related_order_id = o.id
                GROUP BY o.id
                ORDER BY o.id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 1000)),),
            ).fetchall()
        return list(rows)

    def update_user_service_expiry(
        self,
        service_instance_id: int,
        expires_at: Optional[str],
        status: str = "active",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE user_services
                SET expires_at = ?, status = ?
                WHERE id = ?
                """,
                ((expires_at or "")[:64] or None, status[:32], int(service_instance_id)),
            )
            conn.commit()

    def set_default_xui_route(self, server_id: int, inbound_id: int) -> None:
        self.set_setting("xui_default_server_id", str(int(server_id)))
        self.set_setting("xui_default_inbound_id", str(int(inbound_id)))

    def get_default_xui_route(self) -> tuple[Optional[int], Optional[int]]:
        raw_server = self.get_setting("xui_default_server_id")
        raw_inbound = self.get_setting("xui_default_inbound_id")
        try:
            server_id = int(raw_server) if raw_server is not None else None
        except ValueError:
            server_id = None
        try:
            inbound_id = int(raw_inbound) if raw_inbound is not None else None
        except ValueError:
            inbound_id = None
        return server_id, inbound_id

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

    def add_links(self, links: list[str]) -> tuple[int, int]:
        inserted = 0
        duplicates = 0
        with self._connect() as conn:
            for url in links:
                cursor = conn.execute("INSERT OR IGNORE INTO links(url) VALUES(?)", (url,))
                if cursor.rowcount and cursor.rowcount > 0:
                    inserted += 1
                else:
                    duplicates += 1
            conn.commit()
        return inserted, duplicates

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
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO send_logs(job_key, title, chat_id, link, status, detail)
                        VALUES(?, ?, ?, ?, ?, ?)
                        """,
                        (job_key, title, chat_id, link, status, detail[:1000]),
                    )
                    conn.commit()
                return
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() and attempt < max_attempts:
                    time.sleep(0.15 * attempt)
                    continue
                LOGGER.warning("ثبت لاگ ارسال ناموفق بود: %s", exc)
                return

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

    def trim_send_logs(self, keep_last: int) -> int:
        keep = max(int(keep_last), 100)
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT MAX(id) AS max_id FROM send_logs").fetchone()
                max_id = int(row["max_id"]) if row and row["max_id"] is not None else 0
                if max_id <= 0:
                    return 0
                threshold = max_id - keep
                if threshold <= 0:
                    return 0
                cursor = conn.execute("DELETE FROM send_logs WHERE id <= ?", (threshold,))
                conn.commit()
                return int(cursor.rowcount or 0)
        except sqlite3.OperationalError as exc:
            LOGGER.warning("پاک‌سازی لاگ‌های قدیمی ناموفق بود: %s", exc)
            return 0

    def upsert_xui_server(self, name: str, panel_url: str, username: str, password: str) -> int:
        safe_name = name.strip()[:80] or "server"
        tls_default = 1 if XUI_DEFAULT_TLS_VERIFY else 0
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO xui_servers(name, panel_url, username, password, is_active, tls_verify, updated_at)
                VALUES(?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(panel_url, username) DO UPDATE SET
                    name = excluded.name,
                    password = excluded.password,
                    is_active = 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (safe_name, panel_url.strip(), username.strip(), password, tls_default),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id FROM xui_servers WHERE panel_url = ? AND username = ?",
                (panel_url.strip(), username.strip()),
            ).fetchone()
        if not row:
            raise RuntimeError("ثبت سرور ناموفق بود.")
        return int(row["id"])

    def list_xui_servers(self, only_active: bool = True) -> list[XuiServerItem]:
        query = """
            SELECT
                id,
                name,
                panel_url,
                username,
                password,
                is_active,
                COALESCE(tls_verify, 1) AS tls_verify,
                last_check_at,
                last_check_ok,
                COALESCE(last_check_error, '') AS last_check_error
            FROM xui_servers
        """
        if only_active:
            query += " WHERE is_active = 1"
        query += " ORDER BY updated_at DESC, id DESC"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [
            XuiServerItem(
                id=int(row["id"]),
                name=str(row["name"]),
                panel_url=str(row["panel_url"]),
                username=str(row["username"]),
                password=str(row["password"]),
                is_active=bool(row["is_active"]),
                tls_verify=bool(row["tls_verify"]),
                last_check_at=str(row["last_check_at"]) if row["last_check_at"] is not None else None,
                last_check_ok=(
                    bool(row["last_check_ok"])
                    if row["last_check_ok"] is not None
                    else None
                ),
                last_check_error=str(row["last_check_error"] or ""),
            )
            for row in rows
        ]

    def get_xui_server(self, server_id: int) -> Optional[XuiServerItem]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    name,
                    panel_url,
                    username,
                    password,
                    is_active,
                    COALESCE(tls_verify, 1) AS tls_verify,
                    last_check_at,
                    last_check_ok,
                    COALESCE(last_check_error, '') AS last_check_error
                FROM xui_servers
                WHERE id = ?
                """,
                (server_id,),
            ).fetchone()
        if not row:
            return None
        return XuiServerItem(
            id=int(row["id"]),
            name=str(row["name"]),
            panel_url=str(row["panel_url"]),
            username=str(row["username"]),
            password=str(row["password"]),
            is_active=bool(row["is_active"]),
            tls_verify=bool(row["tls_verify"]),
            last_check_at=str(row["last_check_at"]) if row["last_check_at"] is not None else None,
            last_check_ok=(
                bool(row["last_check_ok"])
                if row["last_check_ok"] is not None
                else None
            ),
            last_check_error=str(row["last_check_error"] or ""),
        )

    def set_xui_server_active(self, server_id: int, is_active: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE xui_servers
                SET is_active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (int(is_active), server_id),
            )
            conn.commit()

    def set_xui_server_tls_verify(self, server_id: int, tls_verify: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE xui_servers
                SET tls_verify = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (int(tls_verify), server_id),
            )
            conn.commit()

    def mark_xui_server_check(
        self,
        server_id: int,
        ok: bool,
        error_text: str = "",
        checked_at: Optional[str] = None,
    ) -> None:
        checked_value = checked_at or dt_to_str(utc_now())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE xui_servers
                SET
                    last_check_at = ?,
                    last_check_ok = ?,
                    last_check_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (checked_value, int(ok), error_text[:500], server_id),
            )
            conn.commit()

    def add_xui_batch_log(
        self,
        server_id: int,
        inbound_id: int,
        requested_count: int,
        created_count: int,
        failed_count: int,
        total_gb: int,
        expire_days: int,
        note: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO xui_batches(
                    server_id, inbound_id, requested_count, created_count, failed_count,
                    total_gb, expire_days, note
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    server_id,
                    inbound_id,
                    requested_count,
                    created_count,
                    failed_count,
                    total_gb,
                    expire_days,
                    note[:500],
                ),
            )
            conn.commit()

    def list_xui_batch_logs(self, limit: int = 20, server_id: Optional[int] = None) -> list[sqlite3.Row]:
        safe_limit = max(1, min(limit, 200))
        with self._connect() as conn:
            if server_id is None:
                rows = conn.execute(
                    """
                    SELECT id, server_id, inbound_id, requested_count, created_count, failed_count,
                           total_gb, expire_days, note, created_at
                    FROM xui_batches
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, server_id, inbound_id, requested_count, created_count, failed_count,
                           total_gb, expire_days, note, created_at
                    FROM xui_batches
                    WHERE server_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (server_id, safe_limit),
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


def _normalize_xui_panel_url(raw_url: str) -> str:
    text = raw_url.strip()
    if not text:
        raise ValueError("لینک پنل خالی است.")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("فرمت لینک پنل نامعتبر است.")
    base = f"{parsed.scheme}://{parsed.netloc}"
    path = (parsed.path or "").rstrip("/")
    if "/panel" in path:
        path = path[: path.find("/panel")]
    return base + path


def _extract_subscription_id(link: str) -> str:
    if "#" in link:
        return link.split("#", 1)[1]
    return link.rsplit("/", 1)[-1].strip() or "unknown"


class XuiPanelClient:
    def __init__(self, server: XuiServerItem):
        self.server = server
        self.base_url = _normalize_xui_panel_url(server.panel_url)
        self.cookie_jar = CookieJar()
        if server.tls_verify:
            self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))
        else:
            insecure_ctx = ssl._create_unverified_context()
            self.opener = build_opener(
                HTTPSHandler(context=insecure_ctx),
                HTTPCookieProcessor(self.cookie_jar),
            )

    def _request_json(
        self,
        path: str,
        payload: Optional[dict] = None,
        method: str = "POST",
        timeout: float = XUI_HTTP_TIMEOUT_SECONDS,
    ) -> dict:
        url = self.base_url + path
        data_bytes = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = UrlRequest(url=url, data=data_bytes, headers=headers, method=method)
        try:
            resp = self.opener.open(req, timeout=timeout)
            body = resp.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {body[:300]}")
        except URLError as exc:
            raise RuntimeError(f"اتصال به پنل ناموفق بود: {exc}")
        if not body.strip():
            return {}
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            raise RuntimeError(f"پاسخ پنل JSON نبود: {body[:240]}")
        if isinstance(parsed, dict):
            success = parsed.get("success")
            if success is False:
                msg = parsed.get("msg") or parsed.get("message") or "unknown"
                raise RuntimeError(f"خطای پنل: {msg}")
            return parsed
        raise RuntimeError("فرمت پاسخ پنل نامعتبر است.")

    def login(self) -> None:
        url = self.base_url + "/login"
        body = urlencode({"username": self.server.username, "password": self.server.password}).encode("utf-8")
        req = UrlRequest(
            url=url,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json,text/html",
            },
            method="POST",
        )
        try:
            resp = self.opener.open(req, timeout=XUI_HTTP_TIMEOUT_SECONDS)
            text = resp.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ورود به پنل ناموفق بود: HTTP {exc.code} - {text[:220]}")
        except URLError as exc:
            raise RuntimeError(f"اتصال به پنل ناموفق بود: {exc}")
        if "invalid" in text.lower() or "password" in text.lower() and "wrong" in text.lower():
            raise RuntimeError("نام کاربری یا رمز عبور پنل اشتباه است.")
        if len(self.cookie_jar) == 0:
            # Some versions return JSON + cookie; if cookie missing, calls will fail.
            LOGGER.warning("Login finished but no session cookie detected for server: %s", self.server.name)

    def list_inbounds(self) -> list[dict]:
        try:
            data = self._request_json("/panel/api/inbounds/list", method="GET")
        except Exception:
            data = self._request_json("/panel/inbound/list", method="GET")
        obj = data.get("obj", []) if isinstance(data, dict) else []
        if not isinstance(obj, list):
            return []
        return [item for item in obj if isinstance(item, dict)]

    @staticmethod
    def _clients_payload(items: list[dict]) -> str:
        return json.dumps({"clients": items}, ensure_ascii=False)

    def add_clients_batch(
        self,
        inbound_id: int,
        count: int,
        total_gb: int,
        expire_days: int,
        prefix: str,
    ) -> tuple[list[dict], list[str]]:
        total_bytes = int(total_gb) * (1024**3)
        expire_ts = 0
        if expire_days > 0:
            expire_ts = int((time.time() + expire_days * 86400) * 1000)

        created: list[dict] = []
        failed: list[str] = []
        for idx in range(1, count + 1):
            email = f"{prefix}{idx:03d}"
            client_uuid = str(uuid.uuid4())
            sub_id = secrets.token_hex(8)
            client_entry = {
                "id": client_uuid,
                "email": email,
                "enable": True,
                "totalGB": total_bytes,
                "expiryTime": expire_ts,
                "limitIp": 0,
                "subId": sub_id,
            }
            payload = {
                "id": inbound_id,
                "settings": self._clients_payload([client_entry]),
            }
            try:
                try:
                    self._request_json("/panel/api/inbounds/addClient", payload=payload, method="POST")
                except Exception:
                    self._request_json("/panel/inbound/addClient", payload=payload, method="POST")
            except Exception as exc:
                failed.append(f"{email}: {_shorten_text(str(exc), 200)}")
                continue
            sub_link = f"{self.base_url}/sub/{sub_id}"
            created.append(
                {
                    "email": email,
                    "uuid": client_uuid,
                    "sub_id": sub_id,
                    "sub_link": sub_link,
                }
            )
        return created, failed


def _parse_xui_server_input(raw_text: str) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    parts = [part.strip() for part in raw_text.split("|")]
    if len(parts) != 4:
        return None, None, None, None
    name, panel_url, username, password = parts
    if not all((name, panel_url, username, password)):
        return None, None, None, None
    return name, panel_url, username, password


def _parse_xui_generate_input(raw_text: str) -> tuple[Optional[int], Optional[int], Optional[int], Optional[str]]:
    parts = [part.strip() for part in raw_text.split("|")]
    if len(parts) < 3:
        return None, None, None, None
    if len(parts) == 3:
        parts.append("u")
    try:
        count = int(parts[0])
        total_gb = int(parts[1])
        expire_days = int(parts[2])
    except ValueError:
        return None, None, None, None
    prefix = re.sub(r"[^a-zA-Z0-9_-]", "", parts[3])[:24] or "u"
    if count <= 0 or total_gb <= 0 or expire_days < 0:
        return None, None, None, None
    if count > 1000:
        return None, None, None, None
    return count, total_gb, expire_days, prefix


def _xui_plan_definitions() -> list[dict[str, object]]:
    return [
        {"key": "p1", "label": "پلن 1GB / 30 روز", "gb": 1, "days": 30},
        {"key": "p5", "label": "پلن 5GB / 30 روز", "gb": 5, "days": 30},
        {"key": "p10", "label": "پلن 10GB / 30 روز", "gb": 10, "days": 30},
        {"key": "p30", "label": "پلن 30GB / 60 روز", "gb": 30, "days": 60},
    ]


def _xui_plan_by_key(plan_key: str) -> Optional[dict[str, object]]:
    for plan in _xui_plan_definitions():
        if str(plan.get("key")) == plan_key:
            return plan
    return None


def _build_xui_servers_keyboard(storage: Storage) -> InlineKeyboardMarkup:
    servers = storage.list_xui_servers(only_active=False)
    rows: list[list[InlineKeyboardButton]] = []
    for server in servers[:30]:
        state = "🟢" if server.is_active else "⚪"
        rows.append(
            [InlineKeyboardButton(f"{state} {server.name}"[:64], callback_data=f"xui:server:{server.id}")]
        )
    rows.append(
        [
            InlineKeyboardButton("➕ افزودن سرور جدید", callback_data="xui:add_server"),
            InlineKeyboardButton("🩺 تست همه", callback_data="xui:test_all"),
        ]
    )
    rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="menu:back")])
    return InlineKeyboardMarkup(rows)


def _build_xui_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت به پنل", callback_data="menu:back")]])


def _build_xui_inbounds_keyboard(server_id: int, inbounds: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for inbound in inbounds[:60]:
        inbound_id = int(inbound.get("id", 0))
        remark = str(inbound.get("remark") or inbound.get("tag") or f"inbound-{inbound_id}")
        port = inbound.get("port", "-")
        rows.append(
            [
                InlineKeyboardButton(
                    f"{remark} | {port}"[:64],
                    callback_data=f"xui:pick_inbound:{server_id}:{inbound_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ سرورها", callback_data="menu:xui_servers")])
    rows.append([InlineKeyboardButton("⬅️ پنل اصلی", callback_data="menu:back")])
    return InlineKeyboardMarkup(rows)


def _build_xui_generate_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ شروع ساخت کلاینت", callback_data="xui:generate_confirm")],
            [InlineKeyboardButton("❌ لغو", callback_data="menu:back")],
        ]
    )


def _build_xui_plan_keyboard(server_id: int, inbound_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for plan in _xui_plan_definitions():
        key = str(plan.get("key"))
        label = str(plan.get("label", key))
        rows.append(
            [
                InlineKeyboardButton(
                    f"⚡ {label}"[:64],
                    callback_data=f"xui:plan:{server_id}:{inbound_id}:{key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data=f"xui:server:{server_id}")])
    return InlineKeyboardMarkup(rows)


def _build_xui_server_actions_keyboard(server: XuiServerItem) -> InlineKeyboardMarkup:
    tls_text = "TLS: روشن" if server.tls_verify else "TLS: خاموش"
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("📥 انتخاب Inbound", callback_data=f"xui:server:{server.id}")],
        [
            InlineKeyboardButton("🩺 تست این سرور", callback_data=f"xui:server:{server.id}"),
            InlineKeyboardButton(tls_text, callback_data=f"xui:toggle_tls:{server.id}"),
        ],
        [InlineKeyboardButton("⬅️ بازگشت به لیست سرورها", callback_data="menu:xui_servers")],
    ]
    return InlineKeyboardMarkup(rows)

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

    async def _notify_owner_safe(self, owner_chat_id: Optional[int], text: str) -> None:
        if not owner_chat_id:
            return
        max_attempt = 3
        for attempt in range(1, max_attempt + 1):
            try:
                await self.application.bot.send_message(chat_id=owner_chat_id, text=text)
                return
            except RetryAfter as exc:
                wait_seconds = max(int(exc.retry_after), 1)
                await asyncio.sleep(wait_seconds + 1)
            except TelegramError as exc:
                LOGGER.warning("ارسال پیام به مالک ناموفق بود (attempt=%s): %s", attempt, exc)
                return
            except Exception as exc:  # pragma: no cover
                LOGGER.warning("خطای غیرمنتظره در ارسال پیام مالک (attempt=%s): %s", attempt, exc)
                return

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
            all_groups = await sync_groups_admin_status(
                self.storage, self.application.bot, self.storage.list_groups()
            )
            group_map = {group.chat_id: group for group in all_groups}
            async with self.semaphore:
                await self._notify_owner_safe(owner_chat_id, f"شروع ارسال: {title}\nشناسه کار: {key}")
                group_index = 0
                for group_id in group_ids:
                    group_index += 1
                    group_sent_ok = 0
                    group_sent_fail = 0
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
                            group_sent_fail += 1
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
                        await self._notify_owner_safe(
                            owner_chat_id,
                            (
                                f"⚠️ گروه {known_group.title} ({group_id}) قابل ارسال نیست: {reason}\n"
                                "برای توقف فوری /stop را بزن."
                            ),
                        )
                        await self._notify_owner_safe(
                            owner_chat_id,
                            (
                                f"{title}: گروه {group_index}/{len(group_ids)} پردازش شد ({group_id})\n"
                                f"موفق این گروه: {group_sent_ok}\n"
                                f"ناموفق این گروه: {group_sent_fail}\n"
                                "هشدار: هیچ لینکی به این گروه ارسال نشد."
                            ),
                        )
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
                            group_sent_ok += 1
                        else:
                            result.sent_fail += 1
                            group_sent_fail += 1
                        if self._progress is not None:
                            self._progress["sent_ok"] = result.sent_ok
                            self._progress["sent_fail"] = result.sent_fail
                        if SEND_DELAY_SECONDS > 0:
                            await asyncio.sleep(SEND_DELAY_SECONDS)
                    await self._notify_owner_safe(
                        owner_chat_id,
                        (
                            f"{title}: گروه {group_index}/{len(group_ids)} پردازش شد ({group_id})\n"
                            f"موفق این گروه: {group_sent_ok}\n"
                            f"ناموفق این گروه: {group_sent_fail}"
                            + (
                                "\nهشدار: هیچ لینکی به این گروه ارسال نشد."
                                if group_sent_ok == 0 and group_sent_fail > 0
                                else ""
                            )
                        ),
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
                if result.sent_ok == 0:
                    summary += "\nهشدار: هیچ پیامی با موفقیت ارسال نشد."
                if result.failures:
                    summary += "\nچند خطای اول:\n" + "\n".join(result.failures[:8])
                    reason_counts: Counter[str] = Counter()
                    for failure in result.failures:
                        normalized = failure.split(":", 1)[1].strip() if ":" in failure else failure
                        if normalized:
                            reason_counts[normalized] += 1
                    if reason_counts:
                        summary += "\nدلایل پرتکرار:\n"
                        summary += "\n".join(
                            f"- {reason[:180]} ×{count}"
                            for reason, count in reason_counts.most_common(3)
                        )
                await self._notify_owner_safe(owner_chat_id, summary)

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
                    await self._notify_owner_safe(
                        owner_chat_id,
                        (
                            f"محدودیت تلگرام فعال شد برای گروه {chat_id}.\n"
                            f"{wait_seconds} ثانیه صبر می‌کنم و دوباره تلاش می‌کنم "
                            f"(تلاش {attempt}/{max_retry})."
                        ),
                    )
                await asyncio.sleep(wait_seconds + 1)
                continue
            except (TimedOut, NetworkError) as exc:
                attempt += 1
                wait_seconds = min(2 * attempt, 12)
                self.storage.add_send_log(
                    job_key=key,
                    title=title,
                    chat_id=chat_id,
                    link=text,
                    status="retry",
                    detail=(
                        f"network_retry={exc.__class__.__name__} "
                        f"wait={wait_seconds}s attempt={attempt}/{max_retry}"
                    ),
                )
                if owner_chat_id:
                    now = time.monotonic()
                    if now - self._last_failure_notify_at >= 8:
                        self._last_failure_notify_at = now
                        await self._notify_owner_safe(
                            owner_chat_id,
                            (
                                f"⚠️ اختلال موقت شبکه در ارسال به گروه {chat_id}\n"
                                f"{wait_seconds} ثانیه صبر می‌کنم و دوباره تلاش می‌کنم "
                                f"(تلاش {attempt}/{max_retry})."
                            ),
                        )
                await asyncio.sleep(wait_seconds)
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
                        await self._notify_owner_safe(
                            owner_chat_id,
                            (
                                f"⚠️ هشدار ارسال\n"
                                f"گروه: {chat_id}\n"
                                f"خطا: {str(exc)[:300]}\n"
                                "برای توقف فوری /stop را بزن."
                            ),
                        )
                return False

        fail_message = f"{chat_id}: تلاش مجدد بیش از حد مجاز شد (خطای موقت)"
        result.failures.append(fail_message)
        self.storage.add_send_log(
            job_key=key,
            title=title,
            chat_id=chat_id,
            link=text,
            status="fail",
            detail="retry-limit-exceeded-transient",
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


def is_owner_id(application: Application, user_id: int) -> bool:
    owner_id = get_owner_id(application)
    configured_owner = application.bot_data.get("configured_owner_id")
    if configured_owner and owner_id != configured_owner:
        get_storage(application).set_owner_id(configured_owner)
        owner_id = configured_owner
    return bool(owner_id and int(owner_id) == int(user_id))


def is_admin_id(application: Application, user_id: int) -> bool:
    if is_owner_id(application, user_id):
        return True
    return get_storage(application).is_admin_id(int(user_id))


def ensure_admin_bootstrap(storage: Storage, configured_owner_id: Optional[int]) -> None:
    owner_id = configured_owner_id if configured_owner_id is not None else storage.get_owner_id()
    if owner_id is None:
        return
    if not storage.is_admin_id(owner_id):
        storage.add_admin_id(owner_id)


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


async def sync_groups_admin_status(
    storage: Storage,
    bot,
    groups: list[GroupItem],
) -> list[GroupItem]:
    refreshed: list[GroupItem] = []
    for group in groups:
        is_active = group.is_active
        is_admin = group.is_admin
        try:
            bot_member = await bot.get_chat_member(group.chat_id, bot.id)
            is_admin = bot_member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
            if is_admin != group.is_admin:
                storage.set_group_admin(group.chat_id, is_admin=is_admin)
        except (TimedOut, NetworkError, RetryAfter) as exc:
            LOGGER.warning("بررسی ادمین گروه %s موقتا ناموفق بود: %s", group.chat_id, exc)
        except TelegramError as exc:
            if not _is_access_lost_error(exc):
                LOGGER.warning("بررسی ادمین گروه %s ناموفق بود: %s", group.chat_id, exc)
                refreshed.append(
                    GroupItem(
                        chat_id=group.chat_id,
                        title=group.title,
                        is_active=is_active,
                        is_admin=is_admin,
                    )
                )
                continue
            is_active = False
            is_admin = False
            if group.is_active:
                storage.set_group_active(group.chat_id, is_active=False)
            if group.is_admin:
                storage.set_group_admin(group.chat_id, is_admin=False)
        refreshed.append(
            GroupItem(
                chat_id=group.chat_id,
                title=group.title,
                is_active=is_active,
                is_admin=is_admin,
            )
        )
    return refreshed


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
    user = update.effective_user
    chat = update.effective_chat

    if user is None:
        return False

    if owner_id is None:
        if chat and chat.type == ChatType.PRIVATE and update.effective_message:
            await update.effective_message.reply_text("مالک هنوز تنظیم نشده است. ابتدا در پی‌وی /claim را بزن.")
        return False

    if not is_admin_id(context.application, user.id):
        if chat and chat.type == ChatType.PRIVATE and update.effective_message:
            await update.effective_message.reply_text("فقط ادمین‌های ربات اجازه اجرای این بخش را دارند.")
        return False

    return True


async def strict_owner_required(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    owner_id = get_owner_id(context.application)
    user = update.effective_user
    chat = update.effective_chat
    if user is None:
        return False
    if owner_id is None:
        if chat and chat.type == ChatType.PRIVATE and update.effective_message:
            await update.effective_message.reply_text("مالک هنوز تنظیم نشده است. ابتدا در پی‌وی /claim را بزن.")
        return False
    if not is_owner_id(context.application, user.id):
        if chat and chat.type == ChatType.PRIVATE and update.effective_message:
            await update.effective_message.reply_text("فقط مالک اصلی ربات اجازه این عملیات را دارد.")
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
    chat = update.effective_chat
    user = update.effective_user
    if chat and chat.type == ChatType.PRIVATE:
        if user:
            get_storage(context.application).ensure_user(
                user_id=user.id,
                username=user.username or "",
                full_name=(user.full_name or user.first_name or "")[:120],
            )
        await send_home_menu(update, context, text="ربات آماده است. از دکمه‌ها استفاده کن.")
        return
    if STRICT_OWNER_ONLY and not await owner_required(update, context):
        return
    await update.effective_message.reply_text("بعد از افزودن و ادمین کردن ربات، در همین گروه /register بزن.")


async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_home_menu(update, context, text="پنل اصلی ربات")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    admin_user = is_admin_id(context.application, user.id)
    if update.effective_chat and update.effective_chat.type != ChatType.PRIVATE:
        if STRICT_OWNER_ONLY and not admin_user:
            return
    if not admin_user:
        text = (
            "راهنمای کاربر:\n"
            "/start - نمایش منوی اصلی\n"
            "/panel - نمایش منوی اصلی\n"
            "/cancel - لغو حالت انتظار\n\n"
            "از دکمه‌های «شارژ کیف پول»، «خرید سرویس» و «سرویس‌های من» استفاده کن."
        )
        await update.effective_message.reply_text(text)
        return
    text = (
        "راهنمای ادمین:\n"
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
        "مدیریت سرور ثنایی:\n"
        "از /panel -> «مدیریت سرورهای ثنایی» استفاده کن.\n\n"
        "دستور گروه:\n"
        "/register - ثبت همین گروه در لیست"
    )
    await update.effective_message.reply_text(text)


def build_home_menu_keyboard(is_admin: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("👤 پنل کاربری", callback_data="root:user")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("🛠 پنل مدیریت", callback_data="root:admin")])
    rows.append([InlineKeyboardButton("🔄 بروزرسانی", callback_data="root:refresh")])
    return InlineKeyboardMarkup(rows)


def build_user_menu_keyboard(is_admin: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("💰 کیف پول", callback_data="usr:wallet"),
            InlineKeyboardButton("🧾 سرویس‌های من", callback_data="usr:my_services"),
        ],
        [
            InlineKeyboardButton("🛍 خرید سرویس", callback_data="usr:catalog"),
            InlineKeyboardButton("📦 سفارش‌های من", callback_data="usr:my_orders"),
        ],
        [
            InlineKeyboardButton("➕ درخواست شارژ کیف پول", callback_data="usr:deposit"),
            InlineKeyboardButton("📊 گردش کیف پول", callback_data="usr:wallet_logs"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("🛠 پنل مدیریت", callback_data="root:admin")])
    rows.append([InlineKeyboardButton("⬅️ خانه", callback_data="root:home")])
    return InlineKeyboardMarkup(rows)


def build_admin_menu_keyboard(is_owner: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("📡 پنل ارسال گروهی", callback_data="adm:broadcast_panel")],
        [InlineKeyboardButton("💳 درخواست‌های شارژ", callback_data="adm:deposits")],
        [InlineKeyboardButton("🔍 جستجوی کاربر", callback_data="adm:user_search")],
        [InlineKeyboardButton("🧪 مانیتور مالی/تحویل", callback_data="adm:audit")],
        [InlineKeyboardButton("🛍 مدیریت کاتالوگ سرویس", callback_data="adm:catalog")],
        [InlineKeyboardButton("📋 لیست ادمین‌ها", callback_data="adm:list_admins")],
    ]
    if is_owner:
        rows.append(
            [
                InlineKeyboardButton("➕ افزودن ادمین", callback_data="adm:add_admin"),
                InlineKeyboardButton("➖ حذف ادمین", callback_data="adm:remove_admin"),
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ خانه", callback_data="root:home")])
    return InlineKeyboardMarkup(rows)


def _user_label(user: Optional[object]) -> tuple[str, str]:
    if not user:
        return "", ""
    username = str(getattr(user, "username", "") or "")
    full_name = str(getattr(user, "full_name", "") or getattr(user, "first_name", "") or "")
    return username[:120], full_name[:120]


def _ensure_default_catalog(storage: Storage) -> None:
    if storage.list_catalog_services():
        return
    defaults = [
        ("پلن 1GB | 30 روز", 120000, 1, 30),
        ("پلن 5GB | 30 روز", 360000, 5, 30),
        ("پلن 10GB | 30 روز", 650000, 10, 30),
    ]
    for title, price, gb, days in defaults:
        storage.add_catalog_service(title=title, price=price, volume_gb=gb, duration_days=days)


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
                InlineKeyboardButton("مدیریت سرورهای ثنایی", callback_data="menu:xui_servers"),
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


async def send_main_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str = "پنل ربات آماده است.",
) -> None:
    if not await owner_required(update, context):
        return
    await update.effective_message.reply_text(text, reply_markup=build_main_menu_keyboard())


async def legacy_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await menu_callback(update, context)


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


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_callback_int(data: str, index: int) -> Optional[int]:
    parts = data.split(":")
    if len(parts) <= index:
        return None
    try:
        return int(parts[index])
    except (TypeError, ValueError):
        return None


def _resolve_order_route(storage: Storage, service_id: Optional[int]) -> tuple[Optional[XuiServerItem], Optional[int], str]:
    default_server_id, default_inbound_id = storage.get_default_xui_route()
    inbound_id = default_inbound_id
    server: Optional[XuiServerItem] = None
    source = "default"

    if service_id is not None:
        svc = storage.get_catalog_service(service_id)
        if svc:
            source = f"svc:{service_id}"
            raw_tag = str(svc["title"] or "")
            tagged_inbound = _extract_inbound_id_from_text(raw_tag)
            if tagged_inbound is not None:
                inbound_id = tagged_inbound

    if default_server_id is not None:
        server = storage.get_xui_server(default_server_id)
    if not server:
        servers = storage.list_xui_servers(only_active=True)
        if servers:
            server = servers[0]
            if inbound_id is None:
                try:
                    api = XuiPanelClient(server)
                    api.login()
                    inbounds = api.list_inbounds()
                    if inbounds:
                        inbound_id = _safe_int(inbounds[0].get("id"), 0)
                except Exception:
                    pass
    if server is None:
        return None, None, source
    if inbound_id is None or inbound_id <= 0:
        try:
            api = XuiPanelClient(server)
            api.login()
            inbounds = api.list_inbounds()
            if inbounds:
                inbound_id = _safe_int(inbounds[0].get("id"), 0)
        except Exception:
            inbound_id = None
    if inbound_id is None or inbound_id <= 0:
        return server, None, source
    return server, inbound_id, source


async def _provision_user_order(
    application: Application,
    storage: Storage,
    user_id: int,
    service_id: Optional[int],
    order_id: int,
    order_type: str,
    amount: int,
    renew_service_instance_id: Optional[int] = None,
) -> tuple[bool, str]:
    server, inbound_id, route_source = _resolve_order_route(storage, service_id)
    if not server or not inbound_id:
        detail = "هیچ مسیر پیش‌فرض server/inbound برای تحویل تنظیم نشده است."
        storage.update_order_status(order_id, "failed_no_route", detail)
        storage.adjust_wallet_balance(
            user_id=user_id,
            delta=amount,
            tx_type="order_refund",
            note=f"refund no route order:{order_id}",
            related_order_id=order_id,
        )
        return False, detail

    service = storage.get_catalog_service(service_id) if service_id is not None else None
    volume_gb = _safe_int(service["volume_gb"] if service else 1, 1)
    duration_days = _safe_int(service["duration_days"] if service else 30, 30)
    expires_at = dt_to_str(utc_now() + timedelta(days=max(duration_days, 1)))
    prefix = f"u{user_id}{order_id}"[-16:]

    if order_type == "renew" and renew_service_instance_id:
        current = storage.get_user_service(renew_service_instance_id)
        if not current:
            detail = "سرویس برای تمدید پیدا نشد."
            storage.update_order_status(order_id, "failed_not_found", detail)
            storage.adjust_wallet_balance(
                user_id=user_id,
                delta=amount,
                tx_type="order_refund",
                note=f"refund missing renew target:{order_id}",
                related_order_id=order_id,
            )
            return False, detail
        if _safe_int(current["user_id"]) != user_id:
            detail = "این سرویس متعلق به این کاربر نیست."
            storage.update_order_status(order_id, "failed_ownership", detail)
            storage.adjust_wallet_balance(
                user_id=user_id,
                delta=amount,
                tx_type="order_refund",
                note=f"refund ownership mismatch:{order_id}",
                related_order_id=order_id,
            )
            return False, detail
        storage.update_user_service_expiry(renew_service_instance_id, expires_at=expires_at, status="active")
        storage.update_order_status(order_id, "delivered", f"renewed service:{renew_service_instance_id} exp:{expires_at}")
        return True, f"تمدید انجام شد ✅\nشناسه سرویس: {renew_service_instance_id}\nانقضا: {expires_at}"

    try:
        api = XuiPanelClient(server)
        api.login()
        created, failed = api.add_clients_batch(
            inbound_id=inbound_id,
            count=1,
            total_gb=max(volume_gb, 1),
            expire_days=max(duration_days, 1),
            prefix=prefix,
        )
        if failed and not created:
            raise RuntimeError(failed[0])
    except Exception as exc:
        detail = f"تحویل ناموفق: {_shorten_text(str(exc), 450)}"
        storage.update_order_status(order_id, "failed_provision", detail)
        storage.adjust_wallet_balance(
            user_id=user_id,
            delta=amount,
            tx_type="order_refund",
            note=f"refund provision failed order:{order_id}",
            related_order_id=order_id,
        )
        return False, detail

    if not created:
        detail = "پاسخ سرور خالی بود و سرویسی ساخته نشد."
        storage.update_order_status(order_id, "failed_empty_result", detail)
        storage.adjust_wallet_balance(
            user_id=user_id,
            delta=amount,
            tx_type="order_refund",
            note=f"refund empty result order:{order_id}",
            related_order_id=order_id,
        )
        return False, detail

    item = created[0]
    client_email = str(item.get("email") or f"user-{user_id}-{order_id}")
    sub_link = str(item.get("sub_link") or "")
    if not sub_link:
        sub_link = f"{server.panel_url.rstrip('/')}/sub/{item.get('sub_id', '')}"
    service_instance_id = storage.add_user_service(
        user_id=user_id,
        service_id=service_id,
        order_id=order_id,
        server_id=server.id,
        inbound_id=inbound_id,
        client_email=client_email,
        sub_link=sub_link,
        expires_at=expires_at,
        status="active",
    )
    storage.update_order_status(
        order_id,
        "delivered",
        f"service:{service_instance_id} route:{route_source} server:{server.id} inbound:{inbound_id}",
    )
    try:
        await application.bot.send_message(
            chat_id=user_id,
            text=(
                "سفارش شما تحویل شد ✅\n"
                f"شماره سفارش: {order_id}\n"
                f"شناسه سرویس: {service_instance_id}\n"
                f"ایمیل: {client_email}\n"
                f"لینک ساب:\n{sub_link}\n"
                f"انقضا: {expires_at}"
            ),
        )
    except Exception:
        pass
    return True, f"سرویس ساخته شد ✅\nشناسه سرویس: {service_instance_id}\nلینک ساب: {sub_link}"


async def _purchase_or_renew_service(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    service_id: int,
    order_type: str,
    renew_service_instance_id: Optional[int] = None,
) -> None:
    user = update.effective_user
    if not user:
        return
    storage = get_storage(context.application)
    service = storage.get_catalog_service(service_id)
    if not service or not bool(service["is_active"]):
        await update.effective_message.reply_text("این سرویس فعال نیست یا پیدا نشد.")
        return
    amount = _safe_int(service["price"], 0)
    if amount <= 0:
        await update.effective_message.reply_text("قیمت سرویس نامعتبر است.")
        return
    profile = storage.get_user_profile(user.id)
    balance = _safe_int(profile["wallet_balance"] if profile else 0, 0)
    if balance < amount:
        await update.effective_message.reply_text(
            f"موجودی کافی نیست.\nموجودی: {balance:,}\nهزینه: {amount:,}\nاز بخش «درخواست شارژ کیف پول» شارژ ثبت کن."
        )
        return

    order_id = storage.create_order(
        user_id=user.id,
        service_id=service_id,
        order_type=order_type,
        amount=amount,
        status="provisioning",
        detail="starting",
    )
    ok, new_balance, _ = storage.adjust_wallet_balance(
        user_id=user.id,
        delta=-amount,
        tx_type="purchase_debit",
        note=f"order:{order_id} type:{order_type}",
        related_order_id=order_id,
    )
    if not ok:
        storage.update_order_status(order_id, "failed_wallet", "برداشت کیف پول ناموفق بود.")
        await update.effective_message.reply_text("برداشت کیف پول ناموفق بود. دوباره تلاش کن.")
        return
    storage.update_order_status(order_id, "paid", f"wallet_debited balance:{new_balance}")

    await update.effective_message.reply_text(
        "پرداخت ثبت شد، در حال ساخت/تمدید سرویس...\n"
        f"شماره سفارش: {order_id}\n"
        f"مبلغ کسر شده: {amount:,}\n"
        f"موجودی جدید: {new_balance:,}"
    )
    ok_delivery, message = await _provision_user_order(
        application=context.application,
        storage=storage,
        user_id=user.id,
        service_id=service_id,
        order_id=order_id,
        order_type=order_type,
        amount=amount,
        renew_service_instance_id=renew_service_instance_id,
    )
    if ok_delivery:
        await update.effective_message.reply_text(message)
    else:
        await update.effective_message.reply_text(
            "سفارش ناموفق شد و مبلغ به کیف پول برگشت.\n"
            f"جزئیات: {message}"
        )


def _wallet_summary_text(storage: Storage, user_id: int) -> str:
    profile = storage.get_user_profile(user_id)
    balance = _safe_int(profile["wallet_balance"] if profile else 0, 0)
    total_orders = storage.count_user_orders(user_id)
    total_services = storage.count_user_services(user_id)
    return (
        f"کیف پول شما:\n"
        f"موجودی: {balance:,}\n"
        f"تعداد سفارش‌ها: {total_orders}\n"
        f"تعداد سرویس‌های من: {total_services}"
    )


def _build_catalog_keyboard(services: list[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for svc in services[:60]:
        title = str(svc["title"])
        price = _safe_int(svc["price"])
        rows.append(
            [
                InlineKeyboardButton(
                    f"🛍 {title} | {price:,}",
                    callback_data=f"usr:buy:{_safe_int(svc['id'])}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ پنل کاربری", callback_data="root:user")])
    return InlineKeyboardMarkup(rows)


def _build_my_services_keyboard(items: list[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in items[:60]:
        sid = _safe_int(item["id"])
        status = str(item["status"] or "-")
        rows.append(
            [
                InlineKeyboardButton(
                    f"تمدید سرویس #{sid} ({status})",
                    callback_data=f"usr:renew_pick:{sid}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ پنل کاربری", callback_data="root:user")])
    return InlineKeyboardMarkup(rows)


def _build_admin_pending_deposits_keyboard(rows_data: list[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for row in rows_data[:40]:
        tx_id = _safe_int(row["id"])
        amount = _safe_int(row["amount"])
        user_id = _safe_int(row["user_id"])
        rows.append(
            [
                InlineKeyboardButton(
                    f"✅ تایید #{tx_id} | کاربر {user_id} | {amount:,}",
                    callback_data=f"adm:dep_ok:{tx_id}",
                ),
                InlineKeyboardButton(
                    f"❌ رد #{tx_id}",
                    callback_data=f"adm:dep_rej:{tx_id}",
                ),
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ پنل مدیریت", callback_data="root:admin")])
    return InlineKeyboardMarkup(rows)


def _build_catalog_manage_keyboard(services: list[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("➕ افزودن سرویس جدید", callback_data="adm:catalog_add")]
    ]
    for svc in services[:50]:
        sid = _safe_int(svc["id"])
        active = bool(svc["is_active"])
        title = str(svc["title"])
        rows.append(
            [
                InlineKeyboardButton(
                    f"{'🟢' if active else '⚪'} {title}"[:50],
                    callback_data=f"adm:catalog_toggle:{sid}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ پنل مدیریت", callback_data="root:admin")])
    return InlineKeyboardMarkup(rows)


def _build_admin_catalog_keyboard(services: list[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("➕ افزودن سرویس جدید", callback_data="adm:catalog_add")]
    ]
    for svc in services[:50]:
        sid = _safe_int(svc["id"])
        active = bool(svc["is_active"])
        title = str(svc["title"])
        rows.append(
            [
                InlineKeyboardButton(
                    f"{'🟢' if active else '⚪'} {title}"[:50],
                    callback_data=f"adm:catalog_toggle:{sid}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ پنل مدیریت", callback_data="root:admin")])
    return InlineKeyboardMarkup(rows)


def _build_admin_list_keyboard(admin_ids: list[int], is_owner: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for uid in admin_ids[:50]:
        if is_owner:
            rows.append([InlineKeyboardButton(f"حذف ادمین {uid}", callback_data=f"adm:remove_admin:{uid}")])
    rows.append([InlineKeyboardButton("⬅️ پنل مدیریت", callback_data="root:admin")])
    return InlineKeyboardMarkup(rows)


async def send_home_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str = "پنل اصلی",
) -> None:
    user = update.effective_user
    if not user:
        return
    storage = get_storage(context.application)
    storage.ensure_user(
        user_id=user.id,
        username=user.username or "",
        full_name=(user.full_name or user.first_name or "")[:120],
    )
    is_admin_user = is_admin_id(context.application, user.id)
    await update.effective_message.reply_text(
        text,
        reply_markup=build_home_menu_keyboard(is_admin=is_admin_user),
    )


async def _show_user_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    storage = get_storage(context.application)
    storage.ensure_user(
        user_id=user.id,
        username=user.username or "",
        full_name=(user.full_name or user.first_name or "")[:120],
    )
    await update.effective_message.reply_text(
        _wallet_summary_text(storage, user.id),
        reply_markup=build_user_menu_keyboard(is_admin=is_admin_id(context.application, user.id)),
    )


async def _show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    if not is_admin_id(context.application, user.id):
        await update.effective_message.reply_text("شما دسترسی ادمین ندارید.")
        return
    storage = get_storage(context.application)
    pending = storage.list_pending_deposit_requests(limit=999)
    anomalies = storage.list_order_anomalies(limit=999)
    await update.effective_message.reply_text(
        "پنل مدیریت آماده است.\n"
        f"درخواست شارژ معلق: {len(pending)}\n"
        f"مغایرت مالی/تحویل: {len(anomalies)}",
        reply_markup=build_admin_menu_keyboard(is_owner=is_owner_id(context.application, user.id)),
    )


def _admin_targets(application: Application) -> list[int]:
    storage = get_storage(application)
    targets: set[int] = set(storage.list_admin_ids())
    owner_id = storage.get_owner_id()
    if owner_id:
        targets.add(int(owner_id))
    return sorted(targets)


async def _notify_admins(application: Application, text: str) -> None:
    for admin_id in _admin_targets(application):
        try:
            await application.bot.send_message(chat_id=admin_id, text=text[:3900])
        except Exception:
            continue


def _render_order_row(row: sqlite3.Row) -> str:
    return (
        f"#{_safe_int(row['id'])} | کاربر:{_safe_int(row['user_id'])} | "
        f"{row['order_type']} | مبلغ:{_safe_int(row['amount']):,} | "
        f"وضعیت:{row['status']} | {str(row['created_at'])[:19]}"
    )


def _render_service_row(row: sqlite3.Row) -> str:
    return (
        f"#{_safe_int(row['id'])} | svc:{_safe_int(row['service_id'])} | "
        f"order:{_safe_int(row['order_id'])} | status:{row['status']} | "
        f"exp:{row['expires_at'] or '-'}"
    )


async def root_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    user = update.effective_user
    if not user:
        await query.answer("کاربر نامعتبر", show_alert=True)
        return
    storage = get_storage(context.application)
    username, full_name = _user_label(user)
    storage.ensure_user(user.id, username=username, full_name=full_name)
    admin_user = is_admin_id(context.application, user.id)
    data = query.data
    if data in {"root:home", "root:refresh"}:
        await query.edit_message_text(
            "منوی اصلی",
            reply_markup=build_home_menu_keyboard(is_admin=admin_user),
        )
        return
    if data == "root:user":
        await query.edit_message_text(
            _wallet_summary_text(storage, user.id),
            reply_markup=build_user_menu_keyboard(is_admin=admin_user),
        )
        return
    if data == "root:admin":
        if not admin_user:
            await query.answer("شما دسترسی ادمین ندارید.", show_alert=True)
            return
        pending = storage.list_pending_deposit_requests(limit=999)
        anomalies = storage.list_order_anomalies(limit=999)
        await query.edit_message_text(
            "پنل مدیریت آماده است.\n"
            f"درخواست شارژ معلق: {len(pending)}\n"
            f"مغایرت مالی/تحویل: {len(anomalies)}",
            reply_markup=build_admin_menu_keyboard(is_owner=is_owner_id(context.application, user.id)),
        )
        return
    await query.answer("عملیات نامشخص")


async def user_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    user = update.effective_user
    if not user:
        await query.answer("کاربر نامعتبر", show_alert=True)
        return
    storage = get_storage(context.application)
    username, full_name = _user_label(user)
    storage.ensure_user(user.id, username=username, full_name=full_name)
    data = query.data

    if data == "usr:wallet":
        await query.edit_message_text(
            _wallet_summary_text(storage, user.id),
            reply_markup=build_user_menu_keyboard(is_admin=is_admin_id(context.application, user.id)),
        )
        return

    if data == "usr:wallet_logs":
        txs = storage.list_user_wallet_transactions(user.id, limit=20)
        if not txs:
            await query.edit_message_text(
                "گردش کیف پولی ثبت نشده است.",
                reply_markup=build_user_menu_keyboard(is_admin=is_admin_id(context.application, user.id)),
            )
            return
        lines = []
        for row in txs:
            lines.append(
                f"#{_safe_int(row['id'])} | {row['tx_type']} | {row['status']} | "
                f"{_safe_int(row['amount']):,} | {str(row['created_at'])[:19]}"
            )
        await query.edit_message_text(
            "آخرین گردش کیف پول:\n" + "\n".join(lines[:25]),
            reply_markup=build_user_menu_keyboard(is_admin=is_admin_id(context.application, user.id)),
        )
        return

    if data == "usr:deposit":
        context.user_data["awaiting_deposit_request"] = True
        await query.edit_message_text(
            "مبلغ شارژ و توضیح را بفرست:\n"
            "<amount> | <txHash/توضیح>\n\n"
            "مثال:\n500000 | TRX-abc-123",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ پنل کاربری", callback_data="root:user")]]
            ),
        )
        return

    if data == "usr:catalog":
        _ensure_default_catalog(storage)
        services = storage.list_catalog_services(only_active=True)
        if not services:
            await query.edit_message_text(
                "فعلا سرویسی برای خرید فعال نیست.",
                reply_markup=build_user_menu_keyboard(is_admin=is_admin_id(context.application, user.id)),
            )
            return
        await query.edit_message_text(
            "یک سرویس را برای خرید انتخاب کن:",
            reply_markup=_build_catalog_keyboard(services),
        )
        return

    if data.startswith("usr:buy:"):
        service_id = _parse_callback_int(data, 2)
        if service_id is None:
            await query.answer("شناسه سرویس نامعتبر است.", show_alert=True)
            return
        await _purchase_or_renew_service(update, context, service_id=service_id, order_type="buy")
        return

    if data == "usr:my_orders":
        rows = storage.list_orders(limit=20, user_id=user.id)
        if not rows:
            await query.edit_message_text(
                "سفارشی ثبت نشده است.",
                reply_markup=build_user_menu_keyboard(is_admin=is_admin_id(context.application, user.id)),
            )
            return
        lines = [_render_order_row(row) for row in rows[:20]]
        await query.edit_message_text(
            "آخرین سفارش‌های شما:\n" + "\n".join(lines),
            reply_markup=build_user_menu_keyboard(is_admin=is_admin_id(context.application, user.id)),
        )
        return

    if data == "usr:my_services":
        items = storage.list_user_services(user.id, limit=40)
        if not items:
            await query.edit_message_text(
                "هنوز سرویسی ندارید.",
                reply_markup=build_user_menu_keyboard(is_admin=is_admin_id(context.application, user.id)),
            )
            return
        lines = [_render_service_row(item) for item in items[:10]]
        await query.edit_message_text(
            "سرویس‌های شما:\n" + "\n".join(lines) + "\n\nبرای تمدید، یک مورد را انتخاب کن:",
            reply_markup=_build_my_services_keyboard(items),
        )
        return

    if data.startswith("usr:renew_pick:"):
        service_instance_id = _parse_callback_int(data, 2)
        if service_instance_id is None:
            await query.answer("شناسه سرویس نامعتبر است.", show_alert=True)
            return
        service_item = storage.get_user_service(service_instance_id)
        if not service_item or _safe_int(service_item["user_id"]) != user.id:
            await query.answer("این سرویس متعلق به شما نیست.", show_alert=True)
            return
        svc_id = _safe_int(service_item["service_id"], 0)
        if svc_id <= 0:
            catalogs = storage.list_catalog_services(only_active=True)
            svc_id = _safe_int(catalogs[0]["id"], 0) if catalogs else 0
        if svc_id <= 0:
            await query.answer("پلن تمدید پیدا نشد. با پشتیبانی تماس بگیر.", show_alert=True)
            return
        await query.edit_message_text(
            f"تمدید سرویس #{service_instance_id}\n"
            "برای ادامه پرداخت کیف پول، تایید را بزن.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ تایید تمدید",
                            callback_data=f"usr:renew_do:{service_instance_id}:{svc_id}",
                        )
                    ],
                    [InlineKeyboardButton("⬅️ سرویس‌های من", callback_data="usr:my_services")],
                ]
            ),
        )
        return

    if data.startswith("usr:renew_do:"):
        service_instance_id = _parse_callback_int(data, 2)
        svc_id = _parse_callback_int(data, 3)
        if service_instance_id is None or svc_id is None:
            await query.answer("پارامتر تمدید نامعتبر است.", show_alert=True)
            return
        await _purchase_or_renew_service(
            update,
            context,
            service_id=svc_id,
            order_type="renew",
            renew_service_instance_id=service_instance_id,
        )
        return

    await query.answer("عملیات نامشخص")


async def admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    user = update.effective_user
    if not user:
        await query.answer("کاربر نامعتبر", show_alert=True)
        return
    if not is_admin_id(context.application, user.id):
        await query.answer("شما دسترسی ادمین ندارید.", show_alert=True)
        return
    storage = get_storage(context.application)
    data = query.data

    if data == "adm:broadcast_panel":
        await query.edit_message_text(
            "پنل ارسال گروهی:",
            reply_markup=build_main_menu_keyboard(),
        )
        return

    if data == "adm:deposits":
        rows_data = storage.list_pending_deposit_requests(limit=40)
        if not rows_data:
            await query.edit_message_text(
                "درخواست شارژ معلقی وجود ندارد.",
                reply_markup=build_admin_menu_keyboard(is_owner=is_owner_id(context.application, user.id)),
            )
            return
        lines = [
            f"#{_safe_int(row['id'])} | کاربر:{_safe_int(row['user_id'])} | مبلغ:{_safe_int(row['amount']):,}"
            for row in rows_data[:15]
        ]
        await query.edit_message_text(
            "درخواست‌های شارژ معلق:\n" + "\n".join(lines),
            reply_markup=_build_admin_pending_deposits_keyboard(rows_data),
        )
        return

    if data.startswith("adm:dep_ok:"):
        tx_id = _parse_callback_int(data, 2)
        if tx_id is None:
            await query.answer("شناسه درخواست نامعتبر است.", show_alert=True)
            return
        ok, message, target_user_id, new_balance = storage.approve_deposit_request(tx_id, admin_id=user.id)
        if ok and target_user_id:
            try:
                await context.application.bot.send_message(
                    chat_id=target_user_id,
                    text=f"✅ شارژ کیف پول شما تایید شد.\nموجودی جدید: {new_balance:,}",
                )
            except Exception:
                pass
        await query.answer(message, show_alert=not ok)
        rows_data = storage.list_pending_deposit_requests(limit=40)
        await query.edit_message_text(
            "درخواست‌های شارژ:",
            reply_markup=_build_admin_pending_deposits_keyboard(rows_data),
        )
        return

    if data.startswith("adm:dep_no:") or data.startswith("adm:dep_rej:"):
        tx_id = _parse_callback_int(data, 2)
        if tx_id is None:
            await query.answer("شناسه درخواست نامعتبر است.", show_alert=True)
            return
        ok, message, target_user_id = storage.reject_deposit_request(tx_id, admin_id=user.id, reason="manual")
        if ok and target_user_id:
            try:
                await context.application.bot.send_message(
                    chat_id=target_user_id,
                    text="❌ درخواست شارژ شما رد شد. در صورت نیاز با پشتیبانی تماس بگیر.",
                )
            except Exception:
                pass
        await query.answer(message, show_alert=not ok)
        rows_data = storage.list_pending_deposit_requests(limit=40)
        await query.edit_message_text(
            "درخواست‌های شارژ:",
            reply_markup=_build_admin_pending_deposits_keyboard(rows_data),
        )
        return

    if data == "adm:user_search":
        context.user_data["awaiting_user_search"] = True
        await query.edit_message_text(
            "آیدی کاربر را بفرست تا اطلاعاتش نمایش داده شود.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ پنل مدیریت", callback_data="root:admin")]]
            ),
        )
        return

    if data.startswith("adm:user_adjust:"):
        target_user_id = _parse_callback_int(data, 2)
        if target_user_id is None:
            await query.answer("آیدی کاربر نامعتبر است.", show_alert=True)
            return
        context.user_data["awaiting_wallet_adjust"] = True
        context.user_data["awaiting_wallet_adjust_user_id"] = target_user_id
        await query.edit_message_text(
            f"تنظیم کیف پول کاربر {target_user_id}\n"
            "فرمت:\n<delta> | <note>\n"
            "مثال: +50000 | correction\n"
            "مثال: -25000 | manual debit",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ پنل مدیریت", callback_data="root:admin")]]
            ),
        )
        return

    if data.startswith("adm:user_orders:"):
        target_user_id = _parse_callback_int(data, 2)
        if target_user_id is None:
            await query.answer("آیدی کاربر نامعتبر است.", show_alert=True)
            return
        rows = storage.list_orders(limit=30, user_id=target_user_id)
        if not rows:
            await query.edit_message_text(
                f"سفارشی برای کاربر {target_user_id} ثبت نشده.",
                reply_markup=build_admin_menu_keyboard(is_owner=is_owner_id(context.application, user.id)),
            )
            return
        await query.edit_message_text(
            f"سفارش‌های کاربر {target_user_id}:\n" + "\n".join(_render_order_row(row) for row in rows[:20]),
            reply_markup=build_admin_menu_keyboard(is_owner=is_owner_id(context.application, user.id)),
        )
        return

    if data.startswith("adm:user_services:"):
        target_user_id = _parse_callback_int(data, 2)
        if target_user_id is None:
            await query.answer("آیدی کاربر نامعتبر است.", show_alert=True)
            return
        rows = storage.list_user_services(target_user_id, limit=30)
        if not rows:
            await query.edit_message_text(
                f"سرویسی برای کاربر {target_user_id} ثبت نشده.",
                reply_markup=build_admin_menu_keyboard(is_owner=is_owner_id(context.application, user.id)),
            )
            return
        await query.edit_message_text(
            f"سرویس‌های کاربر {target_user_id}:\n"
            + "\n".join(_render_service_row(row) for row in rows[:20]),
            reply_markup=build_admin_menu_keyboard(is_owner=is_owner_id(context.application, user.id)),
        )
        return

    if data == "adm:audit":
        anomalies = storage.list_order_anomalies(limit=40)
        if not anomalies:
            await query.edit_message_text(
                "مغایرت مالی/تحویل فعالی پیدا نشد ✅",
                reply_markup=build_admin_menu_keyboard(is_owner=is_owner_id(context.application, user.id)),
            )
            return
        lines = []
        for item in anomalies[:20]:
            lines.append(
                f"#{item['order_id']} | u:{item['user_id']} | {item['status']} | "
                f"{item['amount']:,} | {item['issue']}"
            )
        await query.edit_message_text(
            "مغایرت‌های شناسایی‌شده:\n" + "\n".join(lines),
            reply_markup=build_admin_menu_keyboard(is_owner=is_owner_id(context.application, user.id)),
        )
        return

    if data == "adm:catalog":
        _ensure_default_catalog(storage)
        services = storage.list_catalog_services(only_active=False)
        await query.edit_message_text(
            "مدیریت کاتالوگ سرویس:",
            reply_markup=_build_admin_catalog_keyboard(services),
        )
        return

    if data == "adm:catalog_add":
        context.user_data["awaiting_catalog_add"] = True
        await query.edit_message_text(
            "فرمت افزودن سرویس:\n<title> | <price> | <gb> | <days>\n"
            "مثال:\nپلن 20GB | 950000 | 20 | 30",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ پنل مدیریت", callback_data="root:admin")]]
            ),
        )
        return

    if data.startswith("adm:catalog_toggle:"):
        service_id = _parse_callback_int(data, 2)
        if service_id is None:
            await query.answer("شناسه سرویس نامعتبر است.", show_alert=True)
            return
        service = storage.get_catalog_service(service_id)
        if not service:
            await query.answer("سرویس پیدا نشد.", show_alert=True)
            return
        new_value = not bool(service["is_active"])
        storage.set_catalog_service_active(service_id, new_value)
        services = storage.list_catalog_services(only_active=False)
        await query.edit_message_text(
            f"وضعیت سرویس {'فعال' if new_value else 'غیرفعال'} شد.",
            reply_markup=_build_admin_catalog_keyboard(services),
        )
        return

    if data == "adm:list_admins":
        ids = storage.list_admin_ids()
        text = "ادمین‌ها:\n" + ("\n".join(f"- {uid}" for uid in ids) if ids else "هیچ ادمینی ثبت نشده.")
        await query.edit_message_text(
            text,
            reply_markup=_build_admin_list_keyboard(ids, is_owner=is_owner_id(context.application, user.id)),
        )
        return

    if data == "adm:add_admin":
        if not is_owner_id(context.application, user.id):
            await query.answer("فقط مالک می‌تواند ادمین اضافه کند.", show_alert=True)
            return
        context.user_data["awaiting_add_admin"] = True
        await query.edit_message_text(
            "آیدی عددی ادمین جدید را بفرست.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ پنل مدیریت", callback_data="root:admin")]]
            ),
        )
        return

    if data == "adm:remove_admin":
        if not is_owner_id(context.application, user.id):
            await query.answer("فقط مالک می‌تواند ادمین حذف کند.", show_alert=True)
            return
        context.user_data["awaiting_remove_admin"] = True
        await query.edit_message_text(
            "آیدی عددی ادمینی که می‌خواهی حذف شود را بفرست.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ پنل مدیریت", callback_data="root:admin")]]
            ),
        )
        return

    if data.startswith("adm:remove_admin:"):
        if not is_owner_id(context.application, user.id):
            await query.answer("فقط مالک می‌تواند ادمین حذف کند.", show_alert=True)
            return
        target_uid = _parse_callback_int(data, 2)
        if target_uid is None:
            await query.answer("آیدی نامعتبر است.", show_alert=True)
            return
        if is_owner_id(context.application, target_uid):
            await query.answer("مالک اصلی قابل حذف نیست.", show_alert=True)
            return
        storage.remove_admin_id(target_uid)
        await query.answer("ادمین حذف شد.")
        ids = storage.list_admin_ids()
        await query.edit_message_text(
            "ادمین‌ها:\n" + ("\n".join(f"- {uid}" for uid in ids) if ids else "هیچ ادمینی ثبت نشده."),
            reply_markup=_build_admin_list_keyboard(ids, is_owner=True),
        )
        return

    await query.answer("عملیات نامشخص")


def _clear_user_admin_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (
        "awaiting_deposit_request",
        "awaiting_user_search",
        "awaiting_wallet_adjust",
        "awaiting_wallet_adjust_user_id",
        "awaiting_catalog_add",
        "awaiting_add_admin",
        "awaiting_remove_admin",
    ):
        context.user_data.pop(key, None)


def _clear_group_manage_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("awaiting_manual_group_add", None)


def _clear_xui_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (
        "awaiting_xui_server",
        "awaiting_xui_generate",
        "xui_selected_server_id",
        "xui_selected_inbound_id",
        "xui_pending_generate",
        "xui_selected_plan_key",
    ):
        context.user_data.pop(key, None)


async def _send_xui_batch_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    server_name: str,
    inbound_label: str,
    created_items: list[dict],
) -> None:
    if not update.effective_message or not created_items:
        return
    lines: list[str] = []
    for idx, item in enumerate(created_items, start=1):
        lines.append(f"{idx}. {item.get('email','-')}")
        lines.append(f"sub: {item.get('sub_link','-')}")
        lines.append(f"uuid: {item.get('uuid','-')}")
        lines.append("")
    payload = "\n".join(lines).strip() + "\n"
    out_file = io.BytesIO(payload.encode("utf-8"))
    out_file.name = "xui-clients-batch.txt"
    await update.effective_message.reply_document(
        document=out_file,
        filename=out_file.name,
        caption=(
            f"ساخت دسته‌ای انجام شد.\n"
            f"سرور: {server_name}\n"
            f"Inbound: {inbound_label}\n"
            f"تعداد ساخته‌شده: {len(created_items)}"
        ),
    )


async def _handle_xui_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> bool:
    query = update.callback_query
    if not query:
        return False
    storage = get_storage(context.application)

    if data == "menu:xui_servers":
        await query.edit_message_text(
            "مدیریت سرورهای ثنایی:\nیک سرور انتخاب کن یا سرور جدید اضافه کن.",
            reply_markup=_build_xui_servers_keyboard(storage),
        )
        return True

    if data == "xui:add_server":
        context.user_data["awaiting_xui_server"] = True
        await query.edit_message_text(
            "اطلاعات سرور را در یک پیام بفرست:\n"
            "<name> | <panel_url> | <username> | <password>\n\n"
            "مثال:\n"
            "se4 | https://se4.gishevip.xyz:26932/fA1nFc6aTT8rusHYxT/panel/inbounds | admin | pass123",
            reply_markup=_build_xui_back_keyboard(),
        )
        return True

    if data == "xui:test_all":
        servers = storage.list_xui_servers(only_active=False)
        if not servers:
            await query.answer("هیچ سروری ثبت نشده.", show_alert=True)
            return True
        await query.edit_message_text("در حال تست اتصال همه سرورها... لطفا صبر کن.")
        lines = ["نتیجه تست سرورها:"]
        for index, server in enumerate(servers, start=1):
            try:
                api = XuiPanelClient(server)
                api.login()
                inbounds = api.list_inbounds()
                storage.mark_xui_server_check(server.id, ok=True, error_text="")
                mode = "strict" if server.tls_verify else "relaxed"
                lines.append(
                    f"{index}. ✅ {server.name} | inbounds={len(inbounds)} | tls={mode}"
                )
            except Exception as exc:
                storage.mark_xui_server_check(server.id, ok=False, error_text=str(exc))
                lines.append(
                    f"{index}. ❌ {server.name} | {_shorten_text(str(exc), 180)}"
                )
        await query.message.reply_text("\n".join(lines)[:4000])
        await query.message.reply_text(
            "مدیریت سرورها:",
            reply_markup=_build_xui_servers_keyboard(storage),
        )
        return True

    if data.startswith("xui:server:"):
        try:
            server_id = int(data.split(":")[2])
        except (IndexError, ValueError):
            await query.answer("شناسه سرور نامعتبر است.", show_alert=True)
            return True
        server = storage.get_xui_server(server_id)
        if not server:
            await query.answer("سرور پیدا نشد.", show_alert=True)
            return True
        try:
            api = XuiPanelClient(server)
            api.login()
            inbounds = api.list_inbounds()
            storage.mark_xui_server_check(server.id, ok=True, error_text="")
        except Exception as exc:
            storage.mark_xui_server_check(server.id, ok=False, error_text=str(exc))
            await query.edit_message_text(
                f"اتصال به پنل ناموفق بود:\n{_shorten_text(str(exc), 500)}",
                reply_markup=_build_xui_servers_keyboard(storage),
            )
            return True
        context.user_data["xui_selected_server_id"] = server_id
        await query.edit_message_text(
            f"سرور: {server.name}\n"
            f"TLS Verify: {'روشن' if server.tls_verify else 'خاموش'}\n"
            "یک inbound انتخاب کن.",
            reply_markup=_build_xui_inbounds_keyboard(server_id, inbounds),
        )
        return True

    if data.startswith("xui:toggle_tls:"):
        try:
            server_id = int(data.split(":")[2])
        except (IndexError, ValueError):
            await query.answer("شناسه سرور نامعتبر است.", show_alert=True)
            return True
        server = storage.get_xui_server(server_id)
        if not server:
            await query.answer("سرور پیدا نشد.", show_alert=True)
            return True
        new_value = not server.tls_verify
        storage.set_xui_server_tls_verify(server_id, new_value)
        await query.answer(
            f"TLS Verify {'روشن' if new_value else 'خاموش'} شد.",
            show_alert=True,
        )
        try:
            refreshed = storage.get_xui_server(server_id)
            if refreshed is None:
                raise RuntimeError("سرور یافت نشد.")
            api = XuiPanelClient(refreshed)
            api.login()
            inbounds = api.list_inbounds()
            storage.mark_xui_server_check(refreshed.id, ok=True, error_text="")
        except Exception as exc:
            storage.mark_xui_server_check(server_id, ok=False, error_text=str(exc))
            await query.edit_message_text(
                f"بعد از تغییر TLS اتصال ناموفق بود:\n{_shorten_text(str(exc), 500)}",
                reply_markup=_build_xui_servers_keyboard(storage),
            )
            return True
        await query.edit_message_text(
            f"سرور: {refreshed.name}\n"
            f"TLS Verify: {'روشن' if refreshed.tls_verify else 'خاموش'}\n"
            "یک inbound انتخاب کن.",
            reply_markup=_build_xui_inbounds_keyboard(server_id, inbounds),
        )
        return True

    if data.startswith("xui:plan:"):
        parts = data.split(":")
        if len(parts) != 5:
            await query.answer("پارامتر پلن نامعتبر است.", show_alert=True)
            return True
        plan_key = parts[2].strip()
        try:
            server_id = int(parts[3])
            inbound_id = int(parts[4])
        except ValueError:
            await query.answer("پارامتر پلن نامعتبر است.", show_alert=True)
            return True
        plan = _xui_plan_by_key(plan_key)
        if not plan:
            await query.answer("پلن پیدا نشد.", show_alert=True)
            return True
        server = storage.get_xui_server(server_id)
        if not server:
            await query.answer("سرور پیدا نشد.", show_alert=True)
            return True
        context.user_data["xui_selected_server_id"] = server_id
        context.user_data["xui_selected_inbound_id"] = inbound_id
        context.user_data["awaiting_xui_generate"] = True
        context.user_data["xui_selected_plan_key"] = plan_key
        await query.edit_message_text(
            f"پلن انتخاب شد: {plan['label']}\n"
            f"سرور: {server.name}\n"
            f"Inbound: {inbound_id}\n\n"
            "حالا فقط تعداد و prefix را بفرست:\n"
            "<count> | <prefix>\n\n"
            "مثال:\n30 | se4-a",
            reply_markup=_build_xui_back_keyboard(),
        )
        return True

    if data.startswith("xui:pick_inbound:"):
        parts = data.split(":")
        try:
            server_id = int(parts[2])
            inbound_id = int(parts[3])
        except (IndexError, ValueError):
            await query.answer("inbound نامعتبر است.", show_alert=True)
            return True
        server = storage.get_xui_server(server_id)
        if not server:
            await query.answer("سرور پیدا نشد.", show_alert=True)
            return True
        context.user_data["xui_selected_server_id"] = server_id
        context.user_data["xui_selected_inbound_id"] = inbound_id
        context.user_data["awaiting_xui_generate"] = True
        context.user_data.pop("xui_selected_plan_key", None)
        await query.edit_message_text(
            f"سرور: {server.name}\nInbound: {inbound_id}\n\n"
            "حالا پارامتر ساخت را بفرست:\n"
            "<count> | <totalGB> | <expireDays> | <prefix>\n\n"
            "مثال:\n10 | 1 | 30 | se4-u",
            reply_markup=_build_xui_back_keyboard(),
        )
        return True

    if data == "xui:generate_confirm":
        pending = context.user_data.get("xui_pending_generate")
        if not isinstance(pending, dict):
            await query.answer("داده ساخت پیدا نشد.", show_alert=True)
            return True
        server_id = int(pending.get("server_id", 0))
        inbound_id = int(pending.get("inbound_id", 0))
        count = int(pending.get("count", 0))
        total_gb = int(pending.get("total_gb", 0))
        expire_days = int(pending.get("expire_days", 0))
        prefix = str(pending.get("prefix", "u"))
        server = storage.get_xui_server(server_id)
        if not server:
            await query.answer("سرور پیدا نشد.", show_alert=True)
            return True

        await query.edit_message_text("در حال ساخت کلاینت‌ها... لطفا صبر کن.")
        created_items: list[dict] = []
        failed_count = 0
        failed_items: list[str] = []
        try:
            api = XuiPanelClient(server)
            api.login()
            storage.mark_xui_server_check(server.id, ok=True, error_text="")
            created_items, failed_items = api.add_clients_batch(
                inbound_id=inbound_id,
                count=count,
                total_gb=total_gb,
                expire_days=expire_days,
                prefix=prefix,
            )
        except Exception as exc:
            storage.mark_xui_server_check(server.id, ok=False, error_text=str(exc))
            failed_count = count - len(created_items)
            await query.message.reply_text(
                f"ساخت دسته‌ای ناموفق بود:\n{_shorten_text(str(exc), 600)}",
                reply_markup=_build_xui_inbounds_keyboard(server_id, []),
            )
        else:
            failed_count = len(failed_items)
            await query.message.reply_text(
                "ساخت دسته‌ای کامل شد ✅\n"
                f"تعداد ساخته‌شده: {len(created_items)}\n"
                f"ناموفق: {failed_count}\n"
                f"سرور: {server.name}\n"
                f"Inbound: {inbound_id}"
            )
            await _send_xui_batch_file(
                update=update,
                context=context,
                server_name=server.name,
                inbound_label=str(inbound_id),
                created_items=created_items,
            )
            if failed_items:
                await query.message.reply_text(
                    "چند خطای اول ساخت:\n"
                    + "\n".join(f"- {item}" for item in failed_items[:8])
                )
        finally:
            storage.add_xui_batch_log(
                server_id=server_id,
                inbound_id=inbound_id,
                requested_count=count,
                created_count=len(created_items),
                failed_count=max(failed_count, count - len(created_items)),
                total_gb=total_gb,
                expire_days=expire_days,
                note=f"prefix={prefix}",
            )
            _clear_xui_state(context)
            await query.message.reply_text("بازگشت به پنل اصلی", reply_markup=build_main_menu_keyboard())
        return True

    return False


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
    if data.startswith("xui:") or data == "menu:xui_servers":
        handled = await _handle_xui_callback(update, context, data)
        if handled:
            return

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
        groups = await sync_groups_admin_status(storage, context.bot, storage.list_groups())
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
    inserted, duplicates = storage.add_links(links)
    total_links = len(storage.list_links())
    await update.effective_message.reply_text(
        "لینک‌ها پردازش شدند.\n"
        f"کل ورودی معتبر: {len(links)}\n"
        f"جدید اضافه شد: {inserted}\n"
        f"تکراری (قبلاً موجود): {duplicates}\n"
        f"مجموع لینک‌های ذخیره‌شده: {total_links}"
    )


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


async def replacelinks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return
    payload = (update.effective_message.text or "").split(maxsplit=1)
    if len(payload) < 2 or not payload[1].strip():
        await update.effective_message.reply_text(
            "نحوه استفاده:\n"
            "/replacelinks <متن لینک‌ها>\n\n"
            "یا در پنل وب، کادر لینک‌ها را خالی کرده و لیست جدید را جایگزین کن."
        )
        return
    links, invalid_lines = parse_links(payload[1].strip())
    if invalid_lines:
        preview = "\n".join(invalid_lines[:10])
        await update.effective_message.reply_text(
            "چند خط نامعتبر شناسایی شد.\n"
            "فرمت‌های قابل قبول: http/https و همچنین vmess/vless/trojan/ss/ssr و مشابه.\n"
            f"{preview}"
        )
        return
    if not links:
        await update.effective_message.reply_text("هیچ لینک معتبری پیدا نشد.")
        return
    storage = get_storage(context.application)
    storage.replace_links(links)
    await update.effective_message.reply_text(
        f"لیست لینک‌ها کامل جایگزین شد.\nتعداد جدید: {len(links)}"
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

    refreshed = await sync_groups_admin_status(storage, context.bot, groups)
    admin_count = sum(1 for group in refreshed if group.is_active and group.is_admin)
    await update.effective_message.reply_text(
        f"{len(groups)} گروه بررسی شد. "
        f"{admin_count} گروه هم‌اکنون قابل ارسال هستند."
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
    groups = await sync_groups_admin_status(storage, context.bot, groups)
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
        selected = {group.chat_id for group in groups if group.chat_id in selected and group.is_admin}
        if not selected:
            await query.answer("هیچ گروه ادمین فعالی برای ارسال باقی نماند.", show_alert=True)
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
        groups = await sync_groups_admin_status(storage, context.bot, groups)
        if not groups:
            await query.edit_message_text("هیچ گروه فعالی ندارید. ابتدا گروه‌ها را ثبت کن.")
            return
        groups = [group for group in groups if group.is_admin]
        if not groups:
            await query.edit_message_text("هیچ گروهی که ربات در آن ادمین باشد پیدا نشد.")
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
        groups = await sync_groups_admin_status(storage, context.bot, groups)
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
        groups = await sync_groups_admin_status(storage, context.bot, groups)
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
    context.user_data.pop("awaiting_deposit_request", None)
    context.user_data.pop("awaiting_user_search", None)
    context.user_data.pop("awaiting_wallet_adjust", None)
    context.user_data.pop("awaiting_wallet_adjust_user_id", None)
    context.user_data.pop("awaiting_catalog_add", None)
    context.user_data.pop("awaiting_add_admin", None)
    context.user_data.pop("awaiting_remove_admin", None)
    context.user_data.pop("wizard_mode", None)
    context.user_data.pop("awaiting_wizard_links", None)
    context.user_data.pop("wizard_links", None)
    context.user_data.pop("wizard_group_id", None)
    context.user_data.pop("sendlinks_mode", None)
    context.user_data.pop("pending_links", None)
    context.user_data.pop("pending_selected_groups", None)
    context.user_data.pop("awaiting_links_file", None)
    _clear_xui_state(context)
    await send_home_menu(update, context, text="عملیات لغو شد.")


async def private_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE:
        return
    user = update.effective_user
    if not user or not update.effective_message:
        return
    storage = get_storage(context.application)
    username, full_name = _user_label(user)
    storage.ensure_user(user.id, username=username, full_name=full_name)

    if context.user_data.get("awaiting_deposit_request"):
        context.user_data.pop("awaiting_deposit_request", None)
        raw_text = (update.effective_message.text or "").strip()
        parts = [part.strip() for part in raw_text.split("|", 1)]
        if len(parts) != 2:
            await update.effective_message.reply_text(
                "فرمت نامعتبر است.\n"
                "فرمت درست:\n<amount> | <txHash/توضیح>\n"
                "مثال:\n500000 | TRX-abc-123",
                reply_markup=build_user_menu_keyboard(is_admin=is_admin_id(context.application, user.id)),
            )
            return
        try:
            amount = int(parts[0].replace(",", "").replace("_", "").strip())
        except ValueError:
            await update.effective_message.reply_text("مبلغ باید عدد صحیح باشد.")
            return
        if amount <= 0:
            await update.effective_message.reply_text("مبلغ باید بیشتر از صفر باشد.")
            return
        note = parts[1][:300]
        tx_id = storage.create_deposit_request(user.id, amount=amount, note=note)
        admins = storage.list_admin_ids()
        notify_text = (
            "درخواست شارژ جدید 💳\n"
            f"tx_id: {tx_id}\n"
            f"user_id: {user.id}\n"
            f"amount: {amount:,}\n"
            f"note: {note}"
        )
        for admin_id in admins:
            try:
                await context.application.bot.send_message(chat_id=admin_id, text=notify_text)
            except Exception:
                continue
        await update.effective_message.reply_text(
            f"درخواست شارژ ثبت شد ✅\nشناسه درخواست: {tx_id}",
            reply_markup=build_user_menu_keyboard(is_admin=is_admin_id(context.application, user.id)),
        )
        return

    if context.user_data.get("awaiting_user_search") and is_admin_id(context.application, user.id):
        context.user_data.pop("awaiting_user_search", None)
        raw_text = (update.effective_message.text or "").strip()
        try:
            target_user_id = int(raw_text)
        except ValueError:
            await update.effective_message.reply_text("آیدی کاربر باید عدد صحیح باشد.")
            return
        profile = storage.get_user_profile(target_user_id)
        orders_count = storage.count_user_orders(target_user_id)
        services_count = storage.count_user_services(target_user_id)
        txs = storage.list_user_wallet_transactions(target_user_id, limit=10)
        tx_lines = [
            f"#{_safe_int(tx['id'])} | {tx['tx_type']} | {tx['status']} | {_safe_int(tx['amount']):,}"
            for tx in txs[:6]
        ]
        await update.effective_message.reply_text(
            f"کاربر: {target_user_id}\n"
            f"نام: {profile['full_name'] if profile else '-'}\n"
            f"یوزرنیم: @{profile['username'] if profile and profile['username'] else '-'}\n"
            f"موجودی: {_safe_int(profile['wallet_balance'] if profile else 0):,}\n"
            f"تعداد سفارش: {orders_count}\n"
            f"تعداد سرویس: {services_count}\n\n"
            f"آخرین تراکنش‌ها:\n" + ("\n".join(tx_lines) if tx_lines else "ندارد"),
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("💸 تنظیم کیف پول این کاربر", callback_data=f"adm:user_adjust:{target_user_id}")],
                    [InlineKeyboardButton("📦 سفارش‌های کاربر", callback_data=f"adm:user_orders:{target_user_id}")],
                    [InlineKeyboardButton("🧾 سرویس‌های کاربر", callback_data=f"adm:user_services:{target_user_id}")],
                    [InlineKeyboardButton("⬅️ پنل مدیریت", callback_data="root:admin")],
                ]
            ),
        )
        return

    if context.user_data.get("awaiting_wallet_adjust") and is_admin_id(context.application, user.id):
        target_user_id = context.user_data.get("awaiting_wallet_adjust_user_id")
        context.user_data.pop("awaiting_wallet_adjust", None)
        context.user_data.pop("awaiting_wallet_adjust_user_id", None)
        if not isinstance(target_user_id, int):
            await update.effective_message.reply_text("کاربر مقصد مشخص نیست.")
            return
        raw_text = (update.effective_message.text or "").strip()
        parts = [part.strip() for part in raw_text.split("|", 1)]
        if len(parts) != 2:
            await update.effective_message.reply_text(
                "فرمت نامعتبر است.\n<delta> | <note>\nمثال: +50000 | correction"
            )
            return
        try:
            delta = int(parts[0].replace(",", "").replace("_", "").strip())
        except ValueError:
            await update.effective_message.reply_text("delta باید عدد صحیح باشد.")
            return
        note = f"manual_by:{user.id} {parts[1][:220]}"
        ok, new_balance, _ = storage.adjust_wallet_balance(
            user_id=target_user_id,
            delta=delta,
            tx_type="manual_adjust",
            note=note,
        )
        if not ok:
            await update.effective_message.reply_text("عملیات ناموفق بود (موجودی منفی می‌شود).")
            return
        await update.effective_message.reply_text(
            f"کیف پول کاربر {target_user_id} تنظیم شد ✅\n"
            f"تغییر: {delta:,}\n"
            f"موجودی جدید: {new_balance:,}",
            reply_markup=build_admin_menu_keyboard(is_owner=is_owner_id(context.application, user.id)),
        )
        try:
            await context.application.bot.send_message(
                chat_id=target_user_id,
                text=f"کیف پول شما توسط پشتیبانی تغییر کرد.\nمبلغ تغییر: {delta:,}\nموجودی جدید: {new_balance:,}",
            )
        except Exception:
            pass
        return

    if context.user_data.get("awaiting_catalog_add") and is_admin_id(context.application, user.id):
        context.user_data.pop("awaiting_catalog_add", None)
        raw_text = (update.effective_message.text or "").strip()
        parts = [part.strip() for part in raw_text.split("|")]
        if len(parts) != 4 or not parts[0]:
            await update.effective_message.reply_text(
                "فرمت نامعتبر است.\n<title> | <price> | <gb> | <days>\nمثال: پلن 20GB | 950000 | 20 | 30"
            )
            return
        title = parts[0][:120]
        try:
            price = int(parts[1].replace(",", "").replace("_", ""))
            volume_gb = int(parts[2])
            duration_days = int(parts[3])
        except ValueError:
            await update.effective_message.reply_text("price/gb/days باید عدد صحیح باشند.")
            return
        if price <= 0 or volume_gb <= 0 or duration_days <= 0:
            await update.effective_message.reply_text("price و gb و days باید بیشتر از صفر باشند.")
            return
        storage.add_catalog_service(title=title, price=price, volume_gb=volume_gb, duration_days=duration_days)
        services = storage.list_catalog_services(only_active=False)
        await update.effective_message.reply_text(
            "سرویس جدید اضافه شد ✅",
            reply_markup=_build_admin_catalog_keyboard(services),
        )
        return

    if context.user_data.get("awaiting_add_admin") and is_owner_id(context.application, user.id):
        context.user_data.pop("awaiting_add_admin", None)
        raw_text = (update.effective_message.text or "").strip()
        try:
            target_uid = int(raw_text)
        except ValueError:
            await update.effective_message.reply_text("آیدی ادمین باید عدد صحیح باشد.")
            return
        storage.add_admin_id(target_uid)
        ids = storage.list_admin_ids()
        await update.effective_message.reply_text(
            "ادمین اضافه شد ✅",
            reply_markup=_build_admin_list_keyboard(ids, is_owner=True),
        )
        return

    if context.user_data.get("awaiting_remove_admin") and is_owner_id(context.application, user.id):
        context.user_data.pop("awaiting_remove_admin", None)
        raw_text = (update.effective_message.text or "").strip()
        try:
            target_uid = int(raw_text)
        except ValueError:
            await update.effective_message.reply_text("آیدی ادمین باید عدد صحیح باشد.")
            return
        if is_owner_id(context.application, target_uid):
            await update.effective_message.reply_text("مالک اصلی قابل حذف نیست.")
            return
        storage.remove_admin_id(target_uid)
        ids = storage.list_admin_ids()
        await update.effective_message.reply_text(
            "ادمین حذف شد ✅",
            reply_markup=_build_admin_list_keyboard(ids, is_owner=True),
        )
        return

    if (context.user_data.get("awaiting_deposit_request") or context.user_data.get("awaiting_user_search")
        or context.user_data.get("awaiting_wallet_adjust") or context.user_data.get("awaiting_catalog_add")
        or context.user_data.get("awaiting_add_admin") or context.user_data.get("awaiting_remove_admin")):
        await update.effective_message.reply_text(
            "برای این عملیات دسترسی ادمین/مالک لازم است.",
            reply_markup=build_home_menu_keyboard(is_admin=False),
        )
        return

    if not await owner_required(update, context):
        return
    if context.user_data.get("awaiting_xui_server"):
        raw_text = (update.effective_message.text or "").strip()
        parts = [part.strip() for part in raw_text.split("|")]
        if len(parts) != 4 or not all(parts):
            await update.effective_message.reply_text(
                "فرمت افزودن سرور نامعتبر است.\n"
                "فرمت درست:\n"
                "<name> | <panel_url> | <username> | <password>\n\n"
                "مثال:\n"
                "se4 | https://se4.gishevip.xyz:26932/fA1nFc6aTT8rusHYxT/panel/inbounds | admin | pass123",
                reply_markup=_build_xui_back_keyboard(),
            )
            return
        name, panel_url, username, password = parts
        storage = get_storage(context.application)
        try:
            server_id = storage.upsert_xui_server(name=name, panel_url=panel_url, username=username, password=password)
            server = storage.get_xui_server(server_id)
            if not server:
                raise RuntimeError("سرور ذخیره نشد.")
            api = XuiPanelClient(server)
            api.login()
            inbounds = api.list_inbounds()
        except Exception as exc:
            await update.effective_message.reply_text(
                "ذخیره/اتصال سرور ناموفق بود:\n"
                f"{_shorten_text(str(exc), 700)}\n\n"
                "دوباره اطلاعات را درست بفرست.",
                reply_markup=_build_xui_back_keyboard(),
            )
            return
        context.user_data.pop("awaiting_xui_server", None)
        context.user_data["xui_selected_server_id"] = server_id
        await update.effective_message.reply_text(
            f"سرور «{server.name}» ذخیره و تست شد ✅\n"
            "الان یک inbound را انتخاب کن:",
            reply_markup=_build_xui_inbounds_keyboard(server_id, inbounds),
        )
        return
    if context.user_data.get("awaiting_xui_generate"):
        raw_text = (update.effective_message.text or "").strip()
        selected_plan_key = context.user_data.get("xui_selected_plan_key")
        if selected_plan_key:
            plan = _xui_plan_by_key(str(selected_plan_key))
            if plan:
                try:
                    count = int(raw_text)
                except ValueError:
                    await update.effective_message.reply_text("برای پلن آماده فقط تعداد را عددی بفرست.")
                    return
                if count <= 0 or count > 300:
                    await update.effective_message.reply_text("تعداد باید بین 1 تا 300 باشد.")
                    return
                total_gb = int(plan["gb"])
                expire_days = int(plan["days"])
                prefix = re.sub(r"[^a-zA-Z0-9._-]", "", f"{selected_plan_key}-u")[:24] or "u"
                server_id = context.user_data.get("xui_selected_server_id")
                inbound_id = context.user_data.get("xui_selected_inbound_id")
                if not isinstance(server_id, int) or not isinstance(inbound_id, int):
                    await update.effective_message.reply_text(
                        "ابتدا سرور و inbound را از پنل انتخاب کن.",
                        reply_markup=build_main_menu_keyboard(),
                    )
                    _clear_xui_state(context)
                    return
                context.user_data["xui_pending_generate"] = {
                    "server_id": server_id,
                    "inbound_id": inbound_id,
                    "count": count,
                    "total_gb": total_gb,
                    "expire_days": expire_days,
                    "prefix": prefix,
                    "plan_key": selected_plan_key,
                }
                context.user_data["awaiting_xui_generate"] = False
                await update.effective_message.reply_text(
                    "پیش‌نمایش ساخت:\n"
                    f"- پلن: {plan['label']}\n"
                    f"- تعداد: {count}\n"
                    f"- حجم هر کلاینت: {total_gb} GB\n"
                    f"- انقضا: {expire_days} روز\n"
                    f"- prefix: {prefix}\n\n"
                    "برای ساخت، دکمه تایید را بزن.",
                    reply_markup=_build_xui_generate_confirm_keyboard(),
                )
                return
        parts = [part.strip() for part in raw_text.split("|")]
        if len(parts) != 4:
            await update.effective_message.reply_text(
                "فرمت پارامتر ساخت نامعتبر است.\n"
                "فرمت درست:\n"
                "<count> | <totalGB> | <expireDays> | <prefix>\n\n"
                "مثال:\n10 | 1 | 30 | se4-u",
                reply_markup=_build_xui_back_keyboard(),
            )
            return
        try:
            count = int(parts[0])
            total_gb = int(parts[1])
            expire_days = int(parts[2])
        except ValueError:
            await update.effective_message.reply_text("count / totalGB / expireDays باید عدد صحیح باشد.")
            return
        prefix = re.sub(r"[^a-zA-Z0-9._-]", "", parts[3])[:24]
        if not prefix:
            await update.effective_message.reply_text("prefix نامعتبر است. حروف/عدد و -_. مجاز است.")
            return
        if count <= 0 or count > 300:
            await update.effective_message.reply_text("تعداد باید بین 1 تا 300 باشد.")
            return
        if total_gb <= 0 or total_gb > 10000:
            await update.effective_message.reply_text("حجم باید بین 1 تا 10000 گیگ باشد.")
            return
        if expire_days < 0 or expire_days > 3650:
            await update.effective_message.reply_text("تعداد روز باید بین 0 تا 3650 باشد.")
            return
        server_id = context.user_data.get("xui_selected_server_id")
        inbound_id = context.user_data.get("xui_selected_inbound_id")
        if not isinstance(server_id, int) or not isinstance(inbound_id, int):
            await update.effective_message.reply_text(
                "ابتدا سرور و inbound را از پنل انتخاب کن.",
                reply_markup=build_main_menu_keyboard(),
            )
            _clear_xui_state(context)
            return
        context.user_data["xui_pending_generate"] = {
            "server_id": server_id,
            "inbound_id": inbound_id,
            "count": count,
            "total_gb": total_gb,
            "expire_days": expire_days,
            "prefix": prefix,
        }
        context.user_data["awaiting_xui_generate"] = False
        await update.effective_message.reply_text(
            "پیش‌نمایش ساخت:\n"
            f"- تعداد: {count}\n"
            f"- حجم هر کلاینت: {total_gb} GB\n"
            f"- انقضا: {expire_days} روز\n"
            f"- prefix: {prefix}\n\n"
            "برای ساخت، دکمه تایید را بزن.",
            reply_markup=_build_xui_generate_confirm_keyboard(),
        )
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
    last_prune_at = 0.0
    while True:
        try:
            now_mono = time.monotonic()
            if now_mono - last_prune_at >= SEND_LOG_PRUNE_INTERVAL_SECONDS:
                removed = storage.trim_send_logs(SEND_LOG_RETENTION)
                if removed:
                    LOGGER.info("لاگ‌های قدیمی ارسال پاک شدند: %s رکورد", removed)
                last_prune_at = now_mono
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
        inserted, duplicates = storage.add_links(links)
        total_links = len(storage.list_links())
        return RedirectResponse(
            url=_panel_url(
                "/",
                f"پردازش لینک‌ها انجام شد | جدید: {inserted} | تکراری: {duplicates} | مجموع: {total_links}",
            ),
            status_code=303,
        )

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
    app.add_handler(CommandHandler("replacelinks", replacelinks_command))
    app.add_handler(CommandHandler("setlinksfile", setlinksfile_command))
    app.add_handler(CommandHandler("links", links_command))
    app.add_handler(CommandHandler("groups", groups_command))
    app.add_handler(CommandHandler("addgroup", addgroup_command))
    app.add_handler(CommandHandler("removegroup", removegroup_command))
    app.add_handler(CommandHandler("refreshadmins", refresh_admins_command))
    app.add_handler(CommandHandler("sendlinks", sendlinks_command))
    app.add_handler(CommandHandler("panel", panel_command))
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
    app.add_handler(CallbackQueryHandler(root_menu_callback, pattern=r"^root:"))
    app.add_handler(CallbackQueryHandler(user_menu_callback, pattern=r"^usr:"))
    app.add_handler(CallbackQueryHandler(admin_menu_callback, pattern=r"^adm:"))
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
    ensure_admin_bootstrap(storage, configured_owner_id)

    if WEB_PANEL_ENABLED:
        web_thread = threading.Thread(target=run_web_panel, args=(storage,), daemon=True, name="web-panel")
        web_thread.start()
        LOGGER.info("Web panel started at http://%s:%s", WEB_PANEL_HOST, WEB_PANEL_PORT)

    app = build_application(storage, configured_owner_id=configured_owner_id)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
