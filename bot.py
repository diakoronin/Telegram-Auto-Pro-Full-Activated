from __future__ import annotations

import html
import logging
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from bot_state import BotState
from config import Settings
from xui_client import CreateParams, XUIClient, XUIError


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("xui-telegram-bot")


def _is_authorized(update: Update, settings: Settings) -> bool:
    if not settings.admin_ids:
        return True
    user = update.effective_user
    if user is None:
        return False
    return user.id in settings.admin_ids


def _format_inbound(inbound: dict[str, Any]) -> str:
    inbound_id = inbound.get("id")
    remark = inbound.get("remark", "-")
    protocol = inbound.get("protocol", "-")
    port = inbound.get("port", "-")
    enable = "on" if inbound.get("enable", True) else "off"
    return f"#{inbound_id} | {remark} | {protocol}:{port} | {enable}"


def _parse_int(value: str, field_name: str, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be integer") from exc

    if minimum is not None and number < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field_name} must be <= {maximum}")
    return number


def _usage_create() -> str:
    return (
        "Usage:\n"
        "/create <inbound_id> <count 1..200> <volume_gb>=0 unlimited <days>=0 unlimited [prefix] [start_index]\n\n"
        "Example:\n"
        "/create 3 10 50 30 user 0\n"
        "/create 3 5 0 0 vip 0"
    )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings):
        await update.message.reply_text("Access denied.")
        return

    state: BotState = context.application.bot_data["state"]
    group_chat_id = state.get_group_chat_id() or settings.default_group_chat_id or "not set"
    text = (
        "X-UI Telegram Bot ready.\n\n"
        "Commands:\n"
        "/inbounds - list inbounds\n"
        "/setgroup <chat_id> - set destination group\n"
        "/group - show current destination group\n"
        "/create <inbound_id> <count> <volume_gb> <days> [prefix] [start_index]\n"
        "/health - test x-ui connection\n\n"
        f"Current group chat id: <code>{html.escape(str(group_chat_id))}</code>"
    )
    await update.message.reply_text(text=text, parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings):
        await update.message.reply_text("Access denied.")
        return
    await update.message.reply_text(_usage_create())


async def set_group_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings):
        await update.message.reply_text("Access denied.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Usage: /setgroup <chat_id>")
        return

    group_chat_id = context.args[0].strip()
    state: BotState = context.application.bot_data["state"]
    state.set_group_chat_id(group_chat_id)
    await update.message.reply_text(f"Group chat id saved: {group_chat_id}")


async def group_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings):
        await update.message.reply_text("Access denied.")
        return

    state: BotState = context.application.bot_data["state"]
    group_chat_id = state.get_group_chat_id() or settings.default_group_chat_id
    if not group_chat_id:
        await update.message.reply_text("Group chat id not set. Use /setgroup <chat_id>")
        return
    await update.message.reply_text(f"Current group chat id: {group_chat_id}")


async def inbounds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings):
        await update.message.reply_text("Access denied.")
        return

    client: XUIClient = context.application.bot_data["xui_client"]
    try:
        inbounds = client.list_inbounds()
    except XUIError as exc:
        await update.message.reply_text(f"Failed to fetch inbounds: {exc}")
        return

    if not inbounds:
        await update.message.reply_text("No inbounds found.")
        return

    lines = ["Available inbounds:"]
    lines.extend(_format_inbound(i) for i in inbounds)
    await update.message.reply_text("\n".join(lines))


def _resolve_target_group_chat_id(settings: Settings, state: BotState) -> str | None:
    return state.get_group_chat_id() or settings.default_group_chat_id


def _build_group_message(created: list[dict[str, Any]], inbound_id: int) -> str:
    lines: list[str] = [
        f"Created {len(created)} service(s) on inbound #{inbound_id}",
        "",
    ]
    for item in created:
        volume_text = "unlimited" if item["volume_gb"] == 0 else f"{item['volume_gb']} GB"
        days_text = "unlimited" if item["days"] == 0 else f"{item['days']} day(s)"
        lines.extend(
            [
                f"Name: {item['email']}",
                f"Volume: {volume_text}",
                f"Time: {days_text}",
                f"Link: {item['link']}",
                "---",
            ]
        )
    return "\n".join(lines)


async def create_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings):
        await update.message.reply_text("Access denied.")
        return

    if len(context.args) < 4:
        await update.message.reply_text(_usage_create())
        return

    try:
        inbound_id = _parse_int(context.args[0], "inbound_id", minimum=1)
        count = _parse_int(context.args[1], "count", minimum=1, maximum=200)
        volume_gb = _parse_int(context.args[2], "volume_gb", minimum=0)
        days = _parse_int(context.args[3], "days", minimum=0)
        prefix = context.args[4].strip() if len(context.args) >= 5 else "user"
        start_index = _parse_int(context.args[5], "start_index", minimum=0) if len(context.args) >= 6 else 0
    except ValueError as exc:
        await update.message.reply_text(f"Invalid input: {exc}")
        return

    await update.message.reply_text(
        f"Creating {count} service(s) on inbound #{inbound_id}..."
    )

    client: XUIClient = context.application.bot_data["xui_client"]
    state: BotState = context.application.bot_data["state"]

    try:
        result = client.create_clients(
            CreateParams(
                inbound_id=inbound_id,
                count=count,
                volume_gb=volume_gb,
                days=days,
                prefix=prefix,
                start_index=start_index,
            )
        )
    except XUIError as exc:
        await update.message.reply_text(f"Failed to create services: {exc}")
        return

    created = result["created"]
    await update.message.reply_text(f"Done. Created {len(created)} service(s).")

    target_chat_id = _resolve_target_group_chat_id(settings, state)
    if not target_chat_id:
        await update.message.reply_text(
            "No destination group set. Use /setgroup <chat_id> to send service list."
        )
        return

    message = _build_group_message(created, inbound_id)
    max_len = 3900
    for idx in range(0, len(message), max_len):
        await context.bot.send_message(chat_id=target_chat_id, text=message[idx : idx + max_len])

    await update.message.reply_text(f"Service list sent to group: {target_chat_id}")


async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings):
        await update.message.reply_text("Access denied.")
        return

    client: XUIClient = context.application.bot_data["xui_client"]
    try:
        inbounds = client.list_inbounds()
    except XUIError as exc:
        await update.message.reply_text(f"X-UI connection failed: {exc}")
        return
    await update.message.reply_text(f"X-UI ok. Inbounds count: {len(inbounds)}")


def build_application(settings: Settings) -> Application:
    app = Application.builder().token(settings.bot_token).build()
    app.bot_data["settings"] = settings
    app.bot_data["state"] = BotState(settings.state_file)
    app.bot_data["xui_client"] = XUIClient(
        base_url=settings.xui_base_url,
        username=settings.xui_username,
        password=settings.xui_password,
        timeout_seconds=settings.request_timeout_seconds,
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("setgroup", set_group_cmd))
    app.add_handler(CommandHandler("group", group_cmd))
    app.add_handler(CommandHandler("inbounds", inbounds_cmd))
    app.add_handler(CommandHandler("create", create_cmd))
    app.add_handler(CommandHandler("health", health_cmd))
    return app


def main() -> None:
    settings = Settings.from_env()
    app = build_application(settings)
    logger.info("Bot starting polling mode.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
