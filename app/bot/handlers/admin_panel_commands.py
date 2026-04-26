"""Owner-only quick commands for panel setup (no multi-step FSM)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts_fa as T
from app.bot.filters import IsOwner
from app.config import Settings
from app.crypto_store import encrypt_secret
from app.db.models import Panel, PanelType, Server
from app.message_format import format_message
from app.panel.factory import get_provider
from app.panel.types import PanelLogContext
from app.services.audit import write_audit
from app.structured_log import new_request_id

logger = logging.getLogger(__name__)

router = Router(name="admin_panel_commands")
router.message.filter(IsOwner())


def _afmt(settings: Settings, text: str) -> str:
    return format_message(settings, text)


@router.message(Command("paneladd"))
async def cmd_paneladd(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    admin,
    **kwargs,
) -> None:
    """
    /paneladd <marzban|3xui> <name> <base_url> <username> <password>
    Example: /paneladd marzban Main https://sub.example.com admin mysecret
    """
    parts = (message.text or "").split(maxsplit=5)
    if len(parts) < 6:
        await message.answer(
            _afmt(
                settings,
                "فرمت:\n"
                "/paneladd marzban نام_پنل https://url.com یوزر پسورد\n"
                "یا:\n"
                "/paneladd 3xui نام_پنل https://url.com یوزر پسورد",
            )
        )
        return
    _, kind, name, base_url, username, password = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
    k = kind.strip().lower()
    if k in ("marzban", "m"):
        ptype = PanelType.MARZBAN
    elif k in ("3xui", "3x", "xui", "sanaei"):
        ptype = PanelType.SANAEI_3XUI
    else:
        await message.answer(_afmt(settings, "نوع پنل باید marzban یا 3xui باشد."))
        return
    enc_pw = encrypt_secret(password, encryption_key=settings.panel_credential_encryption_key) or password
    p = Panel(
        name=name.strip()[:120],
        type=ptype,
        base_url=base_url.strip().rstrip("/"),
        web_base_path=None,
        username=username.strip(),
        password_encrypted=enc_pw,
        api_token_encrypted=None,
    )
    session.add(p)
    await session.flush()
    await write_audit(
        session,
        actor_telegram_id=message.from_user.id if message.from_user else None,
        actor_role="owner",
        action="panel_created",
        target_type="panel",
        target_id=str(p.id),
    )
    await message.answer(_afmt(settings, f"✅ پنل #{p.id} ثبت شد.\n/paneltest {p.id}"))


@router.message(Command("paneltest"))
async def cmd_paneltest(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    **kwargs,
) -> None:
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(_afmt(settings, "فرمت: /paneltest شناسه_پنل"))
        return
    pid = int(parts[1])
    panel = await session.get(Panel, pid)
    if panel is None:
        await message.answer(_afmt(settings, "پنل یافت نشد."))
        return
    rid = new_request_id()
    prov = get_provider(panel, settings)
    res = await prov.test_connection(panel, ctx=PanelLogContext(request_id=rid))
    panel.last_test_at = datetime.now(tz=UTC)
    panel.last_test_status = "ok" if res.ok else "fail"
    panel.last_test_error = None if res.ok else (res.message or "")[:2000]
    await session.flush()
    if res.ok:
        await message.answer(_afmt(settings, f"✅ اتصال موفق ({res.duration_ms}ms)\nrid={rid}"))
    else:
        await message.answer(
            _afmt(settings, f"❌ اتصال ناموفق\n{res.message}\nrid={rid}")
        )


@router.message(Command("serverbind"))
async def cmd_serverbind(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    **kwargs,
) -> None:
    """Bind server to panel: /serverbind SERVER_ID PANEL_ID [inbound_id]"""
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer(_afmt(settings, "فرمت: /serverbind شناسه_سرور شناسه_پنل [inbound_id]"))
        return
    sid = int(parts[1])
    pid = int(parts[2])
    inbound = int(parts[3]) if len(parts) > 3 else None
    srv = await session.get(Server, sid)
    pan = await session.get(Panel, pid)
    if srv is None or pan is None:
        await message.answer(_afmt(settings, "سرور یا پنل یافت نشد."))
        return
    srv.panel_id = pan.id
    srv.panel_type = pan.type.value
    srv.inbound_id = inbound
    await session.flush()
    await message.answer(_afmt(settings, f"✅ سرور #{sid} به پنل #{pid} متصل شد."))
