"""FSM states."""

from aiogram.fsm.state import State, StatesGroup


class PurchaseStates(StatesGroup):
    choosing_server = State()
    choosing_plan = State()
    custom_name = State()
    confirm = State()


class WalletStates(StatesGroup):
    amount = State()


class SupportStates(StatesGroup):
    message = State()


class ManualImportStates(StatesGroup):
    waiting_text = State()


class AdminTicketStates(StatesGroup):
    reply = State()
