from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from utils.ntfy_push import (
    get_ntfy_priority_options,
    normalize_ntfy_priority,
    normalize_ntfy_settings,
)

from ..main_window_parts.main_window_support import get_secondary_text_color
from ..main_window_parts.main_window_dropdown_widget import QComboBox


class GlobalSettingsDialogPushTabMixin:
    def _create_push_tab(self):
        push_tab = QWidget()

        push_layout = QVBoxLayout(push_tab)
        push_layout.setSpacing(8)
        push_layout.setContentsMargins(10, 8, 10, 10)

        ntfy_settings = normalize_ntfy_settings(self.current_config.get("ntfy_settings"))

        ntfy_group = QGroupBox("ntfy 推送")
        ntfy_layout = QVBoxLayout(ntfy_group)
        ntfy_layout.setSpacing(8)
        ntfy_layout.setContentsMargins(15, 10, 15, 10)

        self.ntfy_enabled_checkbox = QCheckBox("启用 ntfy 执行推送")
        self.ntfy_enabled_checkbox.setChecked(ntfy_settings.get("enabled", False))
        ntfy_layout.addWidget(self.ntfy_enabled_checkbox)

        ntfy_form = QGridLayout()
        ntfy_form.setContentsMargins(0, 0, 0, 0)
        ntfy_form.setHorizontalSpacing(12)
        ntfy_form.setVerticalSpacing(8)
        ntfy_form.setColumnStretch(0, 1)
        ntfy_form.setColumnStretch(1, 1)

        setting_label_width = self._get_ntfy_label_width("服务地址", "Topic", "Token")

        self.ntfy_server_url_edit = QLineEdit(self)
        self.ntfy_server_url_edit.setText(str(ntfy_settings.get("server_url") or "https://ntfy.sh"))
        self.ntfy_server_url_edit.setPlaceholderText("https://ntfy.sh")
        self.ntfy_server_url_edit.setToolTip("公共服务默认 https://ntfy.sh，也可填写自建服务地址")
        self._add_ntfy_setting_field(ntfy_form, 0, 0, "服务地址", self.ntfy_server_url_edit, setting_label_width)

        self.ntfy_topic_edit = QLineEdit(self)
        self.ntfy_topic_edit.setText(str(ntfy_settings.get("topic") or ""))
        self.ntfy_topic_edit.setPlaceholderText("输入订阅 topic")
        self.ntfy_topic_edit.setToolTip("手机端订阅同一个 topic 后即可收到消息")
        self._add_ntfy_setting_field(ntfy_form, 0, 1, "Topic", self.ntfy_topic_edit, setting_label_width)

        self.ntfy_token_edit = QLineEdit(self)
        self.ntfy_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ntfy_token_edit.setText(str(ntfy_settings.get("token") or ""))
        self.ntfy_token_edit.setPlaceholderText("可选，Bearer Token")
        self.ntfy_token_edit.setToolTip("公共 topic 通常可留空；若服务端启用鉴权可填写 Token")
        self._add_ntfy_setting_field(ntfy_form, 1, 0, "Token", self.ntfy_token_edit, setting_label_width)

        priority_group = QGroupBox("推送等级")
        priority_layout = QGridLayout(priority_group)
        priority_layout.setContentsMargins(12, 8, 12, 8)
        priority_layout.setHorizontalSpacing(12)
        priority_layout.setVerticalSpacing(8)
        priority_layout.setColumnStretch(0, 1)
        priority_layout.setColumnStretch(1, 1)

        priority_settings = ntfy_settings.get("priorities", {})
        priority_label_width = self._get_ntfy_label_width("开始推送", "成功结束", "失败结束", "最低等级")

        self.ntfy_start_priority_combo = self._create_ntfy_priority_combo(priority_settings.get("start"))
        self._add_ntfy_priority_field(priority_layout, 0, 0, "开始推送", self.ntfy_start_priority_combo, priority_label_width)

        self.ntfy_success_priority_combo = self._create_ntfy_priority_combo(priority_settings.get("success"))
        self._add_ntfy_priority_field(priority_layout, 0, 1, "成功结束", self.ntfy_success_priority_combo, priority_label_width)

        self.ntfy_failure_priority_combo = self._create_ntfy_priority_combo(priority_settings.get("failure"))
        self._add_ntfy_priority_field(priority_layout, 1, 0, "失败结束", self.ntfy_failure_priority_combo, priority_label_width)

        self.ntfy_minimum_priority_combo = self._create_ntfy_priority_combo(ntfy_settings.get("minimum_priority"))
        self._add_ntfy_priority_field(priority_layout, 1, 1, "最低等级", self.ntfy_minimum_priority_combo, priority_label_width)

        ntfy_layout.addLayout(ntfy_form)
        ntfy_layout.addWidget(priority_group)

        ntfy_limit_hint = QLabel("公共服务限制：正文 4096 字节，每日 250 条，突发 60 条。")
        ntfy_limit_hint.setWordWrap(True)
        ntfy_limit_hint.setStyleSheet(f"color: {get_secondary_text_color()};")
        ntfy_layout.addWidget(ntfy_limit_hint)

        ntfy_priority_hint = QLabel(
            "全局仅区分开始、成功结束、失败结束。步骤详情走卡片自定义推送，所有消息都会受“最低等级”限制。"
        )
        ntfy_priority_hint.setWordWrap(True)
        ntfy_priority_hint.setStyleSheet(f"color: {get_secondary_text_color()};")
        ntfy_layout.addWidget(ntfy_priority_hint)

        push_layout.addWidget(ntfy_group)
        push_layout.addStretch()

        self.tab_widget.addTab(push_tab, "推送设置")

    def _create_ntfy_priority_combo(self, current_value):
        combo = QComboBox(self)
        for option in get_ntfy_priority_options():
            combo.addItem(f"{option['label']} ({option['header']})", option["key"])

        normalized_value = normalize_ntfy_priority(current_value)
        index = combo.findData(normalized_value)
        if index >= 0:
            combo.setCurrentIndex(index)
        return combo

    def _get_ntfy_label_width(self, *label_texts):
        metrics = self.fontMetrics()
        return max(metrics.horizontalAdvance(f"{text}:") for text in label_texts) + 4

    def _add_ntfy_setting_field(self, layout, row, column, label_text, widget, label_width):
        field_widget = QWidget(self)
        field_layout = QHBoxLayout(field_widget)
        field_layout.setContentsMargins(0, 0, 0, 0)
        field_layout.setSpacing(6)

        label = QLabel(f"{label_text}:", field_widget)
        label.setFixedWidth(label_width)

        field_layout.addWidget(label)
        field_layout.addWidget(widget, 1)

        layout.addWidget(field_widget, row, column)

    def _add_ntfy_priority_field(self, layout, row, column, label_text, combo, label_width):
        field_widget = QWidget(self)
        field_layout = QHBoxLayout(field_widget)
        field_layout.setContentsMargins(0, 0, 0, 0)
        field_layout.setSpacing(6)

        label = QLabel(f"{label_text}:", field_widget)
        label.setFixedWidth(label_width)

        field_layout.addWidget(label)
        field_layout.addWidget(combo, 1)

        layout.addWidget(field_widget, row, column)

    def _build_ntfy_settings_from_form(self):
        current_settings = normalize_ntfy_settings(self.current_config.get("ntfy_settings"))

        if hasattr(self, "ntfy_server_url_edit"):
            server_url = str(self.ntfy_server_url_edit.text() or "").strip()
            current_settings["server_url"] = server_url or "https://ntfy.sh"

        if hasattr(self, "ntfy_topic_edit"):
            current_settings["topic"] = str(self.ntfy_topic_edit.text() or "").strip()

        if hasattr(self, "ntfy_token_edit"):
            current_settings["token"] = str(self.ntfy_token_edit.text() or "").strip()

        if hasattr(self, "ntfy_enabled_checkbox"):
            current_settings["enabled"] = self.ntfy_enabled_checkbox.isChecked()

        current_settings["priorities"] = {
            "start": self.ntfy_start_priority_combo.currentData() if hasattr(self, "ntfy_start_priority_combo") else "default",
            "success": self.ntfy_success_priority_combo.currentData() if hasattr(self, "ntfy_success_priority_combo") else "default",
            "failure": self.ntfy_failure_priority_combo.currentData() if hasattr(self, "ntfy_failure_priority_combo") else "high",
        }
        current_settings["minimum_priority"] = (
            self.ntfy_minimum_priority_combo.currentData()
            if hasattr(self, "ntfy_minimum_priority_combo")
            else "min"
        )
        current_settings["enforce_public_limits"] = True

        return normalize_ntfy_settings(current_settings)
