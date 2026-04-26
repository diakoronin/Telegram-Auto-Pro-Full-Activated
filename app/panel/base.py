from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

from app.db.models import Panel, Plan, Server, UserService
from app.panel.types import (
    PanelActionResult,
    PanelCreateResult,
    PanelLogContext,
    PanelTestResult,
    PanelUsageResult,
)

if TYPE_CHECKING:
    from app.config import Settings


class PanelProvider(ABC):
    """Abstract panel integration; one implementation per panel type."""

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings

    @abstractmethod
    async def test_connection(
        self, panel: Panel, *, ctx: PanelLogContext | None = None
    ) -> PanelTestResult:
        ...

    @abstractmethod
    async def create_account(
        self,
        *,
        user_service: UserService,
        panel: Panel,
        server: Server,
        plan: Plan,
        quota_bytes: int,
        expire_at: datetime | None,
        backend_username: str,
        ctx: PanelLogContext | None = None,
    ) -> PanelCreateResult:
        ...

    @abstractmethod
    async def get_account_usage(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        ctx: PanelLogContext | None = None,
    ) -> PanelUsageResult:
        ...

    @abstractmethod
    async def disable_account(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        client_id: str | None,
        ctx: PanelLogContext | None = None,
    ) -> PanelActionResult:
        ...

    @abstractmethod
    async def enable_account(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        client_id: str | None,
        ctx: PanelLogContext | None = None,
    ) -> PanelActionResult:
        ...

    @abstractmethod
    async def delete_account(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        client_id: str | None,
        ctx: PanelLogContext | None = None,
    ) -> PanelActionResult:
        ...

    @abstractmethod
    async def update_quota(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        client_id: str | None,
        quota_bytes: int,
        ctx: PanelLogContext | None = None,
    ) -> PanelActionResult:
        ...

    @abstractmethod
    async def update_expire(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        client_id: str | None,
        expire_at: datetime | None,
        ctx: PanelLogContext | None = None,
    ) -> PanelActionResult:
        ...

    @abstractmethod
    async def get_config_links(
        self,
        *,
        panel: Panel,
        panel_username: str,
        inbound_id: int | None,
        client_id: str | None,
        ctx: PanelLogContext | None = None,
    ) -> tuple[list[str], str | None]:
        """Return (v2ray link lines, raw_subscription_url or None)."""
        ...
