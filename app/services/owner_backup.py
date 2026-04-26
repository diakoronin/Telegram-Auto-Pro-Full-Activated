"""Compressed DB backup: local retention + optional send to owner Telegram chat."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.types import BufferedInputFile

from app.config import Settings
from app.db.base import get_session_factory
from app.services.backup import export_full_backup_bytes
from app.services.backup_pg import run_pg_dump_bytes
from app.services.app_settings import get_setting, set_setting

log = logging.getLogger(__name__)

LAST_DAILY_KEY = "backup_last_daily_date"
TG_DOC_LIMIT = 45 * 1024 * 1024


def _prune_prefix_dir(directory: Path, prefix: str, keep: int) -> None:
    files = sorted(
        [p for p in directory.glob(f"{prefix}*.zip") if p.is_file()],
        key=lambda p: p.name,
        reverse=True,
    )
    for p in files[keep:]:
        try:
            p.unlink(missing_ok=True)
            log.info("backup_retention: removed %s", p)
        except OSError as e:
            log.warning("backup_retention: could not remove %s: %s", p, e)


async def send_backup_to_owner(
    bot: Bot,
    settings: Settings,
    *,
    session_factory: Any | None = None,
    trigger: str = "auto",
) -> tuple[bool, str]:
    """
    Creates backup zip under backups/, enforces retention, sends to OWNER_ID if size allows.
    trigger: 'auto' | 'manual'
    """
    backup_root = Path(os.getenv("BACKUP_DIR", "backups")).resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(backup_root, 0o700)
    except OSError:
        pass

    ts = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    inner_name: str
    raw: bytes | None = None
    err: str | None = None

    if re.search(r"postgresql(\+asyncpg)?://", settings.database_url, re.I):
        raw, err = await asyncio.to_thread(run_pg_dump_bytes, settings.database_url)
        inner_name = f"db_{ts}.dump"
    else:
        raw, inner_name, err = await export_full_backup_bytes(settings.database_url)

    if err or not raw:
        msg = err or "empty backup"
        log.error("owner_backup: build failed %s", msg)
        fac = session_factory or get_session_factory(settings.database_url)
        async with fac() as session:
            from app.services.audit import write_audit

            await write_audit(
                session,
                actor_telegram_id=None,
                actor_role="system",
                action="backup_failed",
                metadata={"error": msg[:500], "trigger": trigger},
            )
            await session.commit()
        try:
            await bot.send_message(
                settings.owner_telegram_id,
                f"❌ بکاپ ناموفق ({trigger})\n{msg[:3500]}",
            )
        except Exception:
            log.exception("owner_backup: notify fail message")
        return False, msg

    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(inner_name, raw)
        if settings.send_env_backup:
            env_path = Path(".env")
            if env_path.is_file():
                try:
                    txt = env_path.read_text(encoding="utf-8", errors="replace")
                    lines = []
                    for line in txt.splitlines():
                        if re.match(
                            r"^\s*(BOT_TOKEN|OWNER_ID|PANEL_CREDENTIAL|DATABASE_URL|PASSWORD)\s*=",
                            line,
                            re.I,
                        ):
                            k = line.split("=", 1)[0]
                            lines.append(f"{k}=***")
                        else:
                            lines.append(line)
                    zf.writestr(f"env_masked_{ts}.txt", "\n".join(lines).encode("utf-8"))
                except OSError:
                    pass
    zbuf.seek(0)
    zdata = zbuf.getvalue()
    zname = f"hourly_{ts}.zip"
    local_path = backup_root / zname
    local_path.write_bytes(zdata)
    log.info("owner_backup: wrote local %s size=%s", local_path, len(zdata))

    # One daily copy per calendar day (Asia/Tehran default)
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(settings.timezone)
    day_key = datetime.now(tz=tz).date().isoformat()
    fac2 = session_factory or get_session_factory(settings.database_url)
    async with fac2() as s_daily:
        last_d = await get_setting(s_daily, LAST_DAILY_KEY, "")
        if last_d != day_key:
            daily_path = backup_root / f"daily_{ts}.zip"
            daily_path.write_bytes(zdata)
            await set_setting(s_daily, LAST_DAILY_KEY, day_key)
            log.info("owner_backup: daily snapshot %s", daily_path)
        await s_daily.commit()

    _prune_prefix_dir(backup_root, "hourly_", settings.backup_retention_hourly)
    _prune_prefix_dir(backup_root, "daily_", settings.backup_retention_daily)

    jd = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    caption = f"💾 بکاپ ({trigger})\n📅 {jd}\n📦 {zname}\n📁 {local_path}"

    ok_send = False
    if len(zdata) <= TG_DOC_LIMIT:
        try:
            await bot.send_document(
                settings.owner_telegram_id,
                document=BufferedInputFile(zdata, filename=zname),
                caption=caption[:1024],
            )
            ok_send = True
            log.info("owner_backup: sent to owner size=%s", len(zdata))
        except Exception as e:
            log.exception("owner_backup: send failed %s", e)
            try:
                await bot.send_message(
                    settings.owner_telegram_id,
                    f"⚠️ ارسال فایل بکاپ به تلگرام ناموفق بود.\n"
                    f"فایل محلی ذخیره شد:\n<code>{local_path}</code>\n"
                    f"حجم: {len(zdata) // (1024*1024)} مگابایت\n{e!s}"[:4000],
                )
            except Exception:
                log.exception("owner_backup: second notify failed")
    else:
        try:
            await bot.send_message(
                settings.owner_telegram_id,
                f"⚠️ حجم بکاپ برای ارسال در تلگرام زیاد است.\n"
                f"فایل محلی:\n<code>{local_path}</code>\n"
                f"حجم: {len(zdata) // (1024*1024)} مگابایت",
            )
        except Exception:
            log.exception("owner_backup: large file notify failed")

    async with fac2() as s_audit:
        from app.services.audit import write_audit

        await write_audit(
            s_audit,
            actor_telegram_id=None,
            actor_role="system",
            action="backup_completed",
            metadata={
                "trigger": trigger,
                "path": str(local_path),
                "bytes": len(zdata),
                "telegram_sent": ok_send,
            },
        )
        await s_audit.commit()

    return True, ""
