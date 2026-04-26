from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts_fa as T
from app.config import Settings
from app.db.models import Admin, PaymentCard, PaymentRequest, Plan, Purchase, Server, User
from app.bot.states import ChargeStates, SupportStates
from app.services.audit import write_audit
from app.services.links import purchase_plan_for_user
from app.services.payments import (
    count_pending_for_user,
    count_receipts_last_hour,
    create_payment_request,
)
from app.services.rate_limit import consume_rate
from app.services.stock_alerts import run_stock_check_after_commit
from app.validation import ValidationError, validate_charge_amount, is_allowed_receipt_content_type

logger = logging.getLogger(__name__)

router = Router(name="user")


def _main_kb(*, card_view_allowed: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=T.BTN_SHOP, callback_data="shop")],
        [
            InlineKeyboardButton(text=T.BTN_WALLET, callback_data="wallet"),
            InlineKeyboardButton(text=T.BTN_CHARGE, callback_data="charge"),
        ],
        [
            InlineKeyboardButton(text=T.BTN_HISTORY, callback_data="hist_purchases"),
            InlineKeyboardButton(
                text=T.BTN_PAYMENT_HISTORY, callback_data="hist_payments"
            ),
        ],
    ]
    if card_view_allowed:
        rows.append([InlineKeyboardButton(text=T.BTN_CARDS, callback_data="show_cards")])
    rows.append([InlineKeyboardButton(text=T.BTN_SUPPORT, callback_data="support")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    command: CommandObject,
    db_user: User | None = None,
    admin: Admin | None = None,
    **kwargs: Any,
) -> None:
    """Always send a visible reply for /start (never silent return)."""
    _ = command  # deep-link args in command.args when needed
    sent = False
    try:
        fu = message.from_user
        if fu is None:
            logger.warning(
                "cmd_start: missing from_user chat_id=%s chat_type=%s",
                message.chat.id,
                message.chat.type,
            )
            await message.answer(T.START_CONTEXT_ERROR)
            sent = True
            return

        if db_user is None:
            logger.error(
                "cmd_start: db_user missing for telegram_id=%s — context middleware bug?",
                fu.id,
            )
            await message.answer(T.START_CONTEXT_ERROR)
            sent = True
            return

        role = admin.role.value if admin else "user"
        logger.info(
            "cmd_start: telegram_id=%s role=%s db_user_id=%s card_view=%s",
            fu.id,
            role,
            db_user.id,
            db_user.card_view_allowed,
        )

        await write_audit(
            session,
            actor_telegram_id=fu.id,
            actor_role=role,
            action="user_start",
            target_type="user",
            target_id=str(db_user.id),
        )
        text = T.START_WELCOME + "\n" + T.MENU_USER
        if admin:
            text += "\n\nبرای پنل مدیریت از /admin استفاده کنید."
        await message.answer(text, reply_markup=_main_kb(card_view_allowed=db_user.card_view_allowed))
        sent = True
        logger.info("cmd_start: reply sent telegram_id=%s", fu.id)
    except Exception:
        logger.exception(
            "cmd_start: exception telegram_id=%s",
            message.from_user.id if message.from_user else None,
        )
        if not sent:
            try:
                await message.answer(T.START_CONTEXT_ERROR)
            except Exception:
                logger.exception("cmd_start: failed to send fallback error message")


@router.message(Command("ping"))
async def cmd_ping(message: Message, **kwargs: Any) -> None:
    await message.answer(T.PONG)


@router.message(Command("menu"))
async def cmd_menu(
    message: Message, db_user: User | None = None, **kwargs: Any
) -> None:
    if message.from_user is None or db_user is None:
        await message.answer(T.START_CONTEXT_ERROR)
        return
    await message.answer(
        T.MENU_USER,
        reply_markup=_main_kb(card_view_allowed=db_user.card_view_allowed),
    )


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(
    callback: CallbackQuery, db_user: User | None = None, **kwargs: Any
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    await callback.message.edit_text(
        T.MENU_USER,
        reply_markup=_main_kb(card_view_allowed=db_user.card_view_allowed),
    )
    await callback.answer()


@router.callback_query(F.data == "show_cards")
async def cb_show_cards(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    if not db_user.card_view_allowed:
        await callback.answer(T.CARDS_NOT_ALLOWED, show_alert=True)
        return
    cards = (
        await session.execute(select(PaymentCard).where(PaymentCard.is_active.is_(True)))
    ).scalars().all()
    if not cards:
        text = T.CARDS_NONE_ACTIVE
    else:
        lines = [T.CARDS_HEADER, ""]
        for c in cards:
            lines.append(f"{c.card_number_masked} — {c.card_holder} — {c.bank_name}")
        text = "\n".join(lines)
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="بازگشت", callback_data="main_menu")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "wallet")
async def cb_wallet(
    callback: CallbackQuery, db_user: User | None = None, **kwargs: Any
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    await callback.message.edit_text(
        T.WALLET_BALANCE.format(balance=db_user.wallet_balance),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="بازگشت", callback_data="main_menu")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "charge")
async def cb_charge(
    callback: CallbackQuery,
    state: FSMContext,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    if db_user.is_blocked:
        await callback.answer(T.BLOCKED_USER, show_alert=True)
        return
    await state.set_state(ChargeStates.waiting_amount)
    await callback.message.edit_text(
        T.AMOUNT_PROMPT,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_fsm")
async def cb_cancel_fsm(
    callback: CallbackQuery,
    state: FSMContext,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        T.MENU_USER,
        reply_markup=_main_kb(card_view_allowed=db_user.card_view_allowed),
    )
    await callback.answer()


@router.message(ChargeStates.waiting_amount)
async def charge_amount(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await state.clear()
        return
    if db_user.is_blocked:
        await state.clear()
        await message.answer(T.BLOCKED_USER)
        return
    try:
        amount = validate_charge_amount(
            message.text or "", settings.min_charge_amount, settings.max_charge_amount
        )
    except ValidationError as e:
        await message.answer(e.message_fa)
        return

    pending = await count_pending_for_user(session, db_user.id)
    if pending >= 3:
        await message.answer(T.PENDING_LIMIT)
        await state.clear()
        return

    if await count_receipts_last_hour(session, db_user.id) >= settings.rate_limit_receipt_hour:
        await message.answer(T.RECEIPT_RATE)
        await state.clear()
        return

    await state.update_data(charge_amount=amount)
    await state.set_state(ChargeStates.waiting_receipt)
    await message.answer(T.RECEIPT_PROMPT)


@router.message(ChargeStates.waiting_receipt, F.photo)
async def charge_receipt_photo(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    after_commit: list,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await state.clear()
        return
    await _finalize_receipt(
        message,
        state,
        session,
        settings,
        after_commit,
        db_user,
        file_id=message.photo[-1].file_id,
        kind="photo",
    )


@router.message(ChargeStates.waiting_receipt, F.document)
async def charge_receipt_doc(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    after_commit: list,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await state.clear()
        return
    doc = message.document
    if not doc or not is_allowed_receipt_content_type(doc.mime_type):
        await message.answer(T.RECEIPT_PROMPT)
        return
    await _finalize_receipt(
        message,
        state,
        session,
        settings,
        after_commit,
        db_user,
        file_id=doc.file_id,
        kind="document",
    )


async def _finalize_receipt(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    after_commit: list,
    db_user: User | None,
    *,
    file_id: str,
    kind: str,
) -> None:
    if db_user is None:
        await state.clear()
        return
    data = await state.get_data()
    amount = int(data.get("charge_amount") or 0)
    if amount <= 0:
        await state.clear()
        await message.answer(T.GENERIC_ERROR)
        return

    if await count_receipts_last_hour(session, db_user.id) >= settings.rate_limit_receipt_hour:
        await message.answer(T.RECEIPT_RATE)
        await state.clear()
        return

    pr = await create_payment_request(
        session,
        user=db_user,
        amount=amount,
        receipt_file_id=file_id,
        receipt_kind=kind,
    )
    await write_audit(
        session,
        actor_telegram_id=message.from_user.id if message.from_user else None,
        actor_role="user",
        action="payment_request_created",
        target_type="payment_request",
        target_id=str(pr.id),
        metadata={"amount": amount},
    )
    await state.clear()
    lines = [T.CHARGE_SUBMITTED]
    if db_user.card_view_allowed:
        cards = (
            await session.execute(
                select(PaymentCard).where(PaymentCard.is_active.is_(True))
            )
        ).scalars().all()
        if cards:
            lines.append("")
            lines.append(T.CARDS_HEADER)
            for c in cards:
                lines.append(f"{c.card_number_masked} — {c.card_holder} — {c.bank_name}")
        else:
            lines.append("")
            lines.append(T.CARDS_NONE_ACTIVE)
    await message.answer("\n".join(lines))

    from app.db.models import AdminRole

    admins = (
        await session.execute(select(Admin).where(Admin.is_active.is_(True)))
    ).scalars().all()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="تأیید", callback_data=f"ap:{pr.id}"),
                InlineKeyboardButton(text="رد", callback_data=f"rj:{pr.id}"),
            ]
        ]
    )
    caption = (
        f"درخواست شارژ\n"
        f"id: {pr.id}\n"
        f"user_id: {db_user.telegram_id}\n"
        f"username: @{db_user.username or '-'}\n"
        f"amount: {amount}"
    )

    bot = message.bot

    async def _notify() -> None:
        for a in admins:
            if a.role in (AdminRole.OWNER, AdminRole.MANAGER):
                try:
                    await bot.send_photo(
                        a.telegram_id,
                        photo=file_id,
                        caption=caption,
                        reply_markup=kb,
                    )
                except Exception:
                    logger.exception("notify admin payment failed")

    after_commit.append(_notify)


@router.callback_query(F.data == "shop")
async def cb_shop(callback: CallbackQuery, session: AsyncSession) -> None:
    rows = (
        await session.execute(
            select(Server, Plan)
            .join(Plan, Plan.server_id == Server.id)
            .where(Server.is_active.is_(True), Plan.is_active.is_(True))
            .order_by(Server.id, Plan.id)
        )
    ).all()
    if not rows:
        await callback.message.edit_text(
            T.NO_PLANS,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="بازگشت", callback_data="main_menu")]
                ]
            ),
        )
        await callback.answer()
        return
    buttons = []
    for srv, pl in rows:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{srv.name} / {pl.name} — {pl.price}",
                    callback_data=f"buy:{pl.id}",
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text="بازگشت", callback_data="main_menu")])
    await callback.message.edit_text(T.SELECT_PLAN, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    after_commit: list,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    plan_id = int(callback.data.split(":", 1)[1])
    plan = await session.get(Plan, plan_id)
    if plan is None or not plan.is_active:
        await callback.answer("پلن نامعتبر است.", show_alert=True)
        return
    ok_rl = await consume_rate(
        session,
        key=f"purchase:{db_user.id}",
        window_seconds=60,
        max_count=settings.rate_limit_purchase_minute,
    )
    if not ok_rl:
        await callback.answer(T.RATE_LIMIT, show_alert=True)
        return
    ok, link_text, err, purchase_id = await purchase_plan_for_user(
        session, user=db_user, plan=plan
    )
    if not ok:
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            actor_role="user",
            action="purchase_failed",
            target_type="plan",
            target_id=str(plan_id),
            metadata={"error": err},
        )
        await callback.answer(err or T.GENERIC_ERROR, show_alert=True)
        return
    await write_audit(
        session,
        actor_telegram_id=callback.from_user.id,
        actor_role="user",
        action="purchase_completed",
        target_type="purchase",
        target_id=str(purchase_id or ""),
    )
    await callback.message.edit_text(
        T.PURCHASE_OK.format(link=link_text),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="بازگشت", callback_data="main_menu")]
            ]
        ),
    )
    bot = callback.bot

    async def _stock() -> None:
        await run_stock_check_after_commit(
            settings.database_url, settings, bot, plan_id=plan_id
        )

    after_commit.append(_stock)
    await callback.answer()


