import os
import time
import uuid
import sqlite3
import threading
from typing import Optional

from helpers import normalize_text, escape_md, bale_mention, get_best_display_name


class Plugin:
    NAME = "rps"

    CHOICES = {
        "rock": {"emoji": "🪨", "fa": "سنگ"},
        "paper": {"emoji": "📄", "fa": "کاغذ"},
        "scissors": {"emoji": "✂️", "fa": "قیچی"},
    }

    RESULT_MATRIX = {
        ("rock", "scissors"): 1,
        ("scissors", "paper"): 1,
        ("paper", "rock"): 1,
        ("scissors", "rock"): 2,
        ("paper", "scissors"): 2,
        ("rock", "paper"): 2,
    }

    ACCEPT_TIMEOUT = 120
    CHOICE_TIMEOUT = 120

    def __init__(self, ctx):
        self.ctx = ctx
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.db_path = os.path.join(self.base_dir, "rps_plugin.db")
        self.timers = {}
        self._init_db()
        self._resume_open_matches()

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _db(self, query, params=(), fetchone=False, fetchall=False):
        conn = self._conn()
        cur = conn.cursor()
        cur.execute(query, params)

        result = None
        if fetchone:
            result = cur.fetchone()
        elif fetchall:
            result = cur.fetchall()

        conn.commit()
        conn.close()
        return result

    def _init_db(self):
        conn = self._conn()
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS rps_matches (
                match_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                chat_title TEXT,
                challenger_id INTEGER NOT NULL,
                challenger_name TEXT NOT NULL,
                opponent_id INTEGER NOT NULL,
                opponent_name TEXT NOT NULL,
                status TEXT NOT NULL,
                challenger_choice TEXT,
                opponent_choice TEXT,
                created_at INTEGER NOT NULL,
                accepted_at INTEGER,
                ended_at INTEGER,
                message_id INTEGER
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS rps_stats (
                user_id INTEGER PRIMARY KEY,
                display_name TEXT,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                draws INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            )
        """)

        conn.commit()
        conn.close()

    def _player_name(self, user: dict) -> str:
        return get_best_display_name(user or {})

    def _mention(self, user_id: int, name: str) -> str:
        return bale_mention(name, user_id)

    def _get_match(self, match_id: str):
        row = self._db("""
            SELECT match_id, chat_id, chat_title, challenger_id, challenger_name,
                   opponent_id, opponent_name, status, challenger_choice, opponent_choice,
                   created_at, accepted_at, ended_at, message_id
            FROM rps_matches
            WHERE match_id = ?
        """, (match_id,), fetchone=True)

        if not row:
            return None

        return {
            "match_id": row[0],
            "chat_id": row[1],
            "chat_title": row[2] or "",
            "challenger_id": row[3],
            "challenger_name": row[4],
            "opponent_id": row[5],
            "opponent_name": row[6],
            "status": row[7],
            "challenger_choice": row[8],
            "opponent_choice": row[9],
            "created_at": row[10],
            "accepted_at": row[11],
            "ended_at": row[12],
            "message_id": row[13],
        }

    def _find_open_match_for_chat(self, chat_id: int):
        row = self._db("""
            SELECT match_id
            FROM rps_matches
            WHERE chat_id = ? AND status IN ('pending', 'active')
            ORDER BY created_at DESC
            LIMIT 1
        """, (chat_id,), fetchone=True)
        return self._get_match(row[0]) if row else None

    def _create_match(self, chat_id: int, chat_title: str, challenger_id: int, challenger_name: str, opponent_id: int, opponent_name: str):
        match_id = uuid.uuid4().hex[:8]
        self._db("""
            INSERT INTO rps_matches (
                match_id, chat_id, chat_title, challenger_id, challenger_name,
                opponent_id, opponent_name, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (
            match_id, chat_id, chat_title,
            challenger_id, challenger_name,
            opponent_id, opponent_name,
            int(time.time())
        ))
        return self._get_match(match_id)

    def _set_message_id(self, match_id: str, message_id: int):
        self._db("""
            UPDATE rps_matches
            SET message_id = ?
            WHERE match_id = ?
        """, (message_id, match_id))

    def _accept_match(self, match_id: str):
        self._db("""
            UPDATE rps_matches
            SET status = 'active', accepted_at = ?
            WHERE match_id = ?
        """, (int(time.time()), match_id))

    def _set_choice(self, match_id: str, user_id: int, choice: str):
        match = self._get_match(match_id)
        if not match:
            return

        if user_id == match["challenger_id"]:
            self._db("UPDATE rps_matches SET challenger_choice = ? WHERE match_id = ?", (choice, match_id))
        elif user_id == match["opponent_id"]:
            self._db("UPDATE rps_matches SET opponent_choice = ? WHERE match_id = ?", (choice, match_id))

    def _finish_match(self, match_id: str, status: str):
        self._db("""
            UPDATE rps_matches
            SET status = ?, ended_at = ?
            WHERE match_id = ?
        """, (status, int(time.time()), match_id))

    def _update_stats(self, winner_id: Optional[int], loser_id: Optional[int], draw_ids=None, winner_name=None, loser_name=None):
        draw_ids = draw_ids or []
        now_ts = int(time.time())

        def ensure_user(uid, name):
            row = self._db("SELECT user_id FROM rps_stats WHERE user_id = ?", (uid,), fetchone=True)
            if not row:
                self._db("""
                    INSERT INTO rps_stats (user_id, display_name, wins, losses, draws, updated_at)
                    VALUES (?, ?, 0, 0, 0, ?)
                """, (uid, name or "کاربر", now_ts))
            else:
                self._db("""
                    UPDATE rps_stats SET display_name = ?, updated_at = ? WHERE user_id = ?
                """, (name or "کاربر", now_ts, uid))

        if winner_id:
            ensure_user(winner_id, winner_name)
            self._db("UPDATE rps_stats SET wins = wins + 1, updated_at = ? WHERE user_id = ?", (now_ts, winner_id))

        if loser_id:
            ensure_user(loser_id, loser_name)
            self._db("UPDATE rps_stats SET losses = losses + 1, updated_at = ? WHERE user_id = ?", (now_ts, loser_id))

        for uid, name in draw_ids:
            ensure_user(uid, name)
            self._db("UPDATE rps_stats SET draws = draws + 1, updated_at = ? WHERE user_id = ?", (now_ts, uid))

    def _schedule_pending_timeout(self, match_id: str):
        self._cancel_timer(match_id)
        t = threading.Timer(self.ACCEPT_TIMEOUT, self._safe_expire_pending, args=(match_id,))
        t.daemon = True
        self.timers[match_id] = t
        t.start()

    def _schedule_active_timeout(self, match_id: str):
        self._cancel_timer(match_id)
        t = threading.Timer(self.CHOICE_TIMEOUT, self._safe_expire_active, args=(match_id,))
        t.daemon = True
        self.timers[match_id] = t
        t.start()

    def _cancel_timer(self, match_id: str):
        timer = self.timers.pop(match_id, None)
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass

    def _safe_expire_pending(self, match_id: str):
        try:
            match = self._get_match(match_id)
            if not match or match["status"] != "pending":
                return

            self._finish_match(match_id, "expired")
            self._send_match_closed(
                match,
                "⌛ *درخواست بازی منقضی شد.*\n\n"
                f"چون *{self._mention(match['opponent_id'], match['opponent_name'])}* در زمان مقرر بازی را نپذیرفت."
            )
        finally:
            self._cancel_timer(match_id)

    def _safe_expire_active(self, match_id: str):
        try:
            match = self._get_match(match_id)
            if not match or match["status"] != "active":
                return
            self._finalize_if_possible(match_id, force_timeout=True)
        finally:
            self._cancel_timer(match_id)

    def _resume_open_matches(self):
        rows = self._db("""
            SELECT match_id, status, created_at, accepted_at
            FROM rps_matches
            WHERE status IN ('pending', 'active')
        """, fetchall=True) or []

        now_ts = int(time.time())

        for match_id, status, created_at, accepted_at in rows:
            if status == "pending":
                remaining = self.ACCEPT_TIMEOUT - (now_ts - created_at)
                if remaining <= 0:
                    self._safe_expire_pending(match_id)
                else:
                    self._schedule_pending_timeout(match_id)
            elif status == "active":
                start_at = accepted_at or created_at
                remaining = self.CHOICE_TIMEOUT - (now_ts - start_at)
                if remaining <= 0:
                    self._safe_expire_active(match_id)
                else:
                    self._schedule_active_timeout(match_id)

    def _challenge_buttons(self, match_id: str):
        return {
            "inline_keyboard": [
                [
                    {"text": "✅ قبول مبارزه", "callback_data": f"rpsa:{match_id}"},
                    {"text": "❌ رد کردن", "callback_data": f"rpsd:{match_id}"}
                ]
            ]
        }

    def _choice_buttons(self, match_id: str):
        return {
            "inline_keyboard": [
                [
                    {"text": "🪨 سنگ", "callback_data": f"rpsc:{match_id}:rock"},
                    {"text": "📄 کاغذ", "callback_data": f"rpsc:{match_id}:paper"},
                    {"text": "✂️ قیچی", "callback_data": f"rpsc:{match_id}:scissors"}
                ],
                [
                    {"text": "🚫 انصراف از بازی", "callback_data": f"rpsx:{match_id}"}
                ]
            ]
        }

    def _choice_label(self, choice: Optional[str]) -> str:
        if not choice:
            return "_هنوز انتخاب نشده_"
        item = self.CHOICES[choice]
        return f"{item['emoji']} {item['fa']}"

    def _pending_text(self, match: dict) -> str:
        return (
            "🎮 *چالش جدید سنگ‌کاغذ‌قیچی!* \n\n"
            f"⚔️ *چالش‌دهنده:* {self._mention(match['challenger_id'], match['challenger_name'])}\n"
            f"🎯 *حریف:* {self._mention(match['opponent_id'], match['opponent_name'])}\n\n"
            "✨ حریف باید با دکمه‌ی زیر بازی را قبول یا رد کند.\n"
            f"⏳ *مهلت پذیرش:* {self.ACCEPT_TIMEOUT} ثانیه"
        )

    def _active_text(self, match: dict) -> str:
        ch_pick = "✅ انتخاب شد" if match["challenger_choice"] else "⏳ در انتظار انتخاب"
        op_pick = "✅ انتخاب شد" if match["opponent_choice"] else "⏳ در انتظار انتخاب"

        return (
            "🔥 *بازی سنگ‌کاغذ‌قیچی شروع شد!* \n\n"
            f"👤 {self._mention(match['challenger_id'], match['challenger_name'])}: {ch_pick}\n"
            f"👤 {self._mention(match['opponent_id'], match['opponent_name'])}: {op_pick}\n\n"
            "🕶️ انتخاب‌ها تا پایان ثبت هر دو طرف مخفی می‌مانند.\n"
            f"⏳ *مهلت انتخاب:* {self.CHOICE_TIMEOUT} ثانیه"
        )

    def _send_or_edit_main_message(self, match: dict):
        if match["status"] == "pending":
            text = self._pending_text(match)
            markup = self._challenge_buttons(match["match_id"])
        elif match["status"] == "active":
            text = self._active_text(match)
            markup = self._choice_buttons(match["match_id"])
        else:
            return

        if match.get("message_id"):
            try:
                self.ctx.api.edit_message_text(
                    match["chat_id"],
                    match["message_id"],
                    text,
                    reply_markup=markup,
                    markup_mode="auto"
                )
                return
            except Exception:
                pass

        sent = self.ctx.api.send_message(
            match["chat_id"],
            text,
            reply_markup=markup,
            markup_mode="auto"
        )
        if isinstance(sent, dict) and sent.get("message_id"):
            self._set_message_id(match["match_id"], sent["message_id"])

    def _send_match_closed(self, match: dict, body: str):
        self.ctx.api.send_message(match["chat_id"], body)

    def _determine_winner(self, challenger_choice: str, opponent_choice: str):
        if challenger_choice == opponent_choice:
            return 0
        return self.RESULT_MATRIX.get((challenger_choice, opponent_choice), 0)

    def _finalize_if_possible(self, match_id: str, force_timeout=False):
        match = self._get_match(match_id)
        if not match or match["status"] != "active":
            return

        ch = match["challenger_choice"]
        op = match["opponent_choice"]

        if not force_timeout and (not ch or not op):
            return

        self._cancel_timer(match_id)

        if force_timeout:
            if ch and not op:
                self._finish_match(match_id, "finished")
                self._update_stats(
                    winner_id=match["challenger_id"],
                    loser_id=match["opponent_id"],
                    winner_name=match["challenger_name"],
                    loser_name=match["opponent_name"]
                )
                self._send_match_closed(
                    match,
                    "⏰ *زمان بازی تمام شد!* \n\n"
                    f"🏆 برنده: {self._mention(match['challenger_id'], match['challenger_name'])}\n"
                    f"دلیل: حریف در زمان مقرر انتخابش را ثبت نکرد."
                )
                return

            if op and not ch:
                self._finish_match(match_id, "finished")
                self._update_stats(
                    winner_id=match["opponent_id"],
                    loser_id=match["challenger_id"],
                    winner_name=match["opponent_name"],
                    loser_name=match["challenger_name"]
                )
                self._send_match_closed(
                    match,
                    "⏰ *زمان بازی تمام شد!* \n\n"
                    f"🏆 برنده: {self._mention(match['opponent_id'], match['opponent_name'])}\n"
                    f"دلیل: حریف در زمان مقرر انتخابش را ثبت نکرد."
                )
                return

            self._finish_match(match_id, "expired")
            self._send_match_closed(
                match,
                "⌛ *بازی بدون نتیجه بسته شد.*\n\nهیچ‌کدام از بازیکن‌ها انتخاب نهایی ثبت نکردند."
            )
            return

        result = self._determine_winner(ch, op)

        if result == 0:
            self._finish_match(match_id, "finished")
            self._update_stats(
                winner_id=None,
                loser_id=None,
                draw_ids=[
                    (match["challenger_id"], match["challenger_name"]),
                    (match["opponent_id"], match["opponent_name"])
                ]
            )
            self._send_match_closed(
                match,
                "🤝 *نتیجه: مساوی!* \n\n"
                f"{self._mention(match['challenger_id'], match['challenger_name'])}: *{self._choice_label(ch)}*\n"
                f"{self._mention(match['opponent_id'], match['opponent_name'])}: *{self._choice_label(op)}*"
            )
            return

        if result == 1:
            winner_id = match["challenger_id"]
            winner_name = match["challenger_name"]
            loser_id = match["opponent_id"]
            loser_name = match["opponent_name"]
        else:
            winner_id = match["opponent_id"]
            winner_name = match["opponent_name"]
            loser_id = match["challenger_id"]
            loser_name = match["challenger_name"]

        self._finish_match(match_id, "finished")
        self._update_stats(
            winner_id=winner_id,
            loser_id=loser_id,
            winner_name=winner_name,
            loser_name=loser_name
        )

        self._send_match_closed(
            match,
            "🏁 *نتیجه‌ی بازی مشخص شد!* \n\n"
            f"{self._mention(match['challenger_id'], match['challenger_name'])}: *{self._choice_label(ch)}*\n"
            f"{self._mention(match['opponent_id'], match['opponent_name'])}: *{self._choice_label(op)}*\n\n"
            f"🏆 *برنده:* {self._mention(winner_id, winner_name)}"
        )

    def _help_text(self):
        return (
            "🕹️ *راهنمای سنگ‌کاغذ‌قیچی* \n\n"
            "• برای شروع، روی پیام حریف ریپلای بزن و بنویس: `سنگ کاغذ قیچی`\n"
            "• حریف باید چالش را قبول کند\n"
            "• بعد هر دو نفر با دکمه، حرکتشان را انتخاب می‌کنند\n"
            "• نتیجه بعد از ثبت هر دو نفر اعلام می‌شود\n\n"
            "📌 *نکته:* در هر گروه هم‌زمان فقط یک بازی فعال مجاز است."
        )

    def on_message(self, message):
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        chat_title = chat.get("title") or ""
        text = normalize_text(message.get("text") or "")
        message_id = message.get("message_id")
        from_user = message.get("from", {}) or {}
        user_id = from_user.get("id")

        if chat_type not in ("group", "supergroup"):
            return False

        if text == "راهنمای سنگ کاغذ قیچی":
            self.ctx.api.send_message(chat_id, self._help_text(), message_id)
            return True

        if text == "سنگ کاغذ قیچی":
            replied = message.get("reply_to_message")
            if not replied or not replied.get("from"):
                self.ctx.api.send_message(
                    chat_id,
                    "⚠️ *برای شروع بازی باید روی پیام طرف مقابل ریپلای بزنی.*\n\n"
                    "مثلاً روی پیامش ریپلای بزن و بنویس: `سنگ کاغذ قیچی`",
                    message_id
                )
                return True

            opponent_user = replied.get("from", {})
            opponent_id = opponent_user.get("id")
            opponent_name = self._player_name(opponent_user)
            challenger_name = self._player_name(from_user)

            if opponent_id == user_id:
                self.ctx.api.send_message(
                    chat_id,
                    "😄 *نمی‌توانی خودت را به مبارزه دعوت کنی!*",
                    message_id
                )
                return True

            existing = self._find_open_match_for_chat(chat_id)
            if existing:
                self.ctx.api.send_message(
                    chat_id,
                    "⏳ *الان یک بازی فعال در این گروه وجود دارد.*\n\n"
                    "اول همان بازی را تمام یا لغو کنید، بعد مبارزه‌ی جدید بسازید.",
                    message_id
                )
                return True

            match = self._create_match(
                chat_id=chat_id,
                chat_title=chat_title,
                challenger_id=user_id,
                challenger_name=challenger_name,
                opponent_id=opponent_id,
                opponent_name=opponent_name,
            )
            self._send_or_edit_main_message(match)
            self._schedule_pending_timeout(match["match_id"])
            return True

        return False

    def on_callback_query(self, callback_query):
        data = callback_query.get("data") or ""
        callback_query_id = callback_query.get("id")
        actor = callback_query.get("from", {}) or {}
        actor_id = actor.get("id")

        if not data.startswith(("rpsa:", "rpsd:", "rpsc:", "rpsx:")):
            return False

        parts = data.split(":")
        action = parts[0]
        match_id = parts[1] if len(parts) > 1 else None
        choice = parts[2] if len(parts) > 2 else None

        if not match_id:
            self.ctx.api.answer_callback_query(callback_query_id, "دکمه نامعتبر است.", show_alert=True)
            return True

        match = self._get_match(match_id)
        if not match:
            self.ctx.api.answer_callback_query(callback_query_id, "این بازی دیگر وجود ندارد.", show_alert=True)
            return True

        if action == "rpsa":
            if match["status"] != "pending":
                self.ctx.api.answer_callback_query(callback_query_id, "این چالش دیگر فعال نیست.")
                return True

            if actor_id != match["opponent_id"]:
                self.ctx.api.answer_callback_query(
                    callback_query_id,
                    "⛔ فقط حریف دعوت‌شده می‌تواند این چالش را قبول کند.",
                    show_alert=True
                )
                return True

            self._accept_match(match_id)
            self._schedule_active_timeout(match_id)
            self.ctx.api.answer_callback_query(callback_query_id, "✅ چالش را قبول کردی. حالا حرکتت را انتخاب کن!")
            self._send_or_edit_main_message(self._get_match(match_id))
            return True

        if action == "rpsd":
            if match["status"] != "pending":
                self.ctx.api.answer_callback_query(callback_query_id, "این چالش دیگر فعال نیست.")
                return True

            if actor_id != match["opponent_id"]:
                self.ctx.api.answer_callback_query(
                    callback_query_id,
                    "⛔ فقط حریف دعوت‌شده می‌تواند این چالش را رد کند.",
                    show_alert=True
                )
                return True

            self._finish_match(match_id, "declined")
            self._cancel_timer(match_id)
            self.ctx.api.answer_callback_query(callback_query_id, "❌ چالش رد شد.")
            self._send_match_closed(
                match,
                f"❌ *چالش رد شد.*\n\n"
                f"{self._mention(match['opponent_id'], match['opponent_name'])} این بازی را نپذیرفت."
            )
            return True

        if action == "rpsc":
            if match["status"] != "active":
                self.ctx.api.answer_callback_query(callback_query_id, "این بازی دیگر فعال نیست.")
                return True

            if actor_id not in (match["challenger_id"], match["opponent_id"]):
                self.ctx.api.answer_callback_query(
                    callback_query_id,
                    "⛔ فقط دو بازیکن این مسابقه می‌توانند حرکت انتخاب کنند.",
                    show_alert=True
                )
                return True

            if choice not in self.CHOICES:
                self.ctx.api.answer_callback_query(callback_query_id, "انتخاب نامعتبر است.", show_alert=True)
                return True

            self._set_choice(match_id, actor_id, choice)
            match = self._get_match(match_id)

            self.ctx.api.answer_callback_query(
                callback_query_id,
                f"✅ انتخابت ثبت شد: {self.CHOICES[choice]['emoji']} {self.CHOICES[choice]['fa']}"
            )

            self._send_or_edit_main_message(match)
            self._finalize_if_possible(match_id, force_timeout=False)
            return True

        if action == "rpsx":
            if match["status"] != "active":
                self.ctx.api.answer_callback_query(callback_query_id, "این بازی دیگر فعال نیست.")
                return True

            if actor_id not in (match["challenger_id"], match["opponent_id"]):
                self.ctx.api.answer_callback_query(
                    callback_query_id,
                    "⛔ فقط بازیکن‌های این مسابقه می‌توانند انصراف بدهند.",
                    show_alert=True
                )
                return True

            self._finish_match(match_id, "cancelled")
            self._cancel_timer(match_id)

            winner_id = match["opponent_id"] if actor_id == match["challenger_id"] else match["challenger_id"]
            winner_name = match["opponent_name"] if actor_id == match["challenger_id"] else match["challenger_name"]
            loser_name = match["challenger_name"] if actor_id == match["challenger_id"] else match["opponent_name"]

            self._update_stats(
                winner_id=winner_id,
                loser_id=actor_id,
                winner_name=winner_name,
                loser_name=loser_name
            )

            self.ctx.api.answer_callback_query(callback_query_id, "🚫 از بازی انصراف دادی.")
            self._send_match_closed(
                match,
                "🚫 *یکی از بازیکن‌ها انصراف داد.*\n\n"
                f"🏆 *برنده:* {self._mention(winner_id, winner_name)}"
            )
            return True

        return False