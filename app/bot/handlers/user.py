from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts_fa as T
from app.config import Settings
from app.db.models import Admin, PaymentRequest, Plan, Purchase, Server, User
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


def _main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
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
            [InlineKeyboardButton(text=T.BTN_SUPPORT, callback_data="support")],
        ]
    )


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    session: AsyncSession,
    db_user: User,
    admin: Admin | None,
    settings: Settings,
) -> None:
    await write_audit(
        session,
        actor_telegram_id=message.from_user.id if message.from_user else None,
        actor_role=admin.role.value if admin else "user",
        action="user_start",
        target_type="user",
        target_id=str(db_user.id),
    )
    text = T.START_WELCOME + "\n" + T.MENU_USER
    if admin:
        text += "\n\nبرای پنل مدیریت از /admin استفاده کنید."
    await message.answer(text, reply_markup=_main_kb())


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    await message.answer(T.MENU_USER, reply_markup=_main_kb())


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text(T.MENU_USER, reply_markup=_main_kb())
    await callback.answer()


@router.callback_query(F.data == "wallet")
async def cb_wallet(callback: CallbackQuery, db_user: User) -> None:
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
async def cb_charge(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
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
async def cb_cancel_fsm(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(T.MENU_USER, reply_markup=_main_kb())
    await callback.answer()


@router.message(ChargeStates.waiting_amount)
async def charge_amount(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
    settings: Settings,
) -> None:
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
    db_user: User,
    settings: Settings,
    after_commit: list,
) -> None:
    await _finalize_receipt(
        message,
        state,
        session,
        db_user,
        settings,
        after_commit,
        file_id=message.photo[-1].file_id,
        kind="photo",
    )


@router.message(ChargeStates.waiting_receipt, F.document)
async def charge_receipt_doc(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
    settings: Settings,
    after_commit: list,
) -> None:
    doc = message.document
    if not doc or not is_allowed_receipt_content_type(doc.mime_type):
        await message.answer(T.RECEIPT_PROMPT)
        return
    await _finalize_receipt(
        message,
        state,
        session,
        db_user,
        settings,
        after_commit,
        file_id=doc.file_id,
        kind="document",
    )


async def _finalize_receipt(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
    settings: Settings,
    after_commit: list,
    *,
    file_id: str,
    kind: str,
) -> None:
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
    await message.answer(T.CHARGE_SUBMITTED)

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
    db_user: User,
    settings: Settings,
    after_commit: list,
) -> None:
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
async def cb_hist_purchases(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
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
async def cb_hist_payments(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
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
async def cb_support(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User, settings: Settings) -> None:
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
    db_user: User,
) -> None:
    from app.db.models import SupportTicket

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
    await message.answer(T.SUPPORT_SENT, reply_markup=_main_kb())
