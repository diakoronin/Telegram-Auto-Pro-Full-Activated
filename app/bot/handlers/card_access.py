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
from app.services.audit import write_audit
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
        "تأیید دسترسی مشاهدهٔ شماره کارت برای کاربر:\n"
        "۱) یک پیام از کاربر را به همین ربات فوروارد کنید، یا\n"
        "۲) شناسهٔ عددی تلگرام کاربر را بفرستید.\n"
        "برای لغو از دکمهٔ بازگشت استفاده کنید.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="حالت فوروارد", callback_data="adm:ca_fwd")],
                [InlineKeyboardButton(text="شناسهٔ عددی", callback_data="adm:ca_tid")],
                [InlineKeyboardButton(text="لغو دسترسی کاربر", callback_data="adm:ca_revoke")],
                [InlineKeyboardButton(text="بازگشت", callback_data="admin_home")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:ca_fwd", IsManagerOrOwner())
async def cb_ca_forward_mode(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.card_access_forward_wait)
    await callback.message.answer(
        "یک پیام از کاربر را به این چت فوروارد کنید (باید فرستنده مشخص باشد؛ "
        "فوروارد ناشناس قابل تأیید نیست).",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:ca_tid", IsManagerOrOwner())
async def cb_ca_tid_mode(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.card_access_tid_wait)
    await callback.message.answer(
        "شناسهٔ عددی تلگرام کاربر را بفرستید:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:ca_revoke", IsManagerOrOwner())
async def cb_ca_revoke_mode(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.card_access_revoke_tid)
    await callback.message.answer(
        "شناسهٔ عددی تلگرام کاربری که باید دسترسی کارت‌اش قطع شود:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="admin_cancel_fsm")]
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
        await message.answer(
            "لطفاً یک پیام فورواردشده از کاربر بفرستید یا دکمهٔ لغو را بزنید."
        )
        return
    tid = _forwarded_user_telegram_id(message)
    if tid is None:
        await message.answer(
            "فرستندهٔ این فوروارد مشخص نیست. از فوروارد ناشناس استفاده نکنید "
            "یا از گزینهٔ شناسهٔ عددی استفاده کنید."
        )
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
        await message.answer("شناسهٔ نامعتبر است.")
        return
    if tid <= 0:
        await message.answer("شناسهٔ نامعتبر است.")
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
        await message.answer("شناسهٔ نامعتبر است.")
        return
    u = await get_user_by_telegram(session, tid)
    if u is None:
        await message.answer("کاربر در ربات ثبت نشده است.")
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
        T.CONFIRM_PROMPT + "\nقطع دسترسی مشاهدهٔ کارت برای این کاربر؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="تأیید قطع", callback_data=f"acf:{cid}"),
                    InlineKeyboardButton(text="لغو", callback_data="noop"),
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
        await message.answer("این کاربر مسدود است؛ ابتدا رفع مسدودیت کنید.")
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
        f"کاربر: telegram_id={target_telegram_id} db_id={u.id}\n"
        + T.CONFIRM_PROMPT
        + "\nفعال‌سازی دسترسی مشاهدهٔ شماره کارت؟",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="تأیید دسترسی", callback_data=f"acf:{cid}"),
                    InlineKeyboardButton(text="لغو", callback_data="noop"),
                ]
            ]
        ),
    )


# Note: grant/revoke execution lives in admin.py admin_cf_router alongside other acf: actions.
