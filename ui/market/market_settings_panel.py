# -*- coding: utf-8 -*-



from __future__ import annotations



import html

import json

from pathlib import Path

from typing import Callable, Optional



from PySide6.QtCore import Qt, Signal, QSize, QPoint

from PySide6.QtGui import QCursor

from PySide6.QtWidgets import (

    QFileDialog,

    QCheckBox,

    QDialog,

    QFrame,

    QHBoxLayout,

    QLabel,

    QLineEdit,

    QListWidget,

    QListWidgetItem,

    QMenu,

    QMessageBox,

    QPlainTextEdit,

    QPushButton,

    QSplitter,

    QTabWidget,

    QTextBrowser,

    QVBoxLayout,

    QWidget,

)



from market.auto_adjust import MarketAutoAdjustResult, MarketPrecheckAutoAdjuster

from market.environment import capture_environment_snapshot

from ui.system_parts.menu_style import apply_unified_menu_style

from market.models import (

    MarketAuthorAccount,

    MarketPackageManifest,

    PrecheckReport,

    RemoteMarketPackageSummary,

)

from market.package_identity import suggest_package_id, validate_package_id, validate_version

from market.package_manager import MarketPackageManager

from market.precheck import MarketPackagePrecheckEngine

from market.protection import is_manifest_protected

from market.refs import build_market_workflow_ref

from market.server_config import get_market_auth_server_base, get_market_update_server_base as resolve_market_update_server_base, get_market_verify_ssl

from market.storage import get_market_auth_state_path





class MarketAuthorAuthDialog(QDialog):

    def __init__(

        self,

        username: str = "",

        password: str = "",

        remember_username: bool = True,

        remember_password: bool = False,

        auto_login: bool = False,

        parent=None,

    ):

        super().__init__(parent)

        self._action = ""

        self.setWindowTitle("作者登录")

        self.setModal(True)

        self.setFixedSize(380, 240)



        layout = QVBoxLayout(self)

        layout.setContentsMargins(16, 16, 16, 16)

        layout.setSpacing(10)



        title_label = QLabel("登录或注册作者账号")

        title_label.setStyleSheet("font-size: 14px; font-weight: bold;")

        layout.addWidget(title_label)



        self.username_input = QLineEdit(self)

        self.username_input.setPlaceholderText("作者用户名")

        self.username_input.setClearButtonEnabled(True)

        self.username_input.setText(str(username or "").strip())

        layout.addWidget(self.username_input)



        self.password_input = QLineEdit(self)

        self.password_input.setPlaceholderText("作者密码")

        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)

        self.password_input.setText(str(password or ""))

        layout.addWidget(self.password_input)



        remember_layout = QHBoxLayout()

        remember_layout.setContentsMargins(0, 0, 0, 0)

        remember_layout.setSpacing(12)



        self.remember_username_checkbox = QCheckBox("记住账号")

        self.remember_username_checkbox.setChecked(bool(remember_username))

        remember_layout.addWidget(self.remember_username_checkbox)



        self.remember_password_checkbox = QCheckBox("记住密码")

        self.remember_password_checkbox.setChecked(bool(remember_password))

        remember_layout.addWidget(self.remember_password_checkbox)



        self.auto_login_checkbox = QCheckBox("自动登录")

        self.auto_login_checkbox.setChecked(bool(auto_login and remember_password))

        remember_layout.addWidget(self.auto_login_checkbox)

        remember_layout.addStretch(1)

        layout.addLayout(remember_layout)



        button_layout = QHBoxLayout()

        button_layout.setContentsMargins(0, 4, 0, 0)

        button_layout.setSpacing(8)

        button_layout.addStretch(1)



        self.cancel_button = QPushButton("取消")

        self.cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(self.cancel_button)



        self.register_button = QPushButton("注册")

        self.register_button.clicked.connect(self._accept_register)

        button_layout.addWidget(self.register_button)



        self.login_button = QPushButton("登录")

        self.login_button.clicked.connect(self._accept_login)

        button_layout.addWidget(self.login_button)



        layout.addLayout(button_layout)



        self.username_input.returnPressed.connect(self.password_input.setFocus)

        self.password_input.returnPressed.connect(self._accept_login)

        self.remember_password_checkbox.toggled.connect(self._sync_remember_options)

        self.auto_login_checkbox.toggled.connect(self._sync_auto_login)

        self._sync_remember_options(self.remember_password_checkbox.isChecked())



    def _accept_login(self) -> None:

        self._action = "login"

        self.accept()



    def _accept_register(self) -> None:

        self._action = "register"

        self.accept()



    def _sync_remember_options(self, checked: bool) -> None:

        if not checked and self.auto_login_checkbox.isChecked():

            self.auto_login_checkbox.blockSignals(True)

            self.auto_login_checkbox.setChecked(False)

            self.auto_login_checkbox.blockSignals(False)



    def _sync_auto_login(self, checked: bool) -> None:

        if checked and not self.remember_password_checkbox.isChecked():

            self.remember_password_checkbox.blockSignals(True)

            self.remember_password_checkbox.setChecked(True)

            self.remember_password_checkbox.blockSignals(False)



    @property

    def action(self) -> str:

        return str(self._action or "").strip()



    @property

    def username(self) -> str:

        return str(self.username_input.text() or "").strip()



    @property

    def password(self) -> str:

        return str(self.password_input.text() or "")



    @property

    def remember_username(self) -> bool:

        return bool(self.remember_username_checkbox.isChecked())



    @property

    def remember_password(self) -> bool:

        return bool(self.remember_password_checkbox.isChecked())



    @property

    def auto_login(self) -> bool:

        return bool(self.auto_login_checkbox.isChecked() and self.remember_password_checkbox.isChecked())





class MarketPublishDialog(QDialog):

    def __init__(

        self,

        package_manager: MarketPackageManager,

        window_title: str,

        submit_text: str,

        initial_archive_path: str = "",

        expected_package_id: str = "",

        parent=None,

    ):

        super().__init__(parent)

        self.package_manager = package_manager

        self.expected_package_id = str(expected_package_id or "").strip()

        self.selected_manifest: Optional[MarketPackageManifest] = None



        self.setWindowTitle(window_title)

        self.setModal(True)

        self.resize(620, 360)



        layout = QVBoxLayout(self)

        layout.setContentsMargins(16, 16, 16, 16)

        layout.setSpacing(10)



        top_layout = QHBoxLayout()

        top_layout.setContentsMargins(0, 0, 0, 0)

        top_layout.setSpacing(8)



        self.archive_input = QLineEdit(self)

        self.archive_input.setPlaceholderText("待发布共享平台包路径")

        self.archive_input.setClearButtonEnabled(True)

        top_layout.addWidget(self.archive_input, 1)



        self.browse_button = QPushButton("选择共享平台包")

        top_layout.addWidget(self.browse_button)

        layout.addLayout(top_layout)



        self.manifest_label = QLabel("未选择待发布共享平台包")

        self.manifest_label.setWordWrap(True)

        self.manifest_label.setObjectName("marketMutedText")

        layout.addWidget(self.manifest_label)



        self.changelog_edit = QPlainTextEdit(self)

        self.changelog_edit.setPlaceholderText("更新说明")

        self.changelog_edit.setFixedHeight(84)

        layout.addWidget(self.changelog_edit)



        self.release_notes_edit = QPlainTextEdit(self)

        self.release_notes_edit.setPlaceholderText("兼容性说明 / 审核备注")

        self.release_notes_edit.setFixedHeight(84)

        layout.addWidget(self.release_notes_edit)



        button_layout = QHBoxLayout()

        button_layout.setContentsMargins(0, 0, 0, 0)

        button_layout.setSpacing(8)

        button_layout.addStretch(1)



        self.cancel_button = QPushButton("取消")

        button_layout.addWidget(self.cancel_button)



        self.submit_button = QPushButton(submit_text)

        button_layout.addWidget(self.submit_button)

        layout.addLayout(button_layout)



        self.browse_button.clicked.connect(self._browse_archive)

        self.cancel_button.clicked.connect(self.reject)

        self.submit_button.clicked.connect(self._accept_if_valid)



        if initial_archive_path:

            self.archive_input.setText(str(initial_archive_path))

            self._load_archive_preview(initial_archive_path)



    def _browse_archive(self) -> None:

        archive_path, _ = QFileDialog.getOpenFileName(

            self,

            "选择待发布共享平台包",

            str(Path.cwd()),

            "LCA 共享平台包 (*.lca_market.zip *.zip)",

        )

        if not archive_path:

            return

        self.archive_input.setText(archive_path)

        self._load_archive_preview(archive_path)



    def _load_archive_preview(self, archive_path: str) -> bool:

        archive_text = str(archive_path or "").strip()

        if not archive_text:

            self.selected_manifest = None

            self.manifest_label.setText("未选择待发布共享平台包")

            return False

        try:

            manifest = self.package_manager.load_manifest_from_archive(archive_text)

        except Exception as exc:

            self.selected_manifest = None

            self.manifest_label.setText(f"读取共享平台包失败：{exc}")

            return False

        if self.expected_package_id and manifest.package_id != self.expected_package_id:

            self.selected_manifest = manifest

            self.manifest_label.setText(

                f"包ID不匹配：当前脚本 {self.expected_package_id}，所选共享平台包 {manifest.package_id}"

            )

            return False

        self.selected_manifest = manifest

        self.manifest_label.setText(

            f"待发布：{manifest.title or manifest.package_id} | 包ID {manifest.package_id} | 版本 {manifest.version}"

        )

        return True



    def _accept_if_valid(self) -> None:

        archive_text = str(self.archive_input.text() or "").strip()

        if not archive_text:

            QMessageBox.warning(self, "脚本共享平台", "请先选择待发布共享平台包")

            self.archive_input.setFocus()

            return

        archive_path = Path(archive_text)

        if not archive_path.exists():

            QMessageBox.warning(self, "脚本共享平台", f"待发布共享平台包不存在：\n{archive_path}")

            self.archive_input.setFocus()

            return

        if not self._load_archive_preview(archive_text):

            QMessageBox.warning(self, "脚本共享平台", "当前共享平台包不符合发布要求，请检查包ID和文件内容")

            return

        self.accept()



    def selected_archive_path(self) -> str:

        return str(self.archive_input.text() or "").strip()



    def changelog_text(self) -> str:

        return str(self.changelog_edit.toPlainText() or "").strip()



    def release_notes_text(self) -> str:

        return str(self.release_notes_edit.toPlainText() or "").strip()





class MarketBuildDialog(QDialog):

    def __init__(

        self,

        initial_data: Optional[dict] = None,

        default_values_provider: Optional[Callable[[str], dict]] = None,

        parent=None,

    ):

        super().__init__(parent)

        self._default_values_provider = default_values_provider

        self._build_data: dict = {}

        self._auto_values: dict[str, str] = {}

        self._applying_form_data = False

        self._output_path_manually_selected = False



        self.setWindowTitle("打包脚本")

        self.setModal(True)

        self.resize(700, 260)

        self.setStyleSheet("QLabel#marketMutedText { color: #667085; }")



        layout = QVBoxLayout(self)

        layout.setContentsMargins(16, 16, 16, 16)

        layout.setSpacing(10)



        helper_label = QLabel("通过选择器选择入口工作流和输出位置，避免手动填写路径。")

        helper_label.setWordWrap(True)

        helper_label.setObjectName("marketMutedText")

        layout.addWidget(helper_label)



        entry_layout = QHBoxLayout()

        entry_layout.setContentsMargins(0, 0, 0, 0)

        entry_layout.setSpacing(8)

        self.entry_workflow_input = QLineEdit(self)

        self.entry_workflow_input.setReadOnly(True)

        self.entry_workflow_input.setPlaceholderText("请选择入口工作流")

        entry_layout.addWidget(self.entry_workflow_input, 1)

        self.browse_entry_button = QPushButton("选择入口")

        entry_layout.addWidget(self.browse_entry_button)

        layout.addLayout(entry_layout)



        output_layout = QHBoxLayout()

        output_layout.setContentsMargins(0, 0, 0, 0)

        output_layout.setSpacing(8)

        self.output_path_input = QLineEdit(self)

        self.output_path_input.setReadOnly(True)

        self.output_path_input.setPlaceholderText("请选择共享平台包保存位置")

        output_layout.addWidget(self.output_path_input, 1)

        self.browse_output_button = QPushButton("保存位置")

        output_layout.addWidget(self.browse_output_button)

        layout.addLayout(output_layout)



        meta_layout = QHBoxLayout()

        meta_layout.setContentsMargins(0, 0, 0, 0)

        meta_layout.setSpacing(8)

        self.package_id_input = QLineEdit(self)

        self.package_id_input.setPlaceholderText("包ID")

        meta_layout.addWidget(self.package_id_input, 2)

        self.version_input = QLineEdit(self)

        self.version_input.setPlaceholderText("版本号")

        self.version_input.setText("1.0.0")

        meta_layout.addWidget(self.version_input, 1)

        self.title_input = QLineEdit(self)

        self.title_input.setPlaceholderText("脚本名称")

        meta_layout.addWidget(self.title_input, 2)

        layout.addLayout(meta_layout)



        extra_layout = QHBoxLayout()

        extra_layout.setContentsMargins(0, 0, 0, 0)

        extra_layout.setSpacing(8)

        self.author_input = QLineEdit(self)

        self.author_input.setPlaceholderText("作者")

        extra_layout.addWidget(self.author_input, 1)

        self.category_input = QLineEdit(self)

        self.category_input.setPlaceholderText("分类")

        extra_layout.addWidget(self.category_input, 1)

        layout.addLayout(extra_layout)



        self.status_label = QLabel("")

        self.status_label.setWordWrap(True)

        self.status_label.setVisible(False)

        layout.addWidget(self.status_label)



        button_layout = QHBoxLayout()

        button_layout.setContentsMargins(0, 4, 0, 0)

        button_layout.setSpacing(8)

        button_layout.addStretch(1)

        self.cancel_button = QPushButton("取消")

        button_layout.addWidget(self.cancel_button)

        self.confirm_button = QPushButton("开始打包")

        button_layout.addWidget(self.confirm_button)

        layout.addLayout(button_layout)



        self.browse_entry_button.clicked.connect(self._browse_entry_workflow)

        self.browse_output_button.clicked.connect(self._browse_output_path)

        self.cancel_button.clicked.connect(self.reject)

        self.confirm_button.clicked.connect(self._accept_if_valid)

        self.package_id_input.textChanged.connect(self._refresh_output_path_if_needed)

        self.version_input.textChanged.connect(self._refresh_output_path_if_needed)

        self.title_input.returnPressed.connect(self._accept_if_valid)



        self._apply_build_data(initial_data or {})

        entry_workflow = str(self.entry_workflow_input.text() or "").strip()

        if entry_workflow:

            self._auto_values = {

                key: str(value or "").strip()

                for key, value in self._default_values(entry_workflow).items()

                if key in {"package_id", "version", "title", "author", "output_path"}

            }



    def _set_status(self, message: str, error: bool = False) -> None:

        message_text = str(message or "").strip()

        self.status_label.setVisible(bool(message_text))

        self.status_label.setText(message_text)

        self.status_label.setStyleSheet("color: #d93025;" if error else "color: #667085;")



    def _default_values(self, entry_workflow: str) -> dict:

        if callable(self._default_values_provider):

            return dict(self._default_values_provider(entry_workflow) or {})

        entry_text = str(entry_workflow or "").strip()

        title = Path(entry_text).stem if entry_text else ""

        package_id = suggest_package_id(title) if title else ""

        version = "1.0.0"

        output_path = str(Path.cwd() / f"{package_id}-{version}.lca_market.zip") if package_id else ""

        return {

            "entry_workflow": entry_text,

            "output_path": output_path,

            "package_id": package_id,

            "version": version,

            "title": title,

            "author": "",

            "category": "",

        }



    def _current_form_data(self) -> dict:

        return {

            "entry_workflow": str(self.entry_workflow_input.text() or "").strip(),

            "output_path": str(self.output_path_input.text() or "").strip(),

            "package_id": str(self.package_id_input.text() or "").strip(),

            "version": str(self.version_input.text() or "").strip(),

            "title": str(self.title_input.text() or "").strip(),

            "author": str(self.author_input.text() or "").strip(),

            "category": str(self.category_input.text() or "").strip(),

        }



    def _apply_build_data(self, build_data: Optional[dict]) -> None:

        data = dict(build_data or {})

        self._applying_form_data = True

        try:

            self.entry_workflow_input.setText(str(data.get("entry_workflow") or "").strip())

            self.output_path_input.setText(str(data.get("output_path") or "").strip())

            self.package_id_input.setText(str(data.get("package_id") or "").strip())

            self.version_input.setText(str(data.get("version") or "").strip() or "1.0.0")

            self.title_input.setText(str(data.get("title") or "").strip())

            self.author_input.setText(str(data.get("author") or "").strip())

            self.category_input.setText(str(data.get("category") or "").strip())

        finally:

            self._applying_form_data = False



    def _refresh_output_path_if_needed(self, _text: str = "") -> None:

        if self._applying_form_data or self._output_path_manually_selected:

            return

        package_id = suggest_package_id(self.package_id_input.text()) or ""

        version = str(self.version_input.text() or "1.0.0").strip() or "1.0.0"

        if not package_id:

            return

        output_path = str(Path.cwd() / f"{package_id}-{version}.lca_market.zip")

        self._applying_form_data = True

        try:

            self.output_path_input.setText(output_path)

        finally:

            self._applying_form_data = False

        self._auto_values["output_path"] = output_path



    def _apply_defaults_from_entry(self, entry_workflow: str) -> None:

        entry_text = str(entry_workflow or "").strip()

        if not entry_text:

            return

        current_data = self._current_form_data()

        defaults = self._default_values(entry_text)

        merged = dict(current_data)

        merged["entry_workflow"] = entry_text



        for key in ("package_id", "version", "title", "author"):

            current_value = str(current_data.get(key) or "").strip()

            auto_value = str(self._auto_values.get(key) or "").strip()

            default_value = str(defaults.get(key) or "").strip()

            if not current_value or current_value == auto_value:

                merged[key] = default_value



        if not str(merged.get("category") or "").strip():

            merged["category"] = str(defaults.get("category") or "").strip()



        current_output = str(current_data.get("output_path") or "").strip()

        auto_output = str(self._auto_values.get("output_path") or "").strip()

        default_output = str(defaults.get("output_path") or "").strip()

        if not self._output_path_manually_selected and (not current_output or current_output == auto_output):

            merged["output_path"] = default_output



        self._auto_values = {

            "package_id": str(defaults.get("package_id") or "").strip(),

            "version": str(defaults.get("version") or "").strip(),

            "title": str(defaults.get("title") or "").strip(),

            "author": str(defaults.get("author") or "").strip(),

            "output_path": str(merged.get("output_path") or "").strip(),

        }

        self._apply_build_data(merged)



    def _browse_entry_workflow(self) -> None:

        entry_workflow, _ = QFileDialog.getOpenFileName(

            self,

            "选择入口工作流",

            str(Path.cwd()),

            "JSON文件 (*.json);;所有文件 (*)",

        )

        if not entry_workflow:

            return

        self._apply_defaults_from_entry(entry_workflow)

        self._set_status("")



    def _browse_output_path(self) -> None:

        package_id = suggest_package_id(self.package_id_input.text()) or "package"

        version = str(self.version_input.text() or "1.0.0").strip() or "1.0.0"

        default_filename = f"{package_id}-{version}.lca_market.zip"

        output_path, _ = QFileDialog.getSaveFileName(

            self,

            "保存共享平台包",

            str(Path.cwd() / default_filename),

            "LCA 共享平台包 (*.lca_market.zip *.zip)",

        )

        if not output_path:

            return

        if not str(output_path).lower().endswith(".zip"):

            output_path = f"{output_path}.lca_market.zip"

        self.output_path_input.setText(str(output_path))

        self._output_path_manually_selected = True

        self._set_status("")



    def _accept_if_valid(self) -> None:

        build_data = self._current_form_data()

        entry_workflow = str(build_data.get("entry_workflow") or "").strip()

        if not entry_workflow:

            self._set_status("请先选择入口工作流", error=True)

            return

        if not Path(entry_workflow).exists():

            self._set_status(f"入口工作流不存在：{entry_workflow}", error=True)

            return



        package_id = suggest_package_id(build_data.get("package_id"))

        if not package_id:

            self._set_status("包ID不能为空", error=True)

            return

        try:

            package_id = validate_package_id(package_id)

        except ValueError as exc:

            self._set_status(str(exc), error=True)

            return



        version = str(build_data.get("version") or "").strip() or "1.0.0"

        try:

            version = validate_version(version)

        except ValueError as exc:

            self._set_status(str(exc), error=True)

            return



        title = str(build_data.get("title") or "").strip()

        if not title:

            self._set_status("脚本名称不能为空", error=True)

            return



        output_path = str(build_data.get("output_path") or "").strip() or str(Path.cwd() / f"{package_id}-{version}.lca_market.zip")

        output_dir = Path(output_path).expanduser().resolve().parent

        if not output_dir.exists():

            self._set_status(f"输出目录不存在：{output_dir}", error=True)

            return



        self._build_data = {

            "entry_workflow": entry_workflow,

            "output_path": output_path,

            "package_id": package_id,

            "version": version,

            "title": title,

            "author": str(build_data.get("author") or "").strip(),

            "category": str(build_data.get("category") or "").strip(),

        }

        self.accept()



    def build_data(self) -> dict:

        return dict(self._build_data)



