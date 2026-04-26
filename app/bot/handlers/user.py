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
from app.db.models import Admin, Link, PaymentCard, PaymentRequest, Plan, Purchase, Server, User
from app.bot.states import ChargeStates, PurchaseStates, SupportStates
from app.message_format import format_message, format_money_toman, format_purchase_datetime
from app.services.audit import write_audit
from app.services.cards import card_display_number, pick_public_card_for_invoice
from app.services.links import purchase_plan_for_user
from app.services.plan_display import plan_display_label
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
from app.validation import (
    ValidationError,
    validate_charge_amount,
    validate_custom_service_name,
    is_allowed_receipt_content_type,
)

logger = logging.getLogger(__name__)

router = Router(name="user")


def _fmt(settings: Settings, text: str) -> str:
    return format_message(settings, text)


def _default_service_name(plan: Plan, server: Server) -> str:
    return f"{plan_display_label(plan)} - {server.name}"


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
    await state.clear()
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
    await state.set_state(None)
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
        f"👤 کاربر: {message.from_user.full_name if message.from_user else '—'}\n"
        f"🆔 آیدی عددی: {db_user.telegram_id}\n"
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
    srvs = (
        await session.execute(
            select(Server)
            .where(Server.is_active.is_(True))
            .order_by(Server.id)
        )
    ).scalars().all()
    has_plan = (
        await session.execute(
            select(Plan.id)
            .join(Server, Server.id == Plan.server_id)
            .where(Server.is_active.is_(True), Plan.is_active.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    if not srvs or has_plan is None:
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
    buttons = [
        [InlineKeyboardButton(text=f"🌐 {s.name}", callback_data=f"shop_srv:{s.id}")]
        for s in srvs
    ]
    buttons.append([InlineKeyboardButton(text=T.BTN_BACK, callback_data="main_menu")])
    await _edit_fmt(
        callback.message,
        settings,
        T.SHOP_PICK_SERVER,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shop_srv:"))
async def cb_shop_server(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    sid = int(callback.data.split(":", 1)[1])
    srv = await session.get(Server, sid)
    if srv is None or not srv.is_active:
        await callback.answer("سرور نامعتبر است.", show_alert=True)
        return
    plans = (
        await session.execute(
            select(Plan)
            .where(Plan.server_id == sid, Plan.is_active.is_(True))
            .order_by(Plan.id)
        )
    ).scalars().all()
    if not plans:
        await callback.answer(T.EMPTY_NO_PLANS_SERVER, show_alert=True)
        return
    buttons = []
    for pl in plans:
        n = await count_unused_for_plan(session, server_id=sid, plan_id=pl.id)
        price_s = format_money_toman(int(pl.price))
        label = plan_display_label(pl)
        if n <= 0:
            row_text = f"📦 {label} | {price_s} تومان | ناموجود ❌"
        else:
            row_text = f"📦 {label} | {price_s} تومان | موجودی: {n} ✅"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=row_text[:64],
                    callback_data=f"shop_plan:{sid}:{pl.id}",
                )
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(text=T.BTN_BACK, callback_data="shop"),
        ]
    )
    await _edit_fmt(
        callback.message,
        settings,
        T.SHOP_PICK_PLAN.format(server=srv.name),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shop_plan:"))
async def cb_shop_plan_pick(
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
    _, sid_s, pid_s = callback.data.split(":", 2)
    sid, pid = int(sid_s), int(pid_s)
    srv = await session.get(Server, sid)
    plan = await session.get(Plan, pid)
    if (
        srv is None
        or plan is None
        or not srv.is_active
        or not plan.is_active
        or plan.server_id != sid
    ):
        await callback.answer("پلن یا سرور نامعتبر است.", show_alert=True)
        return
    stock_n = await count_unused_for_plan(session, server_id=sid, plan_id=pid)
    if stock_n <= 0:
        await callback.message.answer(
            _fmt(
                settings,
                T.STOCK_OUT_DETAIL.format(
                    plan=plan_display_label(plan),
                    server=srv.name,
                ),
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=T.BTN_BACK, callback_data=f"shop_srv:{sid}")],
                ]
            ),
        )
        await callback.answer()
        return
    await state.set_state(PurchaseStates.waiting_custom_name)
    await state.update_data(shop_sid=sid, shop_pid=pid)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=T.SHOP_SKIP_NAME, callback_data="shop_name_skip")],
            [InlineKeyboardButton(text=T.BTN_BACK, callback_data=f"shop_srv:{sid}")],
        ]
    )
    await callback.message.answer(_fmt(settings, T.SHOP_ASK_CUSTOM_NAME), reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "shop_name_skip")
