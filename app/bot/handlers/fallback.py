"""Last-resort handlers so updates never end with silence for the user."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message

from app import texts_fa as T
from app.config import Settings
from app.message_format import format_message

router = Router(name="fallback")


@router.callback_query()
async def cb_unknown(callback: CallbackQuery, settings: Settings) -> None:
    await callback.answer(
        format_message(settings, T.FALLBACK_UNKNOWN_CALLBACK_UX), show_alert=True
    )


@router.message(F.chat.type == ChatType.PRIVATE, F.text, ~F.text.startswith("/"))
async def fallback_private_plain_text(message: Message, settings: Settings) -> None:
    await message.answer(format_message(settings, T.FALLBACK_UNKNOWN_MESSAGE))


@router.message(F.chat.type == ChatType.PRIVATE, F.text.startswith("/"))
async def fallback_private_unknown_command(message: Message, settings: Settings) -> None:
    await message.answer(format_message(settings, T.FALLBACK_UNKNOWN_MESSAGE))
