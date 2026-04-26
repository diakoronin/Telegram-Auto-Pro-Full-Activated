"""File logging setup for bot, panel API, and errors."""

from __future__ import annotations

import logging
import os
from pathlib import Path


def setup_file_logging(
    *,
    log_dir: str,
    log_level: str,
    log_to_file: bool,
) -> None:
    root = logging.getLogger()
    level = getattr(logging, (log_level or "INFO").upper(), logging.INFO)
    root.setLevel(level)

    if not log_to_file:
        return

    p = Path(log_dir)
    p.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s "
        "rid=%(request_id)s svc=%(service_code)s uid=%(user_id)s "
        "%(message)s",
        defaults={
            "request_id": "-",
            "service_code": "-",
            "user_id": "-",
        },
    )

    def add(path: str, name: str) -> None:
        h = logging.FileHandler(path, encoding="utf-8")
        h.setLevel(level)
        h.setFormatter(fmt)
        lg = logging.getLogger(name)
        lg.addHandler(h)
        lg.propagate = True

    add(str(p / "bot.log"), "app")
    add(str(p / "panel_api.log"), "app.panel")
    add(str(p / "errors.log"), "app.errors")


class LogContext(logging.Filter):
    """Filter that injects default context keys for Formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = getattr(record, "request_id", "-")
        if not hasattr(record, "service_code"):
            record.service_code = "-"
        if not hasattr(record, "user_id"):
            record.user_id = "-"
        return True


def install_log_context_filter() -> None:
    f = LogContext()
    logging.getLogger().addFilter(f)
