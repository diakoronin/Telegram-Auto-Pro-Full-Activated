"""Admin: support tickets — filters, detail, reply, close, pagination."""

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
from app.db.models import (
    Admin,
    PanelAccount,
    Plan,
    Server,
    SupportTicket,
    SupportTicketStatus,
    User,
    UserService,
)
from app.message_format import format_message
from app.services.audit import write_audit
from app.services.plan_display import plan_display_label

logger = logging.getLogger(__name__)

router = Router(name="admin_tickets")
router.callback_query.filter(IsManagerOrOwner())
router.message.filter(IsManagerOrOwner())


def _afmt(settings: Settings, text: str) -> str:
    return format_message(settings, text)


def _tickets_filter_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="باز", callback_data="adm:tickets:open:0"),
                InlineKeyboardButton(text="پاسخ داده", callback_data="adm:tickets:answered:0"),
            ],
            [
                InlineKeyboardButton(text="بسته", callback_data="adm:tickets:closed:0"),
                InlineKeyboardButton(text="همه", callback_data="adm:tickets:all:0"),
            ],
            [InlineKeyboardButton(text=T.ADM_BACK, callback_data="adm:cat_users")],
        ]
    )


def _status_from_filter(flt: str) -> SupportTicketStatus | None:
    if flt == "open":
        return SupportTicketStatus.OPEN
    if flt == "answered":
        return SupportTicketStatus.ANSWERED
    if flt == "closed":
        return SupportTicketStatus.CLOSED
    return None


