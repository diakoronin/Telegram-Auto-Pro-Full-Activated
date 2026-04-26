from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from sqlalchemy.ext.asyncio import AsyncSession

from app import texts_fa as T
from app.bot.filters import IsManagerOrOwner
from app.bot.states import AdminStates
from app.db.models import Admin
from app.services.confirmations import create_confirmation
from app.services.users import get_or_create_user, get_user_by_telegram

logger = logging.getLogger(__name__)

router = Router(name="card_access_admin")
router.message.filter(IsManagerOrOwner())
router.callback_query.filter(IsManagerOrOwner())


def _forwarded_user_telegram_id(message: Message) -> int | None:
    """Resolve real user id from forwarded message (Bot API 7+ origin or legacy fields)."""
    fo = message.forward_origin
    if fo is not None:
        type_ = getattr(fo, "type", None)
        type_val = getattr(type_, "value", type_) or str(type_)
        if type_val == "user":
            su = getattr(fo, "sender_user", None)
            if su is not None:
                return int(su.id)
        if type_val == "hidden_user":
            return None
        if type_val == "chat":
            sc = getattr(fo, "sender_chat", None)
            if sc is not None and getattr(sc, "type", None) == "private":
                return int(sc.id)
        return None
    if message.forward_from is not None:
        return int(message.forward_from.id)
    if message.forward_from_chat is not None:
        if getattr(message.forward_from_chat, "type", None) == "private":
            return int(message.forward_from_chat.id)
    return None


@router.callback_query(F.data == "adm:card_access_menu", IsManagerOrOwner())
async def cb_card_access_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        T.CARD_ACCESS_MENU_TEXT,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=T.CARD_ACCESS_BTN_FORWARD_MODE, callback_data="adm:ca_fwd")],
                [InlineKeyboardButton(text=T.CARD_ACCESS_BTN_NUMERIC_ID, callback_data="adm:ca_tid")],
                [InlineKeyboardButton(text=T.CARD_ACCESS_BTN_REVOKE, callback_data="adm:ca_revoke")],
                [InlineKeyboardButton(text=T.CARD_ACCESS_BTN_BACK, callback_data="admin_home")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:ca_fwd", IsManagerOrOwner())
async def cb_ca_forward_mode(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.card_access_forward_wait)
    await callback.message.answer(
        T.CARD_ACCESS_FWD_INSTRUCTION,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=T.CARD_ACCESS_BTN_CANCEL, callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:ca_tid", IsManagerOrOwner())
async def cb_ca_tid_mode(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.card_access_tid_wait)
    await callback.message.answer(
        T.CARD_ACCESS_ASK_TID,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=T.CARD_ACCESS_BTN_CANCEL, callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:ca_revoke", IsManagerOrOwner())
async def cb_ca_revoke_mode(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.card_access_revoke_tid)
    await callback.message.answer(
        T.CARD_ACCESS_ASK_REVOKE_TID,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=T.CARD_ACCESS_BTN_CANCEL, callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.message(AdminStates.card_access_forward_wait)
async def msg_card_access_forward(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
    after_commit: list,
) -> None:
    if (
        message.forward_origin is None
        and message.forward_from is None
        and message.forward_from_chat is None
    ):
        await message.answer(T.CARD_ACCESS_NEED_FORWARD)
        return
    tid = _forwarded_user_telegram_id(message)
    if tid is None:
        await message.answer(T.CARD_ACCESS_HIDDEN_FORWARD)
        return
    await _propose_card_access(
        message,
        state,
        session,
        admin,
        after_commit,
        target_telegram_id=tid,
        via="forward",
    )


@router.message(AdminStates.card_access_tid_wait, F.text)
async def msg_card_access_tid(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
    after_commit: list,
) -> None:
    try:
        tid = int((message.text or "").strip())
    except ValueError:
        await message.answer(T.CARD_ACCESS_INVALID_TID)
        return
    if tid <= 0:
        await message.answer(T.CARD_ACCESS_INVALID_TID)
        return
    await _propose_card_access(
        message,
        state,
        session,
        admin,
        after_commit,
        target_telegram_id=tid,
        via="telegram_id",
    )


@router.message(AdminStates.card_access_revoke_tid, F.text)
async def msg_card_access_revoke_tid(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
    after_commit: list,
) -> None:
    try:
        tid = int((message.text or "").strip())
    except ValueError:
        await message.answer(T.CARD_ACCESS_INVALID_TID)
        return
    u = await get_user_by_telegram(session, tid)
    if u is None:
        await message.answer(T.CARD_ACCESS_USER_NOT_IN_BOT)
        await state.clear()
        return
    cid = await create_confirmation(
        session,
        admin_telegram_id=message.from_user.id,
        action_type="revoke_card_view",
        payload={"action": "revoke_card_view", "user_db_id": u.id},
    )
    await state.clear()
    await message.answer(
        T.CONFIRM_PROMPT + "\n" + T.CARD_ACCESS_REVOKE_CONFIRM_EXTRA,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text=T.CARD_ACCESS_BTN_CONFIRM_REVOKE, callback_data=f"acf:{cid}"),
                    InlineKeyboardButton(text=T.CARD_ACCESS_BTN_CANCEL, callback_data="noop"),
                ]
            ]
        ),
    )


async def _propose_card_access(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    admin: Admin,
    after_commit: list,
    *,
    target_telegram_id: int,
    via: str,
) -> None:
    u = await get_or_create_user(session, target_telegram_id, None)
    if u.is_blocked:
        await message.answer(T.CARD_ACCESS_USER_BLOCKED)
        await state.clear()
        return
    cid = await create_confirmation(
        session,
        admin_telegram_id=message.from_user.id,
        action_type="grant_card_view",
        payload={
            "action": "grant_card_view",
            "user_db_id": u.id,
            "via": via,
        },
    )
    await state.clear()
    await message.answer(
        f"User: telegram_id={target_telegram_id} db_id={u.id}\n"
        + T.CONFIRM_PROMPT
        + "\n"
        + T.CARD_ACCESS_GRANT_CONFIRM_EXTRA,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text=T.CARD_ACCESS_BTN_CONFIRM_GRANT, callback_data=f"acf:{cid}"),
                    InlineKeyboardButton(text=T.CARD_ACCESS_BTN_CANCEL, callback_data="noop"),
                ]
            ]
        ),
    )


# Grant/revoke execution: admin.py admin_cf_router (acf:)
