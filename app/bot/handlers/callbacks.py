from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from sqlalchemy.ext.asyncio import AsyncSession

from app import texts_fa as T
from app.bot.states import AdminStates
from app.db.models import Admin, AdminRole, User
from app.services.audit import write_audit
from app.services.payments import approve_payment_request

logger = logging.getLogger(__name__)

router = Router(name="callbacks")


@router.callback_query(F.data.startswith("ap:"))
async def cb_approve_payment(
    callback: CallbackQuery,
    session: AsyncSession,
    after_commit: list,
    admin: Admin | None = None,
    **kwargs: Any,
) -> None:
    if admin is None or admin.role not in (AdminRole.OWNER, AdminRole.MANAGER):
        await callback.answer(T.UNAUTHORIZED, show_alert=True)
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id if callback.from_user else None,
            actor_role=None,
            action="unauthorized_payment_approve",
            metadata={"data": callback.data},
        )
        return
    pr_id = int(callback.data.split(":", 1)[1])
    pr, err = await approve_payment_request(session, request_id=pr_id, reviewer=admin)
    if err or pr is None:
        await callback.answer(err or T.GENERIC_ERROR, show_alert=True)
        return
    u = await session.get(User, pr.user_id)
    user_tid = u.telegram_id if u else None
    await write_audit(
        session,
        actor_telegram_id=callback.from_user.id,
        actor_role=admin.role.value,
        action="payment_approved",
        target_type="payment_request",
        target_id=str(pr_id),
    )
    bot = callback.bot

    async def _notify() -> None:
        if user_tid:
            try:
                await bot.send_message(user_tid, T.PAYMENT_APPROVED_USER)
            except Exception:
                logger.exception("notify user approve failed")

    after_commit.append(_notify)
    await callback.answer("تأیید شد.")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@router.callback_query(F.data.startswith("rj:"))
async def cb_reject_payment_start(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    admin: Admin | None = None,
    **kwargs: Any,
) -> None:
    if admin is None or admin.role not in (AdminRole.OWNER, AdminRole.MANAGER):
        await callback.answer(T.UNAUTHORIZED, show_alert=True)
        return
    pr_id = int(callback.data.split(":", 1)[1])
    await state.set_state(AdminStates.reject_reason)
    await state.update_data(reject_pr_id=pr_id)
    await callback.message.answer(
        "دلیل رد را بنویسید (الزامی):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()
