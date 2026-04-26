"""Local backup retention helpers (hourly/daily naming convention)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple


def classify_backup_files(
    backup_dir: Path,
    *,
    retention_hourly: int,
    retention_daily: int,
) -> Tuple[List[Path], List[Path]]:
    """Return (to_delete, to_keep) based on hourly_*.sql.gz and daily_*.sql.gz patterns."""
    if not backup_dir.exists():
        return [], []
    hourly = sorted(backup_dir.glob("hourly_*.sql.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    daily = sorted(backup_dir.glob("daily_*.sql.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    keep = set(hourly[:retention_hourly] + daily[:retention_daily])
    all_files = list(backup_dir.glob("*.sql.gz"))
    to_delete = [p for p in all_files if p not in keep]
    return to_delete, list(keep)
