import json
import requests


class BaleAPI:
    def __init__(self, token: str, log_func=print):
        self.base_url = f"https://tapi.bale.ai/bot{token}"
        self.log = log_func

    def api_get(self, method: str, params=None):
        url = f"{self.base_url}/{method}"
        res = requests.get(url, params=params or {}, timeout=40)
        res.raise_for_status()
        data = res.json()

        if not data.get("ok"):
            raise Exception(f"API error in {method}: {json.dumps(data, ensure_ascii=False)}")

        return data.get("result")

    def api_post(self, method: str, payload=None):
        url = f"{self.base_url}/{method}"
        res = requests.post(url, json=payload or {}, timeout=40)
        res.raise_for_status()
        data = res.json()

        if not data.get("ok"):
            raise Exception(f"API error in {method}: {json.dumps(data, ensure_ascii=False)}")

        return data.get("result")

    def get_updates(self, offset=None, timeout=25):
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        return self.api_get("getUpdates", params)

    def send_message(
        self,
        chat_id,
        text,
        reply_to_message_id=None,
        parse_mode="Markdown",
        reply_markup=None,
        disable_web_page_preview=None,
    ):
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }

        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if disable_web_page_preview is not None:
            payload["disable_web_page_preview"] = disable_web_page_preview

        self.log("[LOG] SEND", payload)
        return self.api_post("sendMessage", payload)

    def send_animation(self, chat_id, animation, reply_to_message_id=None, caption=None, parse_mode="Markdown"):
        payload = {
            "chat_id": chat_id,
            "animation": animation,
            "parse_mode": parse_mode,
        }
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        if caption:
            payload["caption"] = caption

        self.log("[LOG] SEND ANIMATION", payload)
        return self.api_post("sendAnimation", payload)

    def edit_message_text(self, chat_id, message_id, text, parse_mode="Markdown", reply_markup=None):
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        self.log("[LOG] EDIT", payload)
        return self.api_post("editMessageText", payload)

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        payload = {
            "callback_query_id": callback_query_id,
            "show_alert": bool(show_alert),
        }
        if text is not None:
            payload["text"] = text

        self.log("[LOG] CALLBACK ANSWER", payload)
        return self.api_post("answerCallbackQuery", payload)

    def pin_chat_message(self, chat_id, message_id, disable_notification=False):
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "disable_notification": bool(disable_notification),
        }
        self.log("[LOG] PIN", payload)
        return self.api_post("pinChatMessage", payload)