from .main_window_hotkey_core_mixin import MainWindowHotkeyCoreMixin
from .main_window_hotkey_handlers_mixin import MainWindowHotkeyHandlersMixin
from .main_window_hotkey_plugin_mixin import MainWindowHotkeyPluginMixin
from .main_window_hotkey_setup_mixin import MainWindowHotkeySetupMixin


class MainWindowHotkeyMixin(
    MainWindowHotkeyCoreMixin,
    MainWindowHotkeyPluginMixin,
    MainWindowHotkeySetupMixin,
    MainWindowHotkeyHandlersMixin,
):
    pass