async def cb_shop_name_skip(
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
    sid = int(data.get("shop_sid") or 0)
    pid = int(data.get("shop_pid") or 0)
    srv = await session.get(Server, sid)
    plan = await session.get(Plan, pid)
    if (
        srv is None
        or plan is None
        or not srv.is_active
        or not plan.is_active
        or plan.server_id != sid
    ):
        await state.clear()
        await callback.answer("اطلاعات نامعتبر است.", show_alert=True)
        return
    name = _default_service_name(plan, srv)
    await state.update_data(shop_custom_name=name)
    await state.set_state(None)
    stock_n = await count_unused_for_plan(session, server_id=sid, plan_id=pid)
    preview = T.SHOP_PURCHASE_PREVIEW.format(
        service_name=name,
        server=srv.name,
        plan=plan_display_label(plan),
        price=format_money_toman(int(plan.price)),
        stock=stock_n,
        wallet=format_money_toman(int(db_user.wallet_balance)),
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=T.BTN_CONFIRM_BUY, callback_data=f"shop_confirm:{sid}:{pid}")],
            [InlineKeyboardButton(text=T.BTN_CHARGE_FROM_PREVIEW, callback_data="charge")],
            [InlineKeyboardButton(text=T.BTN_BACK, callback_data=f"shop_srv:{sid}")],
        ]
    )
    await callback.message.answer(_fmt(settings, preview), reply_markup=kb)
    await callback.answer()


@router.message(PurchaseStates.waiting_custom_name, F.text)
async def msg_purchase_custom_name(
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
    try:
        name = validate_custom_service_name(message.text or "")
    except ValidationError as e:
        await message.answer(_fmt(settings, e.message_fa))
        return
    data = await state.get_data()
    sid = int(data.get("shop_sid") or 0)
    pid = int(data.get("shop_pid") or 0)
    srv = await session.get(Server, sid)
    plan = await session.get(Plan, pid)
    if (
        srv is None
        or plan is None
        or not srv.is_active
        or not plan.is_active
        or plan.server_id != sid
    ):
        await state.clear()
        await message.answer(_fmt(settings, T.GENERIC_ERROR))
        return
    await state.update_data(shop_custom_name=name)
    await state.set_state(None)
    stock_n = await count_unused_for_plan(session, server_id=sid, plan_id=pid)
    preview = T.SHOP_PURCHASE_PREVIEW.format(
        service_name=name,
        server=srv.name,
        plan=plan_display_label(plan),
        price=format_money_toman(int(plan.price)),
        stock=stock_n,
        wallet=format_money_toman(int(db_user.wallet_balance)),
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=T.BTN_CONFIRM_BUY, callback_data=f"shop_confirm:{sid}:{pid}")],
            [InlineKeyboardButton(text=T.BTN_CHARGE_FROM_PREVIEW, callback_data="charge")],
            [InlineKeyboardButton(text=T.BTN_BACK, callback_data=f"shop_srv:{sid}")],
        ]
    )
    await message.answer(_fmt(settings, preview), reply_markup=kb)


