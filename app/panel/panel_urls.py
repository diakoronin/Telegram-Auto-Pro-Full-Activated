from __future__ import annotations

from urllib.parse import urljoin

from app.db.models import Panel


def panel_root_url(panel: Panel) -> str:
    base = (panel.base_url or "").strip().rstrip("/")
    wbp = (panel.web_base_path or "").strip()
    if wbp and not wbp.startswith("/"):
        wbp = "/" + wbp
    if wbp:
        return base + wbp.rstrip("/")
    return base


def marzban_api_url(panel: Panel, path: str) -> str:
    """Marzban mounts API at /api/... (see app/routers in Marzban source)."""
    root = panel_root_url(panel).rstrip("/")
    p = path if path.startswith("/") else "/" + path
    return f"{root}/api{p}"


def xui_api_url(panel: Panel, path: str) -> str:
    p = path if path.startswith("/") else "/" + path
    # 3x-ui: /panel/api/inbounds/...
    root = panel_root_url(panel) + "/"
    return urljoin(root, "panel/api/inbounds" + p)
