from helpers import normalize_text
from db import get_24h_group_stats
from plugins.stats_plugin import format_stats_message, stats_keyboard


def help_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "📚 کلاس‌ها", "callback_data": "help:classes"},
                {"text": "🎮 بازی‌ها", "callback_data": "help:games"},
            ],
            [
                {"text": "📊 آمار", "callback_data": "help:stats"},
                {"text": "🎯 سالمی", "callback_data": "help:salemi"},
            ],
            [
                {"text": "📝 راهنمای دستورها", "callback_data": "help:commands"},
            ],
        ]
    }


def classes_menu_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "📚 برنامه هفتگی", "callback_data": "class:home"},
                {"text": "⚙️ مدیریت کلاس‌ها", "callback_data": "class:manage"},
            ],
            [
                {"text": "🔙 بازگشت", "callback_data": "help:home"},
            ],
        ]
    }


def salemi_menu_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "📋 وضعیت سالمی", "callback_data": "salemi:status"},
                {"text": "❓ راهنمای سالمی", "callback_data": "salemi:help"},
            ],
            [
                {"text": "🔙 بازگشت", "callback_data": "help:home"},
            ],
        ]
    }


def games_menu_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🎮 ورود به لیست بازی‌ها", "callback_data": "games:menu"},
            ],
            [
                {"text": "🔙 بازگشت", "callback_data": "help:home"},
            ],
        ]
    }


class Plugin:
    def __init__(self, ctx):
        self.ctx = ctx

    def _home_text(self):
        return (
            "✨ *راهنمای ربات / Bot Menu*\n\n"
            "به‌جای راهنمای خشک و دستورمحور، از دکمه‌های زیر وارد *خود بخش‌ها* شو 👇\n\n"
            "• *کلاس‌ها*\n"
            "• *بازی‌ها*\n"
            "• *آمار*\n"
            "• *سالمی*\n\n"
            "_و اگر خواستی فهرست همه‌ی دستورها را هم ببینی، دکمه‌ی راهنمای دستورها پایین قرار دارد._"
        )

    def _commands_text(self):
        return (
            "📝 *راهنمای دستورها / Commands Help*\n\n"
            "📚 *کلاس‌ها*\n"
            "• `کلاس‌ها` / `classes`\n"
            "• `کلاس امروز` / `today classes`\n"
            "• `کلاس فردا` / `tomorrow classes`\n\n"
            "🎮 *بازی‌ها*\n"
            "• `بازی‌ها` / `games`\n"
            "• `XO` / `OX`\n"
            "• `سنگ کاغذ قیچی`\n"
            "• `اسم فامیل`\n\n"
            "📊 *آمار*\n"
            "• `آمار`\n"
            "• `stats`\n"
            "• `/stats`\n\n"
            "🎯 *سالمی*\n"
            "• `وضعیت سالمی`\n"
            "• `راهنمای سالمی`\n\n"
            "💡 ولی پیشنهاد اصلی من: *از دکمه‌ها استفاده کن*، چون هم تمیزترند هم سریع‌تر."
        )

    def on_message(self, message):
        text = normalize_text(message.get("text") or "").lower()
        chat_id = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")

        if text in ("/help", "help", "راهنما", "کمک"):
            self.ctx.api.send_message(
                chat_id,
                self._home_text(),
                reply_to_message_id=message_id,
                reply_markup=help_keyboard(),
            )
            return True

        return False

    def on_callback_query(self, callback_query):
        data = callback_query.get("data") or ""
        if not data.startswith("help:"):
            return False

        cq_id = callback_query.get("id")
        msg = callback_query.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        chat_title = chat.get("title") or ""
        message_id = msg.get("message_id")

        if data == "help:home":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                self._home_text(),
                reply_markup=help_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "منوی اصلی راهنما باز شد ✅")
            return True

        if data == "help:commands":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                self._commands_text(),
                reply_markup=help_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "راهنمای دستورها باز شد ✅")
            return True

        if data == "help:classes":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                "📚 *ورود به بخش کلاس‌ها*\n\n"
                "از اینجا وارد *خودِ بخش کلاس‌ها* می‌شوی، نه راهنمای آن.\n"
                "برای ادامه از دکمه‌های زیر استفاده کن 👇",
                reply_markup=classes_menu_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "وارد بخش کلاس‌ها شدی ✅")
            return True

        if data == "help:games":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                "🎮 *ورود به بخش بازی‌ها*\n\n"
                "از اینجا وارد *لیست بازی‌ها* می‌شوی، نه راهنمای آن.\n"
                "برای ادامه دکمه‌ی زیر را بزن 👇",
                reply_markup=games_menu_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "وارد بخش بازی‌ها شدی ✅")
            return True

        if data == "help:stats":
            rows = get_24h_group_stats(chat_id)
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                format_stats_message(chat_title or "", rows),
                reply_markup=stats_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "وارد بخش آمار شدی ✅")
            return True

        if data == "help:salemi":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                "🎯 *ورود به بخش سالمی*\n\n"
                "از اینجا می‌توانی وضعیت یا راهنمای سالمی را باز کنی 👇",
                reply_markup=salemi_menu_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "وارد بخش سالمی شدی ✅")
            return True

        return False