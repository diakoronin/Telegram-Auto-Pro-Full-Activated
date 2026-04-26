from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.panel.errors import PanelErrorCode


@dataclass
class PanelTestResult:
    ok: bool
    duration_ms: int = 0
    error_code: PanelErrorCode | None = None
    message: str | None = None


@dataclass
class PanelCreateResult:
    ok: bool
    panel_account_id: str | None = None  # 3x-ui: vless/trojan client id; Marzban: username
    username: str | None = None
    config_links: list[str] = field(default_factory=list)
    raw_subscription_url: str | None = None
    error_code: PanelErrorCode | None = None
    message: str | None = None


@dataclass
class PanelUsageResult:
    ok: bool
    upload_bytes: int = 0
    download_bytes: int = 0
    total_used_bytes: int = 0
    error_code: PanelErrorCode | None = None
    message: str | None = None


@dataclass
class PanelActionResult:
    ok: bool
    error_code: PanelErrorCode | None = None
    message: str | None = None


@dataclass
class PanelLogContext:
    request_id: str
    service_code: str | None = None
    user_id: str | None = None
