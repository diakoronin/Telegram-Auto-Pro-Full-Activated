from aiogram import Router

from app.bot.handlers import admin, callbacks, user


def setup_routers() -> Router:
    root = Router()
    root.include_router(user.router)
    root.include_router(admin.admin_cf_router)
    root.include_router(admin.router)
    root.include_router(callbacks.router)
    return root
