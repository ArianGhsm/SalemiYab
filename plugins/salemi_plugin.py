from db import (
    add_or_keep_target,
    set_target_reply_text,
    set_target_reply_gif_file,
    get_targets,
    delete_target,
    delete_all_targets,
    set_pending_owner_input,
    get_pending_owner_input,
    clear_pending_owner_input,
)
from helpers import (
    normalize_text,
    get_best_display_name,
    extract_gif_file_id_from_message,
    name_matches,
    escape_md,
)


def salemi_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "📋 وضعیت", "callback_data": "salemi:status"},
                {"text": "❓ راهنما", "callback_data": "salemi:help"},
            ]
        ]
    }


class Plugin:
    def __init__(self, ctx):
        self.ctx = ctx

    def pretty_help(self):
        return (
            "✨ *راهنمای سالمی / Salemi Help*\n\n"
            "• `پیام [اسم]`\n"
            "• `salemi set [name]`\n"
            "• `وضعیت سالمی`\n"
            "• `salemi status`\n"
            "• `حذف سالمی [اسم]`\n"
            "• `salemi delete [name]`\n"
            "• `حذف همه سالمی`\n"
            "• `salemi clear`\n\n"
            "_نکته:_ برای دقت بیشتر، دستور تنظیم را روی پیام همان شخص ریپلای بزن."
        )

    def _status_text(self, chat_id):
        rows = get_targets(chat_id)
        if not rows:
            return "📭 *هنوز هیچ سالمی‌ای ثبت نشده است.*"

        lines = ["📋 *وضعیت سالمی‌ها / Salemi Status*", ""]
        for i, row in enumerate(rows, start=1):
            name, reply_type, reply_text, reply_file_id = row

            if reply_type == "text":
                desc = f"📝 {escape_md(reply_text or 'هنوز تنظیم نشده')}"
            elif reply_type == "gif":
                desc = "🎞️ GIF ذخیره شده"
            else:
                desc = "❓ نامشخص"

            lines.append(f"*{i})* {escape_md(name)}")
            lines.append(f"   {desc}")
            lines.append("")

        lines.append("🛠️ *دستورهای سریع*")
        lines.append("• `پیام [اسم]` / `salemi set [name]`")
        lines.append("• `حذف سالمی [اسم]` / `salemi delete [name]`")
        lines.append("• `حذف همه سالمی` / `salemi clear`")
        return "\n".join(lines)

    def handle_owner_private(self, message):
        chat = message.get("chat", {})
        chat_id = chat.get("id")

        if chat_id != self.ctx.owner_id:
            return False

        pending = get_pending_owner_input(self.ctx.owner_id)
        if not pending:
            return False

        target_group_id, target_name = pending

        gif_file_id = extract_gif_file_id_from_message(message)
        if gif_file_id:
            set_target_reply_gif_file(target_group_id, target_name, gif_file_id)
            clear_pending_owner_input(self.ctx.owner_id)
            self.ctx.api.send_message(
                self.ctx.owner_id,
                f"✅ *GIF ذخیره شد*\n\n"
                f"• مخاطب: *{escape_md(target_name)}*\n"
                f"• گروه: `{target_group_id}`"
            )
            return True

        text = normalize_text(message.get("text") or "")
        if not text:
            self.ctx.api.send_message(
                self.ctx.owner_id,
                "⚠️ *ورودی نامعتبر بود.*\n\nبرای این شخص یک *متن* یا خودِ *GIF* را ارسال کنید."
            )
            return True

        set_target_reply_text(target_group_id, target_name, text)
        clear_pending_owner_input(self.ctx.owner_id)
        self.ctx.api.send_message(
            self.ctx.owner_id,
            f"✅ *پاسخ ذخیره شد*\n\n"
            f"• مخاطب: *{escape_md(target_name)}*\n"
            f"• گروه: `{target_group_id}`"
        )
        return True

    def handle_group_commands(self, message):
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        text = normalize_text(message.get("text") or "")
        message_id = message.get("message_id")

        if chat_type not in ("group", "supergroup"):
            return False

        if not text:
            return False

        text_low = text.lower()

        if text_low in ("راهنمای سالمی", "salemi help", "/salemi"):
            self.ctx.api.send_message(
                chat_id,
                self.pretty_help(),
                reply_to_message_id=message_id,
                reply_markup=salemi_keyboard(),
            )
            return True

        if text_low.startswith("پیام ") or text_low.startswith("salemi set "):
            if text_low.startswith("پیام "):
                typed_name = normalize_text(text[len("پیام "):])
            else:
                typed_name = normalize_text(text[len("salemi set "):])

            replied = message.get("reply_to_message")
            if replied and replied.get("from"):
                real_name = get_best_display_name(replied.get("from", {}))
                target_name = normalize_text(real_name or typed_name)
            else:
                target_name = typed_name

            if not target_name:
                self.ctx.api.send_message(chat_id, "⚠️ *اسم مشخص نیست.*", reply_to_message_id=message_id)
                return True

            add_or_keep_target(chat_id, target_name)
            set_pending_owner_input(self.ctx.owner_id, chat_id, target_name)

            self.ctx.api.send_message(
                chat_id,
                f"✅ *{escape_md(target_name)}* تعیین شد.\n\n"
                f"📩 حالا متن یا GIF این شخص را در *پیوی ربات* برای من بفرست.",
                reply_to_message_id=message_id,
                reply_markup=salemi_keyboard(),
            )
            self.ctx.api.send_message(
                self.ctx.owner_id,
                f"🧩 *تنظیم پاسخ جدید*\n\n"
                f"• مخاطب: *{escape_md(target_name)}*\n"
                f"• گروه: `{chat_id}`\n\n"
                "حالا یکی از این‌ها را بفرست:\n"
                "• *متن*\n"
                "• یا *GIF*"
            )
            return True

        if text_low in ("وضعیت سالمی", "salemi status"):
            self.ctx.api.send_message(
                chat_id,
                self._status_text(chat_id),
                reply_to_message_id=message_id,
                reply_markup=salemi_keyboard(),
            )
            return True

        if text_low.startswith("حذف سالمی ") or text_low.startswith("salemi delete "):
            if text_low.startswith("حذف سالمی "):
                target_name = normalize_text(text[len("حذف سالمی "):])
            else:
                target_name = normalize_text(text[len("salemi delete "):])

            if not target_name:
                self.ctx.api.send_message(chat_id, "⚠️ بعد از دستور حذف، اسم را هم بنویس.", reply_to_message_id=message_id)
                return True

            ok = delete_target(chat_id, target_name)
            if ok:
                self.ctx.api.send_message(
                    chat_id,
                    f"🗑️ *حذف شد*\n\nمخاطب *{escape_md(target_name)}* از لیست سالمی‌ها حذف شد.",
                    reply_to_message_id=message_id,
                    reply_markup=salemi_keyboard(),
                )
            else:
                self.ctx.api.send_message(
                    chat_id,
                    f"❌ *پیدا نشد*\n\nمخاطب *{escape_md(target_name)}* در لیست نبود.",
                    reply_to_message_id=message_id,
                )
            return True

        if text_low in ("حذف همه سالمی", "salemi clear"):
            count = delete_all_targets(chat_id)
            self.ctx.api.send_message(
                chat_id,
                f"🧹 *پاک‌سازی کامل انجام شد*\n\nتعداد حذف‌شده: *{count}*",
                reply_to_message_id=message_id,
                reply_markup=salemi_keyboard(),
            )
            return True

        return False

    def handle_group_auto_reply(self, message):
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        message_id = message.get("message_id")
        from_user = message.get("from", {})

        if chat_type not in ("group", "supergroup"):
            return False

        rows = get_targets(chat_id)
        if not rows:
            return False

        for row in rows:
            saved_name, reply_type, reply_text, reply_file_id = row
            matched = name_matches(saved_name, from_user)
            if not matched:
                continue

            if reply_type == "text" and reply_text:
                self.ctx.api.send_message(
                    chat_id,
                    f"💬 *خطاب به {escape_md(saved_name)}*\n\n{escape_md(reply_text)}",
                    reply_to_message_id=message_id
                )
                return True

            if reply_type == "gif" and reply_file_id:
                self.ctx.api.send_animation(
                    chat_id,
                    reply_file_id,
                    reply_to_message_id=message_id,
                    caption=f"🎯 *خطاب به {escape_md(saved_name)}*"
                )
                return True

            return True

        return False

    def on_message(self, message):
        if self.handle_owner_private(message):
            return True
        if self.handle_group_commands(message):
            return True
        if self.handle_group_auto_reply(message):
            return True
        return False

    def on_callback_query(self, callback_query):
        data = callback_query.get("data") or ""
        if not data.startswith("salemi:"):
            return False

        cq_id = callback_query.get("id")
        msg = callback_query.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        message_id = msg.get("message_id")

        if data == "salemi:help":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                self.pretty_help(),
                reply_markup=salemi_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "راهنمای سالمی باز شد ✨")
            return True

        if data == "salemi:status":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                self._status_text(chat_id),
                reply_markup=salemi_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "وضعیت سالمی بروزرسانی شد ✅")
            return True

        return False