#!/usr/bin/env python3
import asyncio
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


LOGGER = logging.getLogger(__name__)
DB_PATH = os.getenv("DB_PATH", "bot_data.sqlite3")
SEND_DELAY_SECONDS = float(os.getenv("SEND_DELAY_SECONDS", "1.0"))
LINK_PATTERN = re.compile(r"^https?://\S+$", re.IGNORECASE)


@dataclass
class GroupItem:
    chat_id: int
    title: str
    is_active: bool
    is_admin: bool


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS groups (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def set_owner_id(self, owner_id: int) -> None:
        self.set_setting("owner_id", str(owner_id))

    def get_owner_id(self) -> Optional[int]:
        raw_value = self.get_setting("owner_id")
        if raw_value is None:
            return None
        try:
            return int(raw_value)
        except ValueError:
            return None

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            conn.commit()

    def get_setting(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                (key,),
            ).fetchone()
            return row["value"] if row else None

    def upsert_group(
        self,
        chat_id: int,
        title: str,
        is_active: bool,
        is_admin: bool,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO groups(chat_id, title, is_active, is_admin, updated_at)
                VALUES(?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title = excluded.title,
                    is_active = excluded.is_active,
                    is_admin = excluded.is_admin,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, title, int(is_active), int(is_admin)),
            )
            conn.commit()

    def set_group_active(self, chat_id: int, is_active: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE groups
                SET is_active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE chat_id = ?
                """,
                (int(is_active), chat_id),
            )
            conn.commit()

    def set_group_admin(self, chat_id: int, is_admin: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE groups
                SET is_admin = ?, updated_at = CURRENT_TIMESTAMP
                WHERE chat_id = ?
                """,
                (int(is_admin), chat_id),
            )
            conn.commit()

    def list_groups(self, only_active: bool = False) -> list[GroupItem]:
        with self._connect() as conn:
            if only_active:
                rows = conn.execute(
                    """
                    SELECT chat_id, title, is_active, is_admin
                    FROM groups
                    WHERE is_active = 1
                    ORDER BY title COLLATE NOCASE
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT chat_id, title, is_active, is_admin
                    FROM groups
                    ORDER BY title COLLATE NOCASE
                    """
                ).fetchall()
        return [
            GroupItem(
                chat_id=row["chat_id"],
                title=row["title"],
                is_active=bool(row["is_active"]),
                is_admin=bool(row["is_admin"]),
            )
            for row in rows
        ]

    def replace_links(self, links: list[str]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM links")
            conn.executemany(
                "INSERT INTO links(url) VALUES(?)",
                [(url,) for url in links],
            )
            conn.commit()

    def list_links(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT url FROM links ORDER BY id").fetchall()
        return [row["url"] for row in rows]


def parse_links(raw_text: str) -> tuple[list[str], list[str]]:
    valid_links: list[str] = []
    invalid_lines: list[str] = []
    seen: set[str] = set()

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if LINK_PATTERN.match(line):
            if line not in seen:
                valid_links.append(line)
                seen.add(line)
        else:
            invalid_lines.append(line)

    return valid_links, invalid_lines


def get_storage(application: Application) -> Storage:
    return application.bot_data["storage"]


def get_owner_id(application: Application) -> Optional[int]:
    storage = get_storage(application)
    return storage.get_owner_id()


async def owner_required(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    owner_id = get_owner_id(context.application)
    user = update.effective_user
    chat = update.effective_chat

    if user is None:
        return False

    if owner_id is None:
        if chat and chat.type == ChatType.PRIVATE:
            await update.effective_message.reply_text(
                "Owner is not set yet. Send /claim in private chat first."
            )
        return False

    if user.id != owner_id:
        if chat and chat.type == ChatType.PRIVATE:
            await update.effective_message.reply_text("Only owner can run this command.")
        return False

    return True


def build_group_selector(groups: list[GroupItem], selected: set[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for group in groups:
        checked = "✅" if group.chat_id in selected else "⬜"
        admin_mark = "admin" if group.is_admin else "not-admin"
        label = f"{checked} {group.title} ({admin_mark})"
        rows.append(
            [InlineKeyboardButton(label[:64], callback_data=f"sel:toggle:{group.chat_id}")]
        )

    rows.append(
        [
            InlineKeyboardButton("Select all", callback_data="sel:all"),
            InlineKeyboardButton("Clear", callback_data="sel:none"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton("Start send", callback_data="sel:send"),
            InlineKeyboardButton("Cancel", callback_data="sel:cancel"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def get_command_payload(message_text: str) -> str:
    parts = message_text.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat and update.effective_chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text(
            "Bot is running.\n"
            "Use /help to see commands.\n"
            "Use /whoami to get your Telegram user ID."
        )
        return
    await update.effective_message.reply_text(
        "Use /register in this group after adding and promoting the bot."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Commands (private):\n"
        "/claim - set owner (only first time)\n"
        "/whoami - show your user ID\n"
        "/setlinks - paste links one per line\n"
        "/links - show saved links\n"
        "/groups - show saved groups\n"
        "/addgroup <chat_id> <title> - add group manually\n"
        "/removegroup <chat_id> - mark group inactive\n"
        "/refreshadmins - re-check admin status in groups\n"
        "/sendlinks - choose groups and broadcast links\n"
        "/stop - stop active broadcast\n"
        "/cancel - cancel pending text input\n\n"
        "Group command:\n"
        "/register - save this group into bot list"
    )
    await update.effective_message.reply_text(text)


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    await update.effective_message.reply_text(f"Your user ID: {user.id}")


async def claim_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text("Run this command in private chat.")
        return
    if not user:
        return

    storage = get_storage(context.application)
    current_owner = storage.get_owner_id()
    if current_owner is None:
        storage.set_owner_id(user.id)
        await update.effective_message.reply_text(f"Owner set to user id: {user.id}")
        return
    if current_owner == user.id:
        await update.effective_message.reply_text("You are already owner.")
        return
    await update.effective_message.reply_text("Owner already set. You are not allowed.")


async def set_links_from_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw_text: str,
) -> None:
    links, invalid_lines = parse_links(raw_text)
    if invalid_lines:
        invalid_preview = "\n".join(invalid_lines[:10])
        await update.effective_message.reply_text(
            "Invalid lines detected. Only http/https links are accepted.\n"
            f"{invalid_preview}"
        )
        return

    if not links:
        await update.effective_message.reply_text("No valid links found.")
        return

    storage = get_storage(context.application)
    storage.replace_links(links)
    await update.effective_message.reply_text(f"{len(links)} links saved successfully.")


async def setlinks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return

    payload = get_command_payload(update.effective_message.text or "")
    if payload:
        await set_links_from_text(update, context, payload)
        return

    context.user_data["awaiting_links"] = True
    await update.effective_message.reply_text(
        "Send the links now, one per line.\n"
        "Example:\n"
        "https://t.me/channel1\n"
        "https://t.me/channel2"
    )


async def links_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return

    storage = get_storage(context.application)
    links = storage.list_links()
    if not links:
        await update.effective_message.reply_text("No links saved yet.")
        return

    lines = [f"{index + 1}. {link}" for index, link in enumerate(links)]
    await update.effective_message.reply_text("Saved links:\n" + "\n".join(lines))


async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return

    storage = get_storage(context.application)
    groups = storage.list_groups()
    if not groups:
        await update.effective_message.reply_text("No groups saved yet.")
        return

    lines: list[str] = []
    for index, group in enumerate(groups, start=1):
        status = "active" if group.is_active else "inactive"
        admin = "admin" if group.is_admin else "not-admin"
        lines.append(f"{index}. {group.title} | {group.chat_id} | {status} | {admin}")
    await update.effective_message.reply_text("Saved groups:\n" + "\n".join(lines))


async def addgroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return

    parts = (update.effective_message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await update.effective_message.reply_text("Usage: /addgroup <chat_id> <title>")
        return

    try:
        chat_id = int(parts[1])
    except ValueError:
        await update.effective_message.reply_text("chat_id must be an integer.")
        return

    title = parts[2].strip()
    if not title:
        await update.effective_message.reply_text("Group title cannot be empty.")
        return

    storage = get_storage(context.application)
    storage.upsert_group(chat_id=chat_id, title=title, is_active=True, is_admin=False)
    await update.effective_message.reply_text("Group saved.")


async def removegroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return

    parts = (update.effective_message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.effective_message.reply_text("Usage: /removegroup <chat_id>")
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await update.effective_message.reply_text("chat_id must be an integer.")
        return

    storage = get_storage(context.application)
    storage.set_group_active(chat_id, is_active=False)
    await update.effective_message.reply_text("Group marked as inactive.")


async def register_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await update.effective_message.reply_text("Use this command in a group.")
        return
    if not user:
        return

    member = await context.bot.get_chat_member(chat.id, user.id)
    if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        await update.effective_message.reply_text("Only group admins can register this group.")
        return

    bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
    is_bot_admin = bot_member.status == ChatMemberStatus.ADMINISTRATOR

    storage = get_storage(context.application)
    storage.upsert_group(
        chat_id=chat.id,
        title=chat.title or str(chat.id),
        is_active=True,
        is_admin=is_bot_admin,
    )
    await update.effective_message.reply_text("Group registered.")


async def refresh_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return

    storage = get_storage(context.application)
    groups = storage.list_groups(only_active=True)
    if not groups:
        await update.effective_message.reply_text("No active groups to refresh.")
        return

    updated_count = 0
    for group in groups:
        try:
            bot_member = await context.bot.get_chat_member(group.chat_id, context.bot.id)
            is_admin = bot_member.status == ChatMemberStatus.ADMINISTRATOR
            storage.set_group_admin(group.chat_id, is_admin=is_admin)
            updated_count += 1
        except TelegramError:
            storage.set_group_active(group.chat_id, is_active=False)

    await update.effective_message.reply_text(
        f"Checked {len(groups)} groups. Refreshed: {updated_count}."
    )


async def sendlinks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return

    running_task: Optional[asyncio.Task] = context.application.bot_data.get("broadcast_task")
    if running_task and not running_task.done():
        await update.effective_message.reply_text("A broadcast is already running.")
        return

    storage = get_storage(context.application)
    groups = storage.list_groups(only_active=True)
    if not groups:
        await update.effective_message.reply_text("No active groups available.")
        return

    links = storage.list_links()
    if not links:
        await update.effective_message.reply_text("No links saved. Use /setlinks first.")
        return

    selected = {group.chat_id for group in groups}
    context.user_data["selected_groups"] = selected
    context.user_data["selector_group_count"] = len(groups)
    keyboard = build_group_selector(groups, selected)

    await update.effective_message.reply_text(
        f"Choose target groups ({len(selected)}/{len(groups)} selected):",
        reply_markup=keyboard,
    )


async def run_broadcast(
    application: Application,
    owner_chat_id: int,
    group_ids: list[int],
    links: list[str],
    delay_seconds: float,
) -> None:
    storage = get_storage(application)
    groups_by_id = {group.chat_id: group for group in storage.list_groups()}

    sent_ok = 0
    sent_fail = 0
    total = len(group_ids) * len(links)
    failures: list[str] = []

    try:
        for index, group_id in enumerate(group_ids, start=1):
            group_name = groups_by_id.get(group_id, GroupItem(group_id, str(group_id), True, False)).title
            for link in links:
                try:
                    await application.bot.send_message(chat_id=group_id, text=link)
                    sent_ok += 1
                except TelegramError as exc:
                    sent_fail += 1
                    failures.append(f"{group_name} ({group_id}): {exc}")
                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)

            await application.bot.send_message(
                chat_id=owner_chat_id,
                text=f"Finished group {index}/{len(group_ids)}: {group_name}",
            )

        result = f"Broadcast completed.\nSuccess: {sent_ok}\nFailed: {sent_fail}\nTotal: {total}"
        if failures:
            result += "\n\nFirst failures:\n" + "\n".join(failures[:8])
        await application.bot.send_message(chat_id=owner_chat_id, text=result)
    except asyncio.CancelledError:
        await application.bot.send_message(chat_id=owner_chat_id, text="Broadcast stopped.")
        raise
    finally:
        application.bot_data["broadcast_task"] = None


async def selector_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    if not await owner_required(update, context):
        await query.answer("Unauthorized", show_alert=True)
        return

    storage = get_storage(context.application)
    groups = storage.list_groups(only_active=True)
    group_ids = {group.chat_id for group in groups}
    selected = context.user_data.get("selected_groups", set())
    if not isinstance(selected, set):
        selected = set()
    selected = selected & group_ids

    data = query.data
    if data == "sel:all":
        selected = set(group_ids)
    elif data == "sel:none":
        selected = set()
    elif data == "sel:cancel":
        context.user_data.pop("selected_groups", None)
        await query.edit_message_text("Selection canceled.")
        return
    elif data == "sel:send":
        if not selected:
            await query.answer("No groups selected.", show_alert=True)
            return

        links = storage.list_links()
        if not links:
            await query.answer("No links saved.", show_alert=True)
            return

        running_task: Optional[asyncio.Task] = context.application.bot_data.get("broadcast_task")
        if running_task and not running_task.done():
            await query.answer("A broadcast is already running.", show_alert=True)
            return

        owner_chat_id = update.effective_chat.id
        task = asyncio.create_task(
            run_broadcast(
                application=context.application,
                owner_chat_id=owner_chat_id,
                group_ids=sorted(selected),
                links=links,
                delay_seconds=SEND_DELAY_SECONDS,
            )
        )
        context.application.bot_data["broadcast_task"] = task
        await query.edit_message_text(
            f"Broadcast started for {len(selected)} groups and {len(links)} links."
        )
        return
    elif data.startswith("sel:toggle:"):
        try:
            chat_id = int(data.split(":")[2])
        except (IndexError, ValueError):
            await query.answer("Invalid selection")
            return
        if chat_id in selected:
            selected.remove(chat_id)
        else:
            selected.add(chat_id)
    else:
        await query.answer("Unknown action")
        return

    context.user_data["selected_groups"] = selected
    keyboard = build_group_selector(groups, selected)
    await query.edit_message_text(
        f"Choose target groups ({len(selected)}/{len(groups)} selected):",
        reply_markup=keyboard,
    )


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await owner_required(update, context):
        return

    task: Optional[asyncio.Task] = context.application.bot_data.get("broadcast_task")
    if task and not task.done():
        task.cancel()
        await update.effective_message.reply_text("Stop signal sent.")
        return
    await update.effective_message.reply_text("No active broadcast.")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("awaiting_links", None)
    await update.effective_message.reply_text("Canceled.")


async def private_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat or chat.type != ChatType.PRIVATE:
        return
    if not await owner_required(update, context):
        return

    if context.user_data.get("awaiting_links"):
        context.user_data["awaiting_links"] = False
        await set_links_from_text(update, context, update.effective_message.text or "")
        return


async def my_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    status_update = update.my_chat_member
    if status_update is None:
        return
    chat = status_update.chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    new_status = status_update.new_chat_member.status
    is_active = new_status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR)
    is_admin = new_status == ChatMemberStatus.ADMINISTRATOR

    storage = get_storage(context.application)
    storage.upsert_group(
        chat_id=chat.id,
        title=chat.title or str(chat.id),
        is_active=is_active,
        is_admin=is_admin,
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.exception("Unhandled error while processing update", exc_info=context.error)


def build_application() -> Application:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN environment variable is required.")

    storage = Storage(DB_PATH)
    storage.init()

    app = ApplicationBuilder().token(token).build()
    app.bot_data["storage"] = storage
    app.bot_data["broadcast_task"] = None

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("whoami", whoami_command))
    app.add_handler(CommandHandler("claim", claim_command))
    app.add_handler(CommandHandler("setlinks", setlinks_command))
    app.add_handler(CommandHandler("links", links_command))
    app.add_handler(CommandHandler("groups", groups_command))
    app.add_handler(CommandHandler("addgroup", addgroup_command))
    app.add_handler(CommandHandler("removegroup", removegroup_command))
    app.add_handler(CommandHandler("refreshadmins", refresh_admins_command))
    app.add_handler(CommandHandler("sendlinks", sendlinks_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("register", register_group_command))
    app.add_handler(CallbackQueryHandler(selector_callback, pattern=r"^sel:"))
    app.add_handler(
        ChatMemberHandler(
            my_chat_member_handler,
            ChatMemberHandler.MY_CHAT_MEMBER,
        )
    )
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, private_text_handler))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        level=logging.INFO,
    )
    app = build_application()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
