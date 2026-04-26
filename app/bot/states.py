from aiogram.fsm.state import State, StatesGroup


class ChargeStates(StatesGroup):
    waiting_amount = State()
    invoice_review = State()
    waiting_receipt = State()


class SupportStates(StatesGroup):
    pick_service = State()
    waiting_message = State()


class PurchaseStates(StatesGroup):
    waiting_custom_name = State()


class AdminStates(StatesGroup):
    add_server_name = State()
    add_plan_name = State()
    add_plan_display_name = State()
    add_plan_price = State()
    import_links_paste = State()
    add_card_number = State()
    add_card_holder = State()
    add_card_bank = State()
    wallet_user_id = State()
    wallet_amount = State()
    wallet_reason = State()
    manual_deliver_customer = State()
    reject_reason = State()
    refund_purchase_id = State()
    refund_reason = State()
    refund_return = State()
    remove_admin_tid = State()
    return_link_id = State()
    add_admin_tid = State()
    add_admin_role = State()
    unblock_user_tid = State()
    edit_card_holder = State()
    edit_card_bank = State()
    card_access_forward_wait = State()
    card_access_tid_wait = State()
    card_access_revoke_tid = State()
    ticket_reply_text = State()
