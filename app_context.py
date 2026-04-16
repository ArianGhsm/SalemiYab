from config import OWNER_ID
from db import (
    DB_FILE,
    JSON_BACKUP_FILE,
    init_db,
    upsert_group,
    add_message_log,
)
from helpers import get_best_display_name, normalize_text, log
import time


class AppContext:
    def __init__(self, api):
        self.api = api
        self.owner_id = OWNER_ID
        self.offset = None

    def startup(self):
        init_db(log)
        log("[LOG] BOT STARTED")
        log("[LOG] DB FILE", DB_FILE)
        log("[LOG] JSON BACKUP FILE", JSON_BACKUP_FILE)

    def remember_group_and_message(self, message: dict):
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        chat_title = chat.get("title") or chat.get("username") or ""

        if chat_type in ("group", "supergroup") and chat_id:
            upsert_group(chat_id, chat_title, chat_type)

            from_user = message.get("from", {})
            user_id = from_user.get("id")
            display_name = get_best_display_name(from_user)
            username = normalize_text(from_user.get("username") or "")
            text = normalize_text(message.get("text") or "")
            created_at = int(time.time())

            add_message_log(
                chat_id=chat_id,
                user_id=user_id,
                display_name=display_name,
                username=username,
                text=text,
                created_at=created_at,
            )