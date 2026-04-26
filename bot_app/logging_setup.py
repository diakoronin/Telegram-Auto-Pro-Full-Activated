"""Rotating file logging."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bot_app.config import Settings


class RequestIdFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return super().format(record)


def setup_file_logging(settings: Settings) -> None:
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    root.handlers.clear()

    fmt = RequestIdFormatter(
        "%(asctime)s [%(request_id)s] %(levelname)s %(name)s: %(message)s",
    )

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.setLevel(logging.DEBUG)
    root.addHandler(sh)

    if not settings.log_to_file:
        return

    bot_fh = RotatingFileHandler(
        log_dir / "bot.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    bot_fh.setFormatter(fmt)
    bot_fh.setLevel(logging.INFO)
    root.addHandler(bot_fh)

    err_fh = RotatingFileHandler(
        log_dir / "errors.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    err_fh.setFormatter(fmt)
    err_fh.setLevel(logging.ERROR)
    root.addHandler(err_fh)

    panel = logging.getLogger("bot_app.providers")
    panel.setLevel(logging.DEBUG)
    panel_fh = RotatingFileHandler(
        log_dir / "panel_api.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    panel_fh.setFormatter(fmt)
    panel.addHandler(panel_fh)
    panel.propagate = True


def log_adapter(logger: logging.Logger, request_id: str) -> logging.LoggerAdapter:
    return logging.LoggerAdapter(logger, {"request_id": request_id})
