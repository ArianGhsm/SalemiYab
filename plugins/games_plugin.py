from helpers import normalize_text


def games_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "❌⭕ XO / OX", "callback_data": "games:xo"},
                {"text": "✂️ سنگ‌کاغذ‌قیچی", "callback_data": "games:rps"},
            ],
            [
                {"text": "🧠 اسم‌فامیل", "callback_data": "games:sfamil"},
            ],
            [
                {"text": "❓ راهنمای بازی‌ها", "callback_data": "games:help"},
            ],
        ]
    }


def game_panel_keyboard(back=True):
    rows = [
        [{"text": "🎮 بازگشت به لیست بازی‌ها", "callback_data": "games:menu"}]
    ]
    if not back:
        return {"inline_keyboard": []}
    return {"inline_keyboard": rows}


class Plugin:
    def __init__(self, ctx):
        self.ctx = ctx

    def _menu_text(self):
        return (
            "🎮 *لیست بازی‌ها / Games Menu*\n\n"
            "در حال حاضر این *۳ بازی* در ربات فعال هستند:\n\n"
            "• *XO / OX*\n"
            "• *سنگ‌کاغذ‌قیچی*\n"
            "• *اسم‌فامیل*\n\n"
            "برای ورود به هر بازی، از دکمه‌های زیر استفاده کن 👇"
        )

    def _help_text(self):
        return (
            "📖 *راهنمای کلی بازی‌ها / Games Help*\n\n"
            "هر بازی منو و روند مخصوص خودش را دارد.\n"
            "با زدن دکمه‌ی همان بازی، وارد بخش مربوط به خودش می‌شوی.\n\n"
            "✨ از اینجا دیگر مستقیم وارد *راهنمای بازی* نمی‌شوی؛\n"
            "بلکه وارد *خود منوی بازی* می‌شوی."
        )

    def _xo_text(self):
        return (
            "❌⭕ *بخش XO / OX*\n\n"
            "برای شروع بازی، یکی از بازیکن‌ها این یکی را در گروه بفرستد:\n\n"
            "• `XO`\n"
            "• `OX`\n\n"
            "بعد از آن، بازیکن دوم با دکمه‌ی *من داوطلب هستم 🙋‍♂️* وارد می‌شود."
        )

    def _rps_text(self):
        return (
            "✂️ *بخش سنگ‌کاغذ‌قیچی*\n\n"
            "برای شروع، روی پیام حریف *ریپلای* بزن و یکی از این‌ها را بفرست:\n\n"
            "• `سنگ کاغذ قیچی`\n"
            "• یا از فرم انگلیسی دلخواه خودت در آینده استفاده می‌کنیم\n\n"
            "بعد از آن، حریف باید دعوت را قبول کند."
        )

    def _sfamil_text(self):
        return (
            "🧠 *بخش اسم‌فامیل*\n\n"
            "برای شروع بازی، در گروه بنویس:\n\n"
            "• `اسم فامیل`\n\n"
            "بازیکن‌ها با دکمه وارد می‌شوند و میزبان بازی را آغاز می‌کند."
        )

    def on_message(self, message):
        text = normalize_text(message.get("text") or "").lower()
        chat_id = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")

        if text in ("بازی‌ها", "بازی ها", "games", "game"):
            self.ctx.api.send_message(
                chat_id,
                self._menu_text(),
                reply_to_message_id=message_id,
                reply_markup=games_keyboard(),
            )
            return True

        if text in ("راهنمای بازی", "game help"):
            self.ctx.api.send_message(
                chat_id,
                self._help_text(),
                reply_to_message_id=message_id,
                reply_markup=games_keyboard(),
            )
            return True

        return False

    def on_callback_query(self, callback_query):
        data = callback_query.get("data") or ""
        if not data.startswith("games:"):
            return False

        cq_id = callback_query.get("id")
        msg = callback_query.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        message_id = msg.get("message_id")

        if data == "games:menu":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                self._menu_text(),
                reply_markup=games_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "لیست بازی‌ها باز شد ✅")
            return True

        if data == "games:help":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                self._help_text(),
                reply_markup=games_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "راهنمای بازی‌ها باز شد ✨")
            return True

        if data == "games:xo":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                self._xo_text(),
                reply_markup=game_panel_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "وارد بخش XO شدی ✅")
            return True

        if data == "games:rps":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                self._rps_text(),
                reply_markup=game_panel_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "وارد بخش سنگ‌کاغذ‌قیچی شدی ✅")
            return True

        if data == "games:sfamil":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                self._sfamil_text(),
                reply_markup=game_panel_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "وارد بخش اسم‌فامیل شدی ✅")
            return True

        return False