"""Unit tests for central quota consumption helper (no DB)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import PanelAccount, PanelAccountStatus
from app.services.quota import consumed_from_account


def _pa(**kwargs) -> PanelAccount:
    defaults = dict(
        id=1,
        user_service_id=1,
        panel_id=1,
        server_id=1,
        panel_type="marzban",
        panel_account_id="u",
        username="u",
        config_links_json=[],
        raw_subscription_url=None,
        quota_bytes_assigned=30 * 1024**3,
        usage_baseline_bytes=0,
        upload_bytes=0,
        download_bytes=0,
        total_used_bytes=0,
        final_used_bytes=None,
        last_synced_at=None,
        activated_at=None,
        disabled_at=None,
        is_active=True,
        status=PanelAccountStatus.ACTIVE,
    )
    defaults.update(kwargs)
    return PanelAccount(**defaults)


def test_active_account_delta() -> None:
    pa = _pa(usage_baseline_bytes=100, total_used_bytes=100 + 12 * 1024**3)
    assert consumed_from_account(pa) == 12 * 1024**3


def test_inactive_uses_final() -> None:
    pa = _pa(
        is_active=False,
        status=PanelAccountStatus.MIGRATED,
        final_used_bytes=5 * 1024**3,
        total_used_bytes=99,
    )
    assert consumed_from_account(pa) == 5 * 1024**3


if __name__ == "__main__":
    test_active_account_delta()
    test_inactive_uses_final()
    print("quota tests OK")
