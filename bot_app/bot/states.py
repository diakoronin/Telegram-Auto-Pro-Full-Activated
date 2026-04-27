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


class AddPanelStates(StatesGroup):
    name = State()
    base_url = State()
    web_path = State()
    username = State()
    password = State()
    inbound = State()
    marzban_token = State()
    verify_ssl = State()


class UserSearchStates(StatesGroup):
    query = State()


class UserListStates(StatesGroup):
    page = State()


class BulkCreditStates(StatesGroup):
    user_ids = State()
    amount = State()
    confirm = State()


class BroadcastStates(StatesGroup):
    text = State()
    confirm = State()


class AddCardStates(StatesGroup):
    number = State()
    holder = State()
    bank = State()
    confirm = State()


class C2CTextStates(StatesGroup):
    text = State()
    confirm = State()


class EduAddStates(StatesGroup):
    title = State()
    body = State()


class EduEditStates(StatesGroup):
    id = State()
    title = State()
    body = State()


class EduDeleteStates(StatesGroup):
    id = State()
