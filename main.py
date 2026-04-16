from config import TOKEN, POLL_TIMEOUT
from bale_api import BaleAPI
from app_context import AppContext
from plugin_manager import PluginManager
from helpers import log
import time


def main():
    api = BaleAPI(TOKEN, log)
    ctx = AppContext(api)
    ctx.startup()

    manager = PluginManager(ctx)
    manager.load_all()
    manager.on_startup()

    offset = None

    while True:
        try:
            updates = api.get_updates(offset=offset, timeout=POLL_TIMEOUT)

            for update in updates:
                offset = update["update_id"] + 1

                message = update.get("message")
                callback_query = update.get("callback_query")
                channel_post = update.get("channel_post")

                if message:
                    ctx.remember_group_and_message(message)
                    manager.on_message(message)
                    continue

                if callback_query:
                    callback_message = callback_query.get("message") or {}
                    if callback_message:
                        ctx.remember_group_and_message(callback_message)
                    manager.on_callback_query(callback_query)
                    continue

                if channel_post:
                    manager.on_channel_post(channel_post)
                    continue

        except Exception as e:
            log("[LOG] ERROR", repr(e))
            time.sleep(3)


if __name__ == "__main__":
    main()