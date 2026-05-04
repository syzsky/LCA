from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from plugins.adapters.ola.runtime_config import normalize_ola_auth_settings
from ..main_window_parts.main_window_dropdown_helpers import CenteredTextDelegate, NoWheelSpinBox
from ..main_window_parts.main_window_dropdown_widget import CustomDropdown, QComboBox
from ..main_window_parts.main_window_support import normalize_execution_mode_setting
from utils.window_binding_utils import normalize_plugin_ola_binding

OLA_OFFICIAL_REGISTRATION_URL = "https://ola.olaplug.com/AppAreaName/Welcome"


class OLAAuthConfigDialog(QDialog):
    def __init__(self, auth_config, parent=None):
        super().__init__(parent)

        self.setWindowTitle("OLA授权配置")
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 14, 16, 14)

        form_layout = QGridLayout()
        form_layout.setHorizontalSpacing(12)
        form_layout.setVerticalSpacing(10)
        form_layout.setColumnStretch(1, 1)

        normalized_auth = normalize_ola_auth_settings(auth_config)

        user_code_label = QLabel("用户码:")
        self.user_code_edit = QLineEdit(self)
        self.user_code_edit.setClearButtonEnabled(True)
        self.user_code_edit.setPlaceholderText("留空则沿用当前默认授权")
        self.user_code_edit.setText(normalized_auth.get("user_code", ""))
        form_layout.addWidget(user_code_label, 0, 0)
        form_layout.addWidget(self.user_code_edit, 0, 1)

        soft_code_label = QLabel("软件码:")
        self.soft_code_edit = QLineEdit(self)
        self.soft_code_edit.setClearButtonEnabled(True)
        self.soft_code_edit.setPlaceholderText("留空则沿用当前默认授权")
        self.soft_code_edit.setText(normalized_auth.get("soft_code", ""))
        form_layout.addWidget(soft_code_label, 1, 0)
        form_layout.addWidget(self.soft_code_edit, 1, 1)

        feature_list_label = QLabel("功能列表:")
        self.feature_list_edit = QLineEdit(self)
        self.feature_list_edit.setClearButtonEnabled(True)
        self.feature_list_edit.setPlaceholderText("例如：OLA|OLAPlus")
        self.feature_list_edit.setText(normalized_auth.get("feature_list", ""))
        form_layout.addWidget(feature_list_label, 2, 0)
        form_layout.addWidget(self.feature_list_edit, 2, 1)

        official_url_label = QLabel("官方注册地址:")
        official_url_container = QWidget(self)
        official_url_layout = QHBoxLayout(official_url_container)
        official_url_layout.setContentsMargins(0, 0, 0, 0)
        official_url_layout.setSpacing(8)

        self.official_url_edit = QLineEdit(self)
        self.official_url_edit.setReadOnly(True)
        self.official_url_edit.setText(OLA_OFFICIAL_REGISTRATION_URL)
        self.official_url_edit.setCursorPosition(0)
        self.official_url_edit.setToolTip("OLA 插件官方注册地址")
        official_url_layout.addWidget(self.official_url_edit, 1)

        self.copy_official_url_button = QPushButton("复制", self)
        self.copy_official_url_button.setToolTip("复制官方注册地址")
        self.copy_official_url_button.clicked.connect(self._copy_official_url)
        official_url_layout.addWidget(self.copy_official_url_button)

        form_layout.addWidget(official_url_label, 3, 0)
        form_layout.addWidget(official_url_container, 3, 1)

        layout.addLayout(form_layout)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _copy_official_url(self):
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return

        clipboard.setText(OLA_OFFICIAL_REGISTRATION_URL)
        QToolTip.showText(
            self.copy_official_url_button.mapToGlobal(self.copy_official_url_button.rect().center()),
            "已复制到剪贴板",
            self.copy_official_url_button,
        )

    def get_auth_config(self):
        return normalize_ola_auth_settings({
            "user_code": self.user_code_edit.text(),
            "soft_code": self.soft_code_edit.text(),
            "feature_list": self.feature_list_edit.text(),
        })