@router.callback_query(F.data.startswith("shop_confirm:"))
async def cb_shop_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    after_commit: list,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    _, sid_s, pid_s = parts
    sid, pid = int(sid_s), int(pid_s)
    srv = await session.get(Server, sid)
    plan = await session.get(Plan, pid)
    if (
        srv is None
        or plan is None
        or not srv.is_active
        or not plan.is_active
        or plan.server_id != sid
    ):
        await callback.answer("پلن یا سرور نامعتبر است.", show_alert=True)
        return
    data = await state.get_data()
    custom = (data.get("shop_custom_name") or "").strip()
    if not custom:
        custom = _default_service_name(plan, srv)
    stock_n = await count_unused_for_plan(session, server_id=sid, plan_id=pid)
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
        session, user=db_user, plan=plan, custom_service_name=custom
    )
    if not ok:
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            actor_role="user",
            action="purchase_failed",
            target_type="plan",
            target_id=str(pid),
            metadata={"error": err, "server_id": sid},
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
    await state.clear()
    await write_audit(
        session,
        actor_telegram_id=callback.from_user.id,
        actor_role="user",
        action="purchase_completed",
        target_type="purchase",
        target_id=str(purchase_id or ""),
        metadata={"custom_service_name": custom, "server_id": sid},
    )
    body = T.PURCHASE_OK_UX.format(
        service_name=custom,
        plan=plan_display_label(plan),
        server=srv.name,
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
            settings.database_url, settings, bot, plan_id=pid
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
        select(Purchase, Plan, Server)
        .join(Plan, Plan.id == Purchase.plan_id)
        .join(Server, Server.id == Purchase.server_id)
        .where(Purchase.user_id == db_user.id)
        .order_by(Purchase.id.desc())
        .limit(15)
    )
    rows = q.all()
    if not rows:
        text = T.EMPTY_NO_PURCHASES
        kb_rows = [[InlineKeyboardButton(text=T.BTN_BACK, callback_data="main_menu")]]
    else:
        blocks = [T.MY_SERVICES_TITLE + "\n"]
        kb_rows: list[list[InlineKeyboardButton]] = []
        for i, (pur, pl, srv) in enumerate(rows, start=1):
            dt_s = format_purchase_datetime(settings, pur.created_at)
            plan_lbl = plan_display_label(pl)
            blocks.append(
                f"\n{i})\n"
                f"📝 نام سرویس: {pur.custom_service_name}\n"
                f"🌐 سرور: {srv.name}\n"
                f"📦 پلن: {plan_lbl}\n"
                f"💵 مبلغ: {format_money_toman(int(pur.amount_paid))} تومان\n"
                f"🧾 شماره سفارش: #{pur.id}\n"
                f"📅 تاریخ خرید: {dt_s}"
            )
            kb_rows.append(
                [
                    InlineKeyboardButton(
                        text=T.BTN_SERVICE_DETAIL,
                        callback_data=f"pur:{pur.id}",
                    )
                ]
            )
        text = "\n".join(blocks)
        kb_rows.append([InlineKeyboardButton(text=T.BTN_BACK, callback_data="main_menu")])
    await _edit_fmt(
        callback.message,
        settings,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pur:"))
async def cb_purchase_detail(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    db_user: User | None = None,
    **kwargs: Any,
) -> None:
    if db_user is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    pid = int(callback.data.split(":", 1)[1])
    r = await session.execute(
        select(Purchase, Plan, Server, Link)
        .join(Plan, Plan.id == Purchase.plan_id)
        .join(Server, Server.id == Purchase.server_id)
        .join(Link, Link.id == Purchase.link_id)
        .where(Purchase.id == pid, Purchase.user_id == db_user.id)
    )
    row = r.one_or_none()
    if row is None:
        await callback.answer(T.GENERIC_ERROR, show_alert=True)
        return
    pur, pl, srv, link = row
    dt_s = format_purchase_datetime(settings, pur.created_at)
    body = (
        f"{T.MY_SERVICES_DETAIL_TITLE}\n\n"
        f"📝 نام سرویس: {pur.custom_service_name}\n"
        f"🌐 سرور: {srv.name}\n"
        f"📦 پلن: {plan_display_label(pl)}\n"
        f"🔗 لینک سرویس:\n{link.link_text}\n"
        f"🧾 شماره سفارش: #{pur.id}\n"
        f"📅 تاریخ خرید: {dt_s}"
    )
    await _edit_fmt(
        callback.message,
        settings,
        body,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=T.BTN_BACK, callback_data="hist_purchases")]
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
