"""Small SQLite-backed store used for bulk creation jobs and audit log."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    admin_id INTEGER NOT NULL,
    inbound_id INTEGER NOT NULL,
    count INTEGER NOT NULL,
    total_gb INTEGER NOT NULL,
    expiry_days INTEGER NOT NULL,
    prefix TEXT NOT NULL,
    target_chat TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    log TEXT
);

CREATE TABLE IF NOT EXISTS created_clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    inbound_id INTEGER NOT NULL,
    email TEXT NOT NULL,
    client_uuid TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    link TEXT
);
"""


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        self._init()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)

    def create_job(
        self,
        admin_id: int,
        inbound_id: int,
        count: int,
        total_gb: int,
        expiry_days: int,
        prefix: str,
        target_chat: Optional[str],
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO jobs(created_at, admin_id, inbound_id, count, total_gb, expiry_days, prefix, target_chat)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (int(time.time()), admin_id, inbound_id, count, total_gb, expiry_days, prefix, target_chat),
            )
            return int(cur.lastrowid)

    def update_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [job_id]
        with self._conn() as c:
            c.execute(f"UPDATE jobs SET {cols} WHERE id=?", values)

    def record_client(
        self,
        job_id: Optional[int],
        inbound_id: int,
        email: str,
        client_uuid: str,
        link: str,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO created_clients(job_id, inbound_id, email, client_uuid, created_at, link)"
                " VALUES(?,?,?,?,?,?)",
                (job_id, inbound_id, email, client_uuid, int(time.time()), link),
            )

    def recent_jobs(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
