from aiogram import Router

from app.bot.handlers import (
    admin,
    admin_location_requests,
    admin_panel_commands,
    admin_tickets,
    callbacks,
    card_access,
    fallback,
    user,
)


def setup_routers() -> Router:
    root = Router()
    # User-facing commands first so /start and /menu are not skipped by admin FSM
    # or seller-scope rules on other routers.
    root.include_router(user.router)
    root.include_router(admin.admin_cf_router)
    # Card-access FSM must run before generic admin.message handlers so forwarded
    # messages are not swallowed by unrelated admin states.
    root.include_router(card_access.router)
    root.include_router(admin.router)
    root.include_router(admin_tickets.router)
    root.include_router(admin_location_requests.router)
    root.include_router(admin_panel_commands.router)
    root.include_router(callbacks.router)
    # Catch-all last: unknown text/commands and unknown callback_data.
    root.include_router(fallback.router)
    return root
