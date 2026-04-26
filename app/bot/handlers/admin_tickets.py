"""Admin: list and reply to support tickets."""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts_fa as T
from app.bot.filters import IsManagerOrOwner
from app.bot.states import AdminStates
from app.config import Settings
from app.db.models import Admin, AdminRole, SupportTicket, SupportTicketStatus, User, UserService
from app.message_format import format_message

logger = logging.getLogger(__name__)

router = Router(name="admin_tickets")
router.callback_query.filter(IsManagerOrOwner())
router.message.filter(IsManagerOrOwner())


def _afmt(settings: Settings, text: str) -> str:
    return format_message(settings, text)


@router.callback_query(F.data == "adm:tickets")
async def cb_tickets_list(
    callback: CallbackQuery, session: AsyncSession, settings: Settings, **kwargs
) -> None:
    r = await session.execute(
        select(SupportTicket, User)
        .join(User, User.id == SupportTicket.user_id)
        .where(SupportTicket.status == SupportTicketStatus.OPEN)
        .order_by(SupportTicket.id.desc())
        .limit(15)
    )
    rows = r.all()
    if not rows:
        await callback.answer("تیکت بازی نیست.", show_alert=True)
        return
    lines = []
    kb = []
    for t, u in rows:
        lines.append(f"#{t.id} user={u.telegram_id} svc={t.user_service_id or '—'}")
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"پاسخ #{t.id}",
                    callback_data=f"adm:tick_reply:{t.id}",
                )
            ]
        )
    kb.append([InlineKeyboardButton(text=T.ADM_BACK, callback_data="admin_home")])
    await callback.message.edit_text(
        _afmt(settings, "🎫 تیکت‌های باز:\n\n" + "\n".join(lines)),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:tick_reply:"))
async def cb_tick_reply_start(
    callback: CallbackQuery, state: FSMContext, settings: Settings, **kwargs
) -> None:
    tid = int(callback.data.split(":")[2])
    await state.set_state(AdminStates.ticket_reply_text)
    await state.update_data(ticket_reply_id=tid)
    await callback.message.answer(
        _afmt(settings, f"پاسخ تیکت #{tid} را بنویسید:"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]]
        ),
    )
    await callback.answer()


@router.message(AdminStates.ticket_reply_text, F.text)
async def msg_ticket_reply(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    admin: Admin,
    **kwargs,
) -> None:
    data = await state.get_data()
    tid = int(data.get("ticket_reply_id") or 0)
    t = await session.get(SupportTicket, tid)
    if t is None:
        await state.clear()
        await message.answer("تیکت یافت نشد.")
        return
    body = (message.text or "").strip()
    if not body:
        return
    t.admin_reply = body
    t.status = SupportTicketStatus.ANSWERED
    u = await session.get(User, t.user_id)
    await session.flush()
    await state.clear()
    if u:
        try:
            await message.bot.send_message(
                int(u.telegram_id),
                format_message(
                    settings,
                    f"📩 پاسخ پشتیبانی به تیکت #{tid}:\n\n{html.escape(body)}",
                ),
            )
        except Exception:
            logger.exception("send ticket reply to user failed")
    await message.answer(_afmt(settings, "✅ پاسخ ثبت شد و برای کاربر ارسال شد."))