@router.callback_query(F.data == "hist_purchases")
async def cb_hist_purchases(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    q = await session.execute(
        select(Purchase, Plan)
        .join(Plan, Plan.id == Purchase.plan_id)
        .where(Purchase.user_id == db_user.id)
        .order_by(Purchase.id.desc())
        .limit(15)
    )
    lines = []
    for pur, pl in q.all():
        lines.append(
            f"#{pur.id} plan={pl.name} paid={pur.amount_paid} refunded={pur.is_refunded}"
        )
    text = "\n".join(lines) if lines else "خریدی ثبت نشده."
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="بازگشت", callback_data="main_menu")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "hist_payments")
async def cb_hist_payments(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    q = await session.execute(
        select(PaymentRequest)
        .where(PaymentRequest.user_id == db_user.id)
        .order_by(PaymentRequest.id.desc())
        .limit(15)
    )
    lines = []
    for pr in q.scalars().all():
        lines.append(f"#{pr.id} amt={pr.amount} status={pr.status.value}")
    text = "\n".join(lines) if lines else "درخواستی نیست."
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="بازگشت", callback_data="main_menu")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "support")
async def cb_support(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    if db_user.is_blocked:
        await callback.answer(T.SUPPORT_BLOCKED, show_alert=True)
        return
    ok = await consume_rate(
        session,
        key=f"support:{db_user.id}",
        window_seconds=60,
        max_count=settings.rate_limit_support_minute,
    )
    if not ok:
        await callback.answer(T.RATE_LIMIT, show_alert=True)
        return
    await state.set_state(SupportStates.waiting_message)
    uname = settings.support_username
    await callback.message.edit_text(
        f"پیام خود را برای پشتیبانی ارسال کنید.\n"
        f"در تلگرام می‌توانید با @{uname} نیز تماس بگیرید.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="لغو", callback_data="cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.message(SupportStates.waiting_message, F.text)
async def support_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    from app.db.models import SupportTicket

    if db_user is None:
        await state.clear()
        return
    body = (message.text or "").strip()
    if not body:
        await message.answer("متن خالی مجاز نیست.")
        return
    session.add(SupportTicket(user_id=db_user.id, message=body))
    await write_audit(
        session,
        actor_telegram_id=message.from_user.id if message.from_user else None,
        actor_role="user",
        action="support_message",
        target_type="user",
        target_id=str(db_user.id),
        metadata={"len": len(body)},
    )
    await state.clear()
    await message.answer(
        T.SUPPORT_SENT,
        reply_markup=_main_kb(card_view_allowed=db_user.card_view_allowed),
    )
