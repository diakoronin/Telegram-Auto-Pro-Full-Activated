"""Send compressed DB backup to owner Telegram chat."""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.types import BufferedInputFile

from app.config import Settings
from app.services.backup import export_full_backup_bytes

log = logging.getLogger(__name__)


async def send_backup_to_owner(bot: Bot, settings: Settings) -> tuple[bool, str]:
    data, fname, err = await export_full_backup_bytes(settings.database_url)
    if err:
        return False, err
    if not data:
        return False, "empty backup"
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(fname, data)
    zbuf.seek(0)
    zname = fname.rsplit(".", 1)[0] + ".zip"
    jd = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    caption = f"💾 بکاپ خودکار\n📅 {jd}\n📦 {zname}"
    try:
        await bot.send_document(
            settings.owner_telegram_id,
            document=BufferedInputFile(zbuf.getvalue(), filename=zname),
            caption=caption[:1024],
        )
        log.info("owner_backup: sent to owner size=%s", len(zbuf.getvalue()))
        return True, ""
    except Exception as e:
        log.exception("owner_backup: send failed %s", e)
        return False, str(e)
