"""Helpers: outer middlewares receive :class:`Update`, not inner event types."""

from __future__ import annotations

from aiogram.types import CallbackQuery, InaccessibleMessage, Message, TelegramObject, Update


def message_from_event(event: TelegramObject) -> Message | None:
    if isinstance(event, Update):
        return (
            event.message
            or event.edited_message
            or event.business_message
            or event.edited_business_message
        )
    if isinstance(event, Message):
        return event
    return None


def callback_from_event(event: TelegramObject) -> CallbackQuery | None:
    if isinstance(event, Update):
        return event.callback_query
    if isinstance(event, CallbackQuery):
        return event
    return None


def callback_message(event: TelegramObject) -> Message | None:
    """Message attached to callback (for chat context); may be inaccessible."""
    cq = callback_from_event(event)
    if cq is None or cq.message is None:
        return None
    m = cq.message
    if isinstance(m, InaccessibleMessage):
        return None
    return m