class MarketSettingsPanel(QWidget):

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

        super().__init__(parent)

        self.current_config = dict(current_config or {})

        self.config_provider = config_provider

        self.config_applier = config_applier

        self.package_manager = MarketPackageManager()

        self.precheck_engine = MarketPackagePrecheckEngine()

        self.precheck_auto_adjuster = MarketPrecheckAutoAdjuster()



        self.remote_packages: list[RemoteMarketPackageSummary] = []

        self.remote_package_map: dict[str, RemoteMarketPackageSummary] = {}

        self.installed_manifest_map: dict[str, MarketPackageManifest] = {}

        self.pending_archive_path: Optional[Path] = None

        self.pending_manifest: Optional[MarketPackageManifest] = None

        self.pending_report: Optional[PrecheckReport] = None

        self.pending_remote_key: str = ""

        self._last_auto_adjust_result: Optional[MarketAutoAdjustResult] = None



        self._detail_mode: str = "empty"

        self._detail_manifest: Optional[MarketPackageManifest] = None

        self._detail_report: Optional[PrecheckReport] = None

        self._detail_remote: Optional[RemoteMarketPackageSummary] = None

        self._detail_entry_path: Optional[Path] = None

        self._feedback_mode: str = "hidden"

        self._feedback_items: list = []

        self._market_scope: str = "all"

        self.author_account = MarketAuthorAccount()

        self._author_auth_state = self._load_author_auth_state()

        self._author_auth_pending_options = dict(self._author_auth_state)



        self._build_ui()

        self._refresh_author_ui()

        self._restore_author_login_state()

        self.refresh_installed_packages()

        self.refresh_remote_packages()



    def _build_ui(self) -> None:

        self.setObjectName("marketSettingsPanel")

        self.setStyleSheet(

            "QFrame#marketPublishCard {"

            " border: 1px solid rgba(127, 127, 127, 0.22);"

            " border-radius: 12px;"

            " background: rgba(255, 255, 255, 0.72);"

            "}"

            "QFrame#marketHeroCard { border: none; background: transparent; }"

            "QFrame#marketSidebarPanel, QFrame#marketListPanel, QFrame#marketDetailPanel {"

            " border: none;"

            " border-radius: 12px;"

            " background: rgba(255, 255, 255, 0.50);"

            "}"

            "QLabel#marketMutedText { color: #667085; }"

            "QLabel#marketSectionTitle { font-size: 14px; font-weight: bold; }"

            "QPushButton#marketScopeButton {"

            " text-align: left;"

            " padding: 9px 12px;"

            " border-radius: 6px;"

            " border: 1px solid rgba(127, 127, 127, 0.28);"

            " background: rgba(255, 255, 255, 0.88);"

            "}"

            "QPushButton#marketScopeButton:checked {"

            " font-weight: bold;"

            " color: #0b57d0;"

            " border: 1px solid rgba(11, 87, 208, 0.58);"

            " background: rgba(11, 87, 208, 0.10);"

            "}"

            "QListWidget#workflowMarketList { border: none; background: transparent; outline: none; }"

            "QListWidget#workflowMarketList::item {"

            " padding: 7px 10px;"

            " margin: 0;"

            " border: none;"

            " color: #1f2937;"

            " background: transparent;"

            "}"

            "QListWidget#workflowMarketList::item:hover {"

            " color: #0b57d0;"

            " background: transparent;"

            "}"

            "QListWidget#workflowMarketList::item:selected {"

            " color: #0b57d0;"

            " background: transparent;"

            "}"

            "QSplitter#marketMainSplitter::handle { background: transparent; }"

            "QTabWidget#marketDetailTabs::pane { border: none; top: -1px; }"

            "QTabWidget#marketDetailTabs QTabBar { background: transparent; }"

            "QTabWidget#marketDetailTabs QTabBar::tab {"

            " min-width: 0px;"

            " padding: 8px 4px 10px 4px;"

            " margin: 0 18px 0 0;"

            " border: none;"

            " border-bottom: 2px solid transparent;"

            " color: #667085;"

            " background: transparent;"

            " font-weight: 500;"

            "}"

            "QTabWidget#marketDetailTabs QTabBar::tab:hover {"

            " color: #344054;"

            " border-bottom: 2px solid rgba(11, 87, 208, 0.22);"

            "}"

            "QTabWidget#marketDetailTabs QTabBar::tab:selected {"

            " color: #0b57d0;"

            " font-weight: 600;"

            " border-bottom: 2px solid #0b57d0;"

            "}"

        )



        root_layout = QVBoxLayout(self)

        root_layout.setContentsMargins(0, 0, 0, 0)

        root_layout.setSpacing(8)



        hero_card = QFrame(self)

        hero_card.setObjectName("marketHeroCard")

        hero_layout = QVBoxLayout(hero_card)

        hero_layout.setContentsMargins(4, 2, 4, 4)

        hero_layout.setSpacing(8)



        hero_top_layout = QHBoxLayout()

        hero_top_layout.setContentsMargins(0, 0, 0, 0)

        hero_top_layout.setSpacing(12)



        hero_title_box = QVBoxLayout()

        hero_title_box.setContentsMargins(0, 0, 0, 0)

        hero_title_box.setSpacing(4)



        hero_title_label = QLabel("脚本共享平台")

        hero_title_label.setObjectName("marketSectionTitle")

        hero_title_box.addWidget(hero_title_label)



        self.market_summary_label = QLabel("正在加载脚本共享平台...")

        self.market_summary_label.setWordWrap(True)

        self.market_summary_label.setObjectName("marketMutedText")

        hero_title_box.addWidget(self.market_summary_label)



        hero_top_layout.addLayout(hero_title_box, 1)



        account_box = QVBoxLayout()

        account_box.setContentsMargins(0, 0, 0, 0)

        account_box.setSpacing(6)



        self.author_status_label = QLabel("作者未登录")

        self.author_status_label.setWordWrap(True)

        self.author_status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.author_status_label.setObjectName("marketMutedText")

        account_box.addWidget(self.author_status_label)



        author_action_layout = QHBoxLayout()

        author_action_layout.setContentsMargins(0, 0, 0, 0)

        author_action_layout.setSpacing(8)



        self.my_packages_checkbox = QCheckBox("只看我的包")

        self.my_packages_checkbox.setVisible(False)

        author_action_layout.addWidget(self.my_packages_checkbox)



        self.author_auth_button = QPushButton("作者登录 / 注册")

        self.author_auth_button.setMinimumHeight(30)

        author_action_layout.addWidget(self.author_auth_button)



        self.author_logout_button = QPushButton("退出作者")

        self.author_logout_button.setMinimumHeight(30)

        author_action_layout.addWidget(self.author_logout_button)

        account_box.addLayout(author_action_layout)



        hero_top_layout.addLayout(account_box)

        hero_layout.addLayout(hero_top_layout)



        toolbar_layout = QHBoxLayout()

        toolbar_layout.setContentsMargins(0, 0, 0, 0)

        toolbar_layout.setSpacing(8)



        self.search_input = QLineEdit(self)

        self.search_input.setPlaceholderText("搜索脚本、包ID、作者、分类")

        self.search_input.setClearButtonEnabled(True)

        self.search_input.setMinimumHeight(34)

        toolbar_layout.addWidget(self.search_input, 1)



        self.search_button = QPushButton("搜索")

        self.search_button.setMinimumHeight(34)

        toolbar_layout.addWidget(self.search_button)



        self.refresh_button = QPushButton("刷新")

        self.refresh_button.setMinimumHeight(34)

        toolbar_layout.addWidget(self.refresh_button)



        self.import_button = QPushButton("导入本地包")

        self.import_button.setMinimumHeight(34)

        toolbar_layout.addWidget(self.import_button)



        self.build_button = QPushButton("打包脚本")

        self.build_button.setMinimumHeight(34)

        toolbar_layout.addWidget(self.build_button)



        hero_layout.addLayout(toolbar_layout)

        root_layout.addWidget(hero_card)





        self.operation_status_label = QLabel("")

        self.operation_status_label.setWordWrap(True)

        self.operation_status_label.setVisible(False)

        root_layout.addWidget(self.operation_status_label)



        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        splitter.setObjectName("marketMainSplitter")

        splitter.setChildrenCollapsible(False)

        splitter.setHandleWidth(10)



        sidebar_container = QFrame(self)

        sidebar_container.setObjectName("marketSidebarPanel")

        sidebar_container.setMinimumWidth(186)

        sidebar_container.setMaximumWidth(220)

        sidebar_layout = QVBoxLayout(sidebar_container)

        sidebar_layout.setContentsMargins(12, 12, 12, 12)

        sidebar_layout.setSpacing(8)



        sidebar_title_label = QLabel("浏览")

        sidebar_title_label.setObjectName("marketSectionTitle")

        sidebar_layout.addWidget(sidebar_title_label)



        sidebar_hint_label = QLabel("先发现脚本，再下载预检，最后安装运行。")

        sidebar_hint_label.setObjectName("marketMutedText")

        sidebar_hint_label.setWordWrap(True)

        sidebar_layout.addWidget(sidebar_hint_label)



        self.market_scope_all_button = QPushButton("发现")

        self.market_scope_all_button.setObjectName("marketScopeButton")

        self.market_scope_all_button.setCheckable(True)

        self.market_scope_all_button.setMinimumHeight(38)

        sidebar_layout.addWidget(self.market_scope_all_button)



        self.market_scope_runnable_button = QPushButton("可运行")

        self.market_scope_runnable_button.setObjectName("marketScopeButton")

        self.market_scope_runnable_button.setCheckable(True)

        self.market_scope_runnable_button.setMinimumHeight(38)

        sidebar_layout.addWidget(self.market_scope_runnable_button)



        self.market_scope_installed_button = QPushButton("已安装")

        self.market_scope_installed_button.setObjectName("marketScopeButton")

        self.market_scope_installed_button.setCheckable(True)

        self.market_scope_installed_button.setMinimumHeight(38)

        sidebar_layout.addWidget(self.market_scope_installed_button)



        self.market_scope_mine_button = QPushButton("我的发布")

        self.market_scope_mine_button.setObjectName("marketScopeButton")

        self.market_scope_mine_button.setCheckable(True)

        self.market_scope_mine_button.setMinimumHeight(38)

        sidebar_layout.addWidget(self.market_scope_mine_button)



        sidebar_layout.addSpacing(6)

        sidebar_summary_title = QLabel("当前概况")

        sidebar_summary_title.setObjectName("marketSectionTitle")

        sidebar_layout.addWidget(sidebar_summary_title)



        self.sidebar_summary_label = QLabel("等待加载共享平台数据")

        self.sidebar_summary_label.setObjectName("marketMutedText")

        self.sidebar_summary_label.setWordWrap(True)

        sidebar_layout.addWidget(self.sidebar_summary_label)

        sidebar_layout.addStretch(1)

        splitter.addWidget(sidebar_container)



        list_container = QFrame(self)

        list_container.setObjectName("marketListPanel")

        list_layout = QVBoxLayout(list_container)

        list_layout.setContentsMargins(12, 12, 12, 12)

        list_layout.setSpacing(8)



        catalog_header_layout = QHBoxLayout()

        catalog_header_layout.setContentsMargins(0, 0, 0, 0)

        catalog_header_layout.setSpacing(8)



        catalog_title_label = QLabel("脚本库")

        catalog_title_label.setObjectName("marketSectionTitle")

        catalog_header_layout.addWidget(catalog_title_label)



        catalog_header_layout.addStretch(1)



        self.catalog_status_label = QLabel("正在加载脚本...")

        self.catalog_status_label.setObjectName("marketMutedText")

        self.catalog_status_label.setWordWrap(True)

        catalog_header_layout.addWidget(self.catalog_status_label, 0, Qt.AlignmentFlag.AlignRight)

        list_layout.addLayout(catalog_header_layout)



        self.remote_list = QListWidget(self)

        self.remote_list.setObjectName("workflowMarketList")

        self.remote_list.setSpacing(0)

        self.remote_list.setWordWrap(True)

        self.remote_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        list_layout.addWidget(self.remote_list, 1)

        splitter.addWidget(list_container)



        detail_container = QFrame(self)

        detail_container.setObjectName("marketDetailPanel")

        detail_layout = QVBoxLayout(detail_container)

        detail_layout.setContentsMargins(12, 12, 12, 12)

        detail_layout.setSpacing(10)



        self.detail_title_label = QLabel("脚本共享平台")

        self.detail_title_label.setObjectName("marketSectionTitle")

        self.detail_title_label.setWordWrap(True)

        detail_layout.addWidget(self.detail_title_label)



        self.detail_meta_label = QLabel("")

        self.detail_meta_label.setWordWrap(True)

        self.detail_meta_label.setObjectName("marketMutedText")

        detail_layout.addWidget(self.detail_meta_label)



        self.detail_status_label = QLabel("")

        self.detail_status_label.setWordWrap(True)

        self.detail_status_label.setStyleSheet(

            "padding: 9px 0; background: transparent; border: none;"

        )

        detail_layout.addWidget(self.detail_status_label)



        action_layout = QHBoxLayout()

        action_layout.setContentsMargins(0, 0, 0, 0)

        action_layout.setSpacing(8)



        self.primary_action_button = QPushButton("下载并预检")

        self.primary_action_button.setMinimumHeight(34)

        action_layout.addWidget(self.primary_action_button)



        self.recheck_button = QPushButton("重新预检")

        self.recheck_button.setMinimumHeight(34)

        action_layout.addWidget(self.recheck_button)



        self.open_entry_button = QPushButton("打开画布编辑")

        self.open_entry_button.setMinimumHeight(34)

        action_layout.addWidget(self.open_entry_button)



        self.favorite_entry_button = QPushButton("加入收藏")

        self.favorite_entry_button.setMinimumHeight(34)

        action_layout.addWidget(self.favorite_entry_button)



        self.uninstall_button = QPushButton("卸载脚本")

        self.uninstall_button.setMinimumHeight(34)

        action_layout.addWidget(self.uninstall_button)



        self.delete_button = QPushButton("删除版本")

        self.delete_button.setMinimumHeight(34)

        action_layout.addWidget(self.delete_button)

        action_layout.addStretch(1)

        detail_layout.addLayout(action_layout)



        self.detail_tabs = QTabWidget(self)

        self.detail_tabs.setObjectName("marketDetailTabs")

        self.detail_tabs.setDocumentMode(True)

        self.detail_tabs.tabBar().setExpanding(False)

        self.detail_tabs.tabBar().setDrawBase(False)



        self.detail_overview_tab = QWidget(self.detail_tabs)

        detail_overview_layout = QVBoxLayout(self.detail_overview_tab)

        detail_overview_layout.setContentsMargins(0, 8, 0, 0)

        detail_overview_layout.setSpacing(0)



        self.detail_edit = QTextBrowser(self)

        self.detail_edit.setOpenExternalLinks(False)

        self.detail_edit.setPlaceholderText("请选择左侧脚本，或导入本地共享平台包")

        detail_overview_layout.addWidget(self.detail_edit, 1)



        self.detail_tabs.addTab(self.detail_overview_tab, "概览")

        self.detail_info_tab = QWidget(self.detail_tabs)

        detail_info_layout = QVBoxLayout(self.detail_info_tab)

        detail_info_layout.setContentsMargins(0, 8, 0, 0)

        detail_info_layout.setSpacing(0)

        self.detail_info_edit = QTextBrowser(self)

        self.detail_info_edit.setOpenExternalLinks(False)

        self.detail_info_edit.setPlaceholderText("这里显示脚本详情")

        detail_info_layout.addWidget(self.detail_info_edit, 1)

        self.detail_tabs.addTab(self.detail_info_tab, "详情")

        detail_layout.addWidget(self.detail_tabs, 1)



        splitter.addWidget(detail_container)

        splitter.setStretchFactor(0, 0)

        splitter.setStretchFactor(1, 4)

        splitter.setStretchFactor(2, 6)

        splitter.setSizes([164, 250, 646])

        root_layout.addWidget(splitter, 1)



        self.search_input.textChanged.connect(self._apply_remote_filter)

        self.search_input.returnPressed.connect(self._search_market_packages)

        self.search_button.clicked.connect(self._search_market_packages)

        self.my_packages_checkbox.toggled.connect(self._on_my_packages_toggled)

        self.author_auth_button.clicked.connect(self.open_author_auth_dialog)

        self.author_logout_button.clicked.connect(self.logout_author_account)

        self.refresh_button.clicked.connect(self.refresh_remote_packages)

        self.build_button.clicked.connect(self.open_build_dialog)

        self.import_button.clicked.connect(self.select_archive)

        self.remote_list.currentItemChanged.connect(self._on_remote_item_changed)

        self.remote_list.customContextMenuRequested.connect(self._show_remote_list_context_menu)

        self.primary_action_button.clicked.connect(self._on_primary_action_clicked)

        self.recheck_button.clicked.connect(self.recheck_current_package)

        self.open_entry_button.clicked.connect(self.open_current_entry_workflow)

        self.favorite_entry_button.clicked.connect(self.favorite_current_entry_workflow)

        self.uninstall_button.clicked.connect(self.uninstall_current_package)

        self.delete_button.clicked.connect(self.delete_current_remote_package)

        self.market_scope_all_button.clicked.connect(lambda _checked=False: self._set_market_scope("all"))

        self.market_scope_runnable_button.clicked.connect(lambda _checked=False: self._set_market_scope("runnable"))

        self.market_scope_installed_button.clicked.connect(lambda _checked=False: self._set_market_scope("installed"))

        self.market_scope_mine_button.clicked.connect(lambda _checked=False: self._set_market_scope("mine"))



        self._update_scope_buttons()

        self._show_empty_detail()



    def apply_settings_to_config(self) -> None:

        self.current_config["market_update_server_base"] = self.get_market_update_server_base()



    def get_market_update_server_base(self) -> str:

        config_data = self._build_precheck_config()

        return resolve_market_update_server_base(str(config_data.get("market_update_server_base") or "").strip())



    def _get_market_auth_server_base(self) -> str:

        return get_market_auth_server_base()



    def _get_market_verify_ssl(self):

        return get_market_verify_ssl()



    def _get_author_token(self) -> str:

        return str(getattr(self.author_account, "access_token", "") or "").strip()



    @staticmethod

    def _default_author_auth_state() -> dict:

        return {

            "remember_username": True,

            "remember_password": False,

            "auto_login": False,

            "username": "",

            "password": "",

        }



    def _load_author_auth_state(self) -> dict:

        state = self._default_author_auth_state()

        state_path = get_market_auth_state_path()

        if not state_path.exists():

            return state

        try:

            payload = json.loads(state_path.read_text(encoding="utf-8"))

        except Exception:

            return state

        if not isinstance(payload, dict):

            return state

        state["remember_username"] = bool(payload.get("remember_username", state["remember_username"]))

        state["remember_password"] = bool(payload.get("remember_password", state["remember_password"]))

        state["auto_login"] = bool(payload.get("auto_login", state["auto_login"]))

        state["username"] = str(payload.get("username") or "").strip()

        state["password"] = str(payload.get("password") or "")

        if not state["remember_username"]:

            state["username"] = ""

        if not state["remember_password"]:

            state["password"] = ""

            state["auto_login"] = False

        return state



    def _save_author_auth_state(

        self,

        username: str = "",

        password: str = "",

        remember_username: Optional[bool] = None,

        remember_password: Optional[bool] = None,

        auto_login: Optional[bool] = None,

    ) -> None:

        next_state = dict(self._author_auth_state or self._default_author_auth_state())

        if remember_username is not None:

            next_state["remember_username"] = bool(remember_username)

        if remember_password is not None:

            next_state["remember_password"] = bool(remember_password)

        if auto_login is not None:

            next_state["auto_login"] = bool(auto_login)



        if next_state.get("remember_username"):

            next_state["username"] = str(username or next_state.get("username") or "").strip()

        else:

            next_state["username"] = ""



        if next_state.get("remember_password"):

            next_state["password"] = str(password or next_state.get("password") or "")

        else:

            next_state["password"] = ""

            next_state["auto_login"] = False



        if not next_state.get("password"):

            next_state["auto_login"] = False



        self._author_auth_state = next_state

        state_path = get_market_auth_state_path()

        try:

            state_path.write_text(json.dumps(next_state, ensure_ascii=False, indent=2), encoding="utf-8")

        except Exception:

            pass



    def _restore_author_login_state(self) -> None:

        username = str(self._author_auth_state.get("username") or "").strip()

        password = str(self._author_auth_state.get("password") or "")

        auto_login = bool(self._author_auth_state.get("auto_login"))

        if not auto_login or not username or not password:

            return

        try:

            account = self.package_manager.login_author_account(

                username=username,

                password=password,

                auth_server_base=self._get_market_auth_server_base(),

                verify_ssl=self._get_market_verify_ssl(),

            )

        except Exception:

            self._save_author_auth_state(auto_login=False)

            return

        self.author_account = account

        self._refresh_author_ui()



    def _remember_author_auth_preferences(self, username: str, password: str) -> None:

        options = dict(self._author_auth_pending_options or self._author_auth_state or self._default_author_auth_state())

        self._save_author_auth_state(

            username=username,

            password=password,

            remember_username=bool(options.get("remember_username", True)),

            remember_password=bool(options.get("remember_password", False)),

            auto_login=bool(options.get("auto_login", False)),

        )



    def _set_operation_message(self, text: str, error: bool = False) -> None:

        message = str(text or "").strip()

        if not message:

            self.operation_status_label.clear()

            self.operation_status_label.setVisible(False)

            return

        color = "#c62828" if error else "#2e7d32"

        background = "rgba(198, 40, 40, 0.08)" if error else "rgba(46, 125, 50, 0.08)"

        self.operation_status_label.setStyleSheet(

            f"padding: 6px 8px; border-radius: 6px; color: {color}; background: {background};"

        )

        self.operation_status_label.setText(message)

        self.operation_status_label.setVisible(True)



    def _set_feedback_message(self, text: str, error: bool = False) -> None:

        message = str(text or "").strip()

        if not message:

            self.feedback_status_label.clear()

            self.feedback_status_label.setVisible(False)

            return

        color = "#c62828" if error else "#2e7d32"

        background = "rgba(198, 40, 40, 0.08)" if error else "rgba(46, 125, 50, 0.08)"

        self.feedback_status_label.setStyleSheet(

            f"padding: 6px 8px; border-radius: 6px; color: {color}; background: {background};"

        )

        self.feedback_status_label.setText(message)

        self.feedback_status_label.setVisible(True)



    def _set_detail_status_message(self, text: str, passed: Optional[bool] = None) -> None:

        message = str(text or "").strip()

        if not message:

            self.detail_status_label.clear()

            self.detail_status_label.setVisible(False)

            return

        if passed is True:

            color = "#2e7d32"

        elif passed is False:

            color = "#c62828"

        else:

            color = "#475467"

        self.detail_status_label.setStyleSheet(

            f"padding: 9px 0; color: {color}; background: transparent; border: none;"

        )

        self.detail_status_label.setText(message)

        self.detail_status_label.setVisible(True)



    def _update_scope_buttons(self) -> None:

        button_mapping = {

            "all": getattr(self, "market_scope_all_button", None),

            "runnable": getattr(self, "market_scope_runnable_button", None),

            "installed": getattr(self, "market_scope_installed_button", None),

            "mine": getattr(self, "market_scope_mine_button", None),

        }

        current_scope = str(self._market_scope or "all").strip().lower() or "all"

        for scope_name, button in button_mapping.items():

            if button is None:

                continue

            button.blockSignals(True)

            button.setChecked(scope_name == current_scope)

            button.blockSignals(False)



    def _set_market_scope(self, scope: str) -> None:

        normalized_scope = str(scope or "all").strip().lower() or "all"

        if normalized_scope not in {"all", "runnable", "installed", "mine"}:

            normalized_scope = "all"



        if normalized_scope == "mine" and not self._ensure_author_login("查看我的发布"):

            normalized_scope = "all"



        previous_scope = str(self._market_scope or "all").strip().lower() or "all"

        self._market_scope = normalized_scope



        self.my_packages_checkbox.blockSignals(True)

        self.my_packages_checkbox.setChecked(normalized_scope == "mine")

        self.my_packages_checkbox.blockSignals(False)

        self._update_scope_buttons()



        if previous_scope == normalized_scope:

            self._apply_remote_filter()

            return



        if "mine" in {previous_scope, normalized_scope}:

            self.refresh_remote_packages()

            return



        self._apply_remote_filter()



    def _package_matches_market_scope(self, package: RemoteMarketPackageSummary) -> bool:

        scope = str(self._market_scope or "all").strip().lower() or "all"

        if scope == "runnable":

            return bool(package.can_run)

        if scope == "installed":

            return bool(self._get_installed_versions(package.package_id))

        return True



    def _build_market_badge_html(self, text: str, tone: str = "neutral") -> str:

        palette = {

            "neutral": ("#475467", "rgba(15, 23, 42, 0.06)", "rgba(15, 23, 42, 0.14)"),

            "primary": ("#0b57d0", "rgba(11, 87, 208, 0.10)", "rgba(11, 87, 208, 0.22)"),

            "success": ("#137333", "rgba(19, 115, 51, 0.10)", "rgba(19, 115, 51, 0.22)"),

            "warning": ("#b54708", "rgba(245, 158, 11, 0.14)", "rgba(245, 158, 11, 0.28)"),

        }

        fg, bg, border = palette.get(tone, palette["neutral"])

        safe_text = html.escape(str(text or "").strip() or "-")

        return (

            f"<span style=\"padding:2px 8px;border-radius:10px;"

            f"color:{fg};background:{bg};border:1px solid {border};\">{safe_text}</span>"

        )



    @staticmethod

    def _trim_text(text: str, limit: int = 72) -> str:

        normalized = str(text or "").strip()

        if len(normalized) <= limit:

            return normalized

        return normalized[: max(0, limit - 1)].rstrip() + "…"



    @staticmethod

    def _normalize_remote_status(package: RemoteMarketPackageSummary) -> str:

        return str(getattr(package, 'status', '') or '').strip().lower()



    def _is_my_publish_scope(self) -> bool:

        return str(self._market_scope or '').strip().lower() == 'mine'



    def _can_manage_remote_package(self, package: Optional[RemoteMarketPackageSummary]) -> bool:

        return package is not None and self._is_my_publish_scope() and bool(package.can_edit)



    def _describe_remote_status(self, package: RemoteMarketPackageSummary) -> tuple[str, str]:

        normalized_status = self._normalize_remote_status(package)

        if normalized_status == "released":

            return "可运行", "success"

        if normalized_status == "offline":

            return "已下架", "warning"

        if normalized_status == "submitted":

            return "审核中", "warning"

        if normalized_status == "rejected":

            return "已拒绝", "neutral"

        if package.can_delete:

            return "待发布", "warning"

        return "不可运行", "neutral"



    def _can_offline_package(self, package: Optional[RemoteMarketPackageSummary]) -> bool:

        return self._can_manage_remote_package(package) and self._normalize_remote_status(package) == "released"



    def _can_resume_package(self, package: Optional[RemoteMarketPackageSummary]) -> bool:

        return self._can_manage_remote_package(package) and self._normalize_remote_status(package) == "offline"



    def _search_market_packages(self) -> None:

        self.refresh_remote_packages(selected_key=self._get_selected_remote_key())



    def _exec_market_context_menu(self, menu: QMenu, global_pos: QPoint):

        chosen_action = menu.exec(global_pos + QPoint(8, 8))

        if chosen_action is None:

            return None

        if not menu.geometry().contains(QCursor.pos()):

            return None

        return chosen_action



    def _build_remote_list_item_text(self, package: RemoteMarketPackageSummary) -> str:

        installed_versions = self._get_installed_versions(package.package_id)

        if package.version in installed_versions:

            install_text = "已安装"

        elif installed_versions:

            install_text = "已装旧版"

        else:

            install_text = "未安装"

        status_text, _status_tone = self._describe_remote_status(package)

        title = self._trim_text(package.title or package.package_id or "未命名脚本", 24)

        meta_line = " | ".join(

            item

            for item in [

                f"v{package.version or '-'}",

                status_text,

                install_text,

            ]

            if item

        )

        summary_line = self._trim_text(self._build_remote_list_item_summary_text(package), 28)

        parts = [title, meta_line]

        if summary_line:

            parts.append(summary_line)

        return "\n".join(item for item in parts if item)



    def _build_remote_list_item_summary_text(self, package: RemoteMarketPackageSummary) -> str:

        raw_title = str(package.title or package.package_id or "").strip()

        raw_package_id = str(package.package_id or "").strip()

        summary_source = str(package.summary or "").strip()

        def normalize_text(value: str) -> str:

            return " ".join(value.split()).casefold()

        normalized_summary = normalize_text(summary_source)

        duplicated_summary = normalized_summary and normalized_summary in {

            normalize_text(raw_title),

            normalize_text(raw_package_id),

        }

        if duplicated_summary:

            summary_source = ""

        if summary_source:

            return summary_source

        fallback_parts: list[str] = []

        author_name = str(package.author_name or "").strip()

        category = str(package.category or "").strip()

        if author_name:

            fallback_parts.append(f"作者 {author_name}")

        if category:

            fallback_parts.append(f"分类 {category}")

        return " | ".join(fallback_parts)



    def _build_remote_list_item_size(self, package: RemoteMarketPackageSummary) -> QSize:

        summary_line = self._build_remote_list_item_summary_text(package)

        return QSize(0, 68 if summary_line else 54)



    def _build_remote_list_item_tooltip(self, package: RemoteMarketPackageSummary) -> str:



        return "\n".join(

            part

            for part in [

                str(package.title or package.package_id or "未命名脚本").strip(),

                f"作者：{str(package.author_name or '未知作者').strip()}",

                f"包ID：{str(package.package_id or '-').strip()}",

                str(package.summary or "").strip(),

            ]

            if part

        )



    def _set_detail_browser_html(self, overview_html: str, detail_text: str = "") -> None:

        self.detail_edit.setHtml(overview_html)

        if hasattr(self, 'detail_info_edit'):

            detail_source = str(detail_text or '').strip() or "这里会显示脚本的作者、版本、分类、运行要求等详情。"

            self.detail_info_edit.setHtml(self._render_detail_text_as_html(detail_source))



    def _build_remote_detail_text(

        self,

        package: RemoteMarketPackageSummary,

        installed_manifest: Optional[MarketPackageManifest] = None,

    ) -> str:

        installed_versions = self._get_installed_versions(package.package_id)

        status_text, _status_tone = self._describe_remote_status(package)

        lines = [

            "基础信息：",

            f"  脚本名称：{package.title or package.package_id or '未命名脚本'}",

            f"  包ID：{package.package_id or '-'}",

            f"  版本：{package.version or '-'}",

            f"  作者：{package.author_name or '未知作者'}",

            f"  分类：{package.category or '-'}",

            f"  发布状态：{status_text}",

            f"  最新版本：{package.latest_version or package.version or '-'}",

            f"  下载权限：{'可下载' if package.can_run else '不可下载'}",

            f"  本地版本：{', '.join(installed_versions) if installed_versions else '无'}",

        ]

        manage_flags: list[str] = []

        if package.can_edit:

            manage_flags.append("可编辑")

        if package.can_delete:

            manage_flags.append("可删除")

        if manage_flags:

            lines.append(f"  管理权限：{' | '.join(manage_flags)}")

        summary_text = str(package.summary or '').strip()

        if summary_text:

            lines.extend(["", "脚本简介：", f"  {summary_text}"])

        if installed_manifest is not None:

            lines.extend([

                "",

                "本地已安装信息：",

                f"  标题：{installed_manifest.title or installed_manifest.package_id or '-'}",

                f"  入口工作流：{installed_manifest.entry_workflow or '-'}",

                f"  标签：{'?'.join(installed_manifest.tags) if installed_manifest.tags else '无'}",

            ])

            if str(installed_manifest.description or '').strip():

                lines.extend(["", "本地说明：", f"  {str(installed_manifest.description).strip()}"])

        return "\n".join(lines)



    def _append_detail_list_section(self, lines: list[str], title: str, values: list[str]) -> None:

        normalized = [str(item).strip() for item in values if str(item or '').strip()]

        if not normalized:

            return

        if lines:

            lines.append("")

        lines.append(title)

        for item in normalized:

            lines.append(f"- {item}")



    def _build_manifest_detail_text(

        self,

        manifest: MarketPackageManifest,

        report: Optional[PrecheckReport] = None,

        remote_package: Optional[RemoteMarketPackageSummary] = None,

    ) -> str:

        runtime = manifest.runtime_requirement

        target_window = runtime.target_window

        guide = manifest.configuration_guide

        protection = manifest.protection

        lines = [

            "基础信息：",

            f"  脚本名称：{manifest.title or manifest.package_id or '未命名脚本'}",

            f"  包ID：{manifest.package_id or '-'}",

            f"  版本：{manifest.version or '-'}",

            f"  作者：{manifest.author or '-'}",

            f"  分类：{manifest.category or '-'}",

            f"  标签：{'?'.join(manifest.tags) if manifest.tags else '无'}",

            f"  入口工作流：{manifest.entry_workflow or '-'}",

            f"  最低客户端：{manifest.min_client_version or '-'}",

            f"  最高客户端：{manifest.max_client_version or '-'}",

            f"  文件数：{len(manifest.file_hashes or {})}",

        ]

        if remote_package is not None:

            status_text, _status_tone = self._describe_remote_status(remote_package)

            lines.append(f"  远程状态：{status_text}")

        description_text = str(manifest.description or '').strip()

        if description_text:

            lines.extend(["", "脚本描述：", f"  {description_text}"])

        runtime_values = [

            f"执行模式：{runtime.execution_mode}" if runtime.execution_mode else '',

            f"截图引擎：{runtime.screenshot_engine}" if runtime.screenshot_engine else '',

            f"需要插件：{'是' if runtime.plugin_required else '否'}",

            f"插件ID：{runtime.plugin_id}" if runtime.plugin_id else '',

            f"插件最低版本：{runtime.plugin_min_version}" if runtime.plugin_min_version else '',

            f"所需模型：{'?'.join(runtime.required_models)}" if runtime.required_models else '',

            f"所需任务类型：{'?'.join(runtime.required_task_types)}" if runtime.required_task_types else '',

        ]

        self._append_detail_list_section(lines, "运行要求：", runtime_values)

        target_values = [

            f"窗口类型：{target_window.window_kind}" if target_window.window_kind else '',

            f"进程名：{'?'.join(target_window.process_names)}" if target_window.process_names else '',

            f"窗口类名：{'?'.join(target_window.class_names)}" if target_window.class_names else '',

            f"标题关键字：{'?'.join(target_window.title_keywords)}" if target_window.title_keywords else '',

            f"客户区分辨率：{target_window.client_width} x {target_window.client_height}" if target_window.client_width and target_window.client_height else '',

            f"DPI：{target_window.dpi}" if target_window.dpi else '',

            f"缩放比：{target_window.scale_factor}" if target_window.scale_factor else '',

            f"方向：{target_window.orientation}" if target_window.orientation else '',

            "支持多开" if target_window.multi_instance_support else '',

        ] + [f"备注：{item}" for item in target_window.notes]

        self._append_detail_list_section(lines, "目标窗口要求：", target_values)

        if str(guide.summary or '').strip():

            lines.extend(["", "配置说明：", f"  {str(guide.summary).strip()}"])

        self._append_detail_list_section(lines, "必做步骤：", list(guide.required_steps or []))

        self._append_detail_list_section(lines, "推荐步骤：", list(guide.recommended_steps or []))

        self._append_detail_list_section(lines, "窗口说明：", list(guide.target_window_notes or []))

        self._append_detail_list_section(lines, "常见失败：", list(guide.common_failures or []))

        protection_values = [

            f"权限：{'?'.join(manifest.permissions)}" if manifest.permissions else "权限：无",

            f"保护状态：{'已启用' if protection.enabled else '未启用'}",

            f"保护方案：{protection.scheme}" if protection.scheme else '',

            f"线上密钥：{'需要' if protection.requires_online_key else '不需要'}",

            f"公开文件：{'?'.join(protection.public_files)}" if protection.public_files else '',

        ]

        self._append_detail_list_section(lines, "权限与保护：", protection_values)

        if report is not None:

            lines.extend(["", "预检结果：", f"  {self._format_precheck_status(report)}"])

            issue_values = []

            for issue in report.issues:

                severity_text = self._format_issue_severity_text(issue.severity)

                detail = str(issue.message or '').strip()

                action_text = self._format_issue_action_text(issue.action)

                if action_text:

                    detail = f"{detail}?{action_text}" if detail else action_text

                issue_values.append(f"[{severity_text}] {issue.title or '未命名问题'}?{detail}")

            self._append_detail_list_section(lines, "预检问题：", issue_values)

        return "\n".join(lines)



    def _render_detail_text_as_html(self, text: str) -> str:

        lines = str(text or "").splitlines()

        sections: list[str] = ["<div style='line-height:1.75; color:#1f2937;'>"]

        in_list = False

        for raw_line in lines:

            line = str(raw_line or "")

            stripped = line.strip()

            if not stripped:

                if in_list:

                    sections.append("</ul>")

                    in_list = False

                sections.append("<div style='height:8px;'></div>")

                continue



            escaped = html.escape(stripped)

            if stripped.startswith("- "):

                if not in_list:

                    sections.append("<ul style='margin:4px 0 8px 18px; padding:0;'>")

                    in_list = True

                sections.append(f"<li style='margin:2px 0;'>{html.escape(stripped[2:].strip())}</li>")

                continue



            if in_list:

                sections.append("</ul>")

                in_list = False



            if stripped.endswith("："):

                sections.append(f"<div style='margin:8px 0 4px 0; font-weight:600; color:#111827;'>{escaped}</div>")

            elif line.startswith("  "):

                sections.append(f"<div style='margin-left:16px; color:#667085;'>{escaped}</div>")

            else:

                sections.append(f"<div>{escaped}</div>")



        if in_list:

            sections.append("</ul>")

        sections.append("</div>")

        return "".join(sections)



    def _get_feedback_target(self) -> Optional[tuple[str, str]]:

        if self._detail_remote is not None:

            package_id = str(self._detail_remote.package_id or "").strip()

            version = str(self._detail_remote.version or "").strip()

            if package_id and version:

                return package_id, version

        manifest = self._detail_manifest

        if manifest is None:

            return None

        package_id = str(manifest.package_id or "").strip()

        version = str(manifest.version or "").strip()

        if not package_id or not version:

            return None

        if self._find_remote_package(package_id, version) is None:

            return None

        return package_id, version



    def _get_feedback_author_name(self) -> str:

        if self._detail_remote is not None and str(self._detail_remote.author_name or "").strip():

            return str(self._detail_remote.author_name or "").strip()

        if self._detail_manifest is not None and str(self._detail_manifest.author or "").strip():

            return str(self._detail_manifest.author or "").strip()

        return "作者"



    def _current_detail_can_view_feedback(self) -> bool:

        return self._get_feedback_target() is not None



    def _current_detail_can_delete_feedback(self) -> bool:

        if self._detail_remote is not None:

            return bool(self._detail_remote.can_edit)

        manifest = self._detail_manifest

        if manifest is None:

            return False

        linked_remote = self._find_remote_package(manifest.package_id, manifest.version)

        return bool(linked_remote and linked_remote.can_edit)



    def _prefill_feedback_name(self) -> None:

        if self.author_account.is_logged_in and not self.feedback_name_input.text().strip():

            self.feedback_name_input.setText(str(self.author_account.username or "").strip())



    def _set_feedback_mode(self, mode: str) -> None:

        normalized_mode = mode if mode in {"hidden", "submit", "list"} else "hidden"

        self._feedback_mode = normalized_mode

        panel_visible = normalized_mode != "hidden"

        submit_mode = normalized_mode == "submit"

        list_mode = normalized_mode == "list"

        can_view_feedback = self._current_detail_can_view_feedback()

        can_delete_feedback = list_mode and self._current_detail_can_delete_feedback()



        if hasattr(self, 'feedback_empty_label'):

            self.feedback_empty_label.setVisible(not panel_visible)

        self.feedback_panel.setVisible(panel_visible)

        if panel_visible and hasattr(self, 'detail_tabs') and hasattr(self, 'feedback_tab'):

            self.detail_tabs.setCurrentWidget(self.feedback_tab)

        self.feedback_mode_submit_button.setVisible(panel_visible)

        self.feedback_mode_view_button.setVisible(panel_visible and can_view_feedback)

        self.feedback_delete_button.setVisible(can_delete_feedback)

        self.feedback_close_button.setVisible(panel_visible)



        self.feedback_name_input.setVisible(submit_mode)

        self.feedback_contact_input.setVisible(submit_mode)

        self.feedback_title_input.setVisible(submit_mode)

        self.feedback_content_edit.setVisible(submit_mode)

        self.feedback_submit_execute_button.setVisible(submit_mode)

        self.feedback_splitter.setVisible(list_mode)

        self.feedback_list_widget.setVisible(list_mode)

        self.feedback_list_edit.setVisible(list_mode)

        self.feedback_delete_button.setEnabled(can_delete_feedback and self._get_selected_feedback_item() is not None)

        if list_mode:

            self.feedback_splitter.setSizes([96, 260])



        if submit_mode:

            self._prefill_feedback_name()

            self.feedback_header_label.setText(f"给 {self._get_feedback_author_name()} 发表评论或提交 bug")

        elif list_mode:

            self.feedback_header_label.setText("工作流评论")

        else:

            self.feedback_header_label.setText("工作流评论")



    def _reset_feedback_panel(self) -> None:

        self._set_feedback_mode("hidden")

        self._set_feedback_message("")

        self._feedback_items = []

        self.feedback_title_input.clear()

        self.feedback_content_edit.clear()

        self.feedback_list_widget.clear()

        self.feedback_list_edit.clear()

        self.feedback_delete_button.setEnabled(False)



    def _show_feedback_panel(self) -> None:

        if self._feedback_mode != "hidden":

            self._reset_feedback_panel()

            return

        self._load_feedback_list()



    def _show_feedback_submit_mode(self) -> None:

        if self._get_feedback_target() is None:

            self._set_operation_message("当前脚本暂无可评论的远程版本", error=True)

            return

        self._set_feedback_mode("submit")

        self._set_feedback_message("可填写问题现象、复现步骤、窗口配置和报错信息。")

        self.feedback_content_edit.setFocus()



    def _load_feedback_list(self) -> None:

        target = self._get_feedback_target()

        if target is None:

            self._set_operation_message("当前脚本暂无评论记录", error=True)

            return



        package_id, version = target

        self._set_feedback_mode("list")

        self._set_feedback_message("正在加载评论...")

        self._feedback_items = []

        self.feedback_list_widget.clear()

        self.feedback_list_edit.setPlainText("正在加载评论...")

        self.feedback_mode_view_button.setEnabled(False)

        self.feedback_delete_button.setEnabled(False)

        try:

            items = self.package_manager.list_package_feedback(

                package_id,

                version,

                auth_server_base=self._get_market_auth_server_base(),

                verify_ssl=self._get_market_verify_ssl(),

                author_token=self._get_author_token(),

            )

        except Exception as exc:

            self._feedback_items = []

            self.feedback_list_widget.clear()

            self.feedback_list_edit.clear()

            self._set_feedback_message(f"加载评论失败：{exc}", error=True)

            return

        finally:

            self.feedback_mode_view_button.setEnabled(True)



        self._populate_feedback_list(items)

        self._set_feedback_message(f"共 {len(items)} 条评论" if items else "暂无评论")



    def _submit_feedback(self) -> None:

        target = self._get_feedback_target()

        if target is None:

            self._set_operation_message("当前脚本暂无可评论的远程版本", error=True)

            return



        content = str(self.feedback_content_edit.toPlainText() or "").strip()

        if not content:

            self._set_feedback_message("请先填写评论内容", error=True)

            self.feedback_content_edit.setFocus()

            return



        package_id, version = target

        self.feedback_submit_execute_button.setEnabled(False)

        try:

            item = self.package_manager.submit_package_feedback(

                package_id,

                version,

                reporter_name=self.feedback_name_input.text(),

                reporter_contact=self.feedback_contact_input.text(),

                title=self.feedback_title_input.text(),

                content=content,

                auth_server_base=self._get_market_auth_server_base(),

                verify_ssl=self._get_market_verify_ssl(),

                author_token=self._get_author_token(),

            )

        except Exception as exc:

            self._set_feedback_message(f"提交评论失败：{exc}", error=True)

            return

        finally:

            self.feedback_submit_execute_button.setEnabled(True)



        if item.reporter_name and not self.feedback_name_input.text().strip():

            self.feedback_name_input.setText(item.reporter_name)

        self.feedback_title_input.clear()

        self.feedback_content_edit.clear()

        self._set_feedback_message("评论已发布，当前工作流下所有用户都可见。")

        self._set_operation_message(f"已提交评论：{package_id} [{version}]")

        self._load_feedback_list()



    def _populate_feedback_list(self, items: list[object]) -> None:

        self._feedback_items = list(items or [])

        self.feedback_list_widget.blockSignals(True)

        self.feedback_list_widget.clear()



        selected_item: Optional[QListWidgetItem] = None

        for index, item in enumerate(self._feedback_items):

            display_title = str(item.title or "").strip() or "未命名评论"

            reporter_name = str(item.reporter_name or "").strip() or "访客"

            created_at = str(item.created_at or "").strip()

            summary = f"{display_title} · {reporter_name}"

            if created_at:

                summary = f"{summary} · {created_at}"

            list_item = QListWidgetItem(summary)

            list_item.setToolTip(display_title)

            list_item.setData(Qt.ItemDataRole.UserRole, index)

            self.feedback_list_widget.addItem(list_item)

            if selected_item is None:

                selected_item = list_item



        self.feedback_list_widget.blockSignals(False)



        if selected_item is None:

            self.feedback_list_edit.setPlainText("暂无评论")

            self.feedback_delete_button.setEnabled(False)

            return



        self.feedback_list_widget.setCurrentItem(selected_item)

        self._on_feedback_item_changed(selected_item, None)



    def _on_feedback_item_changed(self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]) -> None:

        _ = previous

        item = self._get_selected_feedback_item(current)

        if item is None:

            self.feedback_list_edit.setPlainText("请选择一条评论")

            self.feedback_delete_button.setEnabled(False)

            return

        self.feedback_list_edit.setPlainText(self._format_feedback_item_detail(item))

        self.feedback_delete_button.setEnabled(self._current_detail_can_delete_feedback())



    def _format_feedback_item_detail(self, item: object) -> str:

        lines = [f"标题：{item.title or '未命名评论'}", f"评论人：{item.reporter_name or '访客'}"]

        if item.reporter_contact:

            lines.append(f"联系方式：{item.reporter_contact}")

        if item.created_at:

            lines.append(f"时间：{item.created_at}")

        lines.append(f"状态：{item.status or 'open'}")

        lines.extend(["", "内容：", item.content or "-"])

        return "\n".join(lines)



    def _get_selected_feedback_item(self, current_item: Optional[QListWidgetItem] = None) -> Optional[object]:

        list_item = current_item or self.feedback_list_widget.currentItem()

        if list_item is None:

            return None

        raw_index = list_item.data(Qt.ItemDataRole.UserRole)

        try:

            index = int(raw_index)

        except (TypeError, ValueError):

            return None

        if index < 0 or index >= len(self._feedback_items):

            return None

        return self._feedback_items[index]



    def _delete_selected_feedback(self) -> None:

        target = self._get_feedback_target()

        if target is None:

            self._set_operation_message("当前脚本暂无评论记录", error=True)

            return

        if not self._current_detail_can_delete_feedback():

            self._set_feedback_message("当前账号无权删除评论", error=True)

            return



        feedback_item = self._get_selected_feedback_item()

        if feedback_item is None:

            self._set_feedback_message("请先选择要删除的评论", error=True)

            return



        title = str(feedback_item.title or "").strip() or f"评论 #{feedback_item.id}"

        answer = QMessageBox.question(

            self,

            "脚本共享平台",

            f"确定删除这条评论吗？\n\n{title}",

            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,

            QMessageBox.StandardButton.No,

        )

        if answer != QMessageBox.StandardButton.Yes:

            return



        package_id, version = target

        self.feedback_delete_button.setEnabled(False)

        try:

            self.package_manager.delete_package_feedback(

                package_id,

                version,

                feedback_item.id,

                auth_server_base=self._get_market_auth_server_base(),

                verify_ssl=self._get_market_verify_ssl(),

                author_token=self._get_author_token(),

            )

        except Exception as exc:

            self.feedback_delete_button.setEnabled(True)

            self._set_feedback_message(f"删除评论失败：{exc}", error=True)

            return



        self._set_operation_message(f"已删除评论：{title}")

        self._load_feedback_list()



    def _default_build_values(self, entry_workflow: str = "") -> dict:

        entry_text = str(entry_workflow or "").strip()

        default_title = Path(entry_text).stem if entry_text else ""

        author_name = str(getattr(self.author_account, "username", "") or "").strip() if self.author_account.is_logged_in else ""

        default_seed = f"{author_name}.{default_title}" if author_name and default_title else default_title

        default_package_id = suggest_package_id(default_seed) if default_seed else ""

        version = "1.0.0"

        output_path = str(Path.cwd() / f"{default_package_id}-{version}.lca_market.zip") if default_package_id else ""

        return {

            "entry_workflow": entry_text,

            "output_path": output_path,

            "package_id": default_package_id,

            "version": version,

            "title": default_title,

            "author": author_name,

            "category": "",

        }



    def _normalize_build_data(self, build_data: Optional[dict] = None) -> dict:

        source = dict(build_data or {})

        data = {

            "entry_workflow": str(source.get("entry_workflow") or "").strip(),

            "output_path": str(source.get("output_path") or "").strip(),

            "package_id": str(source.get("package_id") or "").strip(),

            "version": str(source.get("version") or "").strip(),

            "title": str(source.get("title") or "").strip(),

            "author": str(source.get("author") or "").strip(),

            "category": str(source.get("category") or "").strip(),

        }

        defaults = self._default_build_values(data["entry_workflow"])

        if not data["package_id"]:

            data["package_id"] = str(defaults.get("package_id") or "").strip()

        if not data["version"]:

            data["version"] = str(defaults.get("version") or "1.0.0").strip() or "1.0.0"

        if not data["title"]:

            data["title"] = str(defaults.get("title") or "").strip()

        if not data["author"]:

            data["author"] = str(defaults.get("author") or "").strip()

        if not data["category"]:

            data["category"] = str(defaults.get("category") or "").strip()

        if not data["output_path"] and data["package_id"]:

            data["output_path"] = str(Path.cwd() / f"{data['package_id']}-{data['version']}.lca_market.zip")

        return data



    def open_build_dialog(self) -> None:

        self._set_operation_message("")

        dialog = MarketBuildDialog(

            initial_data=self._normalize_build_data(),

            default_values_provider=self._default_build_values,

            parent=self,

        )

        if dialog.exec() != QDialog.DialogCode.Accepted:

            return

        self.build_local_package(dialog.build_data())

    def open_publish_dialog(

        self,

        package: Optional[RemoteMarketPackageSummary] = None,

        initial_archive_path: str = "",

    ) -> None:

        if not self._ensure_author_login("发布脚本"):

            return



        expected_package_id = str(package.package_id or "").strip() if package is not None else ""

        dialog_title = "更新脚本" if expected_package_id else "发布脚本"

        submit_text = "提交更新" if expected_package_id else "提交发布"



        archive_path = str(initial_archive_path or "").strip()

        if not archive_path and self.pending_archive_path and self.pending_archive_path.exists():

            pending_manifest = self.pending_manifest

            if not expected_package_id or (pending_manifest is not None and pending_manifest.package_id == expected_package_id):

                archive_path = str(self.pending_archive_path)



        dialog = MarketPublishDialog(

            self.package_manager,

            window_title=dialog_title,

            submit_text=submit_text,

            initial_archive_path=archive_path,

            expected_package_id=expected_package_id,

            parent=self,

        )

        if dialog.exec() != QDialog.DialogCode.Accepted:

            return



        self.publish_local_package(

            archive_path=dialog.selected_archive_path(),

            changelog=dialog.changelog_text(),

            release_notes=dialog.release_notes_text(),

            expected_package_id=expected_package_id,

        )



    def _refresh_author_ui(self) -> None:

        logged_in = self.author_account.is_logged_in

        if logged_in:

            self.author_status_label.setText(f"作者已登录：{self.author_account.username}")

        else:

            remembered_username = str(self._author_auth_state.get("username") or "").strip()

            if remembered_username:

                self.author_status_label.setText(f"作者未登录，已记住账号：{remembered_username}")

            else:

                self.author_status_label.setText("作者未登录，发布和维护脚本前需要先登录")

            if self._market_scope == "mine":

                self._market_scope = "all"

                self.my_packages_checkbox.blockSignals(True)

                self.my_packages_checkbox.setChecked(False)

                self.my_packages_checkbox.blockSignals(False)

        self.author_auth_button.setVisible(not logged_in)

        self.author_auth_button.setEnabled(True)

        self.author_logout_button.setVisible(logged_in)

        self._update_scope_buttons()



    def _prompt_author_credentials(self, title: str, action: str = "") -> tuple[str, str, str]:

        dialog = MarketAuthorAuthDialog(

            username=str(

                getattr(self.author_account, "username", "")

                or self._author_auth_state.get("username")

                or ""

            ).strip(),

            password=str(self._author_auth_state.get("password") or ""),

            remember_username=bool(self._author_auth_state.get("remember_username", True)),

            remember_password=bool(self._author_auth_state.get("remember_password", False)),

            auto_login=bool(self._author_auth_state.get("auto_login", False)),

            parent=self,

        )

        dialog.setWindowTitle(title)

        if dialog.exec() != QDialog.DialogCode.Accepted:

            return "", "", ""



        username = dialog.username

        if not username:

            self._set_operation_message("用户名不能为空", error=True)

            return "", "", ""



        password = dialog.password

        if not password:

            self._set_operation_message("密码不能为空", error=True)

            return "", "", ""



        resolved_action = str(action or dialog.action or "login").strip().lower() or "login"

        self._author_auth_pending_options = {

            "remember_username": dialog.remember_username,

            "remember_password": dialog.remember_password,

            "auto_login": dialog.auto_login,

            "username": username,

            "password": password,

        }

        return username, password, resolved_action



    def _ensure_author_login(self, action_name: str) -> bool:

        if self.author_account.is_logged_in:

            return True

        self._set_operation_message(f"{action_name}前请先登录作者账号", error=True)

        return False



    def open_author_auth_dialog(self) -> None:

        username, password, action = self._prompt_author_credentials("作者账号", action="")

        if not username or not password:

            return

        if action == "register":

            self.register_author_account(username=username, password=password)

            return

        self.login_author_account(username=username, password=password)



    def register_author_account(self, username: str = "", password: str = "") -> None:

        if not username or not password:

            username, password, _action = self._prompt_author_credentials("注册作者账号", action="register")

        if not username or not password:

            return

        try:

            account = self.package_manager.register_author_account(

                username=username,

                password=password,

                auth_server_base=self._get_market_auth_server_base(),

                verify_ssl=self._get_market_verify_ssl(),

            )

        except Exception as exc:

            self._set_operation_message(f"注册作者账号失败：{exc}", error=True)

            return

        self.author_account = account

        self._remember_author_auth_preferences(username=username, password=password)

        self._refresh_author_ui()

        self.refresh_remote_packages()

        self._set_operation_message(f"作者账号已注册并登录：{account.username}")



    def login_author_account(self, username: str = "", password: str = "") -> None:

        if not username or not password:

            username, password, _action = self._prompt_author_credentials("作者登录", action="login")

        if not username or not password:

            return

        try:

            account = self.package_manager.login_author_account(

                username=username,

                password=password,

                auth_server_base=self._get_market_auth_server_base(),

                verify_ssl=self._get_market_verify_ssl(),

            )

        except Exception as exc:

            self._set_operation_message(f"作者登录失败：{exc}", error=True)

            return

        self.author_account = account

        self._remember_author_auth_preferences(username=username, password=password)

        self._refresh_author_ui()

        self.refresh_remote_packages()

        self._set_operation_message(f"作者已登录：{account.username}")



    def logout_author_account(self) -> None:

        token = self._get_author_token()

        if token:

            try:

                self.package_manager.logout_author_account(

                    token,

                    auth_server_base=self._get_market_auth_server_base(),

                    verify_ssl=self._get_market_verify_ssl(),

                )

            except Exception:

                pass

        self.author_account = MarketAuthorAccount()

        self._save_author_auth_state(auto_login=False)

        if self.my_packages_checkbox.isChecked():

            self.my_packages_checkbox.blockSignals(True)

            self.my_packages_checkbox.setChecked(False)

            self.my_packages_checkbox.blockSignals(False)

        self._refresh_author_ui()

        self.refresh_remote_packages()

        self._set_operation_message("作者账号已退出")



    def _on_my_packages_toggled(self, checked: bool) -> None:

        target_scope = "mine" if checked else "all"

        if checked and not self._ensure_author_login("查看我的发布"):

            self.my_packages_checkbox.blockSignals(True)

            self.my_packages_checkbox.setChecked(False)

            self.my_packages_checkbox.blockSignals(False)

            self._market_scope = "all"

            self._update_scope_buttons()

            return

        self._market_scope = target_scope

        self._update_scope_buttons()

        self.refresh_remote_packages()



    def _find_remote_package(self, package_id: str, version: str) -> Optional[RemoteMarketPackageSummary]:

        key = self._remote_key_by_id(package_id, version)

        return self.remote_package_map.get(key)



    def _current_detail_can_edit(self) -> bool:

        manifest = self._detail_manifest

        if manifest is not None:

            return True

        if self._detail_remote is not None:

            return bool(self._detail_remote.can_run)

        return False



    def _current_detail_supports_favorite(self) -> bool:

        manifest = self._detail_manifest

        if manifest is None:

            return False

        if self._detail_entry_path is not None and self._detail_entry_path.exists():

            return True

        return is_manifest_protected(manifest)



    def build_local_package(self, build_data: Optional[dict] = None) -> None:

        if build_data is None:

            self.open_build_dialog()

            return



        data = self._normalize_build_data(build_data)

        entry_workflow = str(data.get("entry_workflow") or "").strip()

        if not entry_workflow:

            self._set_operation_message("请先选择入口工作流", error=True)

            return

        if not Path(entry_workflow).exists():

            self._set_operation_message(f"入口工作流不存在：{entry_workflow}", error=True)

            return



        package_id = suggest_package_id(data.get("package_id"))

        if not package_id:

            self._set_operation_message("包ID不能为空", error=True)

            return

        try:

            package_id = validate_package_id(package_id)

        except ValueError as exc:

            self._set_operation_message(str(exc), error=True)

            return



        version = str(data.get("version") or "").strip()

        if not version:

            self._set_operation_message("版本号不能为空", error=True)

            return

        try:

            version = validate_version(version)

        except ValueError as exc:

            self._set_operation_message(str(exc), error=True)

            return



        title = str(data.get("title") or "").strip()

        if not title:

            self._set_operation_message("脚本名称不能为空", error=True)

            return



        author = str(data.get("author") or "").strip()

        category = str(data.get("category") or "").strip()

        output_path = str(data.get("output_path") or "").strip() or str(Path.cwd() / f"{package_id}-{version}.lca_market.zip")

        output_dir = Path(output_path).expanduser().resolve().parent

        if not output_dir.exists():

            self._set_operation_message(f"输出目录不存在：{output_dir}", error=True)

            return



        manifest = MarketPackageManifest(

            package_id=package_id,

            version=version,

            title=title,

            author=author,

            category=category,

            description=title,

        )



        try:

            result = self.package_manager.build_package_from_workflow(

                entry_workflow_path=entry_workflow,

                manifest=manifest,

                output_path=output_path,

                config_data=self._build_precheck_config(),

            )

        except Exception as exc:

            self._set_operation_message(f"打包脚本失败：{exc}", error=True)

            return



        self.load_archive(result.archive_path)

        self._set_operation_message(

            "打包完成："

            f"{Path(result.archive_path).name}，"

            f"工作流 {len(result.workflow_files)} 个，"

            f"资源 {len(result.collected_files)} 个，"

            f"字库 {len(result.dict_names)} 个"

        )

        self.open_publish_dialog(initial_archive_path=str(result.archive_path))

    def publish_local_package(

        self,

        archive_path: str = "",

        changelog: str = "",

        release_notes: str = "",

        expected_package_id: str = "",

    ) -> bool:

        if not self._ensure_author_login("发布脚本"):

            return False



        archive_text = str(archive_path or "").strip()

        if not archive_text:

            self._set_operation_message("请先选择待发布共享平台包", error=True)

            return False



        archive_file = Path(archive_text)

        if not archive_file.exists():

            self._set_operation_message(f"待发布共享平台包不存在：{archive_file}", error=True)

            return False



        try:

            manifest = self.package_manager.load_manifest_from_archive(archive_file)

        except Exception as exc:

            QMessageBox.critical(self, "脚本共享平台", f"读取共享平台包失败：\n{exc}")

            return False



        expected_package_id = str(expected_package_id or "").strip()

        if expected_package_id and manifest.package_id != expected_package_id:

            QMessageBox.warning(

                self,

                "脚本共享平台",

                f"更新脚本时包ID必须一致。\n当前脚本：{expected_package_id}\n所选共享平台包：{manifest.package_id}",

            )

            return False



        try:

            result = self.package_manager.publish_package_archive(

                archive_path=archive_file,

                auth_server_base=self._get_market_auth_server_base(),

                update_server_base=self.get_market_update_server_base(),

                verify_ssl=self._get_market_verify_ssl(),

                changelog=str(changelog or "").strip(),

                release_notes=str(release_notes or "").strip(),

                author_token=self._get_author_token(),

            )

        except Exception as exc:

            QMessageBox.critical(self, "脚本共享平台", f"发布脚本失败：\n{exc}")

            self._set_operation_message(f"发布脚本失败：{exc}", error=True)

            return False



        upload_result = result.get("upload") if isinstance(result, dict) else {}

        publish_result = result.get("publish") if isinstance(result, dict) else {}

        self.market_summary_label.setText(f"已提交审核：{manifest.title or manifest.package_id} [{manifest.version}]")

        self._set_operation_message(

            f"发布已提交审核：{manifest.package_id} [{manifest.version}]，"

            f"状态 {publish_result.get('status', 'submitted')}，"

            f"暂存路径 {upload_result.get('storage_path', '-') }"

        )

        self.refresh_remote_packages(selected_key=self._remote_key_by_id(manifest.package_id, manifest.version))

        return True

    def refresh_remote_packages(self, selected_key: str = "") -> None:

        self.refresh_button.setEnabled(False)

        self.primary_action_button.setEnabled(False)

        self.market_summary_label.setText("正在加载脚本共享平台...")

        if hasattr(self, 'catalog_status_label'):

            self.catalog_status_label.setText("正在同步远程脚本列表...")

        try:

            packages = self.package_manager.list_remote_packages(

                auth_server_base=self._get_market_auth_server_base(),

                verify_ssl=self._get_market_verify_ssl(),

                author_token=self._get_author_token(),

                mine_only=self._market_scope == "mine",

            )

        except Exception as exc:

            self.remote_packages = []

            self.remote_package_map.clear()

            self.remote_list.clear()

            self._update_summary_label(extra_message="加载失败")

            self._show_empty_detail(f"加载脚本共享平台失败\n\n{exc}")

            self.refresh_button.setEnabled(True)

            return



        self.remote_packages = packages

        self._apply_remote_filter(selected_key=selected_key)

        self.refresh_button.setEnabled(True)



    def refresh_installed_packages(self) -> None:

        self.installed_manifest_map.clear()

        for manifest in self.package_manager.list_installed_manifests():

            self.installed_manifest_map[self._manifest_key(manifest)] = manifest

        self._update_summary_label()



    def _apply_remote_filter(self, *_args, selected_key: str = "") -> None:

        current_key = selected_key or self._get_selected_remote_key() or self.pending_remote_key

        keyword = str(self.search_input.text() or "").strip().lower()



        filtered_packages: list[RemoteMarketPackageSummary] = []

        for package in self.remote_packages:

            haystack = "\n".join([

                package.title or "",

                package.package_id or "",

                package.category or "",

                package.summary or "",

                package.version or "",

                package.author_name or "",

            ]).lower()

            if keyword and keyword not in haystack:

                continue

            if not self._package_matches_market_scope(package):

                continue

            filtered_packages.append(package)



        self.remote_package_map.clear()

        self.remote_list.blockSignals(True)

        self.remote_list.clear()

        selected_item: Optional[QListWidgetItem] = None



        for package in filtered_packages:

            key = self._remote_key(package)

            self.remote_package_map[key] = package

            item = QListWidgetItem(self._build_remote_list_item_text(package))

            item.setData(Qt.ItemDataRole.UserRole, key)

            item.setToolTip(self._build_remote_list_item_tooltip(package))

            item.setSizeHint(self._build_remote_list_item_size(package))

            self.remote_list.addItem(item)

            if key == current_key:

                selected_item = item



        self.remote_list.blockSignals(False)

        self._update_summary_label(filtered_count=len(filtered_packages))



        if self.remote_list.count() == 0:

            if self._detail_mode not in {"pending", "installed"}:

                empty_message = "没有匹配的脚本" if keyword else "脚本共享平台暂无可用脚本"

                self._show_empty_detail(empty_message)

            return



        if selected_item is None:

            selected_item = self.remote_list.item(0)

        self.remote_list.setCurrentItem(selected_item)



    def _on_remote_item_changed(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:

        if current is None:

            if self._detail_mode not in {"pending", "installed"}:

                self._show_empty_detail()

            return

        key = str(current.data(Qt.ItemDataRole.UserRole) or "")

        package = self.remote_package_map.get(key)

        if package is None:

            self._show_empty_detail()

            return

        self._show_remote_detail(package)



    def _show_remote_list_context_menu(self, pos) -> None:

        menu = apply_unified_menu_style(QMenu(self), frameless=True)

        current_item = self.remote_list.itemAt(pos)

        global_pos = self.remote_list.viewport().mapToGlobal(pos)



        if current_item is None:

            publish_action = menu.addAction("发布脚本")

            chosen_action = self._exec_market_context_menu(menu, global_pos)

            if chosen_action == publish_action:

                self.open_publish_dialog()

            return



        self.remote_list.setCurrentItem(current_item)

        key = str(current_item.data(Qt.ItemDataRole.UserRole) or "")

        package = self.remote_package_map.get(key)

        if package is None:

            return



        update_action = None

        offline_action = None

        resume_action = None

        delete_action = None



        if self._can_manage_remote_package(package):

            update_action = menu.addAction("更新脚本")

            if self._can_offline_package(package):

                offline_action = menu.addAction("暂时下架")

            elif self._can_resume_package(package):

                resume_action = menu.addAction("恢复上架")



        if package.can_delete:

            delete_action = menu.addAction("删除脚本")



        if not menu.actions():

            return



        chosen_action = self._exec_market_context_menu(menu, global_pos)

        if chosen_action is None:

            return

        if chosen_action == update_action:

            self.open_publish_dialog(package=package)

        elif chosen_action == offline_action:

            self._change_remote_package_status(package, "offline")

        elif chosen_action == resume_action:

            self._change_remote_package_status(package, "released")

        elif chosen_action == delete_action:

            self._delete_remote_package(package)







    def _on_primary_action_clicked(self) -> None:

        if self._detail_mode == "remote":

            self.download_selected_remote_package()

            return

        if self._detail_mode == "pending":

            self.install_pending_package()

            return



    def download_selected_remote_package(self) -> None:

        package = self._get_selected_remote_package()

        if package is None:

            QMessageBox.information(self, "脚本共享平台", "请先选择一个脚本")

            return

        installed_manifest = self._find_installed_manifest(package.package_id, package.version)

        if installed_manifest is not None:

            self._show_installed_detail(installed_manifest, remote_package=package)

            QMessageBox.information(self, "脚本共享平台", "当前版本已安装，卸载后才会显示下载")

            return

        if not package.can_run:

            QMessageBox.information(self, "脚本共享平台", "当前版本未发布，暂不可下载")

            return



        self.primary_action_button.setEnabled(False)

        try:

            archive_path = self.package_manager.download_remote_package(

                package.package_id,

                package.version,

                download_url=package.download_url,

                auth_server_base=self._get_market_auth_server_base(),

                update_server_base=self.get_market_update_server_base(),

                verify_ssl=self._get_market_verify_ssl(),

            )

            self.load_archive(archive_path, remote_key=self._remote_key(package))

            self.market_summary_label.setText(

                f"已下载并完成预检：{package.title or package.package_id} [{package.version}]"

            )

        except Exception as exc:

            QMessageBox.critical(self, "脚本共享平台", f"下载脚本失败：\n{exc}")

        finally:

            self.primary_action_button.setEnabled(True)



    def delete_current_remote_package(self) -> None:

        package = self._detail_remote or self._get_selected_remote_package()

        if package is None:

            QMessageBox.information(self, "脚本共享平台", "请先选择一个脚本")

            return

        self._delete_remote_package(package)



    def _delete_remote_package(self, package: RemoteMarketPackageSummary) -> None:

        if not package.can_delete:

            QMessageBox.information(self, "脚本共享平台", "当前版本不允许删除")

            return



        answer = QMessageBox.question(

            self,

            "删除版本",

            f"确认删除该远程版本吗？\n\n包ID：{package.package_id}\n版本：{package.version}\n状态：{package.status or '-'}",

            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,

            QMessageBox.StandardButton.No,

        )

        if answer != QMessageBox.StandardButton.Yes:

            return



        self.delete_button.setEnabled(False)

        try:

            self.package_manager.delete_remote_package(

                package.package_id,

                package.version,

                auth_server_base=self._get_market_auth_server_base(),

                verify_ssl=self._get_market_verify_ssl(),

                author_token=self._get_author_token(),

            )

        except Exception as exc:

            QMessageBox.critical(self, "脚本共享平台", f"删除版本失败：\n{exc}")

            return

        finally:

            self.delete_button.setEnabled(True)



        installed_manifest = self._find_installed_manifest(package.package_id, package.version)

        self.refresh_remote_packages()

        if installed_manifest is not None:

            self._show_installed_detail(installed_manifest, remote_package=None)

        else:

            self._show_empty_detail("远程版本已删除")

        self.market_summary_label.setText(f"已删除远程版本：{package.package_id} [{package.version}]")



    def _change_remote_package_status(self, package: RemoteMarketPackageSummary, action: str) -> None:

        normalized_action = str(action or "").strip().lower()

        if normalized_action not in {"offline", "released"}:

            return



        action_text = "暂时下架" if normalized_action == "offline" else "恢复上架"

        answer = QMessageBox.question(

            self,

            action_text,

            f"确认对脚本执行“{action_text}”吗？\n\n包ID：{package.package_id}\n版本：{package.version}",

            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,

            QMessageBox.StandardButton.No,

        )

        if answer != QMessageBox.StandardButton.Yes:

            return



        try:

            self.package_manager.update_remote_package_status(

                package.package_id,

                package.version,

                normalized_action,

                auth_server_base=self._get_market_auth_server_base(),

                verify_ssl=self._get_market_verify_ssl(),

                author_token=self._get_author_token(),

            )

        except Exception as exc:

            QMessageBox.critical(self, "脚本共享平台", f"{action_text}失败：\n{exc}")

            return



        self.refresh_remote_packages(selected_key=self._remote_key_by_id(package.package_id, package.version))

        self._set_operation_message(f"已完成：{action_text} {package.package_id} [{package.version}]")



    def select_archive(self) -> None:

        archive_path, _ = QFileDialog.getOpenFileName(

            self,

            "选择共享平台包",

            "",

            "LCA 共享平台包 (*.lca_market.zip *.zip)",

        )

        if not archive_path:

            return

        self.load_archive(archive_path)



    def load_archive(self, archive_path: str | Path, remote_key: str = "") -> None:

        archive = Path(archive_path)

        try:

            manifest = self.package_manager.load_manifest_from_archive(archive)

            report = self._run_precheck(manifest)

        except Exception as exc:

            QMessageBox.warning(self, "脚本共享平台", f"读取共享平台包失败：\n{exc}")

            return



        self._update_precheck_operation_message(report)

        self.pending_archive_path = archive

        self.pending_manifest = manifest

        self.pending_report = report

        self.pending_remote_key = str(remote_key or "")

        remote_package = self.remote_package_map.get(self.pending_remote_key) if self.pending_remote_key else None

        self._show_pending_detail(manifest, report, remote_package=remote_package)



    def recheck_current_package(self) -> None:

        manifest = self._detail_manifest or self.pending_manifest

        if manifest is None:

            QMessageBox.information(self, "脚本共享平台", "请先选择或导入一个脚本")

            return

        try:

            report = self._run_precheck(manifest)

        except Exception as exc:

            QMessageBox.warning(self, "脚本共享平台", f"重新预检失败：\n{exc}")

            return





        self._update_precheck_operation_message(report)

        if self._detail_mode == "installed":

            self._show_installed_detail(manifest, report=report)

            return



        if self._detail_mode == "remote" and self._detail_remote is not None and self._detail_manifest is not None:

            self._detail_report = report

            self._show_remote_detail(self._detail_remote)

            return



        self.pending_manifest = manifest

        self.pending_report = report

        self._show_pending_detail(manifest, report, remote_package=self._detail_remote)



    def install_pending_package(self) -> None:

        if not self.pending_archive_path:

            QMessageBox.information(self, "脚本共享平台", "请先下载或导入一个共享平台包")

            return



        if self.pending_report is not None and not self.pending_report.passed:

            answer = QMessageBox.question(

                self,

                "脚本共享平台",

                "当前预检未通过，继续安装可能无法直接运行。\n确定仍然安装吗？",

                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,

                QMessageBox.StandardButton.No,

            )

            if answer != QMessageBox.StandardButton.Yes:

                return



        try:

            manifest = self.package_manager.install_package_from_archive(self.pending_archive_path)

        except Exception as exc:

            QMessageBox.critical(self, "脚本共享平台", f"安装脚本失败：\n{exc}")

            return



        remote_key = self.pending_remote_key or self._remote_key_by_id(manifest.package_id, manifest.version)

        self.pending_archive_path = None

        self.pending_manifest = None

        self.pending_report = None

        self.pending_remote_key = ""



        self.refresh_installed_packages()

        if self.remote_packages:

            self._apply_remote_filter(selected_key=remote_key)



        try:

            report = self._run_precheck(manifest)

        except Exception:

            report = None

        self._update_precheck_operation_message(report)

        remote_package = self.remote_package_map.get(remote_key) if remote_key else self._detail_remote

        self._show_installed_detail(manifest, report=report, remote_package=remote_package)

        self.market_summary_label.setText(

            f"已安装：{manifest.title or manifest.package_id} [{manifest.version}]"

        )



    def uninstall_current_package(self) -> None:

        manifest = self._detail_manifest

        if manifest is None:

            QMessageBox.information(self, "脚本共享平台", "当前没有可卸载的脚本")

            return



        answer = QMessageBox.question(

            self,

            "脚本共享平台",

            f"确定卸载该脚本吗？\n\n名称：{manifest.title or manifest.package_id}\n版本：{manifest.version}",

            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,

            QMessageBox.StandardButton.No,

        )

        if answer != QMessageBox.StandardButton.Yes:

            return



        remote_package = self._detail_remote or self._find_remote_package(manifest.package_id, manifest.version)

        remote_key = self._remote_key(remote_package) if remote_package is not None else ""

        package_id = str(manifest.package_id or "").strip()

        version = str(manifest.version or "").strip()



        self.uninstall_button.setEnabled(False)

        try:

            self.package_manager.uninstall_installed_package(manifest.package_id, manifest.version)

        except Exception as exc:

            QMessageBox.critical(self, "脚本共享平台", f"卸载脚本失败：\n{exc}")

            return

        finally:

            self.uninstall_button.setEnabled(True)



        if package_id and version:

            self.package_uninstalled.emit(package_id, version)



        self.refresh_installed_packages()

        if self.remote_packages:

            self._apply_remote_filter(selected_key=remote_key)



        latest_remote = remote_package or self._find_remote_package(manifest.package_id, manifest.version)

        if latest_remote is not None:

            self._show_remote_detail(latest_remote)

        else:

            self._show_empty_detail("脚本已卸载")



        self.market_summary_label.setText(

            f"已卸载：{manifest.title or manifest.package_id} [{manifest.version}]"

        )



    def open_current_entry_workflow(self) -> None:

        manifest = self._detail_manifest

        if manifest is None:

            QMessageBox.warning(self, "脚本共享平台", "当前没有可打开的脚本")

            return



        if manifest.package_id and manifest.version and manifest.entry_workflow:

            workflow_ref = build_market_workflow_ref(manifest.package_id, manifest.version, manifest.entry_workflow)

            self.entry_workflow_open_requested.emit(workflow_ref)

            return



        entry_path = self._detail_entry_path

        if entry_path is None or not entry_path.exists():

            QMessageBox.warning(self, "脚本共享平台", "未找到入口工作流")

            return

        self.entry_workflow_open_requested.emit(str(entry_path))



    def favorite_current_entry_workflow(self) -> None:

        manifest = self._detail_manifest

        if manifest is None:

            QMessageBox.warning(self, "脚本共享平台", "当前没有可收藏的脚本")

            return

        if not self._current_detail_supports_favorite():

            QMessageBox.information(self, "脚本共享平台", "当前脚本暂不支持加入收藏")

            return



        entry_path = self._detail_entry_path

        display_name = manifest.title or manifest.package_id or (entry_path.stem if entry_path else "未命名脚本")

        if manifest.package_id and manifest.version and manifest.entry_workflow:

            workflow_ref = build_market_workflow_ref(manifest.package_id, manifest.version, manifest.entry_workflow)

            self.entry_workflow_favorite_requested.emit(workflow_ref, display_name)

            return



        if entry_path is None or not entry_path.exists():

            entry_path = self._resolve_installed_entry_workflow(manifest)

            self._detail_entry_path = entry_path

        if entry_path is None or not entry_path.exists():

            QMessageBox.warning(self, "脚本共享平台", "未找到入口工作流")

            return

        self.entry_workflow_favorite_requested.emit(str(entry_path), display_name)



    def _show_empty_detail(self, message: str = "请选择左侧脚本，或导入本地共享平台包") -> None:

        self._detail_mode = "empty"

        self._detail_manifest = None

        self._detail_report = None

        self._detail_remote = None

        self._detail_entry_path = None

        if hasattr(self, 'detail_tabs') and hasattr(self, 'detail_overview_tab'):

            self.detail_tabs.setCurrentWidget(self.detail_overview_tab)

        self.detail_title_label.setText("脚本共享平台")

        self.detail_meta_label.setText("")

        self._set_detail_status_message("先选脚本，再下载预检，最后安装使用。")

        self._set_detail_browser_html(self._render_detail_text_as_html(message))

        self._refresh_detail_actions()



    def _show_remote_detail(self, package: RemoteMarketPackageSummary) -> None:

        installed_manifest = self._find_installed_manifest(package.package_id, package.version)

        installed_versions = self._get_installed_versions(package.package_id)

        installed_text = "未安装"

        if package.version in installed_versions:

            installed_text = "已安装当前版本"

        elif installed_versions:

            installed_text = f"已安装其他版本：{', '.join(installed_versions)}"



        self._detail_mode = "remote"

        self._detail_remote = package

        self._detail_manifest = installed_manifest

        self._detail_report = None

        self._detail_entry_path = self._resolve_installed_entry_workflow(installed_manifest)

        if hasattr(self, 'detail_tabs') and hasattr(self, 'detail_overview_tab'):

            self.detail_tabs.setCurrentWidget(self.detail_overview_tab)



        self.detail_title_label.setText(package.title or package.package_id or "未命名脚本")

        meta_parts = [

            f"版本 {package.version or '-'}",

            f"分类 {package.category or '-'}",

            f"包ID {package.package_id or '-'}",

        ]

        self.detail_meta_label.setText(" · ".join(meta_parts))

        normalized_status = self._normalize_remote_status(package)

        if normalized_status == "released":

            if installed_manifest is not None:

                self._set_detail_status_message(

                    "已安装当前版本。卸载后才会显示下载按钮。",

                    passed=True,

                )

            else:

                self._set_detail_status_message(

                    f"{installed_text}。请先下载并预检，结果会用红绿直接显示。",

                    passed=True,

                )

        elif normalized_status == "offline":

            self._set_detail_status_message(

                f"{installed_text}。当前版本已暂时下架，仅作者可恢复上架、更新或删除。",

                passed=False,

            )

        elif normalized_status == "submitted":

            self._set_detail_status_message(

                f"{installed_text}。当前版本正在审核中，暂不可下载运行。",

                passed=False,

            )

        elif package.can_delete:

            self._set_detail_status_message(

                f"{installed_text}。当前版本未发布，仅作者可删除或重新发布。",

                passed=False,

            )

        else:

            self._set_detail_status_message(

                f"{installed_text}。当前版本未发布，暂不可下载运行。",

                passed=False,

            )

        self._set_detail_browser_html(

            self._render_remote_overview_html(package, installed_versions),

            self._build_remote_detail_text(package, installed_manifest),

        )

        self._refresh_detail_actions()



    def _show_pending_detail(

        self,

        manifest: MarketPackageManifest,

        report: PrecheckReport,

        remote_package: Optional[RemoteMarketPackageSummary] = None,

    ) -> None:

        self._detail_mode = "pending"

        self._detail_manifest = manifest

        self._detail_report = report

        self._detail_remote = remote_package

        self._detail_entry_path = None

        if hasattr(self, 'detail_tabs') and hasattr(self, 'detail_overview_tab'):

            self.detail_tabs.setCurrentWidget(self.detail_overview_tab)



        self.detail_title_label.setText(manifest.title or manifest.package_id or "未命名脚本")

        source_text = "来自脚本共享平台" if remote_package is not None else "本地导入"

        meta_parts = [

            f"版本 {manifest.version or '-'}",

            f"作者 {manifest.author or '-'}",

            source_text,

        ]

        self.detail_meta_label.setText(" · ".join(meta_parts))

        self._set_detail_status_message(self._format_precheck_status(report), passed=report.passed)

        self._set_detail_browser_html(

            self._render_manifest_overview_html(manifest, report=report),

            self._build_manifest_detail_text(manifest, report=report, remote_package=self._detail_remote),

        )

        self._refresh_detail_actions()



    def _show_installed_detail(

        self,

        manifest: MarketPackageManifest,

        report: Optional[PrecheckReport] = None,

        remote_package: Optional[RemoteMarketPackageSummary] = None,

    ) -> None:

        self._detail_mode = "installed"

        self._detail_manifest = manifest

        self._detail_report = report

        self._detail_remote = remote_package or self._find_remote_package(manifest.package_id, manifest.version)

        self._detail_entry_path = self._resolve_installed_entry_workflow(manifest)

        if hasattr(self, 'detail_tabs') and hasattr(self, 'detail_overview_tab'):

            self.detail_tabs.setCurrentWidget(self.detail_overview_tab)



        self.detail_title_label.setText(manifest.title or manifest.package_id or "未命名脚本")

        meta_parts = [

            f"版本 {manifest.version or '-'}",

            f"作者 {manifest.author or '-'}",

            "已安装",

        ]

        self.detail_meta_label.setText(" · ".join(meta_parts))

        if report is not None:

            self._set_detail_status_message(self._format_precheck_status(report), passed=report.passed)

        else:

            self._set_detail_status_message("已安装，可打开画布编辑或加入收藏运行。", passed=True)

        self._set_detail_browser_html(

            self._render_manifest_overview_html(manifest, report=report),

            self._build_manifest_detail_text(manifest, report=report, remote_package=self._detail_remote),

        )

        self._refresh_detail_actions()



    def _refresh_detail_actions(self) -> None:

        has_entry = self._detail_entry_path is not None and self._detail_entry_path.exists()

        supports_favorite = self._current_detail_supports_favorite()

        can_edit_entry = self._current_detail_can_edit() and (has_entry or supports_favorite)



        if self._detail_mode == "remote":

            can_run_remote = self._detail_remote is not None and bool(self._detail_remote.can_run)

            can_delete_remote = self._detail_remote is not None and bool(self._detail_remote.can_delete)

            installed_current_version = self._detail_manifest is not None

            can_download_remote = can_run_remote and not installed_current_version

            self.primary_action_button.setVisible(can_download_remote)

            self.primary_action_button.setEnabled(can_download_remote)

            self.primary_action_button.setText("下载并预检")

            self.recheck_button.setVisible(installed_current_version)

            self.recheck_button.setEnabled(installed_current_version and self._detail_manifest is not None)

            self.open_entry_button.setVisible(installed_current_version and can_edit_entry)

            self.open_entry_button.setEnabled(installed_current_version and can_edit_entry)

            self.open_entry_button.setText("打开画布编辑")

            self.favorite_entry_button.setVisible(installed_current_version and supports_favorite)

            self.uninstall_button.setVisible(installed_current_version)

            self.uninstall_button.setEnabled(installed_current_version)

            self.delete_button.setVisible(can_delete_remote)

            self.delete_button.setEnabled(can_delete_remote)

            return



        if self._detail_mode == "pending":

            self.primary_action_button.setVisible(True)

            self.primary_action_button.setEnabled(self.pending_archive_path is not None)

            self.primary_action_button.setText("安装脚本")

            self.recheck_button.setVisible(True)

            self.recheck_button.setEnabled(self._detail_manifest is not None)

            self.open_entry_button.setVisible(False)

            self.favorite_entry_button.setVisible(False)

            self.uninstall_button.setVisible(False)

            self.delete_button.setVisible(False)

            return



        if self._detail_mode == "installed":

            self.primary_action_button.setVisible(False)

            self.recheck_button.setVisible(True)

            self.recheck_button.setEnabled(self._detail_manifest is not None)

            self.open_entry_button.setVisible(can_edit_entry)

            self.open_entry_button.setEnabled(can_edit_entry)

            self.open_entry_button.setText("打开画布编辑")

            self.favorite_entry_button.setVisible(supports_favorite)

            self.uninstall_button.setVisible(True)

            self.uninstall_button.setEnabled(self._detail_manifest is not None)

            self.delete_button.setVisible(False)

            return



        self.primary_action_button.setVisible(False)

        self.recheck_button.setVisible(False)

        self.open_entry_button.setVisible(False)

        self.favorite_entry_button.setVisible(False)

        self.uninstall_button.setVisible(False)

        self.delete_button.setVisible(False)



    def _update_summary_label(self, filtered_count: Optional[int] = None, extra_message: str = "") -> None:

        if extra_message:

            self.market_summary_label.setText(extra_message)

            if hasattr(self, 'sidebar_summary_label'):

                self.sidebar_summary_label.setText(extra_message)

            if hasattr(self, 'catalog_status_label'):

                self.catalog_status_label.setText(extra_message)

            return



        total_count = len(self.remote_packages)

        installed_count = len(self.installed_manifest_map)

        parts = [f"共享平台 {total_count} 个", f"已安装 {installed_count} 个"]

        if filtered_count is not None and total_count and filtered_count != total_count:

            parts.append(f"筛选后 {filtered_count} 个")

        summary_text = " · ".join(parts)

        self.market_summary_label.setText(summary_text)

        if hasattr(self, 'sidebar_summary_label'):

            self.sidebar_summary_label.setText(

                "\n".join(

                    [

                        f"共享平台脚本：{total_count} 个",

                        f"已安装：{installed_count} 个",

                        f"当前视图：{ {'all': '发现', 'runnable': '可运行', 'installed': '已安装', 'mine': '我的发布'}.get(self._market_scope, '发现') }",

                    ]

                )

            )

        if hasattr(self, 'catalog_status_label'):

            scope_text_map = {

                "all": "当前视图：发现",

                "runnable": "当前视图：可运行",

                "installed": "当前视图：已安装",

                "mine": "当前视图：我的发布",

            }

            scope_text = scope_text_map.get(self._market_scope, "当前视图：发现")

            if filtered_count is None:

                self.catalog_status_label.setText(scope_text)

            else:

                self.catalog_status_label.setText(f"{scope_text} · 共 {filtered_count} 个结果")



    def _run_precheck(self, manifest: MarketPackageManifest) -> PrecheckReport:

        self._last_auto_adjust_result = None

        config_data = self._build_precheck_config()

        snapshot = capture_environment_snapshot(config_data=config_data)

        report = self.precheck_engine.run(manifest, environment=snapshot)

        if not report.issues:

            return report



        auto_adjust_result = self.precheck_auto_adjuster.apply(manifest, report, config_data=config_data)

        self._last_auto_adjust_result = auto_adjust_result

        if not auto_adjust_result.changed:

            return report



        self._apply_runtime_config(auto_adjust_result.updated_config)

        refreshed_config = self._build_precheck_config() if callable(self.config_applier) else dict(auto_adjust_result.updated_config)

        refreshed_snapshot = capture_environment_snapshot(config_data=refreshed_config)

        return self.precheck_engine.run(manifest, environment=refreshed_snapshot)



    def _apply_runtime_config(self, config_data: dict) -> None:

        normalized_config = dict(config_data or {})

        if not normalized_config:

            return

        self.current_config = dict(normalized_config)

        if callable(self.config_applier):

            self.config_applier(dict(normalized_config))



    def _update_precheck_operation_message(self, report: Optional[PrecheckReport]) -> None:

        result = self._last_auto_adjust_result

        if result is None:

            self._set_operation_message("")

            return



        parts = []

        details = []

        if result.applied_items:

            parts.append(f"\u5df2\u81ea\u52a8\u8c03\u6574 {len(result.applied_items)} \u9879")

            details.append(result.applied_items[0])

        if result.skipped_items:

            parts.append(f"\u4ecd\u6709 {len(result.skipped_items)} \u9879\u9700\u624b\u52a8\u5904\u7406")

            details.append(result.skipped_items[0])

        elif result.applied_items and report is not None and report.passed:

            parts.append("\u5f53\u524d\u73af\u5883\u5df2\u7b26\u5408\u8981\u6c42")



        if not parts:

            self._set_operation_message("")

            return



        message = "\uff0c".join(parts)

        if details:

            message = f"{message}\uff1a" + "\uff1b".join(details)

        has_remaining_issue = bool(result.skipped_items) or bool(report is not None and not report.passed)

        self._set_operation_message(message, error=has_remaining_issue)



    def _build_precheck_config(self) -> dict:

        if callable(self.config_provider):

            try:

                config_data = self.config_provider() or {}

            except Exception:

                config_data = dict(self.current_config)

        else:

            config_data = dict(self.current_config)

        if not isinstance(config_data, dict):

            config_data = dict(self.current_config)

        return dict(config_data)



    def _get_selected_remote_key(self) -> str:

        current_item = self.remote_list.currentItem()

        if current_item is None:

            return ""

        return str(current_item.data(Qt.ItemDataRole.UserRole) or "")



    def _get_selected_remote_package(self) -> Optional[RemoteMarketPackageSummary]:

        key = self._get_selected_remote_key()

        return self.remote_package_map.get(key)



    def _get_installed_versions(self, package_id: str) -> list[str]:

        versions = [

            manifest.version

            for manifest in self.installed_manifest_map.values()

            if manifest.package_id == package_id and manifest.version

        ]

        versions.sort()

        return versions



    def _resolve_installed_entry_workflow(self, manifest: Optional[MarketPackageManifest]) -> Optional[Path]:

        if manifest is None:

            return None

        return self.package_manager.get_installed_entry_workflow_path(

            manifest.package_id,

            manifest.version,

            manifest.entry_workflow,

        )



    def _find_installed_manifest(self, package_id: str, version: str) -> Optional[MarketPackageManifest]:

        return self.installed_manifest_map.get(self._remote_key_by_id(package_id, version))



    def _render_remote_overview_html(

        self,

        package: RemoteMarketPackageSummary,

        installed_versions: Optional[list[str]] = None,

    ) -> str:

        installed_versions = installed_versions or []

        normalized_status = self._normalize_remote_status(package)

        status_text, _ = self._describe_remote_status(package)

        installed_current = package.version in installed_versions

        if installed_current:

            install_status = "已安装"

        elif installed_versions:

            install_status = "版本不同"

        else:

            install_status = "未安装"

        cards = [

            {

                "title": "发布状态",

                "passed": normalized_status == "released",

                "status": "已发布" if normalized_status == "released" else status_text,

                "details": [

                    f"当前状态：{status_text}",

                    f"最新版本：{package.latest_version or package.version or '-'}",

                ],

            },

            {

                "title": "下载权限",

                "passed": bool(package.can_run) and not installed_current,

                "status": "已安装" if installed_current else ("可下载" if package.can_run else "不可下载"),

                "details": [

                    f"脚本版本：{package.version or '-'}",

                    "当前版本已安装，卸载后才会显示下载"

                    if installed_current

                    else ("可下载后执行预检" if package.can_run else "当前版本暂不可下载运行"),

                ],

            },

            {

                "title": "安装状态",

                "passed": installed_current,

                "status": install_status,

                "details": [

                    f"本地版本：{', '.join(installed_versions) if installed_versions else '无'}",

                    "当前版本已在本机安装" if installed_current else "当前版本尚未安装到本机",

                ],

            },

            {

                "title": "预检状态",

                "passed": False,

                "status": "可重检" if installed_current else "待预检",

                "details": [

                    "当前版本已安装，可直接重新预检" if installed_current else "先点“下载并预检”",

                    "版本、模式、截图引擎、窗口配置会直接用红绿显示",

                ],

            },

        ]

        sections = ["<div style='color:#1f2937; line-height:1.65;'>"]

        if package.summary:

            sections.append(

                f"<div style='margin:0 0 10px 0; color:#667085;'>{self._escape_multiline_html(package.summary)}</div>"

            )

        sections.append(

            self._build_overview_summary_html(

                "已安装" if installed_current else "尚未预检",

                "当前版本已安装，卸载后才会显示下载。" if installed_current else "先下载并预检，下面会直接显示哪些通过、哪些不通过。",

                passed=installed_current,

            )

        )

        sections.append(self._build_overview_cards_html(cards))

        sections.append("</div>")

        return "".join(sections)



    def _render_manifest_overview_html(

        self,

        manifest: MarketPackageManifest,

        report: Optional[PrecheckReport] = None,

    ) -> str:

        cards = self._build_manifest_overview_cards(manifest, report)

        passed_count = sum(1 for item in cards if item.get("passed"))

        total_count = len(cards)



        sections = ["<div style='color:#1f2937; line-height:1.65;'>"]

        if manifest.description:

            sections.append(

                f"<div style='margin:0 0 10px 0; color:#667085;'>{self._escape_multiline_html(manifest.description)}</div>"

            )

        sections.append(self._build_precheck_summary_html(report, total_count, passed_count))

        sections.append(self._build_overview_cards_html(cards))

        issue_html = self._build_precheck_issue_list_html(report)

        if issue_html:

            sections.append(issue_html)

        sections.append("</div>")

        return "".join(sections)



    def _build_manifest_overview_cards(

        self,

        manifest: MarketPackageManifest,

        report: Optional[PrecheckReport],

    ) -> list[dict]:

        runtime = manifest.runtime_requirement

        target_window = runtime.target_window

        environment = report.environment if report is not None else None

        checked = report is not None



        client_issues = self._collect_precheck_issues(report, {"client_version_too_low", "client_version_too_high"})

        mode_issues = self._collect_precheck_issues(report, {"execution_mode_mismatch"})

        engine_issues = self._collect_precheck_issues(report, {"screenshot_engine_mismatch"})

        plugin_issues = self._collect_precheck_issues(report, {"plugin_required", "plugin_id_mismatch"})

        binding_issues = self._collect_precheck_issues(report, {"bound_window_missing", "window_title_mismatch", "window_class_mismatch"})

        display_issues = self._collect_precheck_issues(

            report,

            {"client_width_exact", "client_height_exact", "client_width_range", "client_height_range", "window_dpi_mismatch", "window_scale_factor_mismatch"},

        )



        plugin_requirement = "无需插件"

        if runtime.plugin_required:

            plugin_requirement = runtime.plugin_id or "需启用插件模式"

            if runtime.plugin_min_version:

                plugin_requirement = f"{plugin_requirement}（最低版本 {runtime.plugin_min_version}）"



        binding_requirement = []

        if target_window.window_kind:

            binding_requirement.append(f"窗口类型 {target_window.window_kind}")

        if target_window.title_keywords:

            binding_requirement.append(f"标题含 {', '.join(target_window.title_keywords)}")

        if target_window.class_names:

            binding_requirement.append(f"类名 {', '.join(target_window.class_names)}")

        if not binding_requirement:

            binding_requirement.append("无强制绑定要求")



        display_requirement = []

        if target_window.client_width is not None or target_window.client_height is not None:

            display_requirement.append(f"尺寸 {self._format_size(target_window.client_width, target_window.client_height)}")

        if target_window.min_client_width is not None or target_window.max_client_width is not None:

            display_requirement.append(f"宽度 {target_window.min_client_width if target_window.min_client_width is not None else '-'} ~ {target_window.max_client_width if target_window.max_client_width is not None else '-'}")

        if target_window.min_client_height is not None or target_window.max_client_height is not None:

            display_requirement.append(f"高度 {target_window.min_client_height if target_window.min_client_height is not None else '-'} ~ {target_window.max_client_height if target_window.max_client_height is not None else '-'}")

        if target_window.dpi is not None:

            display_requirement.append(f"DPI {target_window.dpi}")

        if target_window.scale_factor is not None:

            display_requirement.append(f"缩放 {target_window.scale_factor}")

        if not display_requirement:

            display_requirement.append("无尺寸或 DPI 限制")



        current_window_title = environment.bound_window_title if environment is not None else "未检测"

        current_window_class = environment.bound_window_class_name if environment is not None else "未检测"

        current_display = "未检测"

        if environment is not None:

            current_display_parts = [f"尺寸 {self._format_size(environment.bound_window_client_width, environment.bound_window_client_height)}"]

            if environment.bound_window_dpi is not None:

                current_display_parts.append(f"DPI {environment.bound_window_dpi}")

            if environment.bound_window_scale_factor is not None:

                current_display_parts.append(f"缩放 {environment.bound_window_scale_factor}")

            current_display = "，".join(current_display_parts)



        client_passed, client_status = self._resolve_precheck_card_status(client_issues, checked)

        mode_passed, mode_status = self._resolve_precheck_card_status(mode_issues, checked)

        engine_passed, engine_status = self._resolve_precheck_card_status(engine_issues, checked)

        plugin_passed, plugin_status = self._resolve_precheck_card_status(plugin_issues, checked)

        binding_passed, binding_status = self._resolve_precheck_card_status(binding_issues, checked)

        display_passed, display_status = self._resolve_precheck_card_status(display_issues, checked)



        return [

            {"title": "客户端版本", "passed": client_passed, "status": client_status, "details": [f"要求：{self._format_client_version_requirement(manifest)}", f"当前：{environment.app_version if environment is not None else '未检测'}"]},

            {"title": "执行模式", "passed": mode_passed, "status": mode_status, "details": [f"要求：{runtime.execution_mode or '不限'}", f"当前：{environment.execution_mode if environment is not None else '未检测'}"]},

            {"title": "截图引擎", "passed": engine_passed, "status": engine_status, "details": [f"要求：{runtime.screenshot_engine or '不限'}", f"当前：{environment.screenshot_engine if environment is not None else '未检测'}"]},

            {"title": "插件模式", "passed": plugin_passed, "status": plugin_status, "details": [f"要求：{plugin_requirement}", f"当前：{self._format_current_plugin_text(environment)}"]},

            {"title": "窗口绑定", "passed": binding_passed, "status": binding_status, "details": [f"要求：{'；'.join(binding_requirement)}", f"当前标题：{current_window_title or '-'}", f"当前类名：{current_window_class or '-'}"]},

            {"title": "分辨率 / DPI", "passed": display_passed, "status": display_status, "details": [f"要求：{'；'.join(display_requirement)}", f"当前：{current_display}"]},

        ]



    def _build_precheck_summary_html(

        self,

        report: Optional[PrecheckReport],

        total_count: int,

        passed_count: int,

    ) -> str:

        if report is None:

            return self._build_overview_summary_html("未预检", f"还有 {total_count} 项未检查", passed=False)

        failed_count = max(0, total_count - passed_count)

        if report.passed:

            return self._build_overview_summary_html("已通过", f"{passed_count} 项通过", passed=True)

        return self._build_overview_summary_html("未通过", f"{failed_count} 项需要处理", passed=False)



    def _build_precheck_issue_list_html(self, report: Optional[PrecheckReport]) -> str:

        if report is None or not report.issues:

            return ""

        items: list[str] = []

        for issue in report.issues:

            action_text = self._format_issue_action_text(issue.action)

            detail = str(issue.message or "").strip()

            if action_text:

                detail = f"{detail}，{action_text}"

            items.append(

                "<div style='margin-bottom:6px; color:#c62828;'>"

                f"{html.escape(issue.title or '未命名问题')}：{html.escape(detail)}"

                "</div>"

            )

        return (

            "<div style='margin-top:10px; padding-top:8px; border-top:1px solid rgba(198, 40, 40, 0.14);'>"

            "<div style='font-weight:700; color:#c62828; margin-bottom:6px;'>需要处理</div>"

            + "".join(items)

            + "</div>"

        )



    def _format_precheck_status(self, report: PrecheckReport) -> str:

        blocking_count = len(report.blocking_issues)

        configure_count = len(report.configure_issues)

        warning_count = len(report.warning_issues)

        if report.passed:

            if warning_count:

                return f"预检通过，但有 {warning_count} 项提醒。"

            return "预检通过，可以直接安装运行。"

        details: list[str] = []

        if blocking_count:

            details.append(f"阻塞 {blocking_count} 项")

        if configure_count:

            details.append(f"需配置 {configure_count} 项")

        if warning_count:

            details.append(f"提醒 {warning_count} 项")

        return f"预检未通过：{'，'.join(details) if details else '请先调整当前环境。'}"



    def _build_overview_summary_html(self, title: str, detail: str, passed: bool) -> str:

        color = "#2e7d32" if passed else "#c62828"

        return (

            "<div style='margin-bottom:10px; padding:4px 0 10px 0; border-bottom:1px solid rgba(148, 163, 184, 0.16);'>"

            f"<div style='font-size:14px; font-weight:700; color:{color};'>{html.escape(title)}</div>"

            f"<div style='margin-top:4px; color:#475467;'>{html.escape(detail)}</div>"

            "</div>"

        )



    def _build_overview_cards_html(self, cards: list[dict]) -> str:

        if not cards:

            return ""

        return "<div style='margin:0 0 12px 0;'>" + "".join(self._build_overview_card_html(item) for item in cards) + "</div>"



    def _build_overview_card_html(self, item: dict) -> str:

        passed = bool(item.get("passed"))

        title = str(item.get("title") or "-")

        status = str(item.get("status") or ("通过" if passed else "未通过"))

        details = [str(detail or "").strip() for detail in (item.get("details") or []) if str(detail or "").strip()]

        color = "#2e7d32" if passed else "#c62828"

        dot = "通过" if passed else "未通过"

        extra_detail = ""

        if not passed and details:

            extra_detail = f"<div style='margin:2px 0 0 18px; color:#667085;'>{html.escape(details[0])}</div>"

        elif status == "待预检" and details:

            extra_detail = f"<div style='margin:2px 0 0 18px; color:#667085;'>{html.escape(details[0])}</div>"

        return (

            "<div style='margin-bottom:6px; padding:6px 0; border-bottom:1px solid rgba(148, 163, 184, 0.16);'>"

            "<table width='100%' cellspacing='0' cellpadding='0'><tr>"

            f"<td valign='middle'><div style='font-weight:600; color:#111827;'><span style='color:{color}; margin-right:8px;'>{html.escape(dot)}</span>{html.escape(title)}</div></td>"

            f"<td align='right' valign='middle'><div style='font-weight:700; color:{color};'>{html.escape(status)}</div></td>"

            "</tr></table>"

            f"{extra_detail}"

            "</div>"

        )



    def _collect_precheck_issues(self, report: Optional[PrecheckReport], codes: set[str]) -> list:

        if report is None or not codes:

            return []

        return [item for item in report.issues if item.code in codes]



    def _resolve_precheck_card_status(self, issues: list, checked: bool) -> tuple[bool, str]:

        if not checked:

            return False, "待预检"

        if not issues:

            return True, "通过"

        if any(getattr(item, "severity", "") in {"block", "configure"} for item in issues):

            return False, "未通过"

        return False, "有提醒"



    @staticmethod

    def _format_client_version_requirement(manifest: MarketPackageManifest) -> str:

        min_version = str(manifest.min_client_version or "").strip()

        max_version = str(manifest.max_client_version or "").strip()

        if min_version and max_version:

            return f"{min_version} ~ {max_version}"

        if min_version:

            return f">= {min_version}"

        if max_version:

            return f"<= {max_version}"

        return "不限"



    @staticmethod

    def _format_current_plugin_text(environment) -> str:

        if environment is None:

            return "未检测"

        if not environment.plugin_enabled:

            return "未启用"

        return environment.preferred_plugin or "已启用插件模式"



    @staticmethod

    def _format_issue_severity_text(severity: str) -> str:

        mapping = {"block": "阻塞", "configure": "需配置", "warn": "提醒"}

        return mapping.get(str(severity or "").strip().lower(), "问题")



    @staticmethod

    def _format_issue_action_text(action: str) -> str:

        mapping = {

            "update_client": "更新软件版本",

            "review_compatibility": "确认当前版本是否兼容",

            "open_execution_mode_settings": "切换到要求的执行模式",

            "open_screenshot_engine_settings": "切换到要求的截图引擎",

            "open_plugin_settings": "调整插件模式和插件类型",

            "open_window_binding_settings": "先绑定目标窗口",

            "rebind_window": "重新绑定正确的目标窗口",

            "adjust_window_resolution": "调整窗口分辨率",

            "review_dpi_settings": "检查 DPI 和缩放设置",

        }

        return mapping.get(str(action or "").strip(), "")



    @staticmethod

    def _escape_multiline_html(text: str) -> str:

        return html.escape(str(text or "")).replace("\n", "<br>")



    @staticmethod

    def _manifest_key(manifest: MarketPackageManifest) -> str:

        return f"{manifest.package_id}@{manifest.version}"



    @staticmethod

    def _remote_key(package: RemoteMarketPackageSummary) -> str:

        return f"{package.package_id}@{package.version}"



    @staticmethod

    def _remote_key_by_id(package_id: str, version: str) -> str:

        return f"{package_id}@{version}"



    @staticmethod

    def _format_size(width: Optional[int], height: Optional[int]) -> str:

        if width is None and height is None:

            return "-"

        return f"{width if width is not None else '?'} x {height if height is not None else '?'}"



    @staticmethod

    def _format_generic_value(value) -> str:

        if isinstance(value, list):

            return ", ".join(str(item) for item in value)

        if isinstance(value, dict):

            return ", ".join(f"{key}={val}" for key, val in value.items())

        return str(value)


