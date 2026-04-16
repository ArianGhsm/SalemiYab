# plugins/xo_plugin.py
# -*- coding: utf-8 -*-

import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from helpers import normalize_text, escape_md


@dataclass
class XOGame:
    game_id: str
    chat_id: int
    created_by_message_id: int
    host_id: int
    host_name: str
    host_symbol: str
    guest_symbol: str
    guest_id: Optional[int] = None
    guest_name: str = ""
    board_size: Optional[int] = None
    board: List[str] = field(default_factory=list)
    turn: Optional[str] = None
    status: str = "waiting_for_guest"  # waiting_for_guest | waiting_for_size | playing | finished
    created_at: int = field(default_factory=lambda: int(time.time()))
    render_message_id: Optional[int] = None
    winner_symbol: Optional[str] = None
    finished_reason: str = ""


class Plugin:
    def __init__(self, ctx):
        self.ctx = ctx
        self.games: Dict[str, XOGame] = {}
        self.active_game_by_chat: Dict[int, str] = {}

    # =========================
    # low level safe helpers
    # =========================
    def _log(self, *args):
        try:
            if hasattr(self.ctx.api, "log"):
                self.ctx.api.log("[XO]", *args)
        except Exception:
            pass

    def _safe_answer(self, callback_query_id, text=None, show_alert=False):
        try:
            self.ctx.api.answer_callback_query(
                callback_query_id,
                text=text,
                show_alert=show_alert,
            )
        except Exception as e:
            self._log("answer_callback_query error", repr(e))

    def _safe_send(self, chat_id, text, reply_markup=None, reply_to_message_id=None):
        return self.ctx.api.send_message(
            chat_id,
            text,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup,
        )

    def _safe_edit(self, chat_id, message_id, text, reply_markup=None):
        return self.ctx.api.edit_message_text(
            chat_id,
            message_id,
            text,
            reply_markup=reply_markup,
        )

    def _new_game_id(self) -> str:
        return uuid.uuid4().hex[:8]

    def _player_name(self, user: dict) -> str:
        user = user or {}
        first_name = normalize_text(user.get("first_name") or "")
        last_name = normalize_text(user.get("last_name") or "")
        username = normalize_text(user.get("username") or "")

        full_name = normalize_text(f"{first_name} {last_name}")

        if full_name and username:
            return f"{full_name} (@{username.lstrip('@')})"
        if full_name:
            return full_name
        if first_name and username:
            return f"{first_name} (@{username.lstrip('@')})"
        if first_name:
            return first_name
        if username:
            return f"@{username.lstrip('@')}"
        return "کاربر ناشناس"

    def _build_rows(self, buttons, width):
        return [buttons[i:i + width] for i in range(0, len(buttons), width)]

    def _empty_markup(self):
        # برای حذف کامل دکمه‌ها
        return {"inline_keyboard": []}

    def _join_markup(self, game_id: str):
        return {
            "inline_keyboard": [
                [{"text": "من داوطلب هستم 🙋‍♂️", "callback_data": f"xj:{game_id}"}],
                [{"text": "انصراف ❌", "callback_data": f"xc:{game_id}"}],
            ]
        }

    def _size_markup(self, game_id: str):
        return {
            "inline_keyboard": [
                [
                    {"text": "3×3 🎯", "callback_data": f"xs:{game_id}:3"},
                    {"text": "4×4 🧩", "callback_data": f"xs:{game_id}:4"},
                ],
                [{"text": "انصراف ❌", "callback_data": f"xc:{game_id}"}],
            ]
        }

    def _board_markup(self, game: XOGame, locked: bool = False):
        size = game.board_size or 3
        buttons = []

        for idx, cell in enumerate(game.board):
            text = cell if cell in ("X", "O") else "▫️"
            callback_data = f"xl:{game.game_id}" if locked else f"xm:{game.game_id}:{idx}"
            buttons.append({"text": text, "callback_data": callback_data})

        rows = self._build_rows(buttons, size)

        if locked:
            rows.append([{"text": "بازی به پایان رسیده است 🔒", "callback_data": f"xl:{game.game_id}"}])
        else:
            rows.append([{"text": "انصراف ❌", "callback_data": f"xc:{game.game_id}"}])

        return {"inline_keyboard": rows}

    def _remove_game_from_active_map(self, game: XOGame):
        current = self.active_game_by_chat.get(game.chat_id)
        if current == game.game_id:
            self.active_game_by_chat.pop(game.chat_id, None)

    def _send_or_edit_game_message(self, game: XOGame, text: str, reply_markup=None):
        if game.render_message_id is None:
            sent = self._safe_send(
                game.chat_id,
                text,
                reply_markup=reply_markup,
            )
            game.render_message_id = sent.get("message_id")
            return sent

        return self._safe_edit(
            game.chat_id,
            game.render_message_id,
            text,
            reply_markup=reply_markup,
        )

    def _status_text(self, game: XOGame) -> str:
        host = escape_md(game.host_name)
        guest = escape_md(game.guest_name) if game.guest_name else "_هنوز وارد نشده_"

        host_line = f"👤 *بازیکن اول:* {host} — نماد *{game.host_symbol}*"
        guest_symbol_text = game.guest_symbol if game.guest_name else "—"
        guest_line = f"👤 *بازیکن دوم:* {guest} — نماد *{guest_symbol_text}*"

        if game.status == "waiting_for_guest":
            return (
                "🎮 *بازی جدید XO آماده شد*\n\n"
                f"{host_line}\n"
                f"{guest_line}\n\n"
                "✨ اگر مایل به شرکت در بازی هستید، روی دکمه‌ی زیر بزنید.\n"
                "_این بازی دو نفره است و تا ورود بازیکن دوم آغاز نمی‌شود._"
            )

        if game.status == "waiting_for_size":
            return (
                "🤝 *بازیکن دوم با موفقیت وارد بازی شد*\n\n"
                f"{host_line}\n"
                f"{guest_line}\n\n"
                "📐 *حالا نوبت انتخاب ابعاد صفحه است.*\n"
                f"🌟 {host} عزیز، لطفاً یکی از گزینه‌های زیر را انتخاب کنید:\n\n"
                "• *3×3*\n"
                "• *4×4*"
            )

        if game.status == "playing":
            turn_name = game.host_name if game.turn == game.host_symbol else game.guest_name
            return (
                "✅ *بازی شروع شد!*\n\n"
                f"{host_line}\n"
                f"{guest_line}\n"
                f"📏 *ابعاد:* {game.board_size}×{game.board_size}\n"
                f"🎯 *نوبت:* {escape_md(turn_name)} — *{game.turn}*\n\n"
                "روی یکی از خانه‌ها بزنید تا حرکت ثبت شود."
            )

        if game.status == "finished":
            size_text = f"{game.board_size}×{game.board_size}" if game.board_size else "تعیین نشده"

            if game.finished_reason == "cancelled_before_start":
                return (
                    "🛑 *این بازی توسط آغازکننده پیش از شروع نهایی پایان داده شد.*\n\n"
                    f"{host_line}\n"
                    f"{guest_line}\n"
                    f"📏 *ابعاد:* {size_text}\n\n"
                    "✨ این مسابقه دیگر فعال نیست.\n"
                    "🌷 برای ساخت بازی تازه، دوباره `XO` یا `OX` بفرستید."
                )

            if game.finished_reason == "host_cancelled":
                return (
                    "🛑 *این بازی توسط آغازکننده به پایان رسید.*\n\n"
                    f"{host_line}\n"
                    f"{guest_line}\n"
                    f"📏 *ابعاد:* {size_text}\n\n"
                    "✨ بازی با درخواست بازیکن اول خاتمه داده شد.\n"
                    "🌷 برای شروع بازی جدید، دوباره `XO` یا `OX` بفرستید."
                )

            if game.finished_reason == "guest_cancelled":
                return (
                    "🛑 *این بازی با انصراف بازیکن دوم پایان یافت.*\n\n"
                    f"{host_line}\n"
                    f"{guest_line}\n"
                    f"📏 *ابعاد:* {size_text}\n\n"
                    "✨ این مسابقه دیگر فعال نیست.\n"
                    "🌷 برای شروع بازی جدید، دوباره `XO` یا `OX` بفرستید."
                )

            if game.winner_symbol == "DRAW":
                result_line = "🤝 *نتیجه:* بازی مساوی شد."
            else:
                winner_name = host if game.winner_symbol == game.host_symbol else guest
                result_line = f"🏆 *برنده:* {winner_name} — نماد *{game.winner_symbol}*"

            return (
                "🏁 *بازی XO به پایان رسید*\n\n"
                f"{host_line}\n"
                f"{guest_line}\n"
                f"📏 *ابعاد:* {size_text}\n\n"
                f"{result_line}\n\n"
                "🔒 دکمه‌های بازی قفل شده‌اند.\n"
                "🌷 برای شروع بازی جدید، دوباره `XO` یا `OX` بفرستید."
            )

        return "⚠️ وضعیت بازی نامشخص است."

    def _finish_game(self, game: XOGame, winner_symbol: Optional[str] = None, finished_reason: str = ""):
        game.status = "finished"
        game.winner_symbol = winner_symbol
        game.finished_reason = finished_reason
        self._remove_game_from_active_map(game)

    def _is_host(self, game: XOGame, user_id: int) -> bool:
        return user_id == game.host_id

    def _is_guest(self, game: XOGame, user_id: int) -> bool:
        return game.guest_id is not None and user_id == game.guest_id

    def _cell_owner_symbol(self, game: XOGame, user_id: int) -> Optional[str]:
        if self._is_host(game, user_id):
            return game.host_symbol
        if self._is_guest(game, user_id):
            return game.guest_symbol
        return None

    def _start_board(self, game: XOGame, size: int):
        game.board_size = size
        game.board = [""] * (size * size)
        game.turn = "X"
        game.status = "playing"

    def _check_winner(self, board: List[str], size: int) -> Optional[str]:
        lines = []

        for r in range(size):
            lines.append([r * size + c for c in range(size)])

        for c in range(size):
            lines.append([r * size + c for r in range(size)])

        lines.append([i * size + i for i in range(size)])
        lines.append([i * size + (size - 1 - i) for i in range(size)])

        for line in lines:
            symbols = [board[i] for i in line]
            if symbols[0] and all(s == symbols[0] for s in symbols):
                return symbols[0]

        if all(cell in ("X", "O") for cell in board):
            return "DRAW"

        return None

    # =========================
    # Message entry
    # =========================
    def on_message(self, message):
        try:
            text = normalize_text(message.get("text") or "")
            if text not in ("XO", "OX"):
                return False

            chat = message.get("chat", {}) or {}
            chat_id = chat.get("id")
            chat_type = chat.get("type")
            message_id = message.get("message_id")
            from_user = message.get("from", {}) or {}
            user_id = from_user.get("id")
            host_name = self._player_name(from_user)

            if chat_type not in ("group", "supergroup"):
                self._safe_send(
                    chat_id,
                    "⚠️ *بازی XO فقط در گروه قابل اجراست.*\n\n"
                    "برای این بازی به *دو بازیکن* نیاز داریم؛ لطفاً دستور را داخل گروه بفرستید. 👥",
                    reply_to_message_id=message_id,
                )
                return True

            existing_game_id = self.active_game_by_chat.get(chat_id)
            if existing_game_id:
                existing_game = self.games.get(existing_game_id)
                if existing_game and existing_game.status != "finished":
                    self._safe_send(
                        chat_id,
                        "⚠️ *در حال حاضر یک بازی فعال در این گروه وجود دارد.*\n\n"
                        "لطفاً ابتدا همان بازی را به پایان برسانید یا انصراف دهید، سپس بازی جدید بسازید. 🎮",
                        reply_to_message_id=message_id,
                    )
                    return True

            host_symbol = text[0]
            guest_symbol = text[1]

            game = XOGame(
                game_id=self._new_game_id(),
                chat_id=chat_id,
                created_by_message_id=message_id,
                host_id=user_id,
                host_name=host_name,
                host_symbol=host_symbol,
                guest_symbol=guest_symbol,
            )

            self.games[game.game_id] = game
            self.active_game_by_chat[chat_id] = game.game_id

            self._send_or_edit_game_message(
                game,
                self._status_text(game),
                self._join_markup(game.game_id),
            )
            return True
        except Exception as e:
            self._log("on_message error", repr(e))
            try:
                chat_id = (message.get("chat") or {}).get("id")
                if chat_id:
                    self._safe_send(
                        chat_id,
                        "⚠️ *در اجرای بازی خطایی رخ داد.*\n"
                        "لطفاً یک بار دیگر تلاش کنید.",
                    )
            except Exception:
                pass
            return True

    # =========================
    # Callback entry
    # =========================
    def on_callback_query(self, callback_query):
        cq_id = callback_query.get("id")
        try:
            data = callback_query.get("data") or ""
            if not data.startswith(("xj:", "xs:", "xm:", "xc:", "xl:")):
                return False

            from_user = callback_query.get("from", {}) or {}
            user_id = from_user.get("id")
            user_name = self._player_name(from_user)

            try:
                parts = data.split(":")
                action = parts[0]
                game_id = parts[1]
            except Exception:
                self._safe_answer(
                    cq_id,
                    "درخواست نامعتبر بود ⚠️",
                    show_alert=True,
                )
                return True

            game = self.games.get(game_id)
            if not game:
                self._safe_answer(
                    cq_id,
                    "این بازی دیگر فعال نیست. 🌫️",
                    show_alert=True,
                )
                return True

            if action == "xj":
                return self._handle_join(callback_query, game, user_id, user_name)

            if action == "xs":
                try:
                    size = int(parts[2])
                except Exception:
                    self._safe_answer(
                        cq_id,
                        "اندازه‌ی بازی نامعتبر بود ⚠️",
                        show_alert=True,
                    )
                    return True
                return self._handle_size(callback_query, game, user_id, size)

            if action == "xm":
                try:
                    index = int(parts[2])
                except Exception:
                    self._safe_answer(
                        cq_id,
                        "حرکت نامعتبر بود ⚠️",
                        show_alert=True,
                    )
                    return True
                return self._handle_move(callback_query, game, user_id, index)

            if action == "xc":
                return self._handle_cancel(callback_query, game, user_id)

            if action == "xl":
                self._safe_answer(
                    cq_id,
                    "این بازی به پایان رسیده و دکمه‌ها قفل شده‌اند 🔒",
                )
                return True

            return False
        except Exception as e:
            self._log("on_callback_query error", repr(e))
            self._safe_answer(
                cq_id,
                "⚠️ در پردازش این دکمه خطایی رخ داد.",
                show_alert=True,
            )
            return True

    # =========================
    # Callback handlers
    # =========================
    def _handle_join(self, callback_query, game: XOGame, user_id: int, user_name: str):
        cq_id = callback_query.get("id")

        if game.status != "waiting_for_guest":
            self._safe_answer(
                cq_id,
                "بازیکن دوم قبلاً مشخص شده است ✅",
            )
            return True

        if user_id == game.host_id:
            self._safe_answer(
                cq_id,
                "شما بازیکن اول هستید 🌟\nلطفاً منتظر بمانید تا بازیکن دوم وارد شود.",
                show_alert=True,
            )
            return True

        game.guest_id = user_id
        game.guest_name = user_name
        game.status = "waiting_for_size"

        # مرحله‌ی بعد باید فوراً روی همان پیام اعمال شود
        self._send_or_edit_game_message(
            game,
            self._status_text(game),
            self._size_markup(game.game_id),
        )

        self._safe_answer(
            cq_id,
            "با موفقیت به بازی اضافه شدید 🎉",
        )
        return True

    def _handle_size(self, callback_query, game: XOGame, user_id: int, size: int):
        cq_id = callback_query.get("id")

        if game.status != "waiting_for_size":
            self._safe_answer(
                cq_id,
                "الان زمان انتخاب ابعاد نیست ⏳",
            )
            return True

        if not self._is_host(game, user_id):
            self._safe_answer(
                cq_id,
                "فقط بازیکن اول می‌تواند ابعاد بازی را انتخاب کند 📐",
                show_alert=True,
            )
            return True

        if size not in (3, 4):
            self._safe_answer(
                cq_id,
                "فقط حالت‌های 3×3 و 4×4 مجاز هستند ⚠️",
                show_alert=True,
            )
            return True

        self._start_board(game, size)

        self._send_or_edit_game_message(
            game,
            self._status_text(game),
            self._board_markup(game),
        )

        self._safe_answer(
            cq_id,
            f"ابعاد بازی روی {size}×{size} تنظیم شد ✅",
        )
        return True

    def _handle_move(self, callback_query, game: XOGame, user_id: int, index: int):
        cq_id = callback_query.get("id")

        if game.status != "playing":
            self._safe_answer(
                cq_id,
                "بازی هنوز آماده‌ی حرکت نیست ⏳",
            )
            return True

        symbol = self._cell_owner_symbol(game, user_id)
        if symbol is None:
            self._safe_answer(
                cq_id,
                "شما عضو این بازی نیستید 🙏",
                show_alert=True,
            )
            return True

        if symbol != game.turn:
            self._safe_answer(
                cq_id,
                "الان نوبت شما نیست 🙂",
            )
            return True

        if index < 0 or index >= len(game.board):
            self._safe_answer(
                cq_id,
                "خانه‌ی انتخابی نامعتبر است ⚠️",
                show_alert=True,
            )
            return True

        if game.board[index] in ("X", "O"):
            self._safe_answer(
                cq_id,
                "این خانه قبلاً انتخاب شده است 🚫",
            )
            return True

        game.board[index] = symbol
        winner = self._check_winner(game.board, game.board_size or 3)

        if winner:
            self._finish_game(game, winner_symbol=winner)
            self._send_or_edit_game_message(
                game,
                self._status_text(game),
                self._board_markup(game, locked=True),
            )
            self._safe_answer(
                cq_id,
                "حرکت ثبت شد ✅",
            )
            return True

        game.turn = "O" if game.turn == "X" else "X"

        self._send_or_edit_game_message(
            game,
            self._status_text(game),
            self._board_markup(game),
        )
        self._safe_answer(
            cq_id,
            "حرکت با موفقیت ثبت شد ✨",
        )
        return True

    def _handle_cancel(self, callback_query, game: XOGame, user_id: int):
        cq_id = callback_query.get("id")

        if game.status == "finished":
            self._safe_answer(
                cq_id,
                "این بازی قبلاً تمام شده است 🔒",
            )
            return True

        is_host = self._is_host(game, user_id)
        is_guest = self._is_guest(game, user_id)

        if not is_host and not is_guest:
            self._safe_answer(
                cq_id,
                "فقط بازیکن‌های همین بازی می‌توانند انصراف دهند 🙏",
                show_alert=True,
            )
            return True

        if game.status == "waiting_for_guest":
            if not is_host:
                self._safe_answer(
                    cq_id,
                    "فقط آغازکننده‌ی بازی می‌تواند در این مرحله بازی را پایان دهد ⚠️",
                    show_alert=True,
                )
                return True

            self._finish_game(game, finished_reason="cancelled_before_start")
            self._send_or_edit_game_message(
                game,
                self._status_text(game),
                self._empty_markup(),  # حذف کامل دکمه‌ها
            )
            self._safe_answer(
                cq_id,
                "بازی با موفقیت پایان داده شد ✅",
            )
            return True

        if game.status == "waiting_for_size":
            if is_host:
                self._finish_game(game, finished_reason="host_cancelled")
            else:
                self._finish_game(game, finished_reason="guest_cancelled")

            self._send_or_edit_game_message(
                game,
                self._status_text(game),
                self._empty_markup(),  # حذف کامل دکمه‌ها
            )
            self._safe_answer(
                cq_id,
                "بازی با موفقیت خاتمه داده شد ✅",
            )
            return True

        if game.status == "playing":
            if is_host:
                self._finish_game(
                    game,
                    winner_symbol=game.guest_symbol,
                    finished_reason="host_cancelled",
                )
            else:
                self._finish_game(
                    game,
                    winner_symbol=game.host_symbol,
                    finished_reason="guest_cancelled",
                )

            self._send_or_edit_game_message(
                game,
                self._status_text(game),
                self._empty_markup(),  # طبق خواسته: دکمه‌ها کاملاً حذف شوند
            )
            self._safe_answer(
                cq_id,
                "انصراف ثبت شد و بازی پایان یافت ✅",
            )
            return True

        self._safe_answer(
            cq_id,
            "در این مرحله امکان انجام این عملیات وجود ندارد ⚠️",
            show_alert=True,
        )
        return True