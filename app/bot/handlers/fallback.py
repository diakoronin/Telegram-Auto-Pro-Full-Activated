"""Last-resort handlers so updates never end with silence for the user."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message

from app import texts_fa as T

router = Router(name="fallback")


@router.callback_query()
async def cb_unknown(callback: CallbackQuery) -> None:
    await callback.answer(T.FALLBACK_UNKNOWN_CALLBACK, show_alert=True)


@router.message(F.chat.type == ChatType.PRIVATE, F.text, ~F.text.startswith("/"))
async def fallback_private_plain_text(message: Message) -> None:
    await message.answer(T.FALLBACK_UNKNOWN_MESSAGE)


@router.message(F.chat.type == ChatType.PRIVATE, F.text.startswith("/"))
async def fallback_private_unknown_command(message: Message) -> None:
    await message.answer(T.FALLBACK_UNKNOWN_MESSAGE)
