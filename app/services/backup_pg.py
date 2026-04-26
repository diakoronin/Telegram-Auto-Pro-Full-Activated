"""PostgreSQL logical backup via pg_dump (sync subprocess, called from async via to_thread)."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)


def _database_url_to_pg_conninfo(url: str) -> tuple[str, dict[str, str]]:
    """
    Convert SQLAlchemy async URL to libpq connection target + env (PGPASSWORD).
    Returns (dbname_or_conninfo, env_extra).
    """
    u = url.strip()
    u = re.sub(r"^postgresql\+asyncpg", "postgresql", u, flags=re.I)
    parsed = urlparse(u)
    if parsed.scheme not in ("postgresql", "postgres"):
        raise ValueError("not a postgresql URL")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5432
    user = parsed.username or ""
    password = parsed.password or ""
    db = (parsed.path or "/").lstrip("/")
    if not db:
        raise ValueError("missing database name in URL")
    env = {}
    if password:
        env["PGPASSWORD"] = password
    dsn = f"host={host} port={port} user={user} dbname={db}"
    return dsn, env


def run_pg_dump_bytes(database_url: str) -> tuple[bytes | None, str | None]:
    """Run pg_dump -Fc (custom format) for smaller size; returns (data, error_fa)."""
    try:
        dsn, extra_env = _database_url_to_pg_conninfo(database_url)
    except Exception as e:
        return None, f"آدرس دیتابیس نامعتبر برای pg_dump: {e}"

    env = os.environ.copy()
    env.update(extra_env)
    fd, path = tempfile.mkstemp(prefix="pgdump_", suffix=".dump")
    os.close(fd)
    try:
        cmd = ["pg_dump", "-Fc", "-f", path, dsn]
        r = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            timeout=3600,
            text=False,
        )
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", errors="replace")[:2000]
            log.error("pg_dump failed rc=%s err=%s", r.returncode, err)
            return None, f"pg_dump ناموفق: {err[:500]}"
        data = Path(path).read_bytes()
        return data, None
    except FileNotFoundError:
        return None, "pg_dump روی سرور نصب نیست. با apt install postgresql-client نصب کنید."
    except Exception as e:
        log.exception("pg_dump")
        return None, str(e)
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass
