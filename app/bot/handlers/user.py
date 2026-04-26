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
from app.message_format import format_message, format_money_toman
from app.services.audit import write_audit
from app.services.cards import card_display_number, pick_public_card_for_invoice
from app.services.links import purchase_plan_for_user
from app.services.payments import (
    attach_receipt_to_payment_request,
    cancel_payment_request_by_user,
    count_pending_for_user,
    count_receipts_last_hour,
    create_draft_payment_request,
)
from app.services.rate_limit import consume_rate
from app.services.stock import count_unused_for_plan
from app.services.stock_alerts import run_stock_check_after_commit
from app.validation import ValidationError, validate_charge_amount, is_allowed_receipt_content_type

logger = logging.getLogger(__name__)

router = Router(name="user")


def _fmt(settings: Settings, text: str) -> str:
    return format_message(settings, text)


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


async def _answer_fmt(
    message: Message, settings: Settings, text: str, **kwargs: Any
) -> Any:
    return await message.answer(_fmt(settings, text), **kwargs)


async def _edit_fmt(
    message: Message, settings: Settings, text: str, **kwargs: Any
) -> Any:
    return await message.edit_text(_fmt(settings, text), **kwargs)


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
            await message.answer(_fmt(settings, T.START_CONTEXT_ERROR))
            sent = True
            return

        if db_user is None:
            logger.error(
                "cmd_start: db_user missing for telegram_id=%s — context middleware bug?",
                fu.id,
            )
            await message.answer(_fmt(settings, T.START_CONTEXT_ERROR))
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
        text = T.START_USER.format(brand=settings.brand_name)
        if admin:
            text += T.START_ADMIN_HINT
        await message.answer(
            _fmt(settings, text),
            reply_markup=_main_kb(card_view_allowed=db_user.card_view_allowed),
        )
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
async def cmd_ping(message: Message, settings: Settings, **kwargs: Any) -> None:
    await message.answer(_fmt(settings, T.PING_OK))


@router.message(Command("help"))
async def cmd_help(
    message: Message, settings: Settings, admin: Admin | None = None, **kwargs: Any
) -> None:
    text = T.HELP_ADMIN if admin is not None else T.HELP_USER
    await message.answer(_fmt(settings, text))


