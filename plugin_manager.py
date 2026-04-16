import importlib
import pkgutil
import traceback
import plugins


class PluginManager:
    def __init__(self, ctx):
        self.ctx = ctx
        self.plugins = []

    def load_all(self):
        for _, module_name, _ in pkgutil.iter_modules(plugins.__path__):
            try:
                module = importlib.import_module(f"plugins.{module_name}")
                if hasattr(module, "Plugin"):
                    plugin = module.Plugin(self.ctx)
                    self.plugins.append(plugin)
                    self.ctx.api.log("[LOG] PLUGIN LOADED", module_name)
            except Exception as e:
                self.ctx.api.log("[LOG] PLUGIN LOAD ERROR", module_name, repr(e), traceback.format_exc())

    def on_startup(self):
        for plugin in self.plugins:
            try:
                if hasattr(plugin, "on_startup"):
                    plugin.on_startup()
            except Exception as e:
                self.ctx.api.log("[LOG] PLUGIN STARTUP ERROR", plugin.__class__.__name__, repr(e), traceback.format_exc())

    def on_message(self, message: dict):
        for plugin in self.plugins:
            try:
                if hasattr(plugin, "on_message"):
                    handled = plugin.on_message(message)
                    if handled:
                        return True
            except Exception as e:
                self.ctx.api.log("[LOG] PLUGIN MESSAGE ERROR", plugin.__class__.__name__, repr(e), traceback.format_exc())
        return False

    def on_callback_query(self, callback_query: dict):
        for plugin in self.plugins:
            try:
                if hasattr(plugin, "on_callback_query"):
                    handled = plugin.on_callback_query(callback_query)
                    if handled:
                        return True
            except Exception as e:
                self.ctx.api.log("[LOG] PLUGIN CALLBACK ERROR", plugin.__class__.__name__, repr(e), traceback.format_exc())
        return False