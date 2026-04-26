#!/usr/bin/env python3
"""Write compressed backup to backups/ (used by bot-manager)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import io
import re
import zipfile
from datetime import UTC, datetime

from app.config import load_settings
from app.services.backup import export_full_backup_bytes
from app.services.backup_pg import run_pg_dump_bytes


async def main() -> None:
    settings = load_settings()
    root = Path(__file__).resolve().parents[1]
    backup_dir = root / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    if re.search(r"postgresql(\+asyncpg)?://", settings.database_url, re.I):
        raw, err = await asyncio.to_thread(run_pg_dump_bytes, settings.database_url)
        inner = f"db_{ts}.dump"
    else:
        raw, inner, err = await export_full_backup_bytes(settings.database_url)
    if err or not raw:
        print("ERROR:", err or "empty")
        raise SystemExit(1)
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(inner, raw)
    path = backup_dir / f"manual_{ts}.zip"
    path.write_bytes(zbuf.getvalue())
    print("OK", path)


if __name__ == "__main__":
    asyncio.run(main())
