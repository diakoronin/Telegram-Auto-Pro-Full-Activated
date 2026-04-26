from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app import texts_fa as T
from app.bot.middlewares.update_compat import callback_from_event, message_from_event
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

        msg = message_from_event(event)
        if msg is not None:
            txt = (msg.text or "").strip()
            if txt.startswith("/admin"):
                return await handler(event, data)
            # Sellers still need /start, /menu, and /ping like any user.
            if txt.startswith("/start") or txt.startswith("/menu") or txt.startswith("/ping"):
                return await handler(event, data)
            await msg.answer(
                "فروشنده فقط می‌تواند از دستور /admin برای تحویل دستی لینک استفاده کند."
            )
            return None

        cq = callback_from_event(event)
        if cq is not None:
            d = cq.data or ""
            blocked = (
                d == "shop"
                or d == "wallet"
                or d == "charge"
                or d == "hist_purchases"
                or d == "hist_payments"
                or d == "support"
                or d == "support_menu"
                or d == "support_general"
                or d == "support_pick_svc_menu"
                or d.startswith("supmine:")
                or d.startswith("usloccf:")
                or d == "show_cards"
                or d == "cancel_fsm"
                or d.startswith("shop_srv:")
                or d.startswith("shop_plans:")
                or d.startswith("shop_plan:")
                or d == "shop_name_skip"
                or d.startswith("shop_confirm:")
                or d.startswith("pur:")
                or d.startswith("us:")
                or d.startswith("usloc:")
                or d.startswith("ussup:")
            )
            if blocked:
                await cq.answer(T.UNAUTHORIZED, show_alert=True)
                return None
            return await handler(event, data)

        return await handler(event, data)
