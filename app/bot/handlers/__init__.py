from aiogram import Router

from app.bot.handlers import admin, callbacks, card_access, user


def setup_routers() -> Router:
    root = Router()
    root.include_router(admin.admin_cf_router)
    # Card-access FSM must run before generic admin.message handlers so forwarded
    # messages are not swallowed by unrelated admin states.
    root.include_router(card_access.router)
    root.include_router(admin.router)
    root.include_router(user.router)
    root.include_router(callbacks.router)
    return root