@router.message(Command("menu"))
async def cmd_menu(
    message: Message,
    settings: Settings,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if message.from_user is None or db_user is None:
        await message.answer(_fmt(settings, T.START_CONTEXT_ERROR))
        return
    await message.answer(
        _fmt(settings, T.MENU_USER),
        reply_markup=_main_kb(card_view_allowed=db_user.card_view_allowed),
    )


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(
    callback: CallbackQuery,
    settings: Settings,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    await _edit_fmt(
        callback.message,
        settings,
        T.MENU_USER,
        reply_markup=_main_kb(card_view_allowed=db_user.card_view_allowed),
    )
    await callback.answer()


@router.callback_query(F.data == "show_cards")
async def cb_show_cards(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    if not db_user.card_payment_enabled:
        await callback.answer(T.CHARGE_DISABLED, show_alert=True)
        return
    if not db_user.card_view_allowed:
        await callback.answer(T.CARDS_NOT_ALLOWED, show_alert=True)
        return
    cards = (
        await session.execute(
            select(PaymentCard).where(
                PaymentCard.is_active.is_(True),
                PaymentCard.is_public.is_(True),
            )
        )
    ).scalars().all()
    if not cards:
        text = T.EMPTY_NO_CARDS
    else:
        lines = [T.CARDS_HEADER, ""]
        for c in cards:
            num = card_display_number(c)
            lines.append(f"💳 {num}\n🙎🏻‍♂️ {c.card_holder}\n🏦 {c.bank_name}")
        text = "\n\n".join(lines)
    await _edit_fmt(
        callback.message,
        settings,
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=T.BTN_BACK, callback_data="main_menu")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "wallet")
async def cb_wallet(
    callback: CallbackQuery,
    settings: Settings,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    bal = format_money_toman(int(db_user.wallet_balance))
    await _edit_fmt(
        callback.message,
        settings,
        T.WALLET_BALANCE.format(balance=bal),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=T.BTN_BACK, callback_data="main_menu")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "charge")
async def cb_charge(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    session: AsyncSession,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    if db_user.is_blocked:
        await callback.answer(T.BLOCKED_USER, show_alert=True)
        return
    if not db_user.card_payment_enabled:
        await callback.answer(T.CHARGE_DISABLED, show_alert=True)
        return
    if await pick_public_card_for_invoice(session) is None:
        await callback.answer(T.NO_PUBLIC_CARD, show_alert=True)
        return
    await state.set_state(ChargeStates.waiting_amount)
    await _edit_fmt(
        callback.message,
        settings,
        T.CHARGE_ASK_AMOUNT,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=T.BTN_BACK, callback_data="cancel_fsm")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_fsm")
async def cb_cancel_fsm(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    await state.clear()
    await _edit_fmt(
        callback.message,
        settings,
        T.MENU_USER,
        reply_markup=_main_kb(card_view_allowed=db_user.card_view_allowed),
    )
    await callback.answer()


@router.callback_query(F.data == "charge_send_receipt")
async def cb_charge_send_receipt(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    data = await state.get_data()
    if not data.get("charge_pr_id"):
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    await state.set_state(ChargeStates.waiting_receipt)
    await callback.message.answer(_fmt(settings, T.ASK_RECEIPT_PHOTO))
    await callback.answer("باشه ✅")


@router.callback_query(F.data == "charge_cancel_invoice")
async def cb_charge_cancel_invoice(
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
    data = await state.get_data()
    pr_id = data.get("charge_pr_id")
    if pr_id:
        pr = await session.get(PaymentRequest, int(pr_id))
        if pr is not None:
            await cancel_payment_request_by_user(session, pr=pr)
    await state.clear()
    await callback.message.answer(
        _fmt(settings, T.INVOICE_CANCELLED),
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
        await message.answer(_fmt(settings, T.BLOCKED_USER))
        return
    if not db_user.card_payment_enabled:
        await state.clear()
        await message.answer(_fmt(settings, T.CHARGE_DISABLED))
        return
    try:
        amount = validate_charge_amount(
            message.text or "", settings.min_charge_amount, settings.max_charge_amount
        )
    except ValidationError as e:
        await message.answer(_fmt(settings, e.message_fa))
        return

    pending = await count_pending_for_user(session, db_user.id)
    if pending >= 3:
        await message.answer(_fmt(settings, T.PENDING_LIMIT))
        await state.clear()
        return

    if await count_receipts_last_hour(session, db_user.id) >= settings.rate_limit_receipt_hour:
        await message.answer(_fmt(settings, T.RECEIPT_RATE))
        await state.clear()
        return

    card = await pick_public_card_for_invoice(session)
    if card is None:
        await message.answer(_fmt(settings, T.NO_PUBLIC_CARD))
        await state.clear()
        return

    pr = await create_draft_payment_request(
        session,
        user=db_user,
        amount=amount,
        card=card,
        expire_minutes=settings.payment_expire_minutes,
    )
    await write_audit(
        session,
        actor_telegram_id=message.from_user.id if message.from_user else None,
        actor_role="user",
        action="payment_invoice_created",
        target_type="payment_request",
        target_id=str(pr.id),
        metadata={"amount": amount, "card_id": card.id},
    )
    await state.update_data(charge_amount=amount, charge_pr_id=pr.id)
    await state.set_state(ChargeStates.invoice_review)
    inv = T.INVOICE_CREATED.format(
        card_number=card_display_number(card),
        holder=card.card_holder,
        bank=card.bank_name,
        amount=format_money_toman(amount),
        pr_id=pr.id,
        expire_minutes=settings.payment_expire_minutes,
    )
    await message.answer(
        _fmt(settings, inv),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=T.BTN_SEND_RECEIPT, callback_data="charge_send_receipt")],
                [
                    InlineKeyboardButton(
                        text=T.BTN_CANCEL_INVOICE, callback_data="charge_cancel_invoice"
                    )
                ],
                [InlineKeyboardButton(text=T.BTN_BACK, callback_data="cancel_fsm")],
            ]
        ),
    )


@router.message(ChargeStates.invoice_review)
async def charge_invoice_review_noise(
    message: Message, settings: Settings, **kwargs: Any
) -> None:
    await message.answer(
        _fmt(
            settings,
            "لطفاً ابتدا یکی از دکمه‌های زیر فاکتور را بزنید "
            "یا با «بازگشت» به منو برگردید.",
        )
    )


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
        await message.answer(_fmt(settings, T.ASK_RECEIPT_PHOTO))
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
    pr_id = data.get("charge_pr_id")
    amount = int(data.get("charge_amount") or 0)
    if not pr_id or amount <= 0:
        await state.clear()
        await message.answer(_fmt(settings, T.GENERIC_ERROR))
        return

    if await count_receipts_last_hour(session, db_user.id) >= settings.rate_limit_receipt_hour:
        await message.answer(_fmt(settings, T.RECEIPT_RATE))
        await state.clear()
        return

    pr = await session.get(PaymentRequest, int(pr_id))
    if pr is None:
        await state.clear()
        await message.answer(_fmt(settings, T.GENERIC_ERROR))
        return
    ok_att, err_att = await attach_receipt_to_payment_request(
        session, pr=pr, receipt_file_id=file_id, receipt_kind=kind
    )
    if not ok_att:
        await message.answer(_fmt(settings, err_att or T.GENERIC_ERROR))
        await state.clear()
        return

    await write_audit(
        session,
        actor_telegram_id=message.from_user.id if message.from_user else None,
        actor_role="user",
        action="payment_request_receipt_submitted",
        target_type="payment_request",
        target_id=str(pr.id),
        metadata={"amount": amount},
    )
    await state.clear()
    await message.answer(
        _fmt(
            settings,
            T.RECEIPT_SUBMITTED.format(
                pr_id=pr.id,
                amount=format_money_toman(amount),
            ),
        ),
        reply_markup=_main_kb(card_view_allowed=db_user.card_view_allowed),
    )

    from app.db.models import AdminRole

    admins = (
        await session.execute(select(Admin).where(Admin.is_active.is_(True)))
    ).scalars().all()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ تایید", callback_data=f"ap:{pr.id}"),
                InlineKeyboardButton(text="❌ رد", callback_data=f"rj:{pr.id}"),
            ]
        ]
    )
    uname = f"@{db_user.username}" if db_user.username else "—"
    caption = _fmt(
        settings,
        f"🧾 درخواست شارژ #{pr.id}\n\n"
        f"🆔 آیدی: {db_user.telegram_id}\n"
        f"🔗 یوزرنیم: {uname}\n"
        f"💵 مبلغ: {format_money_toman(amount)} تومان",
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
async def cb_shop(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    rows = (
        await session.execute(
            select(Server, Plan)
            .join(Plan, Plan.server_id == Server.id)
            .where(Server.is_active.is_(True), Plan.is_active.is_(True))
            .order_by(Server.id, Plan.id)
        )
    ).all()
    if not rows:
        await _edit_fmt(
            callback.message,
            settings,
            T.NO_PLANS,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=T.BTN_BACK, callback_data="main_menu")]
                ]
            ),
        )
        await callback.answer()
        return
    buttons = []
    for srv, pl in rows:
        n = await count_unused_for_plan(session, server_id=srv.id, plan_id=pl.id)
        price_s = format_money_toman(int(pl.price))
        if n <= 0:
            label = f"📦 {pl.name} | {price_s} تومان | ناموجود ❌"
        else:
            label = f"📦 {pl.name} | {price_s} تومان | موجودی: {n} ✅"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label[:64],
                    callback_data=f"buy:{pl.id}",
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text=T.BTN_BACK, callback_data="main_menu")])
    await _edit_fmt(
        callback.message,
        settings,
        T.SELECT_PLAN,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
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
    stock_n = await count_unused_for_plan(session, server_id=plan.server_id, plan_id=plan.id)
    if stock_n <= 0:
        await callback.answer(T.STOCK_OUT_USER, show_alert=True)
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
        if err and "لینک تمام" in (err or ""):
            await callback.answer(T.STOCK_OUT_USER, show_alert=True)
        elif err and "موجودی کافی" in (err or ""):
            await callback.answer(
                "موجودی کیف پول کافی نیست. از بخش «شارژ حساب» اقدام کنید.",
                show_alert=True,
            )
        else:
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
    srv = await session.get(Server, plan.server_id)
    srv_name = srv.name if srv else "—"
    body = T.PURCHASE_OK_UX.format(
        plan=plan.name,
        server=srv_name,
        price=format_money_toman(int(plan.price)),
        purchase_id=purchase_id or 0,
        link=link_text or "",
    )
    await _edit_fmt(
        callback.message,
        settings,
        body,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=T.BTN_BACK, callback_data="main_menu")]
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
    settings: Settings,
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
    text = "\n".join(lines) if lines else T.EMPTY_NO_PURCHASES
    await _edit_fmt(
        callback.message,
        settings,
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=T.BTN_BACK, callback_data="main_menu")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "hist_payments")
async def cb_hist_payments(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
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
    text = "\n".join(lines) if lines else T.EMPTY_NO_PAYMENT_REQS
    await _edit_fmt(
        callback.message,
        settings,
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=T.BTN_BACK, callback_data="main_menu")]
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
