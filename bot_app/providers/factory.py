"""Build panel provider instance from DB panel row."""

from __future__ import annotations

from typing import Any, Protocol, Union

from bot_app.providers.marzban import MarzbanProvider
from bot_app.providers.sanaei_3xui import Sanaei3xuiProvider


class PanelRow(Protocol):
    id: int
    type: str
    base_url: str
    web_base_path: str | None
    username: str
    password_encrypted: str
    api_token_encrypted: str | None
    verify_ssl: bool
    timeout_seconds: int
    inbound_id: int | None


def get_provider_for_panel(
    panel: PanelRow,
    *,
    request_id: str,
    encryption_key: str,
) -> Union[MarzbanProvider, Sanaei3xuiProvider]:
    t = (panel.type or "").lower()
    if t in ("marzban",):
        return MarzbanProvider(
            request_id=request_id,
            base_url=panel.base_url,
            username=panel.username,
            password_encrypted=panel.password_encrypted,
            api_token_encrypted=panel.api_token_encrypted,
            encryption_key=encryption_key,
            verify_ssl=bool(panel.verify_ssl),
            timeout_seconds=int(panel.timeout_seconds or 30),
        )
    if t in ("sanaei_3xui", "xui", "3x-ui", "3xui"):
        return Sanaei3xuiProvider(
            request_id=request_id,
            base_url=panel.base_url,
            web_base_path=panel.web_base_path,
            username=panel.username,
            password_encrypted=panel.password_encrypted,
            encryption_key=encryption_key,
            default_inbound_id=panel.inbound_id,
            verify_ssl=bool(panel.verify_ssl),
            timeout_seconds=int(panel.timeout_seconds or 30),
        )
    raise ValueError(f"unknown_panel_type:{panel.type}")
