from __future__ import annotations

from typing import Any

from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message

from app.db.models import Admin, AdminRole


def _admin_from_data(data: dict[str, Any]) -> Admin | None:
    """Router-level filters may not receive injected kwargs; read admin from dispatcher data."""
    a = data.get("admin")
    return a if isinstance(a, Admin) else None


class IsAdmin(Filter):
    async def __call__(
        self,
        event: Message | CallbackQuery,
        admin: Admin | None = None,
        **data: Any,
    ) -> bool:
        adm = admin if admin is not None else _admin_from_data(data)
        return adm is not None


class IsOwner(Filter):
    async def __call__(
        self,
        event: Message | CallbackQuery,
        admin: Admin | None = None,
        **data: Any,
    ) -> bool:
        adm = admin if admin is not None else _admin_from_data(data)
        return adm is not None and adm.role == AdminRole.OWNER


class IsManagerOrOwner(Filter):
    async def __call__(
        self,
        event: Message | CallbackQuery,
        admin: Admin | None = None,
        **data: Any,
    ) -> bool:
        adm = admin if admin is not None else _admin_from_data(data)
        return adm is not None and adm.role in (
            AdminRole.OWNER,
            AdminRole.MANAGER,
        )


class IsSeller(Filter):
    async def __call__(
        self,
        event: Message | CallbackQuery,
        admin: Admin | None = None,
        **data: Any,
    ) -> bool:
        adm = admin if admin is not None else _admin_from_data(data)
        return adm is not None and adm.role == AdminRole.SELLER
