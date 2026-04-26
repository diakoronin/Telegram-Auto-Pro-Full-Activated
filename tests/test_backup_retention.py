from pathlib import Path

from bot_app.services.backup_service import classify_backup_files


def test_retention_keeps_newest(tmp_path: Path):
    (tmp_path / "hourly_old.sql.gz").write_text("a")
    (tmp_path / "hourly_new.sql.gz").write_text("b")
    to_delete, keep = classify_backup_files(tmp_path, retention_hourly=1, retention_daily=0)
    assert len(keep) >= 1
    assert all(p.name.startswith("hourly_") for p in keep if "hourly" in p.name)
