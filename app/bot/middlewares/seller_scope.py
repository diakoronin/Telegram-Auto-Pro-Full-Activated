from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app import texts_fa as T
from app.db.models import Admin, AdminRole


class SellerUserFlowBlockMiddleware(BaseMiddleware):
    """Sellers must not use customer shop/wallet/support; only /admin manual delivery."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        admin: Admin | None = data.get("admin")
        if admin is None or admin.role != AdminRole.SELLER:
            return await handler(event, data)

        if isinstance(event, Message):
            txt = (event.text or "").strip()
            if txt.startswith("/admin"):
                return await handler(event, data)
            await event.answer(
                "فروشنده فقط می‌تواند از دستور /admin برای تحویل دستی لینک استفاده کند."
            )
            return None

        if isinstance(event, CallbackQuery):
            d = event.data or ""
            blocked = (
                d == "shop"
                or d == "wallet"
                or d == "charge"
                or d == "hist_purchases"
                or d == "hist_payments"
                or d == "support"
                or d == "cancel_fsm"
                or d.startswith("buy:")
            )
            if blocked:
                await event.answer(T.UNAUTHORIZED, show_alert=True)
                return None
            return await handler(event, data)

        return await handler(event, data)