class GlobalSettingsDialogExecutionTabMixin:
    def _create_execution_tab(self):

        """创建执行模式设置标签页"""

        self.exec_tab = QWidget()

        exec_layout = QVBoxLayout(self.exec_tab)

        exec_layout.setSpacing(8)

        exec_layout.setContentsMargins(10, 8, 10, 10)

        # --- Execution Mode Group ---

        self.exec_mode_group = QGroupBox("执行模式")

        exec_mode_layout = QVBoxLayout(self.exec_mode_group)

        exec_mode_layout.setSpacing(8)

        exec_mode_layout.setContentsMargins(15, 10, 15, 10)

        # 前后台模式选择（包装在widget中以便单独隐藏）

        self.mode_select_widget = QWidget()

        mode_select_layout = QHBoxLayout(self.mode_select_widget)

        mode_select_layout.setContentsMargins(0, 0, 0, 0)

        mode_label = QLabel("执行模式:")

        mode_label.setFixedWidth(80)

        self.mode_combo = QComboBox(self)

        self.mode_combo.clear()

        for internal_mode, display_mode in self.MODE_DISPLAY_MAP.items():

            self.mode_combo.addItem(display_mode, internal_mode)

        internal_mode = normalize_execution_mode_setting(

            self.current_config.get('execution_mode', 'background_sendmessage')

        )

        index = self.mode_combo.findData(internal_mode)

        if index >= 0:

            self.mode_combo.setCurrentIndex(index)

        else:

            display_mode = self.MODE_DISPLAY_MAP.get(internal_mode, "前台一模式")

            self.mode_combo.setCurrentText(display_mode)

        mode_select_layout.addWidget(mode_label)

        mode_select_layout.addWidget(self.mode_combo)

        exec_mode_layout.addWidget(self.mode_select_widget)

        self.foreground_driver_widget = QWidget()

        foreground_driver_layout = QHBoxLayout(self.foreground_driver_widget)

        foreground_driver_layout.setContentsMargins(0, 0, 0, 0)

        foreground_driver_label = QLabel("鼠标驱动:")

        foreground_driver_label.setFixedWidth(80)

        self.foreground_driver_combo = QComboBox(self)

        for display_name, backend in self.FOREGROUND_DRIVER_BACKEND_MAP.items():

            self.foreground_driver_combo.addItem(display_name, backend)

        legacy_backend = str(self.current_config.get('foreground_driver_backend', 'interception') or 'interception').strip().lower()

        configured_mouse_backend = str(

            self.current_config.get('foreground_mouse_driver_backend', legacy_backend) or legacy_backend

        ).strip().lower()

        backend_index = self.foreground_driver_combo.findData(configured_mouse_backend)

        if backend_index < 0:

            backend_index = self.foreground_driver_combo.findData('interception')

        if backend_index >= 0:

            self.foreground_driver_combo.setCurrentIndex(backend_index)

        foreground_driver_layout.addWidget(foreground_driver_label)

        foreground_driver_layout.addWidget(self.foreground_driver_combo)

        exec_mode_layout.addWidget(self.foreground_driver_widget)

        self.foreground_keyboard_driver_widget = QWidget()

        foreground_keyboard_driver_layout = QHBoxLayout(self.foreground_keyboard_driver_widget)

        foreground_keyboard_driver_layout.setContentsMargins(0, 0, 0, 0)

        foreground_keyboard_driver_label = QLabel("键盘驱动:")

        foreground_keyboard_driver_label.setFixedWidth(80)

        self.foreground_keyboard_driver_combo = QComboBox(self)

        for display_name, backend in self.FOREGROUND_DRIVER_BACKEND_MAP.items():

            self.foreground_keyboard_driver_combo.addItem(display_name, backend)

        configured_keyboard_backend = str(

            self.current_config.get('foreground_keyboard_driver_backend', legacy_backend) or legacy_backend

        ).strip().lower()

        keyboard_backend_index = self.foreground_keyboard_driver_combo.findData(configured_keyboard_backend)

        if keyboard_backend_index < 0:

            keyboard_backend_index = self.foreground_keyboard_driver_combo.findData('interception')

        if keyboard_backend_index >= 0:

            self.foreground_keyboard_driver_combo.setCurrentIndex(keyboard_backend_index)

        foreground_keyboard_driver_layout.addWidget(foreground_keyboard_driver_label)

        foreground_keyboard_driver_layout.addWidget(self.foreground_keyboard_driver_combo)

        exec_mode_layout.addWidget(self.foreground_keyboard_driver_widget)

        self.ib_driver_widget = QWidget()

        ib_driver_layout = QHBoxLayout(self.ib_driver_widget)

        ib_driver_layout.setContentsMargins(0, 0, 0, 0)

        ib_driver_label = QLabel("Ib驱动类型:")

        ib_driver_label.setFixedWidth(80)

        self.ib_driver_combo = QComboBox(self)

        for display_name, ib_driver in self.IB_DRIVER_MAP.items():

            self.ib_driver_combo.addItem(display_name, ib_driver)

        configured_ib_driver = str(self.current_config.get('ibinputsimulator_driver', 'Logitech') or 'Logitech').strip()

        ib_driver_index = self.ib_driver_combo.findData(configured_ib_driver)

        if ib_driver_index < 0:

            ib_driver_index = self.ib_driver_combo.findData('Logitech')

        if ib_driver_index >= 0:

            self.ib_driver_combo.setCurrentIndex(ib_driver_index)

        ib_driver_layout.addWidget(ib_driver_label)

        ib_driver_layout.addWidget(self.ib_driver_combo)

        exec_mode_layout.addWidget(self.ib_driver_widget)

        exec_layout.addWidget(self.exec_mode_group)

        # --- 截图方式选择（原生模式）---

        self.screenshot_engine_group = QGroupBox("截图方式")

        screenshot_engine_layout = QVBoxLayout(self.screenshot_engine_group)

        screenshot_engine_layout.setSpacing(8)

        screenshot_engine_layout.setContentsMargins(15, 10, 15, 10)

        # 截图引擎选择

        screenshot_engine_row = QHBoxLayout()

        screenshot_engine_label = QLabel("截图引擎:")

        screenshot_engine_label.setFixedWidth(80)

        self.screenshot_engine_combo = QComboBox(self)

        self.screenshot_engine_combo.setMinimumWidth(200)

        # 截图引擎选项映射

        self.screenshot_engine_map = {

            "WGC (适用Win11)": "wgc",

            "PrintWindow (适用Win10)": "printwindow",

            "GDI (仅前台)": "gdi",

            "DXGI (仅前台)": "dxgi"

        }

        self.screenshot_engine_reverse_map = {v: k for k, v in self.screenshot_engine_map.items()}

        # 添加选项

        for display_name in self.screenshot_engine_map.keys():

            self.screenshot_engine_combo.addItem(display_name)

        # 从配置读取当前截图引擎

        current_engine = self.current_config.get('screenshot_engine', 'wgc')

        display_engine = self.screenshot_engine_reverse_map.get(current_engine, "WGC (适用Win11)")

        self.screenshot_engine_combo.setCurrentText(display_engine)

        # 设置工具提示

        self.screenshot_engine_combo.setToolTip(

            "WGC: Windows Graphics Capture，Win10 1903+/Win11，GPU加速，支持后台\n"

            "PrintWindow: Win32 API，适用Win10，支持后台窗口\n"

            "GDI: 传统截图方式，仅支持前台（可见区域）\n"

            "DXGI: Desktop Duplication API，高性能，仅支持前台"

        )

        screenshot_engine_row.addWidget(screenshot_engine_label)

        screenshot_engine_row.addWidget(self.screenshot_engine_combo)

        screenshot_engine_layout.addLayout(screenshot_engine_row)

        exec_layout.addWidget(self.screenshot_engine_group)

        # 连接执行模式变化信号，用于控制截图引擎选项可见性

        self.mode_combo.currentTextChanged.connect(self._update_screenshot_engine_visibility)

        self.mode_combo.currentTextChanged.connect(self._update_foreground_driver_visibility)

        self.mode_combo.currentIndexChanged.connect(self._update_screenshot_engine_visibility)

        self.mode_combo.currentIndexChanged.connect(self._update_foreground_driver_visibility)

        self.foreground_driver_combo.currentTextChanged.connect(self._update_foreground_driver_visibility)

        self.foreground_driver_combo.currentIndexChanged.connect(self._update_foreground_driver_visibility)

        self.foreground_keyboard_driver_combo.currentTextChanged.connect(self._update_foreground_driver_visibility)

        self.foreground_keyboard_driver_combo.currentIndexChanged.connect(self._update_foreground_driver_visibility)

        # 初始化时更新截图引擎可见性

        self._update_screenshot_engine_visibility()

        self._update_foreground_driver_visibility()

        # --- 插件系统开关 ---

        plugin_system_group = QGroupBox("插件系统")

        plugin_system_layout = QVBoxLayout(plugin_system_group)

        plugin_system_layout.setSpacing(8)

        plugin_system_layout.setContentsMargins(15, 10, 15, 10)

        # 创建水平布局：复选框 + 提示文字

        plugin_checkbox_layout = QHBoxLayout()

        self.plugin_enabled_check = QCheckBox("启用插件系统（OLA）")

        plugin_settings = self.current_config.get('plugin_settings', {})

        # 在设置初始状态时阻塞信号,避免触发验证逻辑

        self.plugin_enabled_check.blockSignals(True)

        self.plugin_enabled_check.setChecked(plugin_settings.get('enabled', False))

        self.plugin_enabled_check.blockSignals(False)

        # 连接信号：当启用状态改变时，切换绑定参数显示

        self.plugin_enabled_check.stateChanged.connect(self._toggle_binding_params_visibility)

        plugin_checkbox_layout.addWidget(self.plugin_enabled_check)

        self.exec_plugin_auth_button = QPushButton("授权配置", self)
        self.exec_plugin_auth_button.setMinimumWidth(200)
        self.exec_plugin_auth_button.clicked.connect(self._open_plugin_ola_auth_dialog)

        plugin_checkbox_layout.addStretch()  # 添加弹性空间
        plugin_checkbox_layout.addWidget(self.exec_plugin_auth_button)

        plugin_system_layout.addLayout(plugin_checkbox_layout)

        exec_layout.addWidget(plugin_system_group)

        # --- OLA插件绑定参数（仅在启用插件时显示）---

        self.exec_plugin_binding_group = QGroupBox("OLA插件绑定参数")

        # 使用垂直布局，每个参数单独一行，避免挤在一起

        exec_plugin_binding_main_layout = QVBoxLayout(self.exec_plugin_binding_group)

        exec_plugin_binding_main_layout.setSpacing(12)

        exec_plugin_binding_main_layout.setContentsMargins(15, 10, 15, 10)

        self._plugin_ola_auth_config = self._get_effective_plugin_ola_auth()

        binding_header_layout = QHBoxLayout()
        binding_header_layout.setContentsMargins(0, 0, 0, 0)

        self.exec_plugin_binding_scope_label = QLabel("当前应用于: 全局默认")

        self.exec_plugin_binding_scope_label.setStyleSheet("font-size: 9pt;")

        binding_header_layout.addWidget(self.exec_plugin_binding_scope_label)

        exec_plugin_binding_main_layout.addLayout(binding_header_layout)

        # 获取插件设置

        plugin_settings = self.current_config.get('plugin_settings', {})

        ola_binding = plugin_settings.get('ola_binding', {})

        # 使用网格布局，两列显示

        exec_plugin_binding_grid = QGridLayout()

        exec_plugin_binding_grid.setSpacing(10)

        exec_plugin_binding_grid.setHorizontalSpacing(25)  # 水平间距

        exec_plugin_binding_grid.setVerticalSpacing(8)

        # 设置列的拉伸比例

        exec_plugin_binding_grid.setColumnStretch(1, 3)  # 下拉框列占更多空间

        exec_plugin_binding_grid.setColumnStretch(3, 3)

        # 创建自定义 delegate 来设置下拉列表项高度（解决选项拥挤问题）

        class OlaComboItemDelegate(CenteredTextDelegate):

            def sizeHint(self, option, index):

                size = super().sizeHint(option, index)

                size.setHeight(28)  # 设置每项高度为28像素

                return size

        # 第一行：显示模式 | 鼠标模式

        # 显示模式选择

        display_label = QLabel("显示模式:")

        display_label.setMinimumWidth(85)

        self.exec_plugin_display_mode_combo = QComboBox(self)

        self.exec_plugin_display_mode_combo.setMinimumWidth(200)

        self.exec_plugin_display_mode_combo.setItemDelegate(OlaComboItemDelegate(self.exec_plugin_display_mode_combo))

        # 中文显示 -> 英文值的映射

        self.display_mode_map = {

            "标准模式 (前台)": "normal",

            "GDI模式 (后台)": "gdi",

            "GDI2模式 (后台)": "gdi2",

            "GDI3模式 (后台)": "gdi3",

            "GDI4模式 (后台)": "gdi4",

            "GDI5模式 (后台)": "gdi5",

            "DXGI模式 (后台)": "dxgi",

            "VNC模式 (后台)": "vnc",

            "DX模式 (后台)": "dx"

        }

        # 英文值 -> 中文显示的反向映射

        self.display_mode_reverse_map = {v: k for k, v in self.display_mode_map.items()}

        self.exec_plugin_display_mode_combo.addItems(list(self.display_mode_map.keys()))

        # 根据配置设置当前选项

        current_display = ola_binding.get('display_mode', 'normal')

        display_text = self.display_mode_reverse_map.get(current_display, "标准模式 (前台)")

        self.exec_plugin_display_mode_combo.setCurrentText(display_text)

        exec_plugin_binding_grid.addWidget(display_label, 0, 0)

        exec_plugin_binding_grid.addWidget(self.exec_plugin_display_mode_combo, 0, 1)

        # 鼠标模式选择

        mouse_label = QLabel("鼠标模式:")

        mouse_label.setMinimumWidth(85)

        self.exec_plugin_mouse_mode_combo = QComboBox(self)

        self.exec_plugin_mouse_mode_combo.setMinimumWidth(200)

        self.exec_plugin_mouse_mode_combo.setItemDelegate(OlaComboItemDelegate(self.exec_plugin_mouse_mode_combo))

        # 中文显示 -> 英文值的映射

        self.mouse_mode_map = {

            "标准模式 (前台)": "normal",

            "Windows消息模式 (后台)": "windows",

            "Windows3消息模式 (后台)": "windows3",

            "VNC模式 (后台)": "vnc",

            "DX-位置锁定API (后台)": "dx.mouse.position.lock.api",

            "DX-位置锁定消息 (后台)": "dx.mouse.position.lock.message",

            "DX-焦点输入API (后台)": "dx.mouse.focus.input.api",

            "DX-焦点输入消息 (后台)": "dx.mouse.focus.input.message",

            "DX-刷新区域锁定 (后台)": "dx.mouse.clip.lock.api",

            "DX-输入锁定API (后台)": "dx.mouse.input.lock.api",

            "DX-状态API (后台)": "dx.mouse.state.api",

            "DX-状态消息 (后台)": "dx.mouse.state.message",

            "DX-API模拟 (后台)": "dx.mouse.api",

            "DX-光标特征 (后台)": "dx.mouse.cursor",

            "DX-原始输入 (后台)": "dx.mouse.raw.input",

            "DX-输入锁定API2 (后台)": "dx.mouse.input.lock.api2",

            "DX-输入锁定API3 (后台)": "dx.mouse.input.lock.api3",

            "DX-原始输入激活 (后台)": "dx.mouse.raw.input.active"

        }

        self.mouse_mode_reverse_map = {v: k for k, v in self.mouse_mode_map.items()}

        self.exec_plugin_mouse_mode_combo.addItems(list(self.mouse_mode_map.keys()))

        current_mouse = ola_binding.get('mouse_mode', 'normal')

        mouse_text = self.mouse_mode_reverse_map.get(current_mouse, "标准模式 (前台)")

        self.exec_plugin_mouse_mode_combo.setCurrentText(mouse_text)

        exec_plugin_binding_grid.addWidget(mouse_label, 0, 2)

        exec_plugin_binding_grid.addWidget(self.exec_plugin_mouse_mode_combo, 0, 3)

        # 第二行：键盘模式 | 绑定模式

        # 键盘模式选择

        keypad_label = QLabel("键盘模式:")

        keypad_label.setMinimumWidth(85)

        self.exec_plugin_keypad_mode_combo = QComboBox(self)

        self.exec_plugin_keypad_mode_combo.setMinimumWidth(200)

        self.exec_plugin_keypad_mode_combo.setItemDelegate(OlaComboItemDelegate(self.exec_plugin_keypad_mode_combo))

        # 中文显示 -> 英文值的映射

        self.keypad_mode_map = {

            "标准模式 (前台)": "normal",

            "Windows消息模式 (后台)": "windows",

            "VNC模式 (后台)": "vnc",

            "DX-输入锁定API (后台)": "dx.keypad.input.lock.api",

            "DX-状态API (后台)": "dx.keypad.state.api",

            "DX-API模拟 (后台)": "dx.keypad.api",

            "DX-原始输入 (后台)": "dx.keypad.raw.input",

            "DX-原始输入激活 (后台)": "dx.keypad.raw.input.active"

        }

        self.keypad_mode_reverse_map = {v: k for k, v in self.keypad_mode_map.items()}

        self.exec_plugin_keypad_mode_combo.addItems(list(self.keypad_mode_map.keys()))

        current_keypad = ola_binding.get('keypad_mode', 'normal')

        keypad_text = self.keypad_mode_reverse_map.get(current_keypad, "标准模式 (前台)")

        self.exec_plugin_keypad_mode_combo.setCurrentText(keypad_text)

        exec_plugin_binding_grid.addWidget(keypad_label, 1, 0)

        exec_plugin_binding_grid.addWidget(self.exec_plugin_keypad_mode_combo, 1, 1)

        # 绑定模式选择

        bind_mode_label = QLabel("绑定模式:")

        bind_mode_label.setMinimumWidth(85)

        self.exec_plugin_bind_mode_combo = QComboBox(self)

        self.exec_plugin_bind_mode_combo.setMinimumWidth(200)

        self.exec_plugin_bind_mode_combo.setItemDelegate(OlaComboItemDelegate(self.exec_plugin_bind_mode_combo))

        self.exec_plugin_bind_mode_combo.addItems([

            "0 - 推荐模式",

            "1 - 远程线程注入",

            "2 - 驱动注入模式1",

            "3 - 驱动注入模式2",

            "4 - 驱动注入模式3"

        ])

        current_mode = ola_binding.get('mode', 0)

        self.exec_plugin_bind_mode_combo.setCurrentIndex(current_mode)

        exec_plugin_binding_grid.addWidget(bind_mode_label, 1, 2)

        exec_plugin_binding_grid.addWidget(self.exec_plugin_bind_mode_combo, 1, 3)

        # 第三行：鼠标移动方式 | 输入锁定

        # 鼠标移动方式选择

        mouse_move_label = QLabel("鼠标移动方式:")

        mouse_move_label.setMinimumWidth(85)

        self.exec_plugin_mouse_move_combo = QComboBox(self)

        self.exec_plugin_mouse_move_combo.setMinimumWidth(200)

        self.exec_plugin_mouse_move_combo.setItemDelegate(OlaComboItemDelegate(self.exec_plugin_mouse_move_combo))

        self.exec_plugin_mouse_move_combo.addItems(["直接移动(快速)", "轨迹移动(慢速仿真)"])

        # 根据配置设置当前选项

        use_trajectory = ola_binding.get('mouse_move_with_trajectory', False)

        self.exec_plugin_mouse_move_combo.setCurrentIndex(1 if use_trajectory else 0)

        self.exec_plugin_mouse_move_combo.setToolTip(

            "直接移动：瞬间移动到目标位置，速度快(<0.1秒)\n"

            "轨迹移动：模拟真实鼠标移动轨迹，速度慢(可能3-4秒)\n\n"

            "说明：轨迹移动更自然但耗时长，直接移动更快但可能被检测"

        )

        exec_plugin_binding_grid.addWidget(mouse_move_label, 2, 0)

        exec_plugin_binding_grid.addWidget(self.exec_plugin_mouse_move_combo, 2, 1)

        # 公共属性下拉框（第2行右侧，绑定模式下面）

        pubstr_label = QLabel("公共属性:")

        pubstr_label.setMinimumWidth(80)

        self.exec_plugin_pubstr_combo = QComboBox(self)

        self.exec_plugin_pubstr_combo.setMinimumWidth(200)

        self.exec_plugin_pubstr_combo.setItemDelegate(OlaComboItemDelegate(self.exec_plugin_pubstr_combo))

        # pubstr值到索引的映射

        self.pubstr_value_map = {

            "": 0,

            "ola.bypass.guard": 1,

            "dx.public.active.api": 2,

            "dx.public.active.api2": 3,

            "dx.public.focus.message": 4,

            "dx.public.graphic.revert": 5,

        }

        self.pubstr_index_map = {

            0: "",

            1: "ola.bypass.guard",

            2: "dx.public.active.api",

            3: "dx.public.active.api2",

            4: "dx.public.focus.message",

            5: "dx.public.graphic.revert",

        }

        self.exec_plugin_pubstr_combo.addItems([

            "无",

            "ola.bypass.guard (绕过防护)",

            "dx.public.active.api",

            "dx.public.active.api2",

            "dx.public.focus.message",

            "dx.public.graphic.revert",

        ])

        current_pubstr = ola_binding.get('pubstr', '')

        pubstr_index = self.pubstr_value_map.get(current_pubstr, 0)

        self.exec_plugin_pubstr_combo.setCurrentIndex(pubstr_index)

        self.exec_plugin_pubstr_combo.setToolTip("绑定模式公共属性参数，绑定失败时可尝试启用")

        exec_plugin_binding_grid.addWidget(pubstr_label, 2, 2)

        exec_plugin_binding_grid.addWidget(self.exec_plugin_pubstr_combo, 2, 3)

        # 第三行：输入锁定选项

        input_lock_label = QLabel("输入锁定:")

        input_lock_label.setMinimumWidth(85)

        self.exec_plugin_input_lock_checkbox = QCheckBox("后台绑定时锁定前台鼠标键盘")

        current_input_lock = ola_binding.get('input_lock', False)

        self.exec_plugin_input_lock_checkbox.setChecked(current_input_lock)

        self.exec_plugin_input_lock_checkbox.setToolTip(

            "开启：后台绑定时锁定前台鼠标键盘，防止误操作\n"

            "关闭：后台绑定时前台仍可正常操作"

        )

        exec_plugin_binding_grid.addWidget(input_lock_label, 3, 0)

        exec_plugin_binding_grid.addWidget(self.exec_plugin_input_lock_checkbox, 3, 1)

        exec_plugin_binding_main_layout.addLayout(exec_plugin_binding_grid)

        exec_layout.addWidget(self.exec_plugin_binding_group)

        exec_layout.addStretch()

        # 多窗口启动延迟固定为500ms（不显示设置）

        self.multi_window_delay = 500

        plugin_binding_widgets = [
            self.exec_plugin_display_mode_combo,
            self.exec_plugin_mouse_mode_combo,
            self.exec_plugin_keypad_mode_combo,
            self.exec_plugin_bind_mode_combo,
            self.exec_plugin_mouse_move_combo,
            self.exec_plugin_pubstr_combo,
        ]

        for widget in plugin_binding_widgets:

            widget.currentIndexChanged.connect(self._sync_plugin_bound_window_binding_from_controls)

        self.exec_plugin_input_lock_checkbox.stateChanged.connect(self._sync_plugin_bound_window_binding_from_controls)

        self._apply_plugin_ola_auth_to_controls(self._plugin_ola_auth_config)

        self.tab_widget.addTab(self.exec_tab, "执行模式")

    def _get_effective_plugin_ola_auth(self):

        plugin_settings = self.current_config.get('plugin_settings', {})

        if isinstance(plugin_settings, dict) and 'ola_auth' in plugin_settings:

            return normalize_ola_auth_settings(plugin_settings.get('ola_auth'))

        try:

            from plugins.core.manager import PluginManager

            manager = PluginManager()

            manager.load_config()

            return normalize_ola_auth_settings(manager.get_plugin_config('ola'))

        except Exception:

            return normalize_ola_auth_settings({})

    def _collect_current_plugin_ola_auth(self):

        return normalize_ola_auth_settings(getattr(self, '_plugin_ola_auth_config', {}))

    def _apply_plugin_ola_auth_to_controls(self, auth_config):

        self._plugin_ola_auth_config = normalize_ola_auth_settings(auth_config)
        self._refresh_plugin_ola_auth_button()
        self._sync_plugin_ola_auth_from_controls()

    def _refresh_plugin_ola_auth_button(self):

        if not hasattr(self, 'exec_plugin_auth_button'):

            return

        auth = self._collect_current_plugin_ola_auth()
        has_custom_auth = any(bool(str(auth.get(key, '') or '').strip()) for key in ('user_code', 'soft_code', 'feature_list'))

        self.exec_plugin_auth_button.setText("授权配置（已自定义）" if has_custom_auth else "授权配置")
        self.exec_plugin_auth_button.setToolTip("已配置自定义授权参数" if has_custom_auth else "当前使用默认授权参数")

    def _sync_plugin_ola_auth_from_controls(self, *_args):

        plugin_settings = self.current_config.setdefault('plugin_settings', {})

        plugin_settings['ola_auth'] = self._collect_current_plugin_ola_auth()

    def _open_plugin_ola_auth_dialog(self):

        dialog = OLAAuthConfigDialog(self._collect_current_plugin_ola_auth(), parent=self)

        if dialog.exec() != QDialog.DialogCode.Accepted:

            return

        self._apply_plugin_ola_auth_to_controls(dialog.get_auth_config())

    def _collect_current_plugin_ola_binding(self):

        display_mode = self.display_mode_map.get(self.exec_plugin_display_mode_combo.currentText(), 'normal')

        mouse_mode = self.mouse_mode_map.get(self.exec_plugin_mouse_mode_combo.currentText(), 'normal')

        keypad_mode = self.keypad_mode_map.get(self.exec_plugin_keypad_mode_combo.currentText(), 'normal')

        pubstr = ''

        if hasattr(self, 'pubstr_index_map'):

            pubstr = self.pubstr_index_map.get(self.exec_plugin_pubstr_combo.currentIndex(), '')

        return normalize_plugin_ola_binding({
            'display_mode': display_mode,
            'mouse_mode': mouse_mode,
            'keypad_mode': keypad_mode,
            'mode': self.exec_plugin_bind_mode_combo.currentIndex(),
            'mouse_move_with_trajectory': self.exec_plugin_mouse_move_combo.currentIndex() == 1,
            'input_lock': self.exec_plugin_input_lock_checkbox.isChecked(),
            'sim_mode_type': 0,
            'pubstr': pubstr,
        })

    def _apply_plugin_ola_binding_to_controls(self, ola_binding):

        binding = normalize_plugin_ola_binding(
            ola_binding,
            fallback=getattr(self, 'plugin_default_ola_binding', {}) or {},
        )

        default_display_text = next(iter(self.display_mode_map.keys()), '')

        default_mouse_text = next(iter(self.mouse_mode_map.keys()), '')

        default_keypad_text = next(iter(self.keypad_mode_map.keys()), '')

        self._plugin_binding_controls_loading = True

        try:

            self.exec_plugin_display_mode_combo.setCurrentText(
                self.display_mode_reverse_map.get(binding.get('display_mode', 'normal'), default_display_text)
            )

            self.exec_plugin_mouse_mode_combo.setCurrentText(
                self.mouse_mode_reverse_map.get(binding.get('mouse_mode', 'normal'), default_mouse_text)
            )

            self.exec_plugin_keypad_mode_combo.setCurrentText(
                self.keypad_mode_reverse_map.get(binding.get('keypad_mode', 'normal'), default_keypad_text)
            )

            try:

                bind_mode = max(0, int(binding.get('mode', 0)))

            except Exception:

                bind_mode = 0

            bind_mode = min(bind_mode, self.exec_plugin_bind_mode_combo.count() - 1)

            self.exec_plugin_bind_mode_combo.setCurrentIndex(bind_mode)

            self.exec_plugin_mouse_move_combo.setCurrentIndex(1 if binding.get('mouse_move_with_trajectory', False) else 0)

            pubstr_index = self.pubstr_value_map.get(str(binding.get('pubstr', '') or '').strip(), 0)

            self.exec_plugin_pubstr_combo.setCurrentIndex(pubstr_index)

            self.exec_plugin_input_lock_checkbox.setChecked(bool(binding.get('input_lock', False)))

        finally:

            self._plugin_binding_controls_loading = False

    def _get_selected_plugin_bound_window(self):

        if not hasattr(self, 'plugin_bound_windows_combo'):

            return None

        current_index = self.plugin_bound_windows_combo.currentIndex()

        if current_index < 0 or current_index >= len(getattr(self, 'plugin_bound_windows', [])):

            return None

        return self.plugin_bound_windows[current_index]

    def _refresh_plugin_binding_editor_context(self):

        target_window = self._get_selected_plugin_bound_window()

        if target_window:

            title = str(target_window.get('title', '') or '').strip() or '未命名窗口'

            self.exec_plugin_binding_scope_label.setText(f"当前应用于: 插件窗口 {title}")

        else:

            self.exec_plugin_binding_scope_label.setText("当前应用于: 全局默认")

    def _sync_plugin_bound_window_binding_from_controls(self, *_args):

        if getattr(self, '_plugin_binding_controls_loading', False):

            return

        binding = self._collect_current_plugin_ola_binding()

        target_window = self._get_selected_plugin_bound_window()

        if target_window is not None:

            target_window['ola_binding'] = binding.copy()

        else:

            self.plugin_default_ola_binding = binding.copy()

            plugin_settings = self.current_config.setdefault('plugin_settings', {})

            plugin_settings['ola_binding'] = binding.copy()

        self._refresh_plugin_binding_editor_context()

    def _load_selected_plugin_bound_window_binding(self):

        target_window = self._get_selected_plugin_bound_window()

        if target_window is not None:

            self._apply_plugin_ola_binding_to_controls(target_window.get('ola_binding'))

        else:

            self._apply_plugin_ola_binding_to_controls(self.plugin_default_ola_binding)

        self._refresh_plugin_binding_editor_context()

    def _update_screenshot_engine_visibility(self):

        """

        更新截图引擎选择框的可见性

        - 前台一/二模式：显示所有截图引擎选项（全部）

        - 后台模式：仅显示支持后台的引擎（WGC / PrintWindow）

        """

        internal_mode = self.mode_combo.currentData()

        if not internal_mode:

            current_mode = self.mode_combo.currentText()

            internal_mode = self.MODE_INTERNAL_MAP.get(current_mode, "")

        is_foreground = internal_mode.startswith("foreground")

        # 获取当前选择的引擎

        current_engine = self.screenshot_engine_combo.currentText()

        # 清空选项并重新添加

        self.screenshot_engine_combo.clear()

        if is_foreground:

            # 前台一/二模式：显示所有选项

            for display_name in self.screenshot_engine_map.keys():

                self.screenshot_engine_combo.addItem(display_name)

        else:

            # 后台模式：只显示支持后台的引擎

            for display_name, engine in self.screenshot_engine_map.items():

                if engine in ("wgc", "printwindow"):

                    self.screenshot_engine_combo.addItem(display_name)

        # 恢复之前的选择（如果仍然可用）

        index = self.screenshot_engine_combo.findText(current_engine)

        if index >= 0:

            self.screenshot_engine_combo.setCurrentIndex(index)

        else:

            # 后台模式下若之前选择不可用（如 GDI/DXGI），强制切换到 WGC

            if not is_foreground:

                wgc_index = self.screenshot_engine_combo.findText("WGC (适用Win11)")

                if wgc_index >= 0:

                    self.screenshot_engine_combo.setCurrentIndex(wgc_index)

        # Limit popup height to item count to avoid empty space

        item_count = self.screenshot_engine_combo.count()

        if item_count > 0:

            self.screenshot_engine_combo.setMaxVisibleItems(item_count)

