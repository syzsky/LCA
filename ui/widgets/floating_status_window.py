"""
浮动状态窗口模块
主窗口最小化时显示工作流执行状态
"""
import time
import logging
import re
from utils.app_paths import get_config_path
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QPlainTextEdit, QTextEdit, QFrame, QGraphicsDropShadowEffect, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, Slot, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QSize
from PySide6.QtGui import QFont, QColor, QPainter, QBrush, QIcon, QPixmap, QPen, QPalette
from utils.window_activation_utils import show_and_activate_overlay, show_and_raise_widget
from utils.window_coordinate_common import get_available_geometry_for_widget

logger = logging.getLogger(__name__)

_STATUS_TEXT_MAP = {
    "failed": "失败",
    "success": "成功",
    "succeeded": "成功",
    "ok": "成功",
    "pass": "成功",
    "passed": "成功",
    "stopped": "已停止",
    "stop": "停止",
    "completed": "完成",
    "complete": "完成",
    "done": "完成",
    "no_next": "无后续",
    "paused": "已暂停",
    "running": "执行中",
    "error": "错误",
}

_STATUS_WORD_PATTERN = re.compile(r"\b(" + "|".join(_STATUS_TEXT_MAP.keys()) + r")\b", re.IGNORECASE)

def _translate_status_message(message: str) -> str:
    if not message:
        return message
    text = message.strip()
    if not text:
        return text
    text = re.sub(r"卡片ID\s*=\s*", "卡片ID:", text)
    lower = text.lower()
    if lower in _STATUS_TEXT_MAP:
        return _STATUS_TEXT_MAP[lower]

    def _replace(match: re.Match) -> str:
        return _STATUS_TEXT_MAP.get(match.group(0).lower(), match.group(0))

    return _STATUS_WORD_PATTERN.sub(_replace, text)


def _resolve_icon_color(enabled: bool = True) -> str:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app:
        palette = app.palette()
        group = QPalette.ColorGroup.Active if enabled else QPalette.ColorGroup.Disabled
        return palette.color(group, QPalette.ColorRole.ButtonText).name()
    return "#e0e0e0" if enabled else "#aaaaaa"


