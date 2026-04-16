# -*- coding: utf-8 -*-

import json
import os
import re
import threading
import time
from datetime import datetime

from helpers import normalize_text, escape_md

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "prices_channel_cache.json")

SOURCE_CHANNEL_HINT = "@AkhbarDollar"
CHECK_INTERVAL_SECONDS = 1800  # هر نیم ساعت


def prices_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🔄 بروزرسانی / Refresh", "callback_data": "prices:refresh"},
                {"text": "❓ راهنما / Help", "callback_data": "prices:help"},
            ],
        ]
    }


class Plugin:
    def __init__(self, ctx):
        self.ctx = ctx
        self._thread = None
        self._stop = False
        self.data = self._load_data()

    def on_startup(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="prices-watch")
        self._thread.start()

    def _loop(self):
        while not self._stop:
            try:
                self.data["last_checked_at"] = int(time.time())
                self._save()
            except Exception as e:
                self.ctx.api.log("[PRICES] LOOP ERROR", repr(e))
            time.sleep(CHECK_INTERVAL_SECONDS)

    def _load_data(self):
        if not os.path.exists(DATA_FILE):
            return {"latest": None, "last_checked_at": 0}
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"latest": None, "last_checked_at": 0}

    def _save(self):
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)

    def _is_price_post(self, text: str):
        t = normalize_text(text)
        return "قیمت لحظه‌ای دلار" in t and "قیمت طلا" in t and "سکه" in t

    def _extract_field(self, text: str, label: str):
        pattern = rf"{re.escape(label)}\s*=?\s*([^\n\r]+)"
        m = re.search(pattern, text)
        return normalize_text(m.group(1)) if m else ""

    def _parse_post(self, text: str):
        t = text or ""
        result = {
            "raw_text": t,
            "captured_at": int(time.time()),
            "header": self._extract_field(t, "⏰️") or self._extract_field(t, "⏰"),
            "dollar": self._extract_field(t, "💵 قیمت دلار"),
            "ounce": self._extract_field(t, "🌕 اونس جهانی طلا"),
            "gold18": self._extract_field(t, "🌕 قیمت طلا 18 عیار"),
            "gold24": self._extract_field(t, "🌕 قیمت طلا 24 عیار"),
            "sekke_emami": self._extract_field(t, "🌕 سکه امامی"),
            "sekke_bahar": self._extract_field(t, "🌕 سکه تمام بهار آزادی"),
            "nim_sekke": self._extract_field(t, "🌕 نیم سکه"),
            "rob_sekke": self._extract_field(t, "🌕 ربع سکه"),
            "silver999": self._extract_field(t, "🪙 نقره(عیار 999)"),
            "silver925": self._extract_field(t, "🪙 نقره(عیار 925)"),
        }
        return result

    def _format_latest(self):
        latest = self.data.get("latest")
        if not latest:
            return (
                "💰 *قیمت‌ها / Prices*\n\n"
                "هنوز هیچ پیام معتبری از کانال منبع دریافت نشده است.\n\n"
                f"🔹 کانال منبع: `{escape_md(SOURCE_CHANNEL_HINT)}`\n"
                "ربات باید به کانال اضافه شود تا *آخرین پیام قیمت* را ذخیره کند."
            )

        lines = [
            "💰 *قیمت‌های لحظه‌ای دلار، طلا و سکه*",
            "",
        ]

        if latest.get("header"):
            lines.append(f"⏰ *زمان پیام منبع:* {escape_md(latest['header'])}")
            lines.append("")

        mapping = [
            ("💵 *قیمت دلار*", latest.get("dollar")),
            ("🌕 *اونس جهانی طلا*", latest.get("ounce")),
            ("🌕 *طلا ۱۸ عیار*", latest.get("gold18")),
            ("🌕 *طلا ۲۴ عیار*", latest.get("gold24")),
            ("🌕 *سکه امامی*", latest.get("sekke_emami")),
            ("🌕 *سکه تمام بهار آزادی*", latest.get("sekke_bahar")),
            ("🌕 *نیم‌سکه*", latest.get("nim_sekke")),
            ("🌕 *ربع‌سکه*", latest.get("rob_sekke")),
            ("🪙 *نقره 999*", latest.get("silver999")),
            ("🪙 *نقره 925*", latest.get("silver925")),
        ]

        for label, value in mapping:
            if value:
                lines.append(f"{label}: {escape_md(value)}")

        lines.append("")
        lines.append(f"📡 *منبع:* آخرین پیام کانال `{escape_md(SOURCE_CHANNEL_HINT)}`")
        return "\n".join(lines)

    def _help_text(self):
        return (
            "💰 *راهنمای قیمت‌ها / Prices Help*\n\n"
            "• `قیمت`\n"
            "• `prices`\n"
            "• `دلار`\n"
            "• `طلا`\n"
            "• `راهنمای قیمت`\n"
            "• `price help`\n\n"
            "🔹 این پلاگین قیمت‌ها را از *آخرین پیام کانال منبع* می‌خواند، نه از سایت."
        )

    def on_channel_post(self, message):
        text = message.get("text") or message.get("caption") or ""
        if not self._is_price_post(text):
            return False

        chat = message.get("chat") or {}
        username = normalize_text(chat.get("username") or "")
        title = normalize_text(chat.get("title") or "")

        # اگر از همان کانال باشد یا الگوی پیام معتبر باشد، ذخیره می‌کنیم
        if username and username.lower() == SOURCE_CHANNEL_HINT.lstrip("@").lower():
            pass
        elif not self._is_price_post(text):
            return False

        parsed = self._parse_post(text)
        parsed["channel_username"] = username
        parsed["channel_title"] = title
        parsed["message_id"] = message.get("message_id")
        parsed["received_at"] = int(time.time())

        self.data["latest"] = parsed
        self._save()
        self.ctx.api.log("[PRICES] CHANNEL POST CACHED", parsed.get("header"))
        return True

    def on_message(self, message):
        text = normalize_text(message.get("text") or "").lower()
        chat_id = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")

        if text in ("قیمت", "prices", "/prices", "دلار", "طلا"):
            self.ctx.api.send_message(
                chat_id,
                self._format_latest(),
                reply_to_message_id=message_id,
                reply_markup=prices_keyboard(),
            )
            return True

        if text in ("راهنمای قیمت", "price help"):
            self.ctx.api.send_message(
                chat_id,
                self._help_text(),
                reply_to_message_id=message_id,
                reply_markup=prices_keyboard(),
            )
            return True

        return False

    def on_callback_query(self, callback_query):
        data = callback_query.get("data") or ""
        if not data.startswith("prices:"):
            return False

        cq_id = callback_query.get("id")
        msg = callback_query.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        message_id = msg.get("message_id")

        if data == "prices:help":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                self._help_text(),
                reply_markup=prices_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "راهنمای قیمت باز شد ✨")
            return True

        if data == "prices:refresh":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                self._format_latest(),
                reply_markup=prices_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "آخرین قیمت ذخیره‌شده نمایش داده شد ✅")
            return True

        return False