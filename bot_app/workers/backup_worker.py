"""Hourly database backup (PostgreSQL pg_dump) with local retention."""

from __future__ import annotations

import asyncio
import gzip
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from bot_app.services.backup_service import classify_backup_files

logger = logging.getLogger(__name__)


def _is_postgres(url: str) -> bool:
    return "postgres" in (url or "").lower()


async def run_pg_backup(database_url: str, backup_dir: Path, retention_hourly: int, retention_daily: int) -> Path | None:
    if not _is_postgres(database_url):
        logger.info("[BACKUP] skip non-postgresql database_url")
        return None
    parsed = urlparse(database_url.replace("postgresql+asyncpg://", "postgresql://"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_sql = backup_dir / f"hourly_{ts}.sql"
    env = {"PGPASSWORD": parsed.password or ""}
    cmd = [
        "pg_dump",
        "-h",
        parsed.hostname or "localhost",
        "-p",
        str(parsed.port or 5432),
        "-U",
        parsed.username or "postgres",
        "-d",
        (parsed.path or "/").lstrip("/"),
        "-f",
        str(out_sql),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env={**os.environ, **{k: v for k, v in env.items() if v}},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error("[BACKUP] pg_dump failed: %s", stderr.decode(errors="replace")[:500])
        return None
    gz_path = Path(str(out_sql) + ".gz")
    with open(out_sql, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    out_sql.unlink(missing_ok=True)
    to_delete, _ = classify_backup_files(backup_dir, retention_hourly=retention_hourly, retention_daily=retention_daily)
    for p in to_delete:
        try:
            p.unlink()
        except OSError:
            pass
    logger.info("[BACKUP] created %s", gz_path.name)
    return gz_path


async def backup_loop(settings, send_to_owner=None) -> None:
    """send_to_owner: async callable(path: Path) -> None"""
    interval = max(1, int(settings.auto_backup_interval_minutes)) * 60
    backup_root = Path("backups")
    backup_root.mkdir(parents=True, exist_ok=True)
    try:
        backup_root.chmod(0o700)
    except OSError:
        pass
    while True:
        try:
            if settings.auto_backup_enabled:
                path = await run_pg_backup(
                    settings.database_url,
                    backup_root,
                    settings.backup_retention_hourly,
                    settings.backup_retention_daily,
                )
                if path and send_to_owner:
                    await send_to_owner(path)
        except Exception:
            logger.exception("[BACKUP] loop error")
        await asyncio.sleep(interval)
