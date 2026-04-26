from types import SimpleNamespace

from bot_app.services.quota import consumed_from_account, remaining_bytes, total_service_used_bytes


def test_migrated_account_counts_final():
    old = SimpleNamespace(
        is_active=False,
        status="migrated",
        total_used_bytes=100,
        usage_baseline_bytes=0,
        final_used_bytes=80,
    )
    assert consumed_from_account(old) == 80


def test_active_account_baseline():
    acc = SimpleNamespace(
        is_active=True,
        status="active",
        total_used_bytes=500,
        usage_baseline_bytes=100,
        final_used_bytes=None,
    )
    assert consumed_from_account(acc) == 400


def test_total_and_remaining():
    accounts = [
        SimpleNamespace(
            is_active=False,
            status="migrated",
            total_used_bytes=0,
            usage_baseline_bytes=0,
            final_used_bytes=10**9,
        ),
        SimpleNamespace(
            is_active=True,
            status="active",
            total_used_bytes=2 * 10**9,
            usage_baseline_bytes=10**9,
            final_used_bytes=None,
        ),
    ]
    used = total_service_used_bytes(accounts)
    assert used == 10**9 + 10**9
    assert remaining_bytes(30 * 10**9, used) == 28 * 10**9
