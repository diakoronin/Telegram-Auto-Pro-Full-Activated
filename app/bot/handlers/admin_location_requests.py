"""Owner/manager: approve or reject pending location change requests."""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts_fa as T
from app.bot.filters import IsManagerOrOwner
from app.config import Settings
from app.db.models import (
    Admin,
    AdminRole,
    LocationChangeRequest,
    LocationChangeRequestStatus,
    Server,
    User,
    UserService,
)
from app.message_format import format_message
from app.services.audit import write_audit
from app.services.location_migration import migrate_user_service_location
from app.services.wallet import deduct_location_change_fee, refund_location_change_fee

logger = logging.getLogger(__name__)

router = Router(name="admin_location_requests")
router.callback_query.filter(IsManagerOrOwner())


def _fmt(settings: Settings, text: str) -> str:
    return format_message(settings, text)


@router.callback_query(F.data == "adm:locreqs")
async def cb_locreqs_list(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    r = await session.execute(
        select(LocationChangeRequest, UserService, User, Server)
        .join(UserService, UserService.id == LocationChangeRequest.user_service_id)
        .join(User, User.id == LocationChangeRequest.user_id)
        .join(Server, Server.id == LocationChangeRequest.target_server_id)
        .where(LocationChangeRequest.status == LocationChangeRequestStatus.PENDING)
        .order_by(LocationChangeRequest.id.desc())
        .limit(20)
    )
    rows = r.all()
    if not rows:
        await callback.answer("درخواست معلقی نیست.", show_alert=True)
        return
    lines = []
    kb = []
    for req, us, u, tgt in rows:
        lines.append(
            f"#{req.id} svc={us.public_service_code} → {tgt.name} fee={req.fee_amount} tg={u.telegram_id}"
        )
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"✅ تایید #{req.id}",
                    callback_data=f"adm:locapp:{req.id}",
                ),
                InlineKeyboardButton(
                    text=f"❌ رد #{req.id}",
                    callback_data=f"adm:locrej:{req.id}",
                ),
            ]
        )
    kb.append([InlineKeyboardButton(text=T.ADM_BACK, callback_data="adm:cat_mgmt")])
    await callback.message.edit_text(
        _fmt(settings, "🌍 درخواست‌های تغییر لوکیشن (در انتظار):\n\n" + "\n".join(lines)),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:locrej:"))
async def cb_locrej(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    admin: Admin,
) -> None:
    rid = int(callback.data.split(":")[-1])
    req = await session.get(LocationChangeRequest, rid)
    if req is None or req.status != LocationChangeRequestStatus.PENDING:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    req.status = LocationChangeRequestStatus.REJECTED
    u = await session.get(User, req.user_id)
    fee = int(req.fee_amount or 0)
    if fee > 0 and u:
        await refund_location_change_fee(
            session,
            user=u,
            fee=fee,
            original_tx_id=req.wallet_transaction_id,
            reason="location_change_rejected_refund",
        )
    await write_audit(
        session,
        actor_telegram_id=callback.from_user.id,
        actor_role=admin.role.value,
        action="location_change_rejected",
        target_type="location_change_request",
        target_id=str(req.id),
    )
    await session.flush()
    if u:
        try:
            await callback.bot.send_message(
                int(u.telegram_id),
                _fmt(settings, f"❌ درخواست تغییر لوکیشن #{req.id} رد شد."),
            )
        except Exception:
            logger.exception("notify user loc reject")
    await callback.answer("رد شد.")
    callback.data = "adm:locreqs"
    await cb_locreqs_list(callback, session, settings)


@router.callback_query(F.data.startswith("adm:locapp:"))
async def cb_locapp(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    admin: Admin,
) -> None:
    if admin.role == AdminRole.SELLER:
        await callback.answer(T.UNAUTHORIZED, show_alert=True)
        return
    rid = int(callback.data.split(":")[-1])
    req = await session.get(LocationChangeRequest, rid)
    if req is None or req.status != LocationChangeRequestStatus.PENDING:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    us = await session.get(UserService, req.user_service_id)
    tgt = await session.get(Server, req.target_server_id)
    user = await session.get(User, req.user_id)
    if us is None or tgt is None or user is None:
        await callback.answer("داده ناقص است.", show_alert=True)
        return

    fee_amt = int(req.fee_amount or 0)
    if fee_amt > 0 and req.wallet_transaction_id is None:
        wt, err = await deduct_location_change_fee(
            session,
            user=user,
            fee=fee_amt,
            reason=f"location_change_fee_req_{req.id}",
        )
        if err:
            await callback.answer(err, show_alert=True)
            return
        if wt:
            req.wallet_transaction_id = wt.id
            await session.flush()

    ok, err = await migrate_user_service_location(
        session,
        settings=settings,
        us=us,
        target_server=tgt,
        bot=callback.bot,
        request_id=req.request_id,
        fee_amount=fee_amt,
        fee_wallet_tx_id=req.wallet_transaction_id,
    )
    if not ok:
        if fee_amt > 0 and user:
            await refund_location_change_fee(
                session,
                user=user,
                fee=fee_amt,
                original_tx_id=req.wallet_transaction_id,
                reason="location_change_migrate_failed_refund",
            )
        await callback.answer(err or "ناموفق", show_alert=True)
        return
    req.status = LocationChangeRequestStatus.COMPLETED
    await write_audit(
        session,
        actor_telegram_id=callback.from_user.id,
        actor_role=admin.role.value,
        action="location_change_approved",
        target_type="location_change_request",
        target_id=str(req.id),
    )
    await session.flush()
    try:
        await callback.bot.send_message(
            int(user.telegram_id),
            _fmt(settings, f"✅ درخواست تغییر لوکیشن #{req.id} تایید و انجام شد."),
        )
    except Exception:
        logger.exception("notify user loc approve")
    await callback.answer("انجام شد.")
    callback.data = "adm:locreqs"
    await cb_locreqs_list(callback, session, settings)