@router.callback_query(F.data == "adm:tickets")
async def cb_tickets_menu(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    await callback.message.edit_text(
        _afmt(settings, "🎫 تیکت‌های پشتیبانی — فیلتر را انتخاب کنید:"),
        reply_markup=_tickets_filter_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:tickets:"))
async def cb_tickets_list(
    callback: CallbackQuery, session: AsyncSession, settings: Settings, **kwargs
) -> None:
    parts = (callback.data or "").split(":")
    flt = parts[2] if len(parts) > 2 else "open"
    page = int(parts[3]) if len(parts) > 3 else 0
    per = 8
    off = page * per
    st = _status_from_filter(flt)
    q = select(SupportTicket, User).join(User, User.id == SupportTicket.user_id)
    if st is not None:
        q = q.where(SupportTicket.status == st)
    q = q.order_by(SupportTicket.id.desc()).offset(off).limit(per + 1)
    r = await session.execute(q)
    rows = r.all()
    has_more = len(rows) > per
    rows = rows[:per]
    if not rows:
        await callback.answer("تیکتی در این فهرست نیست.", show_alert=True)
        return
    lines = []
    kb = []
    for t, u in rows:
        lines.append(f"#{t.id} {t.status.value} user={u.telegram_id}")
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"#{t.id}",
                    callback_data=f"adm:tick_view:{t.id}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="◀️ قبلی", callback_data=f"adm:tickets:{flt}:{page - 1}")
        )
    if has_more:
        nav.append(
            InlineKeyboardButton(text="بعدی ▶️", callback_data=f"adm:tickets:{flt}:{page + 1}")
        )
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text="🔙 فیلترها", callback_data="adm:tickets")])
    await callback.message.edit_text(
        _afmt(settings, "🎫 فهرست تیکت‌ها:\n\n" + "\n".join(lines)),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:tick_view:"))
async def cb_tick_view(
    callback: CallbackQuery, session: AsyncSession, settings: Settings, **kwargs
) -> None:
    tid = int(callback.data.split(":")[-1])
    t = await session.get(SupportTicket, tid)
    if t is None:
        await callback.answer("تیکت یافت نشد.", show_alert=True)
        return
    u = await session.get(User, t.user_id)
    us = await session.get(UserService, t.user_service_id) if t.user_service_id else None
    pl = await session.get(Plan, us.plan_id) if us else None
    srv = await session.get(Server, us.current_server_id) if us else None
    pa_r = None
    be = "—"
    if us:
        pa_r = await session.execute(
            select(PanelAccount).where(
                PanelAccount.user_service_id == us.id,
                PanelAccount.is_active.is_(True),
            )
        )
        pa = pa_r.scalars().first()
        if pa:
            be = pa.username
    full_name = (u.username and f"@{u.username}") or "—"
    code = us.public_service_code if us else "—"
    srv_n = srv.name if srv else "—"
    plan_n = plan_display_label(pl) if pl else "—"
    body = (
        "🎫 تیکت\n\n"
        f"👤 کاربر: {html.escape(full_name)}\n"
        f"🆔 آیدی عددی: <code>{u.telegram_id}</code>\n"
        f"🔗 یوزرنیم: @{html.escape(u.username or '')}\n\n"
        f"🆔 کد سرویس: <code>{html.escape(code)}</code>\n"
        f"⚙️ نام داخل پنل: <code>{html.escape(be)}</code>\n"
        f"🌐 لوکیشن: {html.escape(srv_n)}\n"
        f"📦 پلن: {html.escape(plan_n)}\n\n"
        f"وضعیت: {t.status.value}\n\n"
        f"📝 پیام:\n{html.escape(t.message or '')}\n"
    )
    if t.admin_reply:
        body += f"\n📩 پاسخ قبلی:\n{html.escape(t.admin_reply)}\n"
    kb = [
        [InlineKeyboardButton(text="✍️ پاسخ", callback_data=f"adm:tick_reply:{t.id}")],
        [InlineKeyboardButton(text="✅ بستن تیکت", callback_data=f"adm:tick_close:{t.id}")],
        [
            InlineKeyboardButton(
                text="👤 مشاهده کاربر",
                callback_data=f"adm:tick_user:{u.telegram_id}",
            )
        ],
    ]
    if us:
        kb.append(
            [InlineKeyboardButton(text="📦 مشاهده سرویس", callback_data=f"adm:tick_svc:{us.id}")]
        )
    kb.append([InlineKeyboardButton(text="🔙 فهرست", callback_data="adm:tickets:open:0")])
    await callback.message.edit_text(
        _afmt(settings, body),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:tick_user:"))
async def cb_tick_user_stub(callback: CallbackQuery, settings: Settings) -> None:
    tid = callback.data.split(":")[-1]
    await callback.answer(_afmt(settings, f"آیدی کاربر: {tid}"), show_alert=True)


@router.callback_query(F.data.startswith("adm:tick_svc:"))
async def cb_tick_svc_stub(callback: CallbackQuery, settings: Settings) -> None:
    sid = callback.data.split(":")[-1]
    await callback.answer(_afmt(settings, f"شناسه سرویس در دیتابیس: {sid}"), show_alert=True)


@router.callback_query(F.data.startswith("adm:tick_reply:"))
async def cb_tick_reply_start(
    callback: CallbackQuery, state: FSMContext, settings: Settings, **kwargs
) -> None:
    tid = int(callback.data.split(":")[-1])
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
    await write_audit(
        session,
        actor_telegram_id=message.from_user.id if message.from_user else None,
        actor_role=admin.role.value,
        action="support_ticket_replied",
        target_type="support_ticket",
        target_id=str(t.id),
    )
    await state.clear()
    if u:
        try:
            await message.bot.send_message(
                int(u.telegram_id),
                format_message(
                    settings,
                    f"📩 پاسخ پشتیبانی به تیکت #{tid}:\n\n{html.escape(body)}",
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("send ticket reply to user failed")
    await message.answer(_afmt(settings, "✅ پاسخ ثبت شد و برای کاربر ارسال شد."))


@router.callback_query(F.data.startswith("adm:tick_close:"))
async def cb_tick_close(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    admin: Admin,
) -> None:
    tid = int(callback.data.split(":")[-1])
    t = await session.get(SupportTicket, tid)
    if t is None:
        await callback.answer("تیکت یافت نشد.", show_alert=True)
        return
    t.status = SupportTicketStatus.CLOSED
    u = await session.get(User, t.user_id)
    await write_audit(
        session,
        actor_telegram_id=callback.from_user.id,
        actor_role=admin.role.value,
        action="support_ticket_closed",
        target_type="support_ticket",
        target_id=str(t.id),
    )
    await session.flush()
    if u:
        try:
            await callback.bot.send_message(
                int(u.telegram_id),
                _afmt(settings, f"✅ تیکت #{tid} بسته شد."),
            )
        except Exception:
            logger.exception("notify ticket close")
    await callback.answer("بسته شد.")
    callback.data = f"adm:tick_view:{tid}"
    await cb_tick_view(callback, session, settings)
