"""Central quota calculation for API services."""

from __future__ import annotations

from typing import Iterable, Protocol, Tuple


class AccountLike(Protocol):
    is_active: bool
    status: str
    total_used_bytes: int
    usage_baseline_bytes: int
    final_used_bytes: int | None


def consumed_from_account(acc: AccountLike) -> int:
    if acc.status == "migrated" or (not acc.is_active and acc.final_used_bytes is not None):
        return int(acc.final_used_bytes or 0)
    raw = int(acc.total_used_bytes or 0) - int(acc.usage_baseline_bytes or 0)
    return max(0, raw)


def total_service_used_bytes(accounts: Iterable[AccountLike]) -> int:
    return sum(consumed_from_account(a) for a in accounts)


def remaining_bytes(total_quota: int, used: int) -> int:
    return max(0, int(total_quota) - int(used))
