# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout

from ..market.market_settings_panel import MarketSettingsPanel
from utils.window_coordinate_common import center_window_on_widget_screen
from utils.window_activation_utils import show_and_activate_overlay


class MarketWindow(QDialog):
    entry_workflow_open_requested = Signal(str)
    entry_workflow_favorite_requested = Signal(str, str)
    package_uninstalled = Signal(str, str)

    def __init__(
        self,
        current_config: dict,
        config_provider: Optional[Callable[[], dict]] = None,
        config_applier: Optional[Callable[[dict], None]] = None,
        parent=None,
    ):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("脚本共享平台")
        self.setModal(False)
        self.setMinimumSize(920, 600)
        self.resize(1040, 660)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)

        self.market_panel = MarketSettingsPanel(
            current_config,
            config_provider=config_provider,
            config_applier=config_applier,
            parent=self,
        )
        self.market_panel.entry_workflow_open_requested.connect(self.entry_workflow_open_requested)
        self.market_panel.entry_workflow_favorite_requested.connect(self.entry_workflow_favorite_requested)
        self.market_panel.package_uninstalled.connect(self.package_uninstalled)
        layout.addWidget(self.market_panel, 1)

    def refresh_market_data(self, force_remote: bool = True) -> None:
        self.market_panel.refresh_installed_packages()
        if force_remote or not getattr(self.market_panel, 'remote_packages', None):
            self.market_panel.refresh_remote_packages()

    def show_window(self) -> None:
        self.refresh_market_data(force_remote=True)
        if self.isMinimized():
            self.showNormal()
        center_window_on_widget_screen(self, self.parentWidget())
        show_and_activate_overlay(self, log_prefix='脚本共享平台', focus=True)
