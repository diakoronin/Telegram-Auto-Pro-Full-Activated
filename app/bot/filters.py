from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import Message, CallbackQuery

from app.db.models import Admin, AdminRole


class IsAdmin(Filter):
    async def __call__(self, event: Message | CallbackQuery, admin: Admin | None) -> bool:
        return admin is not None


class IsOwner(Filter):
    async def __call__(self, event: Message | CallbackQuery, admin: Admin | None) -> bool:
        return admin is not None and admin.role == AdminRole.OWNER


class IsManagerOrOwner(Filter):
    async def __call__(self, event: Message | CallbackQuery, admin: Admin | None) -> bool:
        return admin is not None and admin.role in (
            AdminRole.OWNER,
            AdminRole.MANAGER,
        )


class IsSeller(Filter):
    async def __call__(self, event: Message | CallbackQuery, admin: Admin | None) -> bool:
        return admin is not None and admin.role == AdminRole.SELLER
