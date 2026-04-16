# -*- coding: utf-8 -*-

import json
import os
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from helpers import escape_md, get_best_display_name, normalize_text, build_bale_mention

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "class_reminder_data.json")
TIMEZONE_NAME = "Asia/Tehran"

DEFAULT_REMINDERS = [60, 720, 1440]
DEFAULT_MENTION_MODE = "members"
CHECK_INTERVAL_SECONDS = 30
REMINDER_GRACE_SECONDS = 90
MAX_SENT_KEYS_PER_GROUP = 3000
MAX_MENTIONS_PER_MESSAGE = 40

PERSIAN_WEEKDAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]
WEEKDAY_TO_INDEX = {
    "شنبه": 5,
    "یکشنبه": 6,
    "دوشنبه": 0,
    "سه‌شنبه": 1,
    "سه شنبه": 1,
    "چهارشنبه": 2,
    "پنجشنبه": 3,
    "جمعه": 4,
}
INDEX_TO_WEEKDAY = {v: k for k, v in WEEKDAY_TO_INDEX.items()}
PERSIAN_DIGITS_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


class Plugin:
    def __init__(self, ctx):
        self.ctx = ctx
        self._lock = threading.RLock()
        self._stop = False
        self._thread = None
        self.data = self._load_data()
        self.pending_inputs = {}
        self.wizards = {}

    def on_startup(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="class-reminder-scheduler")
        self._thread.start()
        self._log("[CLASS] plugin started")

    # =========================
    # public
    # =========================
    def on_message(self, message: dict):
        try:
            self._remember_member(message)

            text = normalize_text(message.get("text") or "")
            if not text:
                return False

            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            chat_type = chat.get("type")
            message_id = message.get("message_id")
            from_user = message.get("from") or {}
            user_id = from_user.get("id")
            text_low = text.lower()

            if chat_type not in ("group", "supergroup"):
                return False

            self._ensure_group_seeded(chat_id)

            # ورودی‌های در حال انتظار
            if self._consume_pending_input(chat_id, user_id, text, message_id):
                return True

            if text_low in ("کلاس‌ها", "کلاس ها", "classes"):
                self._send(chat_id, self._home_text(chat_id), reply_to_message_id=message_id, reply_markup=self._home_keyboard())
                return True

            if text_low in ("کلاس امروز", "today classes"):
                self._send(chat_id, self._day_schedule_text(chat_id, self._now().date()), reply_to_message_id=message_id, reply_markup=self._home_keyboard())
                return True

            if text_low in ("کلاس فردا", "tomorrow classes"):
                self._send(chat_id, self._day_schedule_text(chat_id, self._now().date() + timedelta(days=1)), reply_to_message_id=message_id, reply_markup=self._home_keyboard())
                return True

            if text_low in ("تنظیمات کلاس", "class settings"):
                self._send(chat_id, self._settings_text(chat_id), reply_to_message_id=message_id, reply_markup=self._settings_keyboard())
                return True

            if text_low in ("راهنمای کلاس", "class help", "/classes"):
                self._send(chat_id, self._help_text(), reply_to_message_id=message_id, reply_markup=self._home_keyboard())
                return True

            return False

        except Exception as e:
            self._log("[CLASS] on_message error", repr(e), traceback.format_exc())
            try:
                chat_id = (message.get("chat") or {}).get("id")
                message_id = message.get("message_id")
                if chat_id:
                    self._send(chat_id, "⚠️ *در پردازش بخش کلاس‌ها خطایی رخ داد.*", reply_to_message_id=message_id)
            except Exception:
                pass
            return True

    def on_callback_query(self, callback_query: dict):
        data = callback_query.get("data") or ""
        if not data.startswith("class:"):
            return False

        cq_id = callback_query.get("id")
        msg = callback_query.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        message_id = msg.get("message_id")
        from_user = callback_query.get("from") or {}
        user_id = from_user.get("id")

        self._ensure_group_seeded(chat_id)

        try:
            if data == "class:home":
                self.ctx.api.edit_message_text(chat_id, message_id, self._home_text(chat_id), reply_markup=self._home_keyboard())
                self.ctx.api.answer_callback_query(cq_id, "بخش کلاس‌ها باز شد ✅")
                return True

            if data == "class:weekly":
                self.ctx.api.edit_message_text(chat_id, message_id, self._weekly_overview_text(chat_id), reply_markup=self._home_keyboard())
                self.ctx.api.answer_callback_query(cq_id, "برنامه هفتگی نمایش داده شد ✅")
                return True

            if data == "class:today":
                self.ctx.api.edit_message_text(chat_id, message_id, self._day_schedule_text(chat_id, self._now().date()), reply_markup=self._home_keyboard())
                self.ctx.api.answer_callback_query(cq_id, "کلاس‌های امروز نمایش داده شد ✅")
                return True

            if data == "class:tomorrow":
                self.ctx.api.edit_message_text(chat_id, message_id, self._day_schedule_text(chat_id, self._now().date() + timedelta(days=1)), reply_markup=self._home_keyboard())
                self.ctx.api.answer_callback_query(cq_id, "کلاس‌های فردا نمایش داده شد ✅")
                return True

            if data == "class:manage":
                self.ctx.api.edit_message_text(chat_id, message_id, self._manage_text(chat_id), reply_markup=self._manage_keyboard(chat_id, user_id))
                self.ctx.api.answer_callback_query(cq_id, "مدیریت کلاس‌ها باز شد ✅")
                return True

            if data == "class:settings":
                self.ctx.api.edit_message_text(chat_id, message_id, self._settings_text(chat_id), reply_markup=self._settings_keyboard())
                self.ctx.api.answer_callback_query(cq_id, "تنظیمات کلاس باز شد ⚙️")
                return True

            if data == "class:help":
                self.ctx.api.edit_message_text(chat_id, message_id, self._help_text(), reply_markup=self._home_keyboard())
                self.ctx.api.answer_callback_query(cq_id, "راهنمای کلاس باز شد ✨")
                return True

            if data == "class:add":
                if not self._is_group_admin(chat_id, user_id):
                    self.ctx.api.answer_callback_query(cq_id, "فقط ادمین‌های گروه می‌توانند کلاس اضافه کنند ⛔️", show_alert=True)
                    return True
                self.wizards[(chat_id, user_id)] = {"mode": "add"}
                self.ctx.api.edit_message_text(chat_id, message_id, self._add_choose_kind_text(), reply_markup=self._add_kind_keyboard())
                self.ctx.api.answer_callback_query(cq_id, "مرحله‌ی افزودن کلاس شروع شد ✅")
                return True

            if data.startswith("class:addkind:"):
                kind = data.split(":")[-1]
                wizard = self.wizards.get((chat_id, user_id))
                if not wizard:
                    self.ctx.api.answer_callback_query(cq_id, "فرآیند قبلی منقضی شده است.", show_alert=True)
                    return True
                wizard["kind"] = kind
                self.ctx.api.edit_message_text(chat_id, message_id, self._add_choose_day_text(kind), reply_markup=self._add_day_keyboard(kind))
                self.ctx.api.answer_callback_query(cq_id, "نوع کلاس انتخاب شد ✅")
                return True

            if data.startswith("class:addday:"):
                value = data.split(":", 2)[2]
                wizard = self.wizards.get((chat_id, user_id))
                if not wizard:
                    self.ctx.api.answer_callback_query(cq_id, "فرآیند قبلی منقضی شده است.", show_alert=True)
                    return True
                wizard["day"] = value
                self.ctx.api.edit_message_text(chat_id, message_id, self._add_choose_hour_text(), reply_markup=self._hour_keyboard("class:addhour"))
                self.ctx.api.answer_callback_query(cq_id, "روز / تاریخ انتخاب شد ✅")
                return True

            if data.startswith("class:addhour:"):
                value = data.split(":")[-1]
                wizard = self.wizards.get((chat_id, user_id))
                if not wizard:
                    self.ctx.api.answer_callback_query(cq_id, "فرآیند قبلی منقضی شده است.", show_alert=True)
                    return True
                wizard["hour"] = value
                self.ctx.api.edit_message_text(chat_id, message_id, self._add_choose_minute_text(), reply_markup=self._minute_keyboard("class:addmin"))
                self.ctx.api.answer_callback_query(cq_id, "ساعت انتخاب شد ✅")
                return True

            if data.startswith("class:addmin:"):
                value = data.split(":")[-1]
                wizard = self.wizards.get((chat_id, user_id))
                if not wizard:
                    self.ctx.api.answer_callback_query(cq_id, "فرآیند قبلی منقضی شده است.", show_alert=True)
                    return True
                wizard["minute"] = value
                self.ctx.api.edit_message_text(chat_id, message_id, self._add_choose_duration_text(), reply_markup=self._duration_keyboard("class:adddur"))
                self.ctx.api.answer_callback_query(cq_id, "دقیقه انتخاب شد ✅")
                return True

            if data.startswith("class:adddur:"):
                value = data.split(":")[-1]
                wizard = self.wizards.get((chat_id, user_id))
                if not wizard:
                    self.ctx.api.answer_callback_query(cq_id, "فرآیند قبلی منقضی شده است.", show_alert=True)
                    return True
                wizard["duration"] = int(value)
                self.pending_inputs[(chat_id, user_id)] = {"type": "new_title", "wizard": wizard.copy()}
                self.wizards.pop((chat_id, user_id), None)
                self.ctx.api.edit_message_text(
                    chat_id,
                    message_id,
                    "📝 *عنوان کلاس را بفرست*\n\n"
                    "فقط کافی است *عنوان کلاس* را در همین گروه در یک پیام معمولی بنویسی.\n"
                    "بقیه‌ی اطلاعات با دکمه‌ها ثبت شده‌اند. ✨",
                    reply_markup=self._cancel_input_keyboard(),
                )
                self.ctx.api.answer_callback_query(cq_id, "فقط عنوان کلاس باقی مانده است ✅")
                return True

            if data == "class:listedit":
                if not self._is_group_admin(chat_id, user_id):
                    self.ctx.api.answer_callback_query(cq_id, "فقط ادمین‌های گروه می‌توانند کلاس‌ها را مدیریت کنند ⛔️", show_alert=True)
                    return True
                self.ctx.api.edit_message_text(chat_id, message_id, self._pick_class_text(chat_id), reply_markup=self._pick_class_keyboard(chat_id))
                self.ctx.api.answer_callback_query(cq_id, "یکی از کلاس‌ها را انتخاب کن ✅")
                return True

            if data.startswith("class:item:"):
                cid = data.split(":")[-1]
                item = self._get_class(chat_id, cid)
                if not item:
                    self.ctx.api.answer_callback_query(cq_id, "این کلاس پیدا نشد.", show_alert=True)
                    return True
                self.ctx.api.edit_message_text(chat_id, message_id, self._item_text(item), reply_markup=self._item_keyboard(cid))
                self.ctx.api.answer_callback_query(cq_id, "جزئیات کلاس باز شد ✅")
                return True

            if data.startswith("class:itemtoggle:"):
                if not self._is_group_admin(chat_id, user_id):
                    self.ctx.api.answer_callback_query(cq_id, "فقط ادمین‌های گروه مجازند ⛔️", show_alert=True)
                    return True
                cid = data.split(":")[-1]
                with self._lock:
                    item = self._get_class(chat_id, cid)
                    if not item:
                        self.ctx.api.answer_callback_query(cq_id, "کلاس پیدا نشد.", show_alert=True)
                        return True
                    item["active"] = not item.get("active", True)
                    self._save_data_locked()
                self.ctx.api.edit_message_text(chat_id, message_id, self._item_text(item), reply_markup=self._item_keyboard(cid))
                self.ctx.api.answer_callback_query(cq_id, "وضعیت کلاس تغییر کرد ✅")
                return True

            if data.startswith("class:itemdelete:"):
                if not self._is_group_admin(chat_id, user_id):
                    self.ctx.api.answer_callback_query(cq_id, "فقط ادمین‌های گروه مجازند ⛔️", show_alert=True)
                    return True
                cid = data.split(":")[-1]
                with self._lock:
                    group = self._ensure_group_seeded(chat_id)
                    group["classes"] = [x for x in group["classes"] if x.get("id") != cid]
                    self._save_data_locked()
                self.ctx.api.edit_message_text(chat_id, message_id, "🗑 *کلاس با موفقیت حذف شد.*", reply_markup=self._manage_keyboard(chat_id, user_id))
                self.ctx.api.answer_callback_query(cq_id, "کلاس حذف شد ✅")
                return True

            if data.startswith("class:itemedit:"):
                if not self._is_group_admin(chat_id, user_id):
                    self.ctx.api.answer_callback_query(cq_id, "فقط ادمین‌های گروه مجازند ⛔️", show_alert=True)
                    return True
                _, _, cid, field = data.split(":")
                labels = {
                    "title": "عنوان",
                    "teacher": "نام استاد",
                    "description": "توضیحات",
                    "link": "لینک",
                }
                self.pending_inputs[(chat_id, user_id)] = {"type": "edit_field", "class_id": cid, "field": field}
                self.ctx.api.edit_message_text(
                    chat_id,
                    message_id,
                    f"📝 *{labels.get(field, 'مقدار جدید')}* را بفرست\n\n"
                    "پیام بعدی تو برای همین کلاس ذخیره می‌شود.",
                    reply_markup=self._cancel_input_keyboard(),
                )
                self.ctx.api.answer_callback_query(cq_id, "ورود مقدار جدید فعال شد ✅")
                return True

            if data.startswith("class:mention:"):
                if not self._is_group_admin(chat_id, user_id):
                    self.ctx.api.answer_callback_query(cq_id, "فقط ادمین‌های گروه مجازند ⛔️", show_alert=True)
                    return True
                mode = data.split(":")[-1]
                with self._lock:
                    group = self._ensure_group_seeded(chat_id)
                    group["settings"]["mention_mode"] = mode
                    self._save_data_locked()
                self.ctx.api.edit_message_text(chat_id, message_id, self._settings_text(chat_id), reply_markup=self._settings_keyboard())
                self.ctx.api.answer_callback_query(cq_id, "حالت منشن بروزرسانی شد ✅")
                return True

            if data.startswith("class:rem:"):
                if not self._is_group_admin(chat_id, user_id):
                    self.ctx.api.answer_callback_query(cq_id, "فقط ادمین‌های گروه مجازند ⛔️", show_alert=True)
                    return True
                preset = data.split(":")[-1]
                mapping = {
                    "default": [60, 720, 1440],
                    "short": [60],
                    "full": [30, 60, 720, 1440],
                }
                with self._lock:
                    group = self._ensure_group_seeded(chat_id)
                    group["settings"]["reminders"] = mapping.get(preset, DEFAULT_REMINDERS)
                    self._save_data_locked()
                self.ctx.api.edit_message_text(chat_id, message_id, self._settings_text(chat_id), reply_markup=self._settings_keyboard())
                self.ctx.api.answer_callback_query(cq_id, "تنظیم یادآوری بروزرسانی شد ✅")
                return True

            if data == "class:cancelinput":
                self.pending_inputs.pop((chat_id, user_id), None)
                self.wizards.pop((chat_id, user_id), None)
                self.ctx.api.edit_message_text(chat_id, message_id, self._manage_text(chat_id), reply_markup=self._manage_keyboard(chat_id, user_id))
                self.ctx.api.answer_callback_query(cq_id, "عملیات لغو شد.")
                return True

        except Exception as e:
            self._log("[CLASS] callback error", repr(e), traceback.format_exc())
            self.ctx.api.answer_callback_query(cq_id, "⚠️ خطایی در این مرحله رخ داد.", show_alert=True)
            return True

        return False

    # =========================
    # text / keyboards
    # =========================
    def _home_text(self, chat_id: int) -> str:
        return (
            "📚 *بخش کلاس‌ها / Classes*\n\n"
            "این بخش تا حد ممکن *ساده و دکمه‌محور* طراحی شده است.\n"
            "برای دیدن برنامه، مدیریت کلاس‌ها یا تنظیمات، از دکمه‌های زیر استفاده کن 👇"
        )

    def _help_text(self) -> str:
        return (
            "✨ *راهنمای کلاس‌ها / Classes Help*\n\n"
            "این نسخه عمداً ساده‌تر شده است:\n\n"
            "• دیدن برنامه با دکمه\n"
            "• افزودن کلاس تقریباً کامل با دکمه\n"
            "• فقط *عنوان کلاس* با پیام متنی گرفته می‌شود\n"
            "• ویرایش کلاس‌ها هم بدون نمایش ID به کاربر انجام می‌شود\n\n"
            "🔔 یادآوری‌ها خودکار هستند و ادمین‌ها می‌توانند از بخش تنظیمات آن‌ها را تغییر دهند."
        )

    def _manage_text(self, chat_id: int) -> str:
        group = self._ensure_group_seeded(chat_id)
        active_count = len([x for x in group.get("classes", []) if x.get("active", True)])
        return (
            "🛠 *مدیریت کلاس‌ها / Manage Classes*\n\n"
            f"📚 تعداد کلاس‌های فعال: *{active_count}*\n\n"
            "از دکمه‌های زیر برای *افزودن* یا *ویرایش* کلاس‌ها استفاده کن."
        )

    def _settings_text(self, chat_id: int) -> str:
        group = self._ensure_group_seeded(chat_id)
        settings = group.get("settings") or {}
        mention_mode = settings.get("mention_mode", DEFAULT_MENTION_MODE)
        mention_label = {
            "members": "همه اعضای شناخته‌شده",
            "admins": "فقط ادمین‌های گروه",
            "off": "خاموش",
        }.get(mention_mode, "نامشخص")
        return (
            "⚙️ *تنظیمات کلاس‌ها / Class Settings*\n\n"
            f"⏰ *بازه‌های یادآوری:* `{escape_md(self._format_reminder_list(settings.get('reminders') or DEFAULT_REMINDERS))}`\n"
            f"🔔 *حالت منشن:* {escape_md(mention_label)}\n"
            f"👥 *اعضای ذخیره‌شده برای منشن:* {len((group.get('members') or {}))} نفر"
        )

    def _weekly_overview_text(self, chat_id: int) -> str:
        group = self._ensure_group_seeded(chat_id)
        items = [x for x in group.get("classes", []) if x.get("active", True)]

        grouped = {d: [] for d in PERSIAN_WEEKDAYS}
        one_time_items = []

        for item in items:
            if item.get("date"):
                one_time_items.append(item)
            else:
                grouped[item.get("weekday")].append(item)

        lines = ["📚 *برنامه‌ی کلاس‌ها / Weekly Schedule*", ""]
        for day in PERSIAN_WEEKDAYS:
            lines.append(f"*{escape_md(day)}*")
            day_items = sorted(grouped.get(day) or [], key=self._sort_key_for_item)
            if not day_items:
                lines.append("فعلاً خبری نیست.")
                lines.append("")
                continue
            for item in day_items:
                lines.extend(self._format_item_brief_lines(item))
            lines.append("")

        if one_time_items:
            lines.append("*اطلاعیه‌ها و کلاس‌های یک‌باره*")
            for item in sorted(one_time_items, key=self._sort_key_for_item):
                lines.extend(self._format_item_brief_lines(item, include_date=True))
            lines.append("")
        return "\n".join(lines).strip()

    def _day_schedule_text(self, chat_id: int, target_date):
        group = self._ensure_group_seeded(chat_id)
        matches = []
        persian_day = self._weekday_name(target_date)

        for item in group.get("classes") or []:
            if not item.get("active", True):
                continue
            if item.get("date") == target_date.isoformat():
                matches.append(item)
            elif item.get("weekday") == persian_day:
                matches.append(item)

        title = f"📅 *کلاس‌های {escape_md(self._persian_date_label(target_date))}*"
        if not matches:
            return title + "\n\nفعلاً کلاسی برای این روز ثبت نشده است."

        lines = [title, ""]
        for item in sorted(matches, key=self._sort_key_for_item):
            lines.extend(self._format_item_brief_lines(item, include_date=False))
        return "\n".join(lines)

    def _pick_class_text(self, chat_id: int) -> str:
        return (
            "🧩 *انتخاب کلاس*\n\n"
            "یکی از کلاس‌های زیر را برای ویرایش یا حذف انتخاب کن."
        )

    def _item_text(self, item: dict) -> str:
        state = "فعال ✅" if item.get("active", True) else "غیرفعال ⏸"
        lines = [
            "📘 *جزئیات کلاس*",
            "",
            f"📚 *عنوان:* {escape_md(item.get('title') or 'بدون عنوان')}",
            f"🕒 *زمان:* {escape_md(self._item_when_text(item, include_date=True))}",
            f"👨‍🏫 *استاد:* {escape_md(item.get('teacher') or 'ثبت نشده')}",
            f"📝 *توضیحات:* {escape_md(item.get('description') or 'ثبت نشده')}",
            f"🔗 *لینک:* {escape_md(item.get('link') or 'ثبت نشده')}",
            f"📌 *وضعیت:* {state}",
        ]
        return "\n".join(lines)

    def _format_item_brief_lines(self, item: dict, include_date: bool = False) -> List[str]:
        lines = [f"• *{escape_md(item.get('title') or 'بدون عنوان')}*"]
        lines.append(f"  🕒 {escape_md(self._item_when_text(item, include_date=include_date))}")
        if item.get("teacher"):
            lines.append(f"  👨‍🏫 {escape_md(item.get('teacher'))}")
        if item.get("description"):
            lines.append(f"  📝 {escape_md(item.get('description'))}")
        if item.get("link"):
            lines.append(f"  🔗 {escape_md(item.get('link'))}")
        return lines

    def _home_keyboard(self):
        return {
            "inline_keyboard": [
                [
                    {"text": "📚 برنامه هفتگی", "callback_data": "class:weekly"},
                    {"text": "📅 امروز", "callback_data": "class:today"},
                ],
                [
                    {"text": "⏭ فردا", "callback_data": "class:tomorrow"},
                    {"text": "🛠 مدیریت", "callback_data": "class:manage"},
                ],
                [
                    {"text": "⚙️ تنظیمات", "callback_data": "class:settings"},
                    {"text": "❓ راهنما", "callback_data": "class:help"},
                ],
            ]
        }

    def _manage_keyboard(self, chat_id: int, user_id: int):
        rows = []
        if self._is_group_admin(chat_id, user_id):
            rows.append([
                {"text": "➕ افزودن کلاس", "callback_data": "class:add"},
                {"text": "✏️ ویرایش کلاس‌ها", "callback_data": "class:listedit"},
            ])
        rows.append([{"text": "🔙 بازگشت", "callback_data": "class:home"}])
        return {"inline_keyboard": rows}

    def _settings_keyboard(self):
        return {
            "inline_keyboard": [
                [
                    {"text": "👥 منشن اعضا", "callback_data": "class:mention:members"},
                    {"text": "🛡 منشن ادمین‌ها", "callback_data": "class:mention:admins"},
                ],
                [
                    {"text": "🔕 بدون منشن", "callback_data": "class:mention:off"},
                ],
                [
                    {"text": "⏰ پیش‌فرض", "callback_data": "class:rem:default"},
                    {"text": "⚡ کوتاه", "callback_data": "class:rem:short"},
                    {"text": "🧠 کامل", "callback_data": "class:rem:full"},
                ],
                [
                    {"text": "🔙 بازگشت", "callback_data": "class:home"},
                ],
            ]
        }

    def _add_choose_kind_text(self):
        return (
            "➕ *افزودن کلاس جدید*\n\n"
            "اول مشخص کن کلاس *هفتگی* است یا *یک‌باره*."
        )

    def _add_kind_keyboard(self):
        return {
            "inline_keyboard": [
                [
                    {"text": "🗓 هفتگی", "callback_data": "class:addkind:weekly"},
                    {"text": "📌 یک‌باره", "callback_data": "class:addkind:once"},
                ],
                [
                    {"text": "❌ انصراف", "callback_data": "class:cancelinput"},
                ],
            ]
        }

    def _add_choose_day_text(self, kind):
        if kind == "weekly":
            return "🗓 *روز کلاس را انتخاب کن*"
        return "📌 *تاریخ کلاس را انتخاب کن*"

    def _add_day_keyboard(self, kind):
        if kind == "weekly":
            return {
                "inline_keyboard": [
                    [
                        {"text": "شنبه", "callback_data": "class:addday:شنبه"},
                        {"text": "یکشنبه", "callback_data": "class:addday:یکشنبه"},
                    ],
                    [
                        {"text": "دوشنبه", "callback_data": "class:addday:دوشنبه"},
                        {"text": "سه‌شنبه", "callback_data": "class:addday:سه‌شنبه"},
                    ],
                    [
                        {"text": "چهارشنبه", "callback_data": "class:addday:چهارشنبه"},
                        {"text": "پنجشنبه", "callback_data": "class:addday:پنجشنبه"},
                    ],
                    [
                        {"text": "جمعه", "callback_data": "class:addday:جمعه"},
                        {"text": "❌ انصراف", "callback_data": "class:cancelinput"},
                    ],
                ]
            }

        today = self._now().date()
        d1 = today
        d2 = today + timedelta(days=1)
        d3 = today + timedelta(days=2)
        d4 = today + timedelta(days=3)
        d5 = today + timedelta(days=7)
        return {
            "inline_keyboard": [
                [
                    {"text": f"امروز {d1.isoformat()}", "callback_data": f"class:addday:{d1.isoformat()}"},
                    {"text": f"فردا {d2.isoformat()}", "callback_data": f"class:addday:{d2.isoformat()}"},
                ],
                [
                    {"text": d3.isoformat(), "callback_data": f"class:addday:{d3.isoformat()}"},
                    {"text": d4.isoformat(), "callback_data": f"class:addday:{d4.isoformat()}"},
                ],
                [
                    {"text": f"هفته بعد {d5.isoformat()}", "callback_data": f"class:addday:{d5.isoformat()}"},
                ],
                [
                    {"text": "❌ انصراف", "callback_data": "class:cancelinput"},
                ],
            ]
        }

    def _add_choose_hour_text(self):
        return "🕒 *ساعت شروع را انتخاب کن*"

    def _hour_keyboard(self, prefix):
        hours = list(range(8, 21))
        rows = []
        row = []
        for h in hours:
            row.append({"text": f"{h:02d}", "callback_data": f"{prefix}:{h:02d}"})
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([{"text": "❌ انصراف", "callback_data": "class:cancelinput"}])
        return {"inline_keyboard": rows}

    def _add_choose_minute_text(self):
        return "🕒 *دقیقه را انتخاب کن*"

    def _minute_keyboard(self, prefix):
        return {
            "inline_keyboard": [
                [
                    {"text": ":00", "callback_data": f"{prefix}:00"},
                    {"text": ":30", "callback_data": f"{prefix}:30"},
                ],
                [
                    {"text": "❌ انصراف", "callback_data": "class:cancelinput"},
                ],
            ]
        }

    def _add_choose_duration_text(self):
        return (
            "⏳ *مدت کلاس را انتخاب کن*\n\n"
            "اگر ساعت پایان مشخص نیست، گزینه‌ی «فقط ساعت شروع» را بزن."
        )

    def _duration_keyboard(self, prefix):
        return {
            "inline_keyboard": [
                [
                    {"text": "1 ساعت", "callback_data": f"{prefix}:60"},
                    {"text": "1.5 ساعت", "callback_data": f"{prefix}:90"},
                    {"text": "2 ساعت", "callback_data": f"{prefix}:120"},
                ],
                [
                    {"text": "فقط ساعت شروع", "callback_data": f"{prefix}:0"},
                ],
                [
                    {"text": "❌ انصراف", "callback_data": "class:cancelinput"},
                ],
            ]
        }

    def _pick_class_keyboard(self, chat_id: int):
        items = [x for x in self._ensure_group_seeded(chat_id).get("classes", [])]
        items = sorted(items, key=self._sort_key_for_item)[:12]

        rows = []
        for item in items:
            label = f"{item.get('title', 'بدون عنوان')} — {self._item_when_text(item, include_date=True)}"
            rows.append([{"text": label[:55], "callback_data": f"class:item:{item['id']}"}])
        rows.append([{"text": "🔙 بازگشت", "callback_data": "class:manage"}])
        return {"inline_keyboard": rows}

    def _item_keyboard(self, cid: str):
        return {
            "inline_keyboard": [
                [
                    {"text": "✏️ عنوان", "callback_data": f"class:itemedit:{cid}:title"},
                    {"text": "👨‍🏫 استاد", "callback_data": f"class:itemedit:{cid}:teacher"},
                ],
                [
                    {"text": "📝 توضیحات", "callback_data": f"class:itemedit:{cid}:description"},
                    {"text": "🔗 لینک", "callback_data": f"class:itemedit:{cid}:link"},
                ],
                [
                    {"text": "⏯ فعال/غیرفعال", "callback_data": f"class:itemtoggle:{cid}"},
                    {"text": "🗑 حذف", "callback_data": f"class:itemdelete:{cid}"},
                ],
                [
                    {"text": "🔙 بازگشت", "callback_data": "class:listedit"},
                ],
            ]
        }

    def _cancel_input_keyboard(self):
        return {
            "inline_keyboard": [
                [{"text": "❌ انصراف", "callback_data": "class:cancelinput"}]
            ]
        }

    # =========================
    # pending input
    # =========================
    def _consume_pending_input(self, chat_id: int, user_id: int, text: str, message_id: int) -> bool:
        state = self.pending_inputs.get((chat_id, user_id))
        if not state:
            return False

        if state["type"] == "new_title":
            wizard = state["wizard"]
            start_time = f"{wizard['hour']}:{wizard['minute']}"
            end_time = None
            if wizard["duration"] > 0:
                dt = datetime.strptime(start_time, "%H:%M") + timedelta(minutes=wizard["duration"])
                end_time = dt.strftime("%H:%M")

            item = {
                "id": self._new_id(),
                "title": text,
                "teacher": "",
                "description": "",
                "link": "",
                "start_time": start_time,
                "end_time": end_time,
                "active": True,
                "created_at": int(time.time()),
            }

            if wizard["kind"] == "weekly":
                item["weekday"] = wizard["day"]
            else:
                item["date"] = wizard["day"]

            with self._lock:
                group = self._ensure_group_seeded(chat_id)
                group["classes"].append(item)
                self._save_data_locked()

            self.pending_inputs.pop((chat_id, user_id), None)
            self._send(
                chat_id,
                "✅ *کلاس جدید با موفقیت ثبت شد.*\n\n"
                f"📚 *عنوان:* {escape_md(item['title'])}\n"
                f"🕒 *زمان:* {escape_md(self._item_when_text(item, include_date=True))}",
                reply_to_message_id=message_id,
                reply_markup=self._home_keyboard(),
            )
            return True

        if state["type"] == "edit_field":
            cid = state["class_id"]
            field = state["field"]
            with self._lock:
                item = self._get_class(chat_id, cid)
                if not item:
                    self.pending_inputs.pop((chat_id, user_id), None)
                    self._send(chat_id, "⚠️ این کلاس دیگر پیدا نشد.", reply_to_message_id=message_id)
                    return True
                item[field] = text
                self._save_data_locked()

            self.pending_inputs.pop((chat_id, user_id), None)
            self._send(
                chat_id,
                "✅ *اطلاعات کلاس بروزرسانی شد.*",
                reply_to_message_id=message_id,
                reply_markup=self._home_keyboard(),
            )
            return True

        return False

    # =========================
    # scheduler
    # =========================
    def _scheduler_loop(self):
        while not self._stop:
            try:
                self._check_and_send_reminders()
            except Exception as e:
                self._log("[CLASS] scheduler error", repr(e), traceback.format_exc())
            time.sleep(CHECK_INTERVAL_SECONDS)

    def _check_and_send_reminders(self):
        now = self._now()

        with self._lock:
            group_ids = list((self.data.get("groups") or {}).keys())

        for chat_id_str in group_ids:
            chat_id = int(chat_id_str)
            with self._lock:
                group = self.data["groups"].get(str(chat_id))
                if not group:
                    continue
                items = list(group.get("classes") or [])
                reminder_minutes = list(group.get("settings", {}).get("reminders") or DEFAULT_REMINDERS)
                sent_keys = set(group.get("sent_reminders") or [])

            for item in items:
                if not item.get("active", True):
                    continue

                occurrence = self._next_occurrence(item, now)
                if not occurrence:
                    continue

                start_dt, end_dt = occurrence

                for mins in reminder_minutes:
                    reminder_dt = start_dt - timedelta(minutes=mins)
                    reminder_key = f"{item.get('id')}|{start_dt.isoformat()}|{mins}"

                    if reminder_key in sent_keys:
                        continue

                    if reminder_dt <= now <= reminder_dt + timedelta(seconds=REMINDER_GRACE_SECONDS):
                        self._send_reminder(chat_id, item, start_dt, mins)
                        with self._lock:
                            group = self.data["groups"].get(str(chat_id))
                            if not group:
                                continue
                            group.setdefault("sent_reminders", []).append(reminder_key)
                            if len(group["sent_reminders"]) > MAX_SENT_KEYS_PER_GROUP:
                                group["sent_reminders"] = group["sent_reminders"][-MAX_SENT_KEYS_PER_GROUP:]
                            self._save_data_locked()

    def _send_reminder(self, chat_id: int, item: dict, start_dt: datetime, mins_before: int):
        mention_text = self._build_mentions(chat_id)
        lines = [
            f"⏰ *یادآوری کلاس — {escape_md(self._humanize_minutes(mins_before))} مانده به شروع*",
            "",
            f"📚 *عنوان:* {escape_md(item.get('title') or 'بدون عنوان')}",
            f"👨‍🏫 *استاد:* {escape_md(item.get('teacher') or 'ثبت نشده')}",
            f"🗓 *زمان:* {escape_md(self._persian_date_label(start_dt.date()))}",
            f"🕒 *ساعت:* {escape_md(self._time_label(item.get('start_time'), item.get('end_time')))}",
        ]
        if item.get("description"):
            lines.append(f"📝 *توضیحات:* {escape_md(item.get('description'))}")
        if item.get("link"):
            lines.append(f"🔗 *لینک کلاس:* {escape_md(item.get('link'))}")
        lines.append("")
        lines.append("✨ لطفاً به‌موقع آماده باشید.")

        body = "\n".join(lines)
        final_text = f"{mention_text}\n\n{body}" if mention_text else body
        self._send(chat_id, final_text, reply_markup=self._home_keyboard())

    # =========================
    # persistence
    # =========================
    def _load_data(self):
        if not os.path.exists(DATA_FILE):
            return {"version": 2, "groups": {}}
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"version": 2, "groups": {}}
            data.setdefault("version", 2)
            data.setdefault("groups", {})
            return data
        except Exception:
            return {"version": 2, "groups": {}}

    def _save_data_locked(self):
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)

    def _ensure_group_seeded(self, chat_id: int):
        key = str(chat_id)
        with self._lock:
            groups = self.data.setdefault("groups", {})
            if key in groups:
                return groups[key]

            groups[key] = {
                "settings": {
                    "reminders": list(DEFAULT_REMINDERS),
                    "mention_mode": DEFAULT_MENTION_MODE,
                    "timezone": TIMEZONE_NAME,
                },
                "classes": self._build_default_classes(),
                "members": {},
                "sent_reminders": [],
                "created_at": int(time.time()),
            }
            self._save_data_locked()
            return groups[key]

    def _build_default_classes(self) -> List[dict]:
        now = self._now()
        items = []

        def add_weekly(weekday, title, start=None, end=None, teacher="", description="", link=""):
            items.append({
                "id": self._new_id(),
                "weekday": weekday,
                "title": title,
                "teacher": teacher,
                "description": description,
                "link": link,
                "start_time": start,
                "end_time": end,
                "active": True,
                "created_at": int(time.time()),
            })

        def add_once(date_value, title, start=None, end=None, teacher="", description="", link=""):
            items.append({
                "id": self._new_id(),
                "date": date_value.isoformat(),
                "title": title,
                "teacher": teacher,
                "description": description,
                "link": link,
                "start_time": start,
                "end_time": end,
                "active": True,
                "created_at": int(time.time()),
            })

        add_weekly("شنبه", "مبانی مواد دندانی", description="کلاس‌ها آفلاین خواهند بود.")
        add_weekly("یکشنبه", "روش تحقیق", start="13:00", end="15:00", link="https://www.skyroom.online/ch/virtualtums/online-session3")
        add_weekly("دوشنبه", "زبان تخصصی 3 و 4", start="13:30", end="15:30", link="https://www.skyroom.online/ch/tums2/shabani")
        add_weekly("دوشنبه", "تشخیصی 2", start="17:00", end="18:00", link="https://www.skyroom.online/ch/virtualtums/school-of-dentistry")
        add_weekly("دوشنبه", "ترمیمی نظری 1", description="محتوا در نوید بارگذاری خواهد شد (آفلاین).")
        add_weekly("سه‌شنبه", "مبانی پروتز کامل نظری", description="کلاس‌ها آفلاین خواهند بود.")
        add_weekly("سه‌شنبه", "تجهیزات دندانپزشکی و ارگونومی", description="فعلاً خبری نیست.")
        add_weekly("چهارشنبه", "روش تحقیق", start="13:00", end="15:00", link="https://www.skyroom.online/ch/virtualtums/online-session3")
        add_weekly("چهارشنبه", "سالمندشناسی", start="11:00", end="12:00", link="https://www.skyroom.online/ch/virtualtums/online-session3")

        next_sunday = self._next_weekday_date_from(now.date(), WEEKDAY_TO_INDEX["یکشنبه"])
        next_wednesday = self._next_weekday_date_from(now.date(), WEEKDAY_TO_INDEX["چهارشنبه"])

        add_once(
            next_sunday,
            "تشخیصی ۱",
            start="11:00",
            teacher="شیرازیان",
            description="لطفاً سه جلسه‌ای که در نوید بارگذاری شده را حتماً مطالعه داشته باشید. در قالب کوئیز یا پرسش‌وپاسخ نمره خواهد داشت.",
        )
        add_once(
            next_wednesday,
            "جلسه جبرانی",
            start="09:00",
            teacher="شیخ بهایی",
            description="جلسه جبرانی این هفته و جلسه هفته بعد با هم برگزار می‌شود. محتوا در نوید برگزار می‌شود.",
        )

        return items

    # =========================
    # mentions
    # =========================
    def _remember_member(self, message: dict):
        chat = message.get("chat") or {}
        chat_type = chat.get("type")
        chat_id = chat.get("id")
        if chat_type not in ("group", "supergroup") or not chat_id:
            return

        user = message.get("from") or {}
        user_id = user.get("id")
        if not user_id:
            return

        with self._lock:
            group = self._ensure_group_seeded(chat_id)
            members = group.setdefault("members", {})
            members[str(user_id)] = {
                "name": get_best_display_name(user),
                "last_seen_at": int(time.time()),
            }
            self._save_data_locked()

    def _build_mentions(self, chat_id: int) -> str:
        group = self._ensure_group_seeded(chat_id)
        mode = (group.get("settings") or {}).get("mention_mode", DEFAULT_MENTION_MODE)
        if mode == "off":
            return ""

        users = []
        if mode == "admins":
            users = self._get_chat_admins(chat_id)
        else:
            members = list((group.get("members") or {}).items())
            members.sort(key=lambda x: x[1].get("last_seen_at", 0), reverse=True)
            for uid, info in members:
                users.append({"id": int(uid), "name": info.get("name") or "کاربر"})

        seen = set()
        parts = []
        for user in users:
            uid = user.get("id")
            if not uid or uid in seen:
                continue
            seen.add(uid)
            parts.append(build_bale_mention(uid, user.get("name") or "کاربر"))
            if len(parts) >= MAX_MENTIONS_PER_MESSAGE:
                break
        return " ".join(parts)

    def _get_chat_admins(self, chat_id: int):
        try:
            rows = self.ctx.api.api_get("getChatAdministrators", {"chat_id": chat_id}) or []
        except Exception as e:
            self._log("[CLASS] getChatAdministrators error", repr(e))
            return []
        res = []
        for row in rows:
            user = row.get("user") or {}
            if user.get("id"):
                res.append({"id": user["id"], "name": get_best_display_name(user)})
        return res

    # =========================
    # helpers
    # =========================
    def _send(self, chat_id, text, reply_to_message_id=None, reply_markup=None):
        return self.ctx.api.send_message(chat_id, text, reply_to_message_id=reply_to_message_id, reply_markup=reply_markup)

    def _log(self, *args):
        try:
            self.ctx.api.log(*args)
        except Exception:
            print(*args, flush=True)

    def _is_group_admin(self, chat_id: int, user_id: int) -> bool:
        try:
            rows = self.ctx.api.api_get("getChatAdministrators", {"chat_id": chat_id}) or []
            for row in rows:
                user = row.get("user") or {}
                if user.get("id") == user_id:
                    return True
        except Exception as e:
            self._log("[CLASS] admin check error", repr(e))
        return False

    def _get_class(self, chat_id: int, cid: str):
        group = self._ensure_group_seeded(chat_id)
        for item in group.get("classes", []):
            if item.get("id") == cid:
                return item
        return None

    def _now(self) -> datetime:
        if ZoneInfo is None:
            return datetime.now()
        return datetime.now(ZoneInfo(TIMEZONE_NAME))

    def _new_id(self) -> str:
        return uuid.uuid4().hex[:8]

    def _weekday_name(self, d) -> str:
        return INDEX_TO_WEEKDAY.get(d.weekday(), "نامشخص")

    def _persian_date_label(self, d) -> str:
        return f"{self._weekday_name(d)} {d.isoformat()}"

    def _time_label(self, start_time: Optional[str], end_time: Optional[str]) -> str:
        if start_time and end_time:
            return f"{start_time} تا {end_time}"
        if start_time:
            return start_time
        return "ساعت ثبت نشده"

    def _item_when_text(self, item: dict, include_date: bool = False) -> str:
        if item.get("date"):
            day_text = item["date"]
            try:
                parsed = datetime.strptime(item["date"], "%Y-%m-%d").date()
                day_text = self._persian_date_label(parsed)
            except Exception:
                pass
        else:
            day_text = item.get("weekday") or "روز نامشخص"

        time_text = self._time_label(item.get("start_time"), item.get("end_time"))
        if include_date or item.get("date"):
            return f"{day_text} — {time_text}"
        return time_text if item.get("start_time") else day_text

    def _sort_key_for_item(self, item: dict):
        if item.get("date"):
            return (0, item.get("date"), item.get("start_time") or "99:99", item.get("title") or "")
        weekday = item.get("weekday")
        weekday_order = PERSIAN_WEEKDAYS.index(weekday) if weekday in PERSIAN_WEEKDAYS else 99
        return (1, weekday_order, item.get("start_time") or "99:99", item.get("title") or "")

    def _next_weekday_date_from(self, base_date, target_weekday_idx: int):
        delta = (target_weekday_idx - base_date.weekday()) % 7
        if delta == 0:
            delta = 7
        return base_date + timedelta(days=delta)

    def _next_occurrence(self, item: dict, now: datetime) -> Optional[Tuple[datetime, Optional[datetime]]]:
        if not item.get("start_time"):
            return None

        candidates = []
        if item.get("date"):
            try:
                d = datetime.strptime(item["date"], "%Y-%m-%d").date()
            except Exception:
                return None
            start_dt = self._combine_date_time(d, item.get("start_time"))
            end_dt = self._combine_date_time(d, item.get("end_time")) if item.get("end_time") else None
            if start_dt and start_dt >= now - timedelta(days=2):
                candidates.append((start_dt, end_dt))
        elif item.get("weekday"):
            weekday_index = WEEKDAY_TO_INDEX.get(item["weekday"])
            if weekday_index is None:
                return None
            for add_days in range(0, 8):
                d = now.date() + timedelta(days=add_days)
                if d.weekday() != weekday_index:
                    continue
                start_dt = self._combine_date_time(d, item.get("start_time"))
                end_dt = self._combine_date_time(d, item.get("end_time")) if item.get("end_time") else None
                if start_dt and start_dt >= now - timedelta(days=2):
                    candidates.append((start_dt, end_dt))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0]

    def _combine_date_time(self, d, hhmm: Optional[str]) -> Optional[datetime]:
        if not hhmm:
            return None
        hour, minute = hhmm.split(":")
        if ZoneInfo is None:
            return datetime(d.year, d.month, d.day, int(hour), int(minute))
        return datetime(d.year, d.month, d.day, int(hour), int(minute), tzinfo=ZoneInfo(TIMEZONE_NAME))

    def _format_reminder_list(self, minutes_list: List[int]) -> str:
        return ",".join(self._humanize_minutes(m) for m in minutes_list)

    def _humanize_minutes(self, mins: int) -> str:
        if mins % 1440 == 0:
            return f"{mins // 1440}d"
        if mins % 60 == 0:
            return f"{mins // 60}h"
        return f"{mins}m"