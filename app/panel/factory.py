from __future__ import annotations

from app.config import Settings
from app.db.models import Panel, PanelType
from app.panel.base import PanelProvider
from app.panel.marzban_provider import MarzbanProvider
from app.panel.sanaei_3xui_provider import Sanaei3xuiProvider


def get_provider(panel: Panel, settings: Settings) -> PanelProvider:
    if panel.type == PanelType.MARZBAN:
        return MarzbanProvider(settings)
    if panel.type == PanelType.SANAEI_3XUI:
        return Sanaei3xuiProvider(settings)
    if panel.type == PanelType.XUI:
        return Sanaei3xuiProvider(settings)
    raise ValueError(f"Unsupported panel type: {panel.type}")
