"""Local backup retention pruning."""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.services.owner_backup import _prune_prefix_dir


def test_prune_keeps_newest_hourly_files() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        for i in range(5):
            (p / f"hourly_old{i}.zip").write_text("x")
        names = sorted(p.glob("hourly_*.zip"), key=lambda x: x.name)
        for f in names:
            f.unlink()
        for i in range(3):
            (p / f"hourly_{i:03d}.zip").write_text("data")
        _prune_prefix_dir(p, "hourly_", keep=2)
        remaining = sorted(p.glob("hourly_*.zip"))
        assert len(remaining) == 2