def create_icon(icon_type: str, size: int = 16, color: str = None) -> QIcon:
    """创建简单的图标"""
    if color is None:
        color = _resolve_icon_color(True)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidth(2)
    painter.setPen(pen)

    margin = 3
    if icon_type == "play":
        # 三角形播放图标
        points = [
            (margin + 1, margin),
            (size - margin, size // 2),
            (margin + 1, size - margin)
        ]
        painter.setBrush(QBrush(QColor(color)))
        from PySide6.QtGui import QPolygon
        from PySide6.QtCore import QPoint as QP
        polygon = QPolygon([QP(p[0], p[1]) for p in points])
        painter.drawPolygon(polygon)
    elif icon_type == "pause":
        # 双竖线暂停图标
        w = 3
        painter.fillRect(margin, margin, w, size - margin * 2, QColor(color))
        painter.fillRect(size - margin - w, margin, w, size - margin * 2, QColor(color))
    elif icon_type == "stop":
        # 方形停止图标
        painter.fillRect(margin, margin, size - margin * 2, size - margin * 2, QColor(color))
    elif icon_type == "expand":
        # 向下箭头
        painter.drawLine(margin, margin + 2, size // 2, size - margin - 2)
        painter.drawLine(size // 2, size - margin - 2, size - margin, margin + 2)
    elif icon_type == "collapse":
        # 向上箭头
        painter.drawLine(margin, size - margin - 2, size // 2, margin + 2)
        painter.drawLine(size // 2, margin + 2, size - margin, size - margin - 2)
    elif icon_type == "hide":
        # 叉号关闭图标
        painter.drawLine(margin, margin, size - margin, size - margin)
        painter.drawLine(size - margin, margin, margin, size - margin)
    elif icon_type == "eye":
        # 眼睛：一个扁椭圆 + 小圆点
        cx, cy = size // 2, size // 2
        painter.drawEllipse(cx - 4, cy - 2, 8, 4)  # 椭圆眼眶
        painter.drawEllipse(cx - 1, cy - 1, 2, 2)  # 瞳孔
    elif icon_type == "eye-off":
        cx, cy = size // 2, size // 2
        painter.drawEllipse(cx - 4, cy - 2, 8, 4)
        painter.drawEllipse(cx - 1, cy - 1, 2, 2)
        painter.drawLine(margin, margin, size - margin, size - margin)  # 斜线

    painter.end()
    return QIcon(pixmap)


class StatusIndicator(QWidget):
    """状态指示器"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(8, 8)
        self._color = QColor("#808080")
        self._blinking = False
        self._blink_state = True

        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._toggle_blink)

    def set_running(self):
        self._color = QColor("#4CAF50")
        self._blinking = True
        self._blink_timer.start(500)
        self.update()

    def set_paused(self):
        self._blinking = False
        self._blink_timer.stop()
        self._color = QColor("#FFC107")
        self._blink_state = True
        self.update()

    def set_stopped(self):
        self._blinking = False
        self._blink_timer.stop()
        self._color = QColor("#808080")
        self._blink_state = True
        self.update()

    def _toggle_blink(self):
        self._blink_state = not self._blink_state
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._blink_state:
            painter.setBrush(QBrush(self._color))
        else:
            painter.setBrush(QBrush(self._color.darker(150)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, 8, 8)


class LogView(QTextEdit):
    """日志显示（支持彩色文本）"""

    # 参数颜色映射
    _PARAM_COLORS = {
        # 状态标签颜色
        "成功": "#4CAF50",  # 绿色
        "失败": "#F44336",  # 红色
        "警告": "#FFC107",  # 黄色
        "信息": "#2196F3",  # 蓝色
        # 重要参数颜色
        "主卡片ID": "#FF9800",  # 橙色
        "子卡片ID": "#FFB74D",  # 浅橙色
        "卡片ID": "#FF9800",  # 橙色
        "目标文字": "#E91E63",  # 粉色
        "窗口": "#9C27B0",  # 紫色
        "置信度": "#00BCD4",  # 青色
        "操作": "#8BC34A",  # 浅绿色
        "图片": "#795548",  # 棕色
        "识别区域": "#607D8B",  # 蓝灰色
        "区域尺寸": "#607D8B",  # 蓝灰色
        "匹配": "#3F51B5",  # 靛蓝色
        "识别": "#009688",  # 蓝绿色
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setUndoRedoEnabled(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._max_blocks = 100
        # 使用文档级环形块上限，避免日志文本在内存中持续累积
        self.document().setMaximumBlockCount(self._max_blocks)

    def appendColoredLog(self, log_text: str):
        """添加带颜色的日志"""
        colored_html = self._colorize_log(log_text)
        self.append(colored_html)

    def _colorize_log(self, text: str) -> str:
        """将日志文本转换为带颜色的HTML"""
        import html
        # 先转义HTML特殊字符
        escaped = html.escape(text)

        # 处理状态标签 [成功] [失败] 等
        for status, color in [("成功", "#4CAF50"), ("失败", "#F44336"),
                               ("警告", "#FFC107"), ("信息", "#2196F3")]:
            escaped = escaped.replace(
                f"[{status}]",
                f'<span style="color:{color};font-weight:bold;">[{status}]</span>'
            )

        # 处理参数名=值 格式
        result = []
        parts = escaped.split(" | ")
        for i, part in enumerate(parts):
            if "=" in part and i > 0:  # 跳过时间戳部分
                # 分离参数名和值
                eq_pos = part.find("=")
                param_name = part[:eq_pos]
                param_value = part[eq_pos+1:]

                # 查找匹配的颜色
                color = None
                for key, c in self._PARAM_COLORS.items():
                    if key in param_name:
                        color = c
                        break

                if color:
                    part = f'<span style="color:{color};">{param_name}</span>=<span style="color:{color};">{param_value}</span>'
                else:
                    part = f'{param_name}={param_value}'
            result.append(part)

        return " | ".join(result)

    def clear(self):
        """清空日志"""
        super().clear()


class FloatingStatusWindow(QWidget):
    """浮动状态窗口"""

    request_start = Signal()
    request_pause = Signal()
    request_stop = Signal()
    request_restore_main = Signal()
    request_temporary_hide = Signal()

    COLLAPSED_HEIGHT = 52
    EXPANDED_HEIGHT = 480
    WINDOW_WIDTH = 380

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        # Keep persistent floating window alive; avoid auto-delete on close.
        self._disable_auto_delete = True

        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._is_expanded = False
        self._is_dragging = False
        self._drag_start_pos = QPoint()
        self._is_running = False
        self._is_paused = False

        self._live_preview_enabled = False
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._capture_and_display)
        self._target_hwnd = None
        self._preview_freq = 1000
        self._bound_windows = []  # 所有绑定窗口列表
        self._active_window_index = 0  # 当前预览的窗口索引

        self._start_hotkey = "Num7"
        self._pause_hotkey = "F11"

        self._init_ui()
        self._load_hotkey_config()

        self.setFixedWidth(self.WINDOW_WIDTH)
        self.setMinimumHeight(self.COLLAPSED_HEIGHT)
        self.setMaximumHeight(self.COLLAPSED_HEIGHT)

        self._height_anim = QPropertyAnimation(self, b"minimumHeight")
        self._height_anim.setDuration(150)
        self._height_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._height_anim.finished.connect(self._on_animation_finished)

        self._height_anim2 = QPropertyAnimation(self, b"maximumHeight")
        self._height_anim2.setDuration(150)
        self._height_anim2.setEasingCurve(QEasingCurve.Type.OutCubic)

        # 注册主题切换回调
        self._register_theme_callback()

    def _register_theme_callback(self):
        """注册主题切换回调"""
        try:
            from themes import get_theme_manager
            theme_manager = get_theme_manager()
            theme_manager.register_theme_change_callback(self._on_theme_changed)
        except Exception as e:
            logger.debug(f"注册主题回调失败: {e}")

    def _on_theme_changed(self, theme_name: str = None):
        """主题切换时更新样式"""
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                self.setStyleSheet(app.styleSheet())
                self._update_button_states()
                self._btn_expand.setIcon(
                    create_icon(
                        "collapse" if self._is_expanded else "expand",
                        14,
                        color=_resolve_icon_color(True),
                    )
                )
                self._btn_hide.setIcon(create_icon("hide", 14, color=_resolve_icon_color(True)))
                logger.debug(f"浮动窗口主题已更新: {theme_name}")
        except Exception as e:
            logger.debug(f"更新浮动窗口主题失败: {e}")

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(0)

        # 容器
        self._container = QFrame()
        self._container.setObjectName("floatingContainer")
        self._container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(12, 8, 12, 8)
        container_layout.setSpacing(0)

        # 阴影
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(12)
        shadow.setXOffset(0)
        shadow.setYOffset(2)
        shadow.setColor(QColor(0, 0, 0, 50))
        self._container.setGraphicsEffect(shadow)

        # 头部
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)

        # 状态指示器
        self._status_indicator = StatusIndicator()
        header_layout.addWidget(self._status_indicator)

        # 状态文字
        self._status_label = QLabel("就绪")
        self._status_label.setObjectName("floatingStatusLabel")
        header_layout.addWidget(self._status_label, 1)

        # 控制按钮
        btn_widget = QWidget()
        btn_layout = QHBoxLayout(btn_widget)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(4)

        self._btn_start = QPushButton()
        self._btn_start.setObjectName("floatingCtrlBtn")
        self._btn_start.setFixedSize(28, 28)
        self._btn_start.setToolTip("开始")
        self._btn_start.setIcon(create_icon("play"))
        self._btn_start.setIconSize(QSize(16, 16))
        self._btn_start.clicked.connect(self._on_start_clicked)
        btn_layout.addWidget(self._btn_start)

        self._btn_pause = QPushButton()
        self._btn_pause.setObjectName("floatingCtrlBtn")
        self._btn_pause.setFixedSize(28, 28)
        self._btn_pause.setToolTip("暂停")
        self._btn_pause.setIcon(create_icon("pause"))
        self._btn_pause.setIconSize(QSize(16, 16))
        self._btn_pause.clicked.connect(self._on_pause_clicked)
        btn_layout.addWidget(self._btn_pause)

        self._btn_stop = QPushButton()
        self._btn_stop.setObjectName("floatingCtrlBtn")
        self._btn_stop.setFixedSize(28, 28)
        self._btn_stop.setToolTip("停止")
        self._btn_stop.setIcon(create_icon("stop"))
        self._btn_stop.setIconSize(QSize(16, 16))
        self._btn_stop.clicked.connect(self._on_stop_clicked)
        btn_layout.addWidget(self._btn_stop)

        header_layout.addWidget(btn_widget)

        # 实时预览切换按钮
        self._btn_preview = QPushButton()
        self._btn_preview.setObjectName("floatingCtrlBtn")
        self._btn_preview.setFixedSize(28, 28)
        self._btn_preview.setToolTip("开启实时画面")
        self._btn_preview.setIcon(create_icon("eye", 16))
        self._btn_preview.setIconSize(QSize(16, 16))
        self._btn_preview.setCheckable(True)
        self._btn_preview.clicked.connect(self._toggle_live_preview)
        header_layout.addWidget(self._btn_preview)

        # 展开按钮
        self._btn_expand = QPushButton()
        self._btn_expand.setObjectName("floatingExpandBtn")
        self._btn_expand.setFixedSize(24, 24)
        self._btn_expand.setToolTip("展开日志")
        self._btn_expand.setIcon(create_icon("expand", 14))
        self._btn_expand.setIconSize(QSize(14, 14))
        self._btn_expand.clicked.connect(self._toggle_expand)
        header_layout.addWidget(self._btn_expand)

        self._btn_hide = QPushButton()
        self._btn_hide.setObjectName("floatingExpandBtn")
        self._btn_hide.setFixedSize(24, 24)
        self._btn_hide.setToolTip("临时隐藏")
        self._btn_hide.setIcon(create_icon("hide", 14))
        self._btn_hide.setIconSize(QSize(14, 14))
        self._btn_hide.clicked.connect(self._on_temporary_hide_clicked)
        header_layout.addWidget(self._btn_hide)

        container_layout.addWidget(header)

        # 日志区域
        self._log_panel = QWidget()
        self._log_panel.setVisible(False)
        self._log_panel.setObjectName("floatingLogPanel")
        self._log_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        log_layout = QVBoxLayout(self._log_panel)
        log_layout.setContentsMargins(0, 8, 0, 0)
        log_layout.setSpacing(0)

        self._log_view = LogView()
        self._log_view.setObjectName("floatingLogView")
        self._log_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        log_layout.addWidget(self._log_view, 1)

        container_layout.addWidget(self._log_panel, 1)

        # 实时画面预览面板
        self._preview_panel = QWidget()
        self._preview_panel.setVisible(False)
        self._preview_panel.setObjectName("floatingPreviewPanel")
        self._preview_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._preview_panel.setFixedHeight(380)
        preview_layout = QVBoxLayout(self._preview_panel)
        preview_layout.setContentsMargins(0, 4, 0, 0)
        preview_layout.setSpacing(4)

        # 预览面板顶部：提示文字 + 刷新频率选择
        preview_header = QWidget()
        preview_header_layout = QHBoxLayout(preview_header)
        preview_header_layout.setContentsMargins(0, 0, 0, 0)
        preview_header_layout.setSpacing(6)

        preview_label = QLabel("🖥️ 实时画面")
        preview_label.setObjectName("floatingPreviewLabel")
        preview_label.setStyleSheet("font-size:11px; color:#888;")
        preview_header_layout.addWidget(preview_label)

        preview_header_layout.addStretch()

        freq_label = QLabel("刷新:")
        freq_label.setStyleSheet("font-size:10px; color:#888;")
        preview_header_layout.addWidget(freq_label)

        from PySide6.QtWidgets import QComboBox
        self._preview_freq_combo = QComboBox()
        self._preview_freq_combo.addItems(["0.5s", "1s", "2s"])
        self._preview_freq_combo.setCurrentIndex(1)  # 默认1s
        self._preview_freq_combo.setFixedWidth(60)
        self._preview_freq_combo.setStyleSheet("font-size:10px;")
        self._preview_freq_combo.currentIndexChanged.connect(self._on_preview_freq_changed)
        preview_header_layout.addWidget(self._preview_freq_combo)

        preview_layout.addWidget(preview_header)

        # 画面显示区域
        self._preview_label = QLabel()
        self._preview_label.setObjectName("floatingPreviewImage")
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._preview_label.setMinimumHeight(200)
        self._preview_label.setStyleSheet("background-color: #1a1a1a; border-radius: 4px;")
        self._preview_label.setText("暂无画面")
        preview_layout.addWidget(self._preview_label, 1)

        container_layout.addWidget(self._preview_panel, 1)
        main_layout.addWidget(self._container, 1)

        self._update_button_states()

    def _load_hotkey_config(self):
        try:
            import json
            import os
            config_path = get_config_path()
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                self._start_hotkey = config.get('start_task_hotkey', 'Num7')
                self._pause_hotkey = config.get('pause_workflow_hotkey', 'F11')
        except Exception as e:
            logger.warning(f"加载快捷键配置失败: {e}")

    def _update_button_states(self):
        if self._is_running:
            if self._is_paused:
                # 暂停状态：开始按钮可用(用于恢复)，暂停按钮禁用
                self._btn_start.setEnabled(True)
                self._btn_start.setIcon(create_icon("play", color=_resolve_icon_color(True)))
                self._btn_start.setToolTip("恢复执行")
                self._btn_pause.setEnabled(False)
            else:
                # 运行状态：开始按钮禁用，暂停按钮可用
                self._btn_start.setEnabled(False)
                self._btn_start.setIcon(create_icon("play", color=_resolve_icon_color(False)))
                self._btn_start.setToolTip("开始")
                self._btn_pause.setEnabled(True)
            self._btn_stop.setEnabled(True)
            self._btn_pause.setIcon(create_icon("pause", color=_resolve_icon_color(self._btn_pause.isEnabled())))
            self._btn_pause.setToolTip("暂停")
        else:
            # 未运行状态
            self._btn_start.setEnabled(True)
            self._btn_start.setIcon(create_icon("play", color=_resolve_icon_color(True)))
            self._btn_start.setToolTip("开始")
            self._btn_pause.setEnabled(False)
            self._btn_stop.setEnabled(False)
            self._btn_pause.setIcon(create_icon("pause", color=_resolve_icon_color(False)))
            self._btn_pause.setToolTip("暂停")
        self._btn_stop.setIcon(create_icon("stop", color=_resolve_icon_color(self._btn_stop.isEnabled())))

    def _toggle_expand(self):
        self._is_expanded = not self._is_expanded
        if self._is_expanded:
            self._btn_expand.setIcon(create_icon("collapse", 14, color=_resolve_icon_color(True)))
            self._btn_expand.setToolTip("收起日志")
            self._log_panel.setVisible(True)
            self._preview_panel.setVisible(True)
            target_height = self.EXPANDED_HEIGHT
            # 如果日志为空，显示提示
            if self._log_view.document().isEmpty():
                timestamp = time.strftime("%H:%M:%S")
                self._log_view.appendColoredLog(f"[{timestamp}] [信息] 等待执行日志...")
        else:
            self._btn_expand.setIcon(create_icon("expand", 14, color=_resolve_icon_color(True)))
            self._btn_expand.setToolTip("展开日志")
            self._preview_panel.setVisible(False)
            target_height = self.COLLAPSED_HEIGHT

        current_height = self.height()

        self._height_anim.stop()
        self._height_anim2.stop()

        self._height_anim.setStartValue(current_height)
        self._height_anim.setEndValue(target_height)

        self._height_anim2.setStartValue(current_height)
        self._height_anim2.setEndValue(target_height)

        self._height_anim.start()
        self._height_anim2.start()

    def _on_animation_finished(self):
        if not self._is_expanded:
            self._log_panel.setVisible(False)

    def _on_start_clicked(self):
        if self._is_paused:
            # 暂停状态下点击开始按钮，发送恢复信号
            self.request_pause.emit()
        else:
            self.request_start.emit()

    def _on_pause_clicked(self):
        self.request_pause.emit()

    def _on_stop_clicked(self):
        self.request_stop.emit()

    def _on_temporary_hide_clicked(self):
        self.request_temporary_hide.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = True
            self._drag_start_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_dragging:
            self.move(event.globalPosition().toPoint() - self._drag_start_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._is_dragging = False
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.request_restore_main.emit()
        event.accept()

    def show_at_top_center(self, anchor_widget=None):
        from PySide6.QtWidgets import QApplication
        # 确保浮动窗口继承应用样式
        app = QApplication.instance()
        if app:
            self.setStyleSheet(app.styleSheet())
        geometry = get_available_geometry_for_widget(anchor_widget or self)
        if geometry and not geometry.isEmpty():
            x = geometry.left() + max(0, (geometry.width() - self.width()) // 2)
            y = geometry.top() + 10
            self.move(x, y)
        show_and_raise_widget(self, log_prefix='浮动状态窗口')

    def show_without_activate(self, anchor_widget=None):
        self.show_at_top_center(anchor_widget=anchor_widget)

    @Slot(str, str)
    def on_step_started(self, card_type: str, card_name: str):
        text = f"{card_type}: {card_name}" if card_name else card_type
        if len(text) > 40:
            text = text[:37] + "..."
        self._status_label.setText(text)

    @Slot(str, str, bool)
    def on_step_log(self, card_type: str, message: str, success: bool):
        try:
            timestamp = time.strftime("%H:%M:%S")
            message = _translate_status_message(message)
            if len(message) > 200:
                message = message[:197] + "..."
            is_start_log = "开始执行" in message and "执行成功" not in message and "执行失败" not in message
            status = "信息" if is_start_log else ("成功" if success else "失败")
            log_text = f"[{timestamp}] [{status}] {card_type}: {message}"
            self._log_view.appendColoredLog(log_text)
            # 确保滚动到底部
            self._log_view.verticalScrollBar().setValue(
                self._log_view.verticalScrollBar().maximum()
            )
        except Exception as e:
            logger.error(f"on_step_log error: {e}")

    @Slot()
    def on_execution_started(self):
        self._is_running = True
        self._is_paused = False
        self._status_indicator.set_running()
        self._status_label.setText("执行中...")
        self._update_button_states()
        # 添加开始日志
        timestamp = time.strftime("%H:%M:%S")
        self._log_view.appendColoredLog(f"[{timestamp}] [信息] 主工作流开始执行")
        # 自动开启预览（如果有目标窗口句柄）
        if self._target_hwnd is not None and self._target_hwnd > 0:
            self._live_preview_enabled = True
            self._btn_preview.setChecked(True)
            self._btn_preview.setIcon(create_icon("eye", 16, color=_resolve_icon_color(True)))
            self._btn_preview.setToolTip("关闭实时画面")
            self._preview_timer.start(self._preview_freq)

    @Slot(bool, str)
    def on_execution_finished(self, success: bool, message: str):
        self._is_running = False
        self._is_paused = False
        self._status_indicator.set_stopped()
        if success:
            self._status_label.setText("执行完成")
        else:
            translated = _translate_status_message(message)
            msg = translated[:20] + "..." if len(translated) > 20 else translated
            self._status_label.setText(f"已停止: {msg}" if msg else "已停止")
        self._update_button_states()
        # 添加结束日志
        timestamp = time.strftime("%H:%M:%S")
        status = "完成" if success else "停止"
        translated_message = _translate_status_message(message)
        self._log_view.appendColoredLog(f"[{timestamp}] [信息] 主工作流{status}: {translated_message[:40]}")
        # 自动停止预览
        self.stop_live_preview()

    @Slot()
    def on_execution_paused(self):
        self._is_paused = True
        self._status_indicator.set_paused()
        self._status_label.setText("已暂停")
        self._update_button_states()

    @Slot()
    def on_execution_resumed(self):
        self._is_paused = False
        self._status_indicator.set_running()
        self._status_label.setText("执行中...")
        self._update_button_states()

    # ── 实时画面预览相关 ──────────────────────────────────────────

    def set_target_hwnd(self, hwnd: int):
        """设置目标窗口句柄"""
        self._target_hwnd = hwnd

    def set_bound_windows(self, bound_windows: list):
        """设置所有绑定窗口列表，用于多窗口预览"""
        self._bound_windows = bound_windows or []
        # 如果当前目标窗口不在列表中，切换到第一个
        if self._target_hwnd:
            found = False
            for i, win in enumerate(self._bound_windows):
                if win.get('hwnd') == self._target_hwnd:
                    found = True
                    break
            if not found and self._bound_windows:
                self._target_hwnd = self._bound_windows[0].get('hwnd', None)
                self._active_window_index = 0

    def get_active_window_info(self):
        """获取当前活跃窗口信息"""
        if 0 <= self._active_window_index < len(self._bound_windows):
            return self._bound_windows[self._active_window_index]
        return None

    def switch_to_window(self, index: int):
        """切换到指定索引的窗口预览"""
        if 0 <= index < len(self._bound_windows):
            self._active_window_index = index
            win = self._bound_windows[index]
            self._target_hwnd = win.get('hwnd', None)
            # 更新预览面板标题显示
            if self._preview_panel.isVisible():
                self._update_preview_header()

    def _toggle_live_preview(self):
        """开/关实时画面预览"""
        if self._live_preview_enabled:
            self.stop_live_preview()
        else:
            # 检查是否有有效窗口
            hwnd = self._get_first_bound_hwnd()
            if not hwnd:
                logger.warning("没有绑定窗口，无法开启实时预览")
                return
            self._target_hwnd = hwnd
            self._live_preview_enabled = True
            self._preview_timer.start(self._preview_freq)
            self._btn_preview.setIcon(create_icon("eye", 16, color=_resolve_icon_color(True)))
            self._btn_preview.setToolTip("关闭实时画面")

    def _on_preview_freq_changed(self, index: int):
        """刷新频率变更"""
        freq_map = {0: 500, 1: 1000, 2: 2000}
        self._preview_freq = freq_map.get(index, 1000)
        if self._preview_timer.isActive():
            self._preview_timer.setInterval(self._preview_freq)

    def _capture_and_display(self):
        """定时触发：截图并显示（当前活跃窗口）"""
        if not self._bound_windows:
            self._preview_label.setText("未绑定窗口")
            return
        # 自动获取第一个绑定窗口的 hwnd
        current_hwnd = self._get_first_bound_hwnd()
        if current_hwnd is None or current_hwnd <= 0:
            self._preview_label.setText("未绑定窗口")
            return
        if not self._live_preview_enabled:
            return
        # 使用当前获取的 hwnd
        self._target_hwnd = current_hwnd
        try:
            from services.screenshot_pool import capture_window
            frame = capture_window(self._target_hwnd, client_area_only=True, use_cache=False)
            if frame is None:
                return
            pixmap = self._numpy_to_pixmap(frame, max_width=360)
            if pixmap is not None:
                self._preview_label.setPixmap(pixmap)
                # 更新窗口标签
                self._update_preview_header()
            else:
                self._preview_label.setText("转换失败")
        except Exception as e:
            logger.warning(f"实时画面截图失败: {e}")

    def _update_preview_header(self):
        """更新预览面板头部显示（窗口名+角色标签）"""
        win = self.get_active_window_info()
        if not win:
            return
        title = win.get('title', '未知窗口')
        role = win.get('role', '')
        role_text = f" [{role}]" if role else ""
        hwnd = win.get('hwnd', 0)
        if hwnd and hwnd != 0:
            label_text = f"🖥️ {title}{role_text} (句柄: {hwnd})"
        else:
            label_text = f"🖥️ {title}{role_text}"
        # 找到预览面板的header label并更新
        for child in self._preview_panel.findChildren(QLabel):
            if child.objectName() == "floatingPreviewLabel":
                child.setText(label_text)
                break

    def _numpy_to_pixmap(self, frame, max_width=360):
        """np.ndarray → QPixmap 转换"""
        try:
            import cv2
            from PIL import Image as PILImage
            height, width = frame.shape[:2]
            scale = max_width / width
            new_w = max_width
            new_h = int(height * scale)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            if scale < 1.0:
                rgb = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
            pil_img = PILImage.fromarray(rgb)
            from PySide6.QtGui import QImage
            data = pil_img.tobytes("raw", "RGB")
            qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format_RGB888)
            from PySide6.QtGui import QPixmap
            return QPixmap.fromImage(qimg)
        except Exception as e:
            logger.warning(f"转换截图失败: {e}")
            return None

    def stop_live_preview(self):
        """停止实时画面预览"""
        self._live_preview_enabled = False
        self._preview_timer.stop()
        self._btn_preview.setChecked(False)
        self._btn_preview.setIcon(create_icon("eye", 16, color=_resolve_icon_color(False)))
        self._btn_preview.setToolTip("开启实时画面")
        self._preview_label.clear()
        self._preview_label.setText("暂无画面")
        self._update_preview_header()

    def reset(self):
        self._is_running = False
        self._is_paused = False
        self._status_indicator.set_stopped()
        self._status_label.setText("就绪")
        self._log_view.clear()
        self._update_button_states()

    def clear_logs(self):
        self._log_view.clear()


class FloatingWindowController:
    """浮动窗口控制器"""

    def __init__(self, main_window, floating_window: FloatingStatusWindow):
        self._main = main_window
        self._floating = floating_window
        self._is_workflow_running = False
        self._enabled = True
        self._temporarily_hidden = False
        self._floating.request_restore_main.connect(self._restore_main_window)
        self._floating.request_temporary_hide.connect(self._hide_temporarily)

    def set_enabled(self, enabled: bool):
        if bool(enabled) != self._enabled:
            self._temporarily_hidden = False
        self._enabled = bool(enabled)
        self._update_visibility()

    def on_main_window_state_changed(self, minimized: bool):
        if not minimized:
            self._temporarily_hidden = False
        self._update_visibility()

    def on_workflow_started(self):
        self._is_workflow_running = True
        self._floating.clear_logs()
        # 获取第一个绑定窗口的句柄，用于实时画面预览
        hwnd = self._get_first_bound_hwnd()
        if hwnd:
            self._floating.set_target_hwnd(hwnd)
        self._floating.on_execution_started()
        self._update_visibility()

    def _get_first_bound_hwnd(self):
        """获取第一个绑定的窗口句柄"""
        for window_info in getattr(self._main, 'bound_windows', []) or []:
            hwnd = int(window_info.get('hwnd', 0) or 0)
            if hwnd > 0:
                return hwnd
        return None



    def on_bound_windows_changed(self):
        """绑定窗口变化时更新实时预览（外部调用）"""
        hwnd = self._get_first_bound_hwnd()
        if hwnd:
            self._floating.set_target_hwnd(hwnd)
            self._floating.set_bound_windows(getattr(self._main, 'bound_windows', []) or [])

    def on_workflow_finished(self, success: bool, message: str):
        self._is_workflow_running = False
        self._floating.on_execution_finished(success, message)

    def on_workflow_paused(self):
        self._floating.on_execution_paused()

    def on_workflow_resumed(self):
        self._floating.on_execution_resumed()

    def _hide_temporarily(self):
        if not self._enabled:
            return
        self._temporarily_hidden = True
        self._floating.hide()
        logger.info("浮动窗口已临时隐藏，将在主窗口恢复后自动重新启用")

    def _update_visibility(self):
        if not self._enabled:
            self._floating.hide()
            return

        if self._temporarily_hidden:
            self._floating.hide()
            return

        if self._main.isMinimized():
            self._floating.show_without_activate(anchor_widget=self._main)
        else:
            self._floating.hide()

    def _restore_main_window(self):
        if hasattr(self._main, "restore_main_window"):
            self._main.restore_main_window()
        else:
            self._main.setWindowState(
                self._main.windowState() & ~Qt.WindowState.WindowMinimized
            )
            show_and_activate_overlay(self._main, log_prefix='主窗口恢复', focus=True)
        self._floating.hide()
