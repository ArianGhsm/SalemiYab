import re
import time
import requests

from db import (
    DB_FILE,
    JSON_BACKUP_FILE,
    init_db,
    add_or_keep_target,
    set_target_reply_text,
    set_target_reply_gif_file,
    get_targets,
    delete_target,
    delete_all_targets,
    set_pending_owner_input,
    get_pending_owner_input,
    clear_pending_owner_input,
    upsert_group,
    add_message_log,
)
from plugins.stats_plugin import send_group_24h_stats, run_startup_stats_for_all_groups


def log(*args):
    print(*args, flush=True)


def normalize_text(s: str) -> str:
    s = (s or "").strip()
    s = s.replace("ي", "ی").replace("ك", "ک")
    s = s.replace("\u200c", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def normalize_match_text(s: str) -> str:
    s = normalize_text(s).lower()
    s = re.sub(r"[^\w\u0600-\u06FF ]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def build_name_candidates(user: dict):
    first_name = normalize_match_text(user.get("first_name") or "")
    last_name = normalize_match_text(user.get("last_name") or "")
    username = normalize_match_text(user.get("username") or "")

    full_name = normalize_match_text(f"{first_name} {last_name}")
    candidates = set()

    if first_name:
        candidates.add(first_name)
    if last_name:
        candidates.add(last_name)
    if full_name:
        candidates.add(full_name)
    if username:
        candidates.add(username)
        candidates.add(username.lstrip("@"))
    if first_name and last_name:
        candidates.add(normalize_match_text(first_name + last_name))

    return sorted(c for c in candidates if c)


def name_matches(saved_name: str, user: dict) -> bool:
    saved = normalize_match_text(saved_name)
    if not saved:
        return False

    candidates = build_name_candidates(user)
    for c in candidates:
        if saved == c:
            return True
        if saved in c:
            return True
        if c in saved:
            return True
    return False


def get_best_display_name(user: dict) -> str:
    first_name = normalize_text(user.get("first_name") or "")
    last_name = normalize_text(user.get("last_name") or "")
    username = normalize_text(user.get("username") or "")

    full_name = normalize_text(f"{first_name} {last_name}")
    if full_name:
        return full_name
    if first_name:
        return first_name
    if username:
        return username
    return "کاربر"


def extract_gif_file_id_from_message(message: dict):
    animation = message.get("animation")
    if isinstance(animation, dict):
        file_id = animation.get("file_id") or animation.get("id")
        if file_id:
            return file_id

    document = message.get("document")
    if isinstance(document, dict):
        mime_type = (document.get("mime_type") or "").lower()
        file_name = (document.get("file_name") or "").lower()
        file_id = document.get("file_id") or document.get("id")

        if file_id and (
            "gif" in mime_type or
            file_name.endswith(".gif") or
            mime_type == "video/mp4"
        ):
            return file_id

    return None


def fetch_url(url: str):
    res = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    res.raise_for_status()
    return res.text


def get_dollar_price():
    html = fetch_url("https://bonbast.com/")
    m = re.search(r"US Dollar\s*/\s*IRR.*?([\d,]{4,})", html, re.I | re.S)
    if not m:
        raise Exception("قیمت دلار پیدا نشد")
    return m.group(1) + " تومان"


def get_gold_price():
    html = fetch_url("https://bonbast.com/")
    m = re.search(r"Gold\s*\^\{Gram\}.*?([\d,]{4,})", html, re.I | re.S)
    if not m:
        raise Exception("قیمت طلای گرمی پیدا نشد")
    return m.group(1) + " تومان"


def get_coin_prices():
    emami_html = fetch_url("https://www.tgju.org/profile/sekee")
    rob_html = fetch_url("https://www.tgju.org/profile/rob")

    m1 = re.search(r"قیمت سکه امامی.*?برابر با\s*([\d,]{4,})\s*ریال", emami_html, re.I | re.S)
    if not m1:
        m1 = re.search(r"سکه امامی.*?([\d,]{4,})\s*ریال", emami_html, re.I | re.S)

    m2 = re.search(r"قیمت ربع سکه.*?برابر با\s*([\d,]{4,})\s*ریال", rob_html, re.I | re.S)
    if not m2:
        m2 = re.search(r"ربع سکه.*?([\d,]{4,})\s*ریال", rob_html, re.I | re.S)

    emami = m1.group(1) + " ریال" if m1 else None
    rob = m2.group(1) + " ریال" if m2 else None

    if not emami and not rob:
        raise Exception("قیمت سکه پیدا نشد")

    return emami, rob


class BotApp:
    def __init__(self, api, owner_id: int):
        self.api = api
        self.owner_id = owner_id
        self.offset = None

    def startup(self):
        init_db(log)
        log("[LOG] BOT STARTED")
        log("[LOG] DB FILE", DB_FILE)
        log("[LOG] JSON BACKUP FILE", JSON_BACKUP_FILE)
        run_startup_stats_for_all_groups(self.api, log)

    def log_group_and_message(self, message: dict):
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

            add_message_log(chat_id, user_id, display_name, username, text, created_at)

    def handle_owner_private(self, message: dict):
        chat = message.get("chat", {})
        chat_id = chat.get("id")

        if chat_id != self.owner_id:
            return

        pending = get_pending_owner_input(self.owner_id)
        if not pending:
            return

        target_group_id, target_name = pending

        gif_file_id = extract_gif_file_id_from_message(message)
        if gif_file_id:
            set_target_reply_gif_file(target_group_id, target_name, gif_file_id)
            clear_pending_owner_input(self.owner_id)
            self.api.send_message(self.owner_id, f"گیف «{target_name}» ذخیره شد.\nگروه: {target_group_id}")
            return

        text = normalize_text(message.get("text") or "")
        if not text:
            self.api.send_message(
                self.owner_id,
                "برای این شخص یا متن بفرست، یا خود GIF را فوروارد/ارسال کن."
            )
            return

        set_target_reply_text(target_group_id, target_name, text)
        clear_pending_owner_input(self.owner_id)
        self.api.send_message(self.owner_id, f"پیام «{target_name}» ذخیره شد.\nگروه: {target_group_id}")

    def handle_group_commands(self, message: dict):
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        text = normalize_text(message.get("text") or "")
        message_id = message.get("message_id")
        chat_title = chat.get("title") or ""

        if chat_type not in ("group", "supergroup"):
            return True

        if not text:
            return False

        if text.startswith("پیام "):
            typed_name = normalize_text(text[len("پیام "):])
            replied = message.get("reply_to_message")

            if replied and replied.get("from"):
                real_name = get_best_display_name(replied.get("from", {}))
                target_name = normalize_text(real_name or typed_name)
            else:
                target_name = typed_name

            if not target_name:
                self.api.send_message(chat_id, "بعد از «پیام» اسم طرف را بنویس.", message_id)
                return True

            add_or_keep_target(chat_id, target_name)
            set_pending_owner_input(self.owner_id, chat_id, target_name)

            self.api.send_message(chat_id, f"{target_name} تعیین شد", message_id)
            self.api.send_message(
                self.owner_id,
                f"برای «{target_name}» در گروه {chat_id} چه چیزی ذخیره کنم؟\n"
                f"- متن ساده بفرست\n"
                f"- یا خود GIF را فوروارد/ارسال کن\n"
                f"لینک نفرست."
            )
            return True

        if text == "وضعیت سالمی":
            rows = get_targets(chat_id)
            if not rows:
                self.api.send_message(chat_id, "هنوز کسی تعیین نشده.", message_id)
                return True

            lines = ["لیست سالمی‌ها:"]
            for i, row in enumerate(rows, start=1):
                name, reply_type, reply_text, reply_file_id = row

                if reply_type == "text":
                    desc = reply_text or "هنوز پیامی تعیین نشده"
                elif reply_type == "gif":
                    desc = "GIF ذخیره شده" if reply_file_id else "GIF بدون داده"
                else:
                    desc = "نامشخص"

                lines.append(f"{i}) {name} -> {desc}")

            lines.append("")
            lines.append("دستورها:")
            lines.append("پیام [اسم]")
            lines.append("حذف سالمی [اسم]")
            lines.append("حذف همه سالمی")
            lines.append("آمار")

            self.api.send_message(chat_id, "\n".join(lines), message_id)
            return True

        if text.startswith("حذف سالمی "):
            target_name = normalize_text(text[len("حذف سالمی "):])
            if not target_name:
                self.api.send_message(chat_id, "بعد از «حذف سالمی» اسم را بنویس.", message_id)
                return True

            ok = delete_target(chat_id, target_name)
            if ok:
                self.api.send_message(chat_id, f"{target_name} حذف شد.", message_id)
            else:
                self.api.send_message(chat_id, f"{target_name} پیدا نشد.", message_id)
            return True

        if text == "حذف همه سالمی":
            count = delete_all_targets(chat_id)
            self.api.send_message(chat_id, f"همه حذف شدند. تعداد: {count}", message_id)
            return True

        if text == "دلار":
            try:
                price = get_dollar_price()
                self.api.send_message(chat_id, f"قیمت دلار آزاد: {price}", message_id)
            except Exception as e:
                self.api.send_message(chat_id, f"خطا در دریافت قیمت دلار: {e}", message_id)
            return True

        if text == "طلا":
            try:
                price = get_gold_price()
                self.api.send_message(chat_id, f"قیمت طلای گرمی: {price}", message_id)
            except Exception as e:
                self.api.send_message(chat_id, f"خطا در دریافت قیمت طلا: {e}", message_id)
            return True

        if text == "سکه":
            try:
                emami, rob = get_coin_prices()
                lines = []
                if emami:
                    lines.append(f"سکه تمام / امامی: {emami}")
                if rob:
                    lines.append(f"ربع سکه: {rob}")
                self.api.send_message(chat_id, "\n".join(lines), message_id)
            except Exception as e:
                self.api.send_message(chat_id, f"خطا در دریافت قیمت سکه: {e}", message_id)
            return True

        if text == "آمار":
            try:
                send_group_24h_stats(self.api, chat_id, chat_title)
            except Exception as e:
                self.api.send_message(chat_id, f"خطا در ساخت آمار: {e}", message_id)
            return True

        return False

    def handle_group_auto_reply(self, message: dict):
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        message_id = message.get("message_id")
        from_user = message.get("from", {})
        text = normalize_text(message.get("text") or "")

        if chat_type not in ("group", "supergroup"):
            return

        if not chat_id or not message_id:
            return

        rows = get_targets(chat_id)
        if not rows:
            return

        log("[LOG] AUTO CHECK", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "from_user": from_user,
            "candidates": build_name_candidates(from_user)
        })

        for row in rows:
            saved_name, reply_type, reply_text, reply_file_id = row
            matched = name_matches(saved_name, from_user)

            log("[LOG] MATCH CHECK", {
                "saved_name": saved_name,
                "matched": matched,
                "reply_text": reply_text if reply_type == "text" else ("GIF" if matched else None)
            })

            if not matched:
                continue

            if reply_type == "text" and reply_text:
                self.api.send_message(chat_id=chat_id, text=reply_text, reply_to_message_id=message_id)
                return

            if reply_type == "gif" and reply_file_id:
                self.api.send_animation(
                    chat_id=chat_id,
                    animation=reply_file_id,
                    reply_to_message_id=message_id,
                    caption=f"خطاب به {saved_name}"
                )
                return

            return

    def handle_update(self, update: dict):
        message = update.get("message")
        if not message:
            return

        self.log_group_and_message(message)

        chat = message.get("chat", {})
        chat_type = chat.get("type")

        if chat_type == "private":
            self.handle_owner_private(message)
            return

        handled = self.handle_group_commands(message)
        if handled:
            return

        self.handle_group_auto_reply(message)

    def run_forever(self, poll_timeout=25):
        self.startup()

        while True:
            try:
                updates = self.api.get_updates(offset=self.offset, timeout=poll_timeout)
                for update in updates:
                    self.offset = update["update_id"] + 1
                    self.handle_update(update)
            except Exception as e:
                log("[LOG] ERROR", repr(e))
                time.sleep(3)