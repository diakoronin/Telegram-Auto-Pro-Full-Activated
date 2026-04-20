"""Shared formatting and helper utilities."""
from __future__ import annotations

import re
import time
from typing import Iterable, List
from urllib.parse import urlparse


def human_bytes(n: int) -> str:
    if n <= 0:
        return "∞ (نامحدود)"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


def days_from_now_ms(days: int) -> int:
    """Return an absolute expiry timestamp (ms) `days` from now, or 0 for unlimited."""
    if days <= 0:
        return 0
    return int((time.time() + days * 86400) * 1000)


def format_expiry(expiry_ms: int) -> str:
    if not expiry_ms:
        return "∞ (نامحدود)"
    remaining = expiry_ms / 1000 - time.time()
    if remaining <= 0:
        return "منقضی شده"
    days = int(remaining // 86400)
    return f"{days} روز مانده"


_SAFE_EMAIL = re.compile(r"[^a-zA-Z0-9_\-.]+")


def sanitize_prefix(prefix: str, fallback: str = "user") -> str:
    cleaned = _SAFE_EMAIL.sub("", prefix or "").strip("-_.") or fallback
    return cleaned[:32]


def server_host_from_base(base_url: str) -> str:
    try:
        return urlparse(base_url).hostname or ""
    except Exception:
        return ""


def chunk(iterable: Iterable, size: int) -> List[list]:
    out: List[list] = []
    current: list = []
    for item in iterable:
        current.append(item)
        if len(current) >= size:
            out.append(current)
            current = []
    if current:
        out.append(current)
    return out
