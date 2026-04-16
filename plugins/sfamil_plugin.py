import os
import re
import json
import time
import uuid
import random
import sqlite3
import threading
from typing import Dict, List, Optional, Tuple

from helpers import normalize_text, escape_md


class Plugin:
    NAME = "sfamil"

    LETTERS = [
        "ا", "ب", "پ", "ت", "ث", "ج", "چ", "ح", "خ",
        "د", "ذ", "ر", "ز", "ژ", "س", "ش", "ص", "ض",
        "ط", "ظ", "ع", "غ", "ف", "ق", "ک", "گ", "ل",
        "م", "ن", "و", "ه", "ی"
    ]

    DEFAULT_CATEGORIES = [
        "اسم",
        "فامیل",
        "کشور",
        "شهر",
        "غذا",
        "میوه",
        "رنگ",
        "حیوان",
    ]

    DEFAULT_ROUND_SECONDS = 150

    def __init__(self, ctx):
        self.ctx = ctx
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.db_path = os.path.join(self.base_dir, "sfamil_plugin.db")
        self.timers: Dict[str, threading.Timer] = {}
        self._init_db()
        self._resume_active_games()

    # =========================
    # DB
    # =========================
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
            CREATE TABLE IF NOT EXISTS sf_known_private_users (
                user_id INTEGER PRIMARY KEY,
                display_name TEXT,
                username TEXT,
                updated_at INTEGER NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS sf_games (
                game_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                chat_title TEXT,
                host_id INTEGER NOT NULL,
                host_name TEXT NOT NULL,
                status TEXT NOT NULL,
                letter TEXT,
                categories_json TEXT NOT NULL,
                round_seconds INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                started_at INTEGER,
                ended_at INTEGER,
                lobby_message_id INTEGER,
                result_message_id INTEGER
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS sf_players (
                game_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                display_name TEXT,
                username TEXT,
                joined_at INTEGER NOT NULL,
                submitted_at INTEGER,
                PRIMARY KEY (game_id, user_id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS sf_answers (
                game_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                answers_json TEXT NOT NULL,
                score INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (game_id, user_id)
            )
        """)

        conn.commit()
        conn.close()

    # =========================
    # logging
    # =========================
    def _log(self, *args):
        try:
            self.ctx.api.log("[SFAMIL]", *args)
        except Exception:
            pass

    # =========================
    # helpers
    # =========================
    def _now(self) -> int:
        return int(time.time())

    def _new_game_id(self) -> str:
        return uuid.uuid4().hex[:8]

    def _player_name(self, user: dict) -> str:
        user = user or {}
        first_name = normalize_text(user.get("first_name") or "")
        last_name = normalize_text(user.get("last_name") or "")
        username = normalize_text(user.get("username") or "")

        full_name = normalize_text(f"{first_name} {last_name}")
        if full_name:
            return full_name
        if first_name:
            return first_name
        if username:
            return f"@{username.lstrip('@')}"
        return "کاربر"

    def _player_username(self, user: dict) -> str:
        return normalize_text((user or {}).get("username") or "")

    def _mention(self, user_id: int, name: str) -> str:
        return f"[{escape_md(name)}](tg://user?id={user_id})"

    def _normalize_answer(self, text: str) -> str:
        text = normalize_text(text or "")
        text = text.replace("‌", " ")
        text = re.sub(r"\s+", " ", text).strip()
        return text.lower()

    def _starts_with_letter(self, value: str, letter: str) -> bool:
        value = normalize_text(value)
        if not value:
            return False
        return value.startswith(letter)

    # =========================
    # known private users
    # =========================
    def _mark_private_ready(self, user_id: int, display_name: str, username: str):
        self._db("""
            INSERT OR REPLACE INTO sf_known_private_users (user_id, display_name, username, updated_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, display_name, username, self._now()))

    def _has_private_ready(self, user_id: int) -> bool:
        row = self._db("""
            SELECT user_id FROM sf_known_private_users WHERE user_id = ?
        """, (user_id,), fetchone=True)
        return row is not None

    # =========================
    # game storage
    # =========================
    def _get_active_game_by_chat(self, chat_id: int):
        row = self._db("""
            SELECT game_id, chat_id, chat_title, host_id, host_name, status, letter,
                   categories_json, round_seconds, created_at, started_at, ended_at,
                   lobby_message_id, result_message_id
            FROM sf_games
            WHERE chat_id = ? AND status IN ('lobby', 'collecting')
            ORDER BY created_at DESC
            LIMIT 1
        """, (chat_id,), fetchone=True)
        return self._row_to_game(row)

    def _get_game(self, game_id: str):
        row = self._db("""
            SELECT game_id, chat_id, chat_title, host_id, host_name, status, letter,
                   categories_json, round_seconds, created_at, started_at, ended_at,
                   lobby_message_id, result_message_id
            FROM sf_games
            WHERE game_id = ?
        """, (game_id,), fetchone=True)
        return self._row_to_game(row)

    def _row_to_game(self, row):
        if not row:
            return None
        return {
            "game_id": row[0],
            "chat_id": row[1],
            "chat_title": row[2] or "",
            "host_id": row[3],
            "host_name": row[4],
            "status": row[5],
            "letter": row[6],
            "categories": json.loads(row[7]),
            "round_seconds": row[8],
            "created_at": row[9],
            "started_at": row[10],
            "ended_at": row[11],
            "lobby_message_id": row[12],
            "result_message_id": row[13],
        }

    def _create_game(self, chat_id: int, chat_title: str, host_id: int, host_name: str):
        game_id = self._new_game_id()
        self._db("""
            INSERT INTO sf_games (
                game_id, chat_id, chat_title, host_id, host_name, status, letter,
                categories_json, round_seconds, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'lobby', NULL, ?, ?, ?)
        """, (
            game_id, chat_id, chat_title, host_id, host_name,
            json.dumps(self.DEFAULT_CATEGORIES, ensure_ascii=False),
            self.DEFAULT_ROUND_SECONDS,
            self._now()
        ))
        self._add_player(game_id, host_id, host_name, "")
        return self._get_game(game_id)

    def _update_lobby_message_id(self, game_id: str, message_id: int):
        self._db("""
            UPDATE sf_games SET lobby_message_id = ? WHERE game_id = ?
        """, (message_id, game_id))

    def _start_game(self, game_id: str, letter: str):
        self._db("""
            UPDATE sf_games
            SET status = 'collecting', letter = ?, started_at = ?
            WHERE game_id = ?
        """, (letter, self._now(), game_id))

    def _finish_game(self, game_id: str):
        self._db("""
            UPDATE sf_games
            SET status = 'finished', ended_at = ?
            WHERE game_id = ?
        """, (self._now(), game_id))

    def _cancel_game(self, game_id: str):
        self._db("""
            UPDATE sf_games
            SET status = 'cancelled', ended_at = ?
            WHERE game_id = ?
        """, (self._now(), game_id))

    def _add_player(self, game_id: str, user_id: int, display_name: str, username: str):
        self._db("""
            INSERT OR REPLACE INTO sf_players (
                game_id, user_id, display_name, username, joined_at, submitted_at
            )
            VALUES (
                ?, ?, ?, ?,
                COALESCE((SELECT joined_at FROM sf_players WHERE game_id = ? AND user_id = ?), ?),
                COALESCE((SELECT submitted_at FROM sf_players WHERE game_id = ? AND user_id = ?), NULL)
            )
        """, (
            game_id, user_id, display_name, username,
            game_id, user_id, self._now(),
            game_id, user_id
        ))

    def _set_submitted(self, game_id: str, user_id: int):
        self._db("""
            UPDATE sf_players
            SET submitted_at = ?
            WHERE game_id = ? AND user_id = ?
        """, (self._now(), game_id, user_id))

    def _get_players(self, game_id: str):
        rows = self._db("""
            SELECT user_id, display_name, username, joined_at, submitted_at
            FROM sf_players
            WHERE game_id = ?
            ORDER BY joined_at ASC
        """, (game_id,), fetchall=True) or []

        return [
            {
                "user_id": r[0],
                "display_name": r[1] or "کاربر",
                "username": r[2] or "",
                "joined_at": r[3],
                "submitted_at": r[4],
            }
            for r in rows
        ]

    def _get_player(self, game_id: str, user_id: int):
        row = self._db("""
            SELECT user_id, display_name, username, joined_at, submitted_at
            FROM sf_players
            WHERE game_id = ? AND user_id = ?
        """, (game_id, user_id), fetchone=True)
        if not row:
            return None
        return {
            "user_id": row[0],
            "display_name": row[1] or "کاربر",
            "username": row[2] or "",
            "joined_at": row[3],
            "submitted_at": row[4],
        }

    def _save_answers(self, game_id: str, user_id: int, answers: List[str]):
        self._db("""
            INSERT OR REPLACE INTO sf_answers (game_id, user_id, answers_json, score)
            VALUES (?, ?, ?, COALESCE((SELECT score FROM sf_answers WHERE game_id = ? AND user_id = ?), 0))
        """, (game_id, user_id, json.dumps(answers, ensure_ascii=False), game_id, user_id))
        self._set_submitted(game_id, user_id)

    def _set_score(self, game_id: str, user_id: int, score: int):
        self._db("""
            UPDATE sf_answers SET score = ? WHERE game_id = ? AND user_id = ?
        """, (score, game_id, user_id))

    def _get_answers(self, game_id: str):
        rows = self._db("""
            SELECT user_id, answers_json, score
            FROM sf_answers
            WHERE game_id = ?
        """, (game_id,), fetchall=True) or []

        out = {}
        for r in rows:
            out[r[0]] = {
                "answers": json.loads(r[1]),
                "score": r[2],
            }
        return out

    def _all_submitted(self, game_id: str) -> bool:
        rows = self._get_players(game_id)
        return len(rows) > 0 and all(p["submitted_at"] is not None for p in rows)

    # =========================
    # timers / resume
    # =========================
    def _resume_active_games(self):
        rows = self._db("""
            SELECT game_id
            FROM sf_games
            WHERE status = 'collecting'
        """, fetchall=True) or []

        for (game_id,) in rows:
            game = self._get_game(game_id)
            if not game or not game["started_at"]:
                continue

            ends_at = game["started_at"] + game["round_seconds"]
            remaining = ends_at - self._now()

            if remaining <= 0:
                try:
                    self._finalize_game(game_id)
                except Exception as e:
                    self._log("resume finalize error", repr(e))
            else:
                self._schedule_finish(game_id, remaining)

    def _schedule_finish(self, game_id: str, seconds: int):
        if game_id in self.timers:
            try:
                self.timers[game_id].cancel()
            except Exception:
                pass

        timer = threading.Timer(seconds, self._safe_finalize_from_timer, args=(game_id,))
        timer.daemon = True
        self.timers[game_id] = timer
        timer.start()

    def _safe_finalize_from_timer(self, game_id: str):
        try:
            self._finalize_game(game_id)
        except Exception as e:
            self._log("timer finalize error", repr(e))

    # =========================
    # rendering
    # =========================
    def _lobby_buttons(self, game_id: str):
        return {
            "inline_keyboard": [
                [
                    {"text": "🎮 ورود به بازی", "callback_data": f"sfj:{game_id}"},
                    {"text": "▶️ شروع بازی", "callback_data": f"sfs:{game_id}"}
                ],
                [
                    {"text": "❌ لغو بازی", "callback_data": f"sfc:{game_id}"}
                ]
            ]
        }

    def _lobby_text(self, game: dict) -> str:
        players = self._get_players(game["game_id"])
        lines = [
            "🎲 *اتاق جدید اسم‌فامیل ساخته شد!*",
            "",
            f"👑 *میزبان:* {escape_md(game['host_name'])}",
            f"🆔 *کد بازی:* `{game['game_id']}`",
            f"🧩 *دسته‌ها:* {', '.join([escape_md(x) for x in game['categories']])}",
            f"⏳ *زمان هر دور:* {game['round_seconds']} ثانیه",
            "",
            f"👥 *شرکت‌کننده‌ها:* {len(players)} نفر",
        ]

        for i, p in enumerate(players, start=1):
            lines.append(f"• *{i})* {self._mention(p['user_id'], p['display_name'])}")

        lines += [
            "",
            "✨ هر کسی می‌خواهد وارد بازی شود، روی دکمه‌ی *ورود به بازی* بزند.",
            "🚀 فقط میزبان می‌تواند بازی را شروع کند.",
            "_پاسخ‌ها در پیوی ربات گرفته می‌شوند و نتیجه در گروه اعلام می‌شود._"
        ]
        return "\n".join(lines)

    def _send_or_edit_lobby(self, game: dict):
        text = self._lobby_text(game)
        markup = self._lobby_buttons(game["game_id"])

        if game.get("lobby_message_id"):
            try:
                self.ctx.api.edit_message_text(
                    game["chat_id"],
                    game["lobby_message_id"],
                    text,
                    reply_markup=markup,
                    markup_mode="auto"
                )
                return
            except Exception as e:
                self._log("edit lobby failed", repr(e))

        sent = self.ctx.api.send_message(
            game["chat_id"],
            text,
            reply_markup=markup,
            markup_mode="auto"
        )
        if isinstance(sent, dict) and sent.get("message_id"):
            self._update_lobby_message_id(game["game_id"], sent["message_id"])

    def _private_prompt_text(self, game: dict, player_name: str) -> str:
        cats = game["categories"]
        numbered = "\n".join([f"*{i+1})* {escape_md(c)}" for i, c in enumerate(cats)])
        return (
            f"🎯 *نوبت ثبت جواب‌هاست!*\n\n"
            f"🏷️ *بازی:* `{game['game_id']}`\n"
            f"🔤 *حرف این دور:* *{escape_md(game['letter'])}*\n"
            f"👤 *شرکت‌کننده:* {escape_md(player_name)}\n"
            f"⏳ *مهلت:* {game['round_seconds']} ثانیه\n\n"
            f"🧩 *دسته‌ها:*\n{numbered}\n\n"
            f"✍️ جواب‌ها را به یکی از این دو شکل بفرست:\n"
            f"• هر جواب در یک خط، به ترتیب دسته‌ها\n"
            f"• یا به شکل `اسم: ...` و `فامیل: ...`\n\n"
            f"_بعد از ارسال، اگر خواستی می‌توانی دوباره جواب‌هایت را اصلاح کنی تا قبل از پایان زمان._"
        )

    def _group_private_needed_text(self, game: dict, user_id: int, display_name: str) -> str:
        return (
            f"📩 {self._mention(user_id, display_name)}\n\n"
            f"برای شرکت در *اسم‌فامیل*، اول یک پیام در *پیوی ربات* بفرست تا جواب‌هایت را آنجا بگیرم ✨\n"
            f"بعد از آن، فرم این دور برایت ارسال می‌شود."
        )

    # =========================
    # answer parsing / scoring
    # =========================
    def _parse_private_answers(self, game: dict, text: str) -> List[str]:
        text = text.strip()
        categories = game["categories"]

        # حالت key:value
        if ":" in text or "：" in text:
            lines = [normalize_text(x) for x in text.splitlines() if normalize_text(x)]
            mapping = {}
            for line in lines:
                line = line.replace("：", ":")
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                mapping[normalize_text(k)] = normalize_text(v)

            answers = []
            for c in categories:
                answers.append(mapping.get(normalize_text(c), ""))
            return answers

        # حالت خط‌به‌خط
        lines = [normalize_text(x) for x in text.splitlines()]
        answers = lines[:len(categories)]
        while len(answers) < len(categories):
            answers.append("")
        return answers

    def _score_game(self, game: dict) -> Tuple[List[dict], List[dict]]:
        players = self._get_players(game["game_id"])
        player_map = {p["user_id"]: p for p in players}
        answers_map = self._get_answers(game["game_id"])
        cats = game["categories"]
        letter = game["letter"]

        # category index => normalized answer => list[user_id]
        buckets = [dict() for _ in cats]

        for user_id, payload in answers_map.items():
            answers = payload["answers"]
            for i, ans in enumerate(answers):
                raw = normalize_text(ans)
                norm = self._normalize_answer(raw)
                if not raw:
                    continue
                if not self._starts_with_letter(raw, letter):
                    continue
                buckets[i].setdefault(norm, []).append(user_id)

        results = []
        for p in players:
            uid = p["user_id"]
            payload = answers_map.get(uid, {"answers": [""] * len(cats), "score": 0})
            answers = payload["answers"]
            total = 0
            breakdown = []

            for i, ans in enumerate(answers):
                raw = normalize_text(ans)
                norm = self._normalize_answer(raw)
                pts = 0
                reason = "خالی"

                if raw and self._starts_with_letter(raw, letter):
                    users = buckets[i].get(norm, [])
                    if len(users) <= 1:
                        pts = 10
                        reason = "یکتا"
                    else:
                        pts = 5
                        reason = "مشترک"
                elif raw:
                    reason = "حرف اشتباه"

                breakdown.append({
                    "category": cats[i],
                    "answer": raw,
                    "points": pts,
                    "reason": reason,
                })
                total += pts

            self._set_score(game["game_id"], uid, total)
            results.append({
                "user_id": uid,
                "display_name": p["display_name"],
                "username": p["username"],
                "score": total,
                "answers": breakdown,
                "submitted": p["submitted_at"] is not None,
            })

        results.sort(key=lambda x: (-x["score"], x["display_name"]))
        return results, players

    def _result_text(self, game: dict, results: List[dict], players: List[dict]) -> str:
        lines = [
            "🏁 *نتیجه‌ی اسم‌فامیل اعلام شد!*",
            "",
            f"🏷️ *گروه:* {escape_md(game['chat_title'] or '')}",
            f"🔤 *حرف این دور:* *{escape_md(game['letter'])}*",
            f"🆔 *کد بازی:* `{game['game_id']}`",
            "",
            "🏆 *رتبه‌بندی نهایی*",
        ]

        for i, r in enumerate(results, start=1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "▫️"
            lines.append(
                f"{medal} *{i})* {self._mention(r['user_id'], r['display_name'])} — *{r['score']}* امتیاز"
            )

        not_submitted = [p for p in players if p["submitted_at"] is None]
        if not_submitted:
            lines += ["", "⌛ *افرادی که جواب ثبت نکردند:*"]
            for p in not_submitted:
                lines.append(f"• {self._mention(p['user_id'], p['display_name'])}")

        lines += [
            "",
            "_امتیازدهی خودکار:_ جواب یکتا = ۱۰، جواب مشترک = ۵، جواب نامعتبر/خالی = ۰"
        ]
        return "\n".join(lines)

    # =========================
    # game flow
    # =========================
    def _start_round(self, game_id: str):
        game = self._get_game(game_id)
        if not game or game["status"] != "lobby":
            return

        players = self._get_players(game_id)
        if len(players) < 2:
            self.ctx.api.send_message(
                game["chat_id"],
                "⚠️ *برای شروع اسم‌فامیل حداقل ۲ نفر لازم است.*"
            )
            return

        letter = random.choice(self.LETTERS)
        self._start_game(game_id, letter)
        game = self._get_game(game_id)

        # ارسال راهنما در پیوی یا منشن در گروه
        ready_count = 0
        for p in players:
            if self._has_private_ready(p["user_id"]):
                ready_count += 1
                try:
                    self.ctx.api.send_message(
                        p["user_id"],
                        self._private_prompt_text(game, p["display_name"])
                    )
                except Exception as e:
                    self._log("private send failed", p["user_id"], repr(e))
                    self.ctx.api.send_message(
                        game["chat_id"],
                        self._group_private_needed_text(game, p["user_id"], p["display_name"])
                    )
            else:
                self.ctx.api.send_message(
                    game["chat_id"],
                    self._group_private_needed_text(game, p["user_id"], p["display_name"])
                )

        self.ctx.api.send_message(
            game["chat_id"],
            f"🚀 *بازی شروع شد!*\n\n"
            f"🔤 حرف این دور: *{escape_md(letter)}*\n"
            f"👥 شرکت‌کننده‌ها: *{len(players)}* نفر\n"
            f"⏳ زمان: *{game['round_seconds']}* ثانیه\n\n"
            f"📩 فرم پاسخ برای کسانی که قبلاً به پیوی ربات پیام داده‌اند ارسال شد.\n"
            f"✨ اگر کسی هنوز پیوی نداده، در گروه منشن شده است."
        )

        self._schedule_finish(game_id, game["round_seconds"])

    def _finalize_game(self, game_id: str):
        game = self._get_game(game_id)
        if not game or game["status"] != "collecting":
            return

        if game_id in self.timers:
            try:
                self.timers[game_id].cancel()
            except Exception:
                pass
            self.timers.pop(game_id, None)

        results, players = self._score_game(game)
        self._finish_game(game_id)
        game = self._get_game(game_id)

        self.ctx.api.send_message(
            game["chat_id"],
            self._result_text(game, results, players)
        )

    def _active_collecting_games_for_user(self, user_id: int):
        rows = self._db("""
            SELECT g.game_id
            FROM sf_games g
            JOIN sf_players p ON p.game_id = g.game_id
            WHERE p.user_id = ? AND g.status = 'collecting'
            ORDER BY g.started_at DESC
        """, (user_id,), fetchall=True) or []
        return [self._get_game(r[0]) for r in rows]

    # =========================
    # commands
    # =========================
    def _help_text(self) -> str:
        return (
            "🎲 *راهنمای اسم‌فامیل* \n\n"
            "• در گروه بنویس: `اسم فامیل`\n"
            "• بازیکن‌ها با دکمه وارد می‌شوند\n"
            "• میزبان بازی را شروع می‌کند\n"
            "• جواب‌ها در *پیوی ربات* جمع می‌شود\n"
            "• نتیجه در گروه اعلام می‌شود\n\n"
            "📌 *نکته مهم:* هر کسی می‌خواهد جواب بدهد باید حداقل یک‌بار به پیوی ربات پیام داده باشد."
        )

    # =========================
    # plugin entrypoints
    # =========================
    def on_message(self, message):
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        chat_title = chat.get("title") or ""
        text = normalize_text(message.get("text") or "")
        message_id = message.get("message_id")
        from_user = message.get("from", {})
        user_id = from_user.get("id")
        display_name = self._player_name(from_user)
        username = self._player_username(from_user)

        # هر پیام خصوصی => کاربر آماده‌ی پیوی
        if chat_type == "private" and user_id:
            self._mark_private_ready(user_id, display_name, username)

            active_games = self._active_collecting_games_for_user(user_id)
            if not active_games:
                return False

            if len(active_games) > 1:
                self.ctx.api.send_message(
                    user_id,
                    "⚠️ *هم‌زمان در چند بازی فعال هستی.*\n\n"
                    "فعلاً این نسخه جواب را برای بازی‌های هم‌زمان جداگانه با دستور ویژه نگرفته؛ "
                    "اول یکی از بازی‌ها را تمام کن یا فقط در یک بازی فعال بمان."
                )
                return True

            game = active_games[0]
            player = self._get_player(game["game_id"], user_id)
            if not player:
                return True

            answers = self._parse_private_answers(game, text)
            self._save_answers(game["game_id"], user_id, answers)

            self.ctx.api.send_message(
                user_id,
                f"✅ *جواب‌ها ثبت شد.*\n\n"
                f"🏷️ بازی: `{game['game_id']}`\n"
                f"🔤 حرف: *{escape_md(game['letter'])}*\n"
                f"✨ اگر خواستی تا قبل از پایان زمان، دوباره پیام بده تا جایگزین شود."
            )

            if self._all_submitted(game["game_id"]):
                self._finalize_game(game["game_id"])

            return True

        if chat_type not in ("group", "supergroup"):
            return False

        if text == "راهنمای اسم فامیل":
            self.ctx.api.send_message(chat_id, self._help_text(), message_id)
            return True

        if text == "اسم فامیل":
            existing = self._get_active_game_by_chat(chat_id)
            if existing:
                self.ctx.api.send_message(
                    chat_id,
                    "⚠️ *الان یک بازی فعال در این گروه وجود دارد.*\n\n"
                    "اول همان بازی را تمام یا لغو کنید، بعد بازی جدید بسازید.",
                    message_id
                )
                return True

            game = self._create_game(chat_id, chat_title, user_id, display_name)
            self._send_or_edit_lobby(game)
            return True

        return False

    def on_callback_query(self, callback_query):
        data = callback_query.get("data") or ""
        callback_query_id = callback_query.get("id")
        from_user = callback_query.get("from", {}) or {}
        user_id = from_user.get("id")
        display_name = self._player_name(from_user)
        username = self._player_username(from_user)

        if not data.startswith(("sfj:", "sfs:", "sfc:")):
            return False

        try:
            action, game_id = data.split(":", 1)
        except ValueError:
            self.ctx.api.answer_callback_query(callback_query_id, "داده دکمه نامعتبر است.", show_alert=True)
            return True

        game = self._get_game(game_id)
        if not game:
            self.ctx.api.answer_callback_query(callback_query_id, "این بازی دیگر وجود ندارد.", show_alert=True)
            return True

        if game["status"] not in ("lobby", "collecting"):
            self.ctx.api.answer_callback_query(callback_query_id, "این بازی قبلاً بسته شده است 🔒")
            return True

        if action == "sfj":
            if game["status"] != "lobby":
                self.ctx.api.answer_callback_query(callback_query_id, "بازی قبلاً شروع شده است.", show_alert=True)
                return True

            exists = self._get_player(game_id, user_id)
            if exists:
                self.ctx.api.answer_callback_query(callback_query_id, "تو قبلاً وارد این بازی شده‌ای ✅")
                return True

            self._add_player(game_id, user_id, display_name, username)
            self.ctx.api.answer_callback_query(callback_query_id, "🎉 با موفقیت وارد بازی شدی.")
            self._send_or_edit_lobby(self._get_game(game_id))
            return True

        if action == "sfs":
            if user_id != game["host_id"]:
                self.ctx.api.answer_callback_query(callback_query_id, "فقط میزبان می‌تواند بازی را شروع کند.", show_alert=True)
                return True

            if game["status"] != "lobby":
                self.ctx.api.answer_callback_query(callback_query_id, "بازی قبلاً شروع شده است.", show_alert=True)
                return True

            self.ctx.api.answer_callback_query(callback_query_id, "🚀 بازی شروع شد!")
            self._start_round(game_id)
            return True

        if action == "sfc":
            if user_id != game["host_id"]:
                self.ctx.api.answer_callback_query(callback_query_id, "فقط میزبان می‌تواند بازی را لغو کند.", show_alert=True)
                return True

            self._cancel_game(game_id)
            if game_id in self.timers:
                try:
                    self.timers[game_id].cancel()
                except Exception:
                    pass
                self.timers.pop(game_id, None)

            self.ctx.api.answer_callback_query(callback_query_id, "🛑 بازی لغو شد.")
            self.ctx.api.send_message(
                game["chat_id"],
                f"🛑 *بازی اسم‌فامیل لغو شد.*\n\n"
                f"👑 میزبان: {escape_md(game['host_name'])}"
            )
            return True

        return False