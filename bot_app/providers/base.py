"""Panel provider interface and normalized types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol


class PanelErrorCode(str, Enum):
    invalid_credentials = "invalid_credentials"
    connection_timeout = "connection_timeout"
    connection_refused = "connection_refused"
    ssl_error = "ssl_error"
    invalid_inbound = "invalid_inbound"
    quota_error = "quota_error"
    user_already_exists = "user_already_exists"
    user_not_found = "user_not_found"
    panel_unavailable = "panel_unavailable"
    unknown_provider_error = "unknown_provider_error"


@dataclass
class ProviderResult:
    ok: bool
    data: Optional[Dict[str, Any]] = None
    error_code: Optional[PanelErrorCode] = None
    error_message: Optional[str] = None
    raw_status: Optional[int] = None


@dataclass
class AccountUsage:
    upload_bytes: int = 0
    download_bytes: int = 0
    total_used_bytes: int = 0


class PanelProvider(Protocol):
    request_id: str

    async def test_connection(self) -> ProviderResult: ...

    async def create_account(
        self,
        username: str,
        quota_bytes: int,
        expire_timestamp_ms: int,
        inbound_id: Optional[int] = None,
        email: Optional[str] = None,
    ) -> ProviderResult: ...

    async def get_account_usage(self, username: str) -> ProviderResult: ...

    async def disable_account(self, username: str) -> ProviderResult: ...

    async def enable_account(self, username: str) -> ProviderResult: ...

    async def delete_account(self, username: str) -> ProviderResult: ...

    async def get_config_links(self, username: str) -> ProviderResult: ...

    async def update_quota(self, username: str, quota_bytes: int) -> ProviderResult: ...

    async def update_expire(self, username: str, expire_timestamp_ms: int) -> ProviderResult: ...
