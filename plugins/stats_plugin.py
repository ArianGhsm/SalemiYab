from config import SEND_STARTUP_STATS
from db import get_24h_group_stats, get_24h_group_stats_with_ids, get_all_groups
from helpers import normalize_text, escape_md, build_bale_mention


def format_stats_message(chat_title: str, rows, compact: bool = False):
    header = "📊 *آمار ۲۴ ساعت اخیر / 24h Stats*"
    if chat_title:
        header += f"\n🏷️ *گروه:* {escape_md(chat_title)}"

    if not rows:
        return f"{header}\n\n📭 در ۲۴ ساعت اخیر، *هیچ پیام ثبت‌شده‌ای* برای این گروه ندارم."

    lines = [header, "", "🏆 *رتبه‌بندی اعضا*"]
    limit = 3 if compact else 10

    for i, (name, msg_count) in enumerate(rows[:limit], start=1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "▫️"
        lines.append(f"{medal} *{i})* {escape_md(name)} — *{msg_count}* پیام")

    if compact and len(rows) > limit:
        lines.append("")
        lines.append(f"_و {len(rows) - limit} نفر دیگر…_")

    return "\n".join(lines)


def build_mentions_block(rows_with_ids, limit=8):
    parts = []
    seen = set()

    for user_id, name, _count in rows_with_ids:
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        parts.append(build_bale_mention(user_id, name))
        if len(parts) >= limit:
            break

    return " ".join(parts)


def stats_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🔄 بروزرسانی", "callback_data": "stats:refresh"},
                {"text": "🏆 خلاصه", "callback_data": "stats:compact"},
                {"text": "❓ راهنما", "callback_data": "stats:help"},
            ],
            [
                {"text": "📣 منشن", "callback_data": "stats:mention"},
                {"text": "📌 پین", "callback_data": "stats:pin"},
            ],
        ]
    }


class Plugin:
    def __init__(self, ctx):
        self.ctx = ctx

    def on_startup(self):
        if not SEND_STARTUP_STATS:
            return

        groups = get_all_groups()
        for chat_id, title, chat_type in groups:
            if chat_type not in ("group", "supergroup"):
                continue
            try:
                rows = get_24h_group_stats(chat_id)
                self.ctx.api.send_message(
                    chat_id,
                    format_stats_message(title or "", rows),
                    reply_markup=stats_keyboard(),
                )
            except Exception as e:
                self.ctx.api.log("[LOG] STARTUP STATS ERROR", {"chat_id": chat_id, "error": repr(e)})

    def on_message(self, message):
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        chat_title = chat.get("title") or ""
        message_id = message.get("message_id")
        text = normalize_text(message.get("text") or "").lower()

        if text not in ("آمار", "stats", "/stats"):
            return False

        if chat_type == "private":
            if chat_id != self.ctx.owner_id:
                return True

            groups = get_all_groups()
            if not groups:
                self.ctx.api.send_message(chat_id, "📭 *هنوز هیچ گروهی ثبت نشده است.*")
                return True

            self.ctx.api.send_message(
                chat_id,
                f"📚 *آمار همه گروه‌ها*\n\nتعداد گروه‌های ثبت‌شده: *{len(groups)}*"
            )
            for group_chat_id, title, group_type in groups:
                if group_type not in ("group", "supergroup"):
                    continue
                rows = get_24h_group_stats(group_chat_id)
                self.ctx.api.send_message(chat_id, format_stats_message(title or "", rows))
            return True

        if chat_type in ("group", "supergroup"):
            rows = get_24h_group_stats(chat_id)
            self.ctx.api.send_message(
                chat_id,
                format_stats_message(chat_title or "", rows),
                reply_to_message_id=message_id,
                reply_markup=stats_keyboard(),
            )
            return True

        return False

    def on_callback_query(self, callback_query):
        data = callback_query.get("data") or ""
        if not data.startswith("stats:"):
            return False

        cq_id = callback_query.get("id")
        msg = callback_query.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        chat_title = chat.get("title") or ""
        message_id = msg.get("message_id")

        rows = get_24h_group_stats(chat_id)

        if data == "stats:help":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                "📊 *راهنمای آمار / Stats Help*\n\n"
                "• `آمار`\n"
                "• `stats`\n"
                "• `/stats`\n\n"
                "🔄 *بروزرسانی* → تازه‌سازی همین پیام\n"
                "🏆 *خلاصه* → نمایش جمع‌وجورتر\n"
                "📣 *منشن* → ارسال منشنِ نفرات برتر\n"
                "📌 *پین* → پین کردن همین پیام آمار",
                reply_markup=stats_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "راهنمای آمار باز شد ✨")
            return True

        if data == "stats:compact":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                format_stats_message(chat_title or "", rows, compact=True),
                reply_markup=stats_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "نمای جمع‌وجور باز شد ✅")
            return True

        if data == "stats:refresh":
            self.ctx.api.edit_message_text(
                chat_id,
                message_id,
                format_stats_message(chat_title or "", rows),
                reply_markup=stats_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "آمار بروزرسانی شد ✅")
            return True

        if data == "stats:mention":
            rows_with_ids = get_24h_group_stats_with_ids(chat_id)
            mention_block = build_mentions_block(rows_with_ids, limit=8)

            if not mention_block:
                self.ctx.api.answer_callback_query(cq_id, "برای منشن، هنوز داده‌ی کافی از کاربران ندارم.", show_alert=True)
                return True

            body = format_stats_message(chat_title or "", rows, compact=True)
            text = f"{mention_block}\n\n{body}"

            self.ctx.api.send_message(
                chat_id,
                text,
                reply_to_message_id=message_id,
                reply_markup=stats_keyboard(),
            )
            self.ctx.api.answer_callback_query(cq_id, "منشنِ نفرات برتر ارسال شد 📣")
            return True

        if data == "stats:pin":
            try:
                self.ctx.api.pin_chat_message(chat_id, message_id, disable_notification=False)
                self.ctx.api.answer_callback_query(cq_id, "پیام آمار پین شد 📌")
            except Exception as e:
                self.ctx.api.log("[LOG] STATS PIN ERROR", repr(e))
                self.ctx.api.answer_callback_query(cq_id, "پین کردن انجام نشد. شاید ربات دسترسی پین ندارد.", show_alert=True)
            return True

        return False