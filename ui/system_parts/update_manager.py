"""主窗口更新管理。"""

from __future__ import annotations

import logging
import time
from typing import Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout

from utils.updater import (
    UPDATE_STATUS_CHECKING,
    UPDATE_STATUS_DOWNLOADING,
    UPDATE_STATUS_ERROR,
    UPDATE_STATUS_IDLE,
    UPDATE_STATUS_INSTALLING,
    UPDATE_STATUS_READY,
    check_update_now,
    get_update_status,
    request_install,
    spawn_updater_process,
    stop_updater,
)

logger = logging.getLogger(__name__)

STATUS_POLL_INTERVAL_MS = 2000
INSTALL_START_TIMEOUT_SEC = 15.0


class UpdateNotificationDialog(QDialog):
    """更新包就绪后给用户的安装确认弹窗。"""

    def __init__(self, update_info: dict, parent=None):
        super().__init__(parent)
        self.update_info = update_info
        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle("发现新版本")
        self.setFixedWidth(360)
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        new_version = self.update_info.get("new_version", "?")
        current_version = self.update_info.get("current_version", "?")

        title_label = QLabel(f"发现新版本 v{new_version}")
        title_label.setObjectName("update_title_label")
        layout.addWidget(title_label)

        version_label = QLabel(f"当前版本: v{current_version}")
        layout.addWidget(version_label)

        changelog = list(self.update_info.get("changelog", []) or [])
        if changelog:
            changelog_label = QLabel("更新内容:")
            layout.addWidget(changelog_label)

            changelog_content = QLabel("\n".join(f"  - {item}" for item in changelog))
            changelog_content.setWordWrap(True)
            layout.addWidget(changelog_content)

        tip_label = QLabel("安装包已下载完成，是否立即安装？")
        tip_label.setObjectName("update_tip_label")
        layout.addWidget(tip_label)

        self.install_btn = QPushButton("立即安装")
        self.install_btn.setProperty("class", "primary")
        self.install_btn.clicked.connect(self.accept)
        layout.addWidget(self.install_btn)

        self.later_btn = QPushButton("稍后提醒")
        self.later_btn.clicked.connect(self.reject)
        layout.addWidget(self.later_btn)


class UpdateIntegration:
    """主窗口更新管理器。"""

    def __init__(self, main_window):
        self.main_window = main_window
        self.updater_thread = None
        self.status_timer: Optional[QTimer] = None
        self._notified = False
        self._last_status = None
        self._is_running = False
        self._check_interval = 3600
        self._ready_remind_at = 0.0
        self._pending_install_request = False
        self._install_requested_at = 0.0

    def start(self, check_interval: int = 3600) -> None:
        """启动更新守护线程。"""
        if self._is_running:
            logger.warning("更新线程已在运行，跳过重复启动")
            return

        self._check_interval = max(60, int(check_interval or 3600))
        logger.info(f"启动更新线程，检查间隔: {self._check_interval} 秒")
        self.updater_thread = spawn_updater_process(check_interval=self._check_interval)
        if not self.updater_thread:
            logger.error("更新线程启动失败")
            return

        self._is_running = True
        self._ensure_status_timer()

    def stop(self) -> None:
        """停止更新线程。"""
        self._stop_status_timer()
        stop_updater()

        thread = self.updater_thread
        self.updater_thread = None
        if thread and thread.is_alive():
            try:
                thread.join(timeout=2.5)
            except Exception as exc:
                logger.warning(f"等待更新线程退出失败: {exc}")
            if thread.is_alive():
                logger.warning("更新线程未在超时内退出")

        self._is_running = False
        self._last_status = None
        self._notified = False
        self._ready_remind_at = 0.0
        self._pending_install_request = False
        self._install_requested_at = 0.0
        logger.info("更新线程已停止")

    def enable(self, enabled: bool) -> None:
        """启用或停用自动更新。"""
        if enabled and not self._is_running:
            self.start(check_interval=self._check_interval)
        elif not enabled and self._is_running:
            self.stop()

    def check_now(self) -> None:
        """立即检查更新。"""
        self._notified = False
        self._ready_remind_at = 0.0
        if not self._ensure_updater_running():
            return
        check_update_now()

    def setup_menu_action(self, help_menu) -> QAction:
        """在帮助菜单中添加检查更新入口。"""
        check_update_action = QAction("检查更新", self.main_window)
        check_update_action.triggered.connect(self._manual_check)
        help_menu.addAction(check_update_action)
        return check_update_action

    def _manual_check(self) -> None:
        self._notified = False
        self._ready_remind_at = 0.0
        if not self._ensure_updater_running():
            QMessageBox.warning(self.main_window, "检查更新", "更新服务启动失败，请稍后重试。")
            return
        check_update_now()
        QMessageBox.information(
            self.main_window,
            "检查更新",
            "正在后台检查更新，如有新版本将自动下载并通知您。",
        )

    def _ensure_updater_running(self) -> bool:
        if self._is_running:
            return True
        self.start(check_interval=self._check_interval)
        return self._is_running

    def _ensure_status_timer(self) -> None:
        if self.status_timer is None:
            self.status_timer = QTimer(self.main_window)
            self.status_timer.timeout.connect(self._poll_status)
        if not self.status_timer.isActive():
            self.status_timer.start(STATUS_POLL_INTERVAL_MS)

    def _stop_status_timer(self) -> None:
        timer = self.status_timer
        self.status_timer = None
        if timer is None:
            return
        timer.stop()
        try:
            timer.deleteLater()
        except Exception as exc:
            logger.warning(f"释放更新状态定时器失败: {exc}")

    def _poll_status(self) -> None:
        self._handle_install_timeout()

        status = get_update_status()
        if not isinstance(status, dict):
            return

        current_status = str(status.get("status", "") or "").strip()
        data = status.get("data")
        if not isinstance(data, dict):
            data = {}

        if current_status == UPDATE_STATUS_READY:
            self._handle_ready_status(data)
            self._last_status = current_status
            return

        if current_status == self._last_status:
            return
        self._last_status = current_status

        if current_status in {UPDATE_STATUS_IDLE, UPDATE_STATUS_CHECKING, UPDATE_STATUS_DOWNLOADING}:
            if current_status != UPDATE_STATUS_IDLE:
                self._notified = False
                self._ready_remind_at = 0.0
            return

        if current_status == UPDATE_STATUS_INSTALLING:
            self._handle_installing_status()
            return

        if current_status == UPDATE_STATUS_ERROR:
            self._handle_error_status(data)

    def _handle_ready_status(self, data: dict) -> None:
        if self._pending_install_request:
            return

        now_ts = time.time()
        if self._notified and (self._ready_remind_at <= 0 or now_ts < self._ready_remind_at):
            return

        self._notified = True
        self._ready_remind_at = 0.0
        self._show_update_notification(data)

    def _handle_installing_status(self) -> None:
        if not self._pending_install_request:
            logger.info("检测到安装流程已启动")
            return

        self._pending_install_request = False
        self._install_requested_at = 0.0
        logger.info("检测到安装程序已启动，主程序准备退出")
        QApplication.quit()

    def _handle_error_status(self, data: dict) -> None:
        error_msg = str(data.get("error", "") or "未知错误").strip()
        logger.error(f"更新错误: {error_msg}")

        if not self._pending_install_request:
            return

        self._pending_install_request = False
        self._install_requested_at = 0.0
        QMessageBox.warning(self.main_window, "更新失败", f"安装程序启动失败：{error_msg}")

    def _handle_install_timeout(self) -> None:
        if not self._pending_install_request:
            return

        if time.time() - self._install_requested_at < INSTALL_START_TIMEOUT_SEC:
            return

        self._pending_install_request = False
        self._install_requested_at = 0.0
        logger.error("等待安装程序启动超时")
        QMessageBox.warning(self.main_window, "更新失败", "安装程序启动超时，请稍后重试。")

    def _build_update_info(self, data: dict) -> dict:
        from app_core.app_config import APP_VERSION

        return {
            "new_version": data.get("new_version", "?"),
            "current_version": APP_VERSION,
            "changelog": data.get("changelog", []),
        }

    def _show_update_notification(self, data: dict) -> None:
        dialog = UpdateNotificationDialog(self._build_update_info(data), self.main_window)
        accepted = int(dialog.exec()) == int(QDialog.DialogCode.Accepted)
        if accepted:
            self._do_install()
            return

        self._ready_remind_at = time.time() + self._check_interval
        logger.info(f"用户选择稍后提醒，将在 {self._check_interval} 秒后再次提示")

    def _do_install(self) -> None:
        """请求更新线程启动安装流程。"""
        if not self._ensure_updater_running():
            QMessageBox.warning(self.main_window, "更新失败", "更新服务未启动，无法开始安装。")
            return

        QMessageBox.information(
            self.main_window,
            "准备更新",
            "即将关闭程序并启动安装向导，请按提示完成更新。",
        )
        self._pending_install_request = True
        self._install_requested_at = time.time()
        request_install()


def add_update_to_window(
    main_window,
    help_menu=None,
    auto_check: bool = True,
    check_interval: int = 3600,
) -> UpdateIntegration:
    """为主窗口挂载更新功能。"""
    integration = UpdateIntegration(main_window)

    if help_menu:
        integration.setup_menu_action(help_menu)

    if auto_check:
        delay_ms = 3000
        logger.info(f"更新功能将在 {delay_ms // 1000} 秒后启动，检查间隔: {check_interval} 秒")
        QTimer.singleShot(delay_ms, lambda: integration.start(check_interval=check_interval))

    return integration
