from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.db import models


def _sqlite_path_from_url(database_url: str) -> Path | None:
    m = re.match(r"sqlite\+aiosqlite:///(.+)", database_url.strip())
    if not m:
        return None
    return Path(m.group(1)).expanduser()


async def export_full_backup_bytes(database_url: str) -> tuple[bytes, str, str | None]:
    """
    Returns (content_bytes, filename, error_fa).
    SQLite: raw DB file copy.
    Other: JSON export of all mapped tables (may be large).
    """
    ts = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    sp = _sqlite_path_from_url(database_url)
    if sp is not None and sp.exists():
        data = sp.read_bytes()
        return data, f"backup_{ts}.sqlite3", None

    engine = create_async_engine(database_url, echo=False)
    try:
        async with engine.connect() as conn:
            def table_names(sync_conn):
                return inspect(sync_conn).sorted_table_names

            names = await conn.run_sync(table_names)
        export: dict[str, list[dict[str, object]]] = {}
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            meta = models.Base.metadata
            for name in names:
                table = meta.tables.get(name)
                if table is None:
                    continue
                rows = []
                res = await session.execute(select(table))
                for row in res.mappings().all():
                    rowd = dict(row)
                    for k, v in list(rowd.items()):
                        if hasattr(v, "isoformat"):
                            rowd[k] = v.isoformat()
                        elif isinstance(v, bytes):
                            rowd[k] = v.hex()
                    rows.append(rowd)
                export[name] = rows
        raw = json.dumps(export, ensure_ascii=False).encode("utf-8")
        return raw, f"backup_{ts}.json", None
    finally:
        await engine.dispose()


def write_temp_backup_file(content: bytes, suffix: str) -> str:
    fd, path = tempfile.mkstemp(prefix="tg_sales_backup_", suffix=suffix)
    os.write(fd, content)
    os.close(fd)
    return path
