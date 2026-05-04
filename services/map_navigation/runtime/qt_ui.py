# -*- coding: utf-8 -*-
from __future__ import annotations

import ctypes
from collections import deque
from ctypes import wintypes
import logging
import os
import sys
import threading
import time
from typing import Optional

import cv2
import mss
import numpy as np
from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent, QColor, QGuiApplication, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from services.map_navigation.runtime import bridge, config
from services.map_navigation.runtime.minimap_region import (
    build_minimap_region_payload_from_qt_rect,
    has_valid_minimap_region_payload,
    resolve_minimap_bound_window_info,
    resolve_minimap_capture_region,
    resolve_minimap_qt_rect,
)
from services.map_navigation.runtime.route_manager import RouteManager
from services.map_navigation.runtime.screen_capture import capture_region_bgr
from services.map_navigation.runtime.tracker_engine import LoftrEngine
from services.map_navigation.runtime.window_anchor import compute_dynamic_island_position
from themes import get_theme_manager
from ui.system_parts.menu_style import apply_unified_menu_style
from utils.app_paths import get_config_path
from utils.dpi_awareness import enable_process_dpi_awareness
from utils.window_activation_utils import schedule_overlay_activation_boost, show_and_activate_overlay
from utils.window_coordinate_common import (
    build_window_info,
    get_qt_virtual_desktop_rect,
    get_window_client_qt_global_rect,
    native_rect_to_qt_global_rect,
)


logger = logging.getLogger(__name__)

_TARGET_HWND_ENV_NAME = "LCA_LKMAPTOOLS_TARGET_HWND"
_DEBUG_SNAPSHOT_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "LCA",
    "runtime_data",
    "map_navigation_debug",
)


def _coerce_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _coerce_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _get_target_hwnd() -> int:
    return _coerce_int(os.environ.get(_TARGET_HWND_ENV_NAME, "0"), 0)


def _write_debug_snapshot(name: str, image: np.ndarray) -> None:
    if image is None or not isinstance(image, np.ndarray) or image.size <= 0:
        return
    try:
        os.makedirs(_DEBUG_SNAPSHOT_DIR, exist_ok=True)
        cv2.imwrite(os.path.join(_DEBUG_SNAPSHOT_DIR, name), image)
    except Exception:
        logger.debug("[地图导航] 写入调试截图失败: %s", name, exc_info=True)


def _ensure_qt_application() -> tuple[QApplication, object]:
    enable_process_dpi_awareness()
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    theme_manager = get_theme_manager(get_config_path())
    theme_manager.apply_theme(app, theme_manager.get_theme_mode())
    return app, theme_manager


def _has_valid_minimap_config() -> bool:
    minimap = config.MINIMAP
    return has_valid_minimap_region_payload(minimap)


def _with_alpha(color: QColor, alpha: int) -> QColor:
    shaded = QColor(color)
    shaded.setAlpha(max(0, min(255, int(alpha))))
    return shaded


def _create_panel_shadow(parent: QWidget | None = None) -> QGraphicsDropShadowEffect:
    shadow = QGraphicsDropShadowEffect(parent)
    shadow.setBlurRadius(12)
    shadow.setXOffset(0)
    shadow.setYOffset(2)
    shadow.setColor(QColor(0, 0, 0, 50))
    return shadow


def _get_minimap_selector_palette(theme_manager) -> dict[str, QColor]:
    is_dark = bool(theme_manager.is_dark_mode())
    overlay_alpha = 214 if is_dark else 192
    surface_alpha = 232 if is_dark else 224
    chip_alpha = 244 if is_dark else 236
    shadow_alpha = 124 if is_dark else 86
    return {
        "overlay_fill": _with_alpha(theme_manager.get_qcolor("card"), overlay_alpha),
        "overlay_tint": _with_alpha(theme_manager.get_qcolor("accent"), 24 if is_dark else 18),
        "panel_fill": _with_alpha(theme_manager.get_qcolor("card"), surface_alpha),
        "panel_border": theme_manager.get_qcolor("border"),
        "accent": theme_manager.get_qcolor("accent"),
        "accent_hover": theme_manager.get_qcolor("accent_hover"),
        "guide": _with_alpha(theme_manager.get_qcolor("border_light"), 220 if is_dark else 196),
        "chip_fill": _with_alpha(theme_manager.get_qcolor("card_title"), chip_alpha),
        "chip_border": _with_alpha(theme_manager.get_qcolor("border"), 232 if is_dark else 216),
        "text": theme_manager.get_qcolor("text"),
        "text_secondary": theme_manager.get_qcolor("text_secondary"),
        "text_shadow": _with_alpha(theme_manager.get_qcolor("background"), shadow_alpha),
        "preview_background": theme_manager.get_qcolor("card"),
        "preview_canvas": theme_manager.get_qcolor("canvas"),
        "preview_border": theme_manager.get_qcolor("border"),
        "preview_text": theme_manager.get_qcolor("text"),
        "preview_text_secondary": theme_manager.get_qcolor("text_secondary"),
    }


class PersistentCheckMenu(QMenu):
    def mouseReleaseEvent(self, event) -> None:
        action = self.actionAt(event.pos())
        if (
            action is not None
            and action.isEnabled()
            and action.isCheckable()
            and event.button() == Qt.MouseButton.LeftButton
        ):
            action.trigger()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class MinimapPreviewDialog(QDialog):
    def __init__(
        self,
        theme_manager,
        capture_region: dict[str, int],
        preview_bgr: np.ndarray,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.capture_region = dict(capture_region)
        self.preview_qimage = _numpy_bgr_to_qimage(preview_bgr)
        self.setObjectName("mapNavPreviewDialog")
        self.setWindowTitle("确认输出图像 (已缩放至 180x180)")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(350, 350)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(12)

        self.preview_label = QLabel(self)
        self.preview_label.setObjectName("minimapPreviewImage")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setFixedSize(196, 196)
        self.preview_label.setPixmap(QPixmap.fromImage(self.preview_qimage))
        root_layout.addWidget(self.preview_label, 0, Qt.AlignmentFlag.AlignHCenter)

        info_text = (
            f"屏幕截取: {self.capture_region['width']}x{self.capture_region['height']}"
            " -> AI输入: 180x180"
        )
        self.info_label = QLabel(info_text, self)
        self.info_label.setObjectName("minimapPreviewInfo")
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_label.setWordWrap(True)
        root_layout.addWidget(self.info_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(12)
        root_layout.addLayout(button_row)

        self.retake_button = QPushButton("重新截取", self)
        self.retake_button.clicked.connect(self.reject)
        self.retake_button.setMinimumWidth(108)
        button_row.addWidget(self.retake_button)

        self.confirm_button = QPushButton("确定", self)
        self.confirm_button.setProperty("primary", True)
        self.confirm_button.setAutoDefault(True)
        self.confirm_button.setDefault(True)
        self.confirm_button.setMinimumWidth(108)
        self.confirm_button.clicked.connect(self.accept)
        button_row.addWidget(self.confirm_button)

        self.apply_theme()
        self._center_to_capture_region()

    def _center_to_capture_region(self) -> None:
        region = self.capture_region
        native_rect = (
            int(region.get("left", 0)),
            int(region.get("top", 0)),
            int(region.get("left", 0) + region.get("width", 0)),
            int(region.get("top", 0) + region.get("height", 0)),
        )
        qt_rect = native_rect_to_qt_global_rect(native_rect)
        if qt_rect is not None and not qt_rect.isEmpty():
            center_point = qt_rect.center()
        else:
            center_point = QPoint(
                int(region.get("left", 0) + region.get("width", 0) // 2),
                int(region.get("top", 0) + region.get("height", 0) // 2),
            )
        screen = QGuiApplication.screenAt(center_point)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        available_rect = screen.availableGeometry()
        frame_rect = self.frameGeometry()
        self.move(
            int(available_rect.center().x() - frame_rect.width() / 2),
            int(available_rect.center().y() - frame_rect.height() / 2),
        )

    def apply_theme(self) -> None:
        for widget in (self, self.preview_label, self.info_label, self.retake_button, self.confirm_button):
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class MinimapMaskSelectorOverlay(QWidget):
    selection_confirmed = Signal(dict)
    selection_cancelled = Signal()
    _MIN_FRAME_SIZE = 120
    _FRAME_SIDE_PADDING = 8
    _TOP_HINT_HEIGHT = 38
    _BOTTOM_HINT_HEIGHT = 72

    def __init__(self, theme_manager, target_hwnd: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent=None)
        self.theme_manager = theme_manager
        self.target_hwnd = int(target_hwnd or 0)
        self.window_info = resolve_minimap_bound_window_info(
            self.target_hwnd,
            config.MINIMAP if isinstance(config.MINIMAP, dict) else None,
        )
        self._confirmed = False
        self._closing = False
        self._preview_pending = False
        self._pending_capture_region: Optional[dict[str, int]] = None
        self._pending_selection_payload: Optional[dict[str, object]] = None
        self._dragging = False
        self._drag_offset = QPoint()
        self.preview_dialog: Optional[MinimapPreviewDialog] = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setWindowTitle("小地图校准器")

        size, pos_x, pos_y = self._resolve_initial_geometry()
        self._apply_frame_geometry(int(pos_x), int(pos_y), int(size))

    def _resolve_initial_geometry(self) -> tuple[int, int, int]:
        minimap = config.MINIMAP if isinstance(config.MINIMAP, dict) else {}
        saved_qt_rect = resolve_minimap_qt_rect(minimap, target_hwnd=self.target_hwnd)
        if saved_qt_rect is not None:
            rect = QRect(*saved_qt_rect)
            if not rect.isEmpty():
                size = max(self._MIN_FRAME_SIZE, min(800, max(int(rect.width()), int(rect.height()))))
                pos_x = int(rect.center().x() - size / 2)
                pos_y = int(rect.center().y() - size / 2)
                return size, pos_x, pos_y

        size = 180
        pos_x = 100
        pos_y = 100
        target_rect = self._get_target_client_qt_rect()
        if not target_rect.isEmpty():
            size = max(
                self._MIN_FRAME_SIZE,
                min(220, max(1, int(target_rect.width()) - 32), max(1, int(target_rect.height()) - 32)),
            )
            pos_x = int(target_rect.x()) + max(16, int(target_rect.width()) - size - 24)
            pos_y = int(target_rect.y()) + 24
        return size, pos_x, pos_y

    def _get_target_client_qt_rect(self) -> QRect:
        if self.target_hwnd > 0:
            self.window_info = resolve_minimap_bound_window_info(
                self.target_hwnd,
                config.MINIMAP if isinstance(config.MINIMAP, dict) else None,
            )
        if not isinstance(self.window_info, dict):
            return QRect()
        client_qt_rect = get_window_client_qt_global_rect(self.window_info)
        if client_qt_rect is None or client_qt_rect.isEmpty():
            return QRect()
        return QRect(client_qt_rect)

    def _clamp_frame_geometry(self, frame_left: int, frame_top: int, frame_size: int) -> tuple[int, int, int]:
        size = max(self._MIN_FRAME_SIZE, min(800, int(frame_size)))
        target_rect = self._get_target_client_qt_rect()
        if not target_rect.isEmpty():
            size = min(size, max(1, int(target_rect.width())), max(1, int(target_rect.height())))
            min_left = int(target_rect.x())
            max_left = int(target_rect.x() + target_rect.width() - size)
            min_top = int(target_rect.y())
            max_top = int(target_rect.y() + target_rect.height() - size)
            frame_left = max(min_left, min(int(frame_left), max_left))
            frame_top = max(min_top, min(int(frame_top), max_top))
            return int(frame_left), int(frame_top), int(size)

        desktop_rect = get_qt_virtual_desktop_rect()
        if desktop_rect is not None and desktop_rect.width() > 0 and desktop_rect.height() > 0:
            min_left = int(desktop_rect.x()) + 8
            max_left = int(desktop_rect.x() + desktop_rect.width() - size - 8)
            min_top = int(desktop_rect.y()) + 8
            max_top = int(desktop_rect.y() + desktop_rect.height() - size - 8)
            if min_left > max_left:
                max_left = min_left
            if min_top > max_top:
                max_top = min_top
            frame_left = max(min_left, min(int(frame_left), max_left))
            frame_top = max(min_top, min(int(frame_top), max_top))
        return int(frame_left), int(frame_top), int(size)

    def _current_selection_qt_rect(self) -> QRect:
        outer_rect = self.geometry()
        frame_rect = self._frame_rect()
        return QRect(
            int(outer_rect.x() + frame_rect.x()),
            int(outer_rect.y() + frame_rect.y()),
            int(frame_rect.width()),
            int(frame_rect.height()),
        )

    def _build_current_selection_payload(self) -> Optional[dict[str, object]]:
        if self.target_hwnd > 0:
            self.window_info = resolve_minimap_bound_window_info(self.target_hwnd)
        selection_rect = self._current_selection_qt_rect()
        return build_minimap_region_payload_from_qt_rect(
            (
                int(selection_rect.x()),
                int(selection_rect.y()),
                int(selection_rect.width()),
                int(selection_rect.height()),
            ),
            target_hwnd=self.target_hwnd,
        )

    def apply_theme(self) -> None:
        self.update()
        if self.preview_dialog is not None and self.preview_dialog.isVisible():
            self.preview_dialog.apply_theme()

    def _frame_rect(self) -> QRect:
        size = max(1, self.width() - self._FRAME_SIDE_PADDING * 2)
        return QRect(
            int(self._FRAME_SIDE_PADDING),
            int(self._TOP_HINT_HEIGHT),
            int(size),
            int(size),
        )

    def _frame_size(self) -> int:
        return int(self._frame_rect().width())

    def _apply_frame_geometry(self, frame_left: int, frame_top: int, frame_size: int) -> None:
        frame_left, frame_top, size = self._clamp_frame_geometry(frame_left, frame_top, frame_size)
        self.setGeometry(
            int(frame_left - self._FRAME_SIDE_PADDING),
            int(frame_top - self._TOP_HINT_HEIGHT),
            int(size + self._FRAME_SIDE_PADDING * 2),
            int(size + self._TOP_HINT_HEIGHT + self._BOTTOM_HINT_HEIGHT),
        )

    def activate_selector_window(self) -> None:
        if self.target_hwnd > 0:
            self.window_info = resolve_minimap_bound_window_info(
                self.target_hwnd,
                config.MINIMAP if isinstance(config.MINIMAP, dict) else None,
            )
            current_rect = self._current_selection_qt_rect()
            if not current_rect.isEmpty():
                self._apply_frame_geometry(
                    int(current_rect.x()),
                    int(current_rect.y()),
                    int(max(current_rect.width(), current_rect.height())),
                )
        show_and_activate_overlay(self, log_prefix="地图导航小地图遮罩层", focus=True)
        schedule_overlay_activation_boost(
            self,
            log_prefix="地图导航小地图遮罩层置顶",
            intervals_ms=(50, 150, 300, 500),
            focus=True,
        )

    def _current_capture_region(self) -> dict[str, int]:
        payload = self._build_current_selection_payload() or {}
        return {
            "left": _coerce_int(payload.get("left"), 0),
            "top": _coerce_int(payload.get("top"), 0),
            "width": _coerce_int(payload.get("width"), 0),
            "height": _coerce_int(payload.get("height"), 0),
        }

    def _resize_selector(self, delta: int) -> None:
        current_rect = self._current_selection_qt_rect()
        new_size = int(current_rect.width()) + int(delta)
        if not self._MIN_FRAME_SIZE <= new_size <= 800:
            return
        center_x = current_rect.x() + current_rect.width() / 2.0
        center_y = current_rect.y() + current_rect.height() / 2.0
        frame_left = int(round(center_x - new_size / 2.0))
        frame_top = int(round(center_y - new_size / 2.0))
        self._apply_frame_geometry(frame_left, frame_top, new_size)
        self.update()

    def prepare_preview(self) -> None:
        if self._preview_pending:
            return
        selection_payload = self._build_current_selection_payload() or {}
        region = self._current_capture_region()
        if region["width"] <= 0 or region["height"] <= 0:
            return
        self._pending_selection_payload = dict(selection_payload)
        self._pending_capture_region = region
        self._preview_pending = True
        self.hide()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        QTimer.singleShot(100, self._show_preview_dialog)

    def _show_preview_dialog(self) -> None:
        self._preview_pending = False
        region = dict(self._pending_capture_region or {})
        if not region:
            self._restore_selector_after_preview()
            return

        preview_bgr = capture_region_bgr(region)
        if preview_bgr is None or preview_bgr.size == 0:
            logger.warning("[地图导航] 小地图预览截图失败，将返回遮罩层继续调整")
            self._restore_selector_after_preview()
            return

        preview_bgr = cv2.resize(preview_bgr, (180, 180), interpolation=cv2.INTER_AREA)
        preview_bgra = self._build_circular_preview_bgra(preview_bgr)
        self.preview_dialog = MinimapPreviewDialog(self.theme_manager, region, preview_bgra, self)
        dialog_result = self.preview_dialog.exec()
        self.preview_dialog = None
        if dialog_result == int(QDialog.DialogCode.Accepted):
            self._confirmed = True
            selected_payload = dict(self._pending_selection_payload or {})
            if not selected_payload:
                selected_payload = dict(region)
            self.selection_confirmed.emit(selected_payload)
            self.close()
            return
        self._restore_selector_after_preview()

    def _restore_selector_after_preview(self) -> None:
        self._pending_capture_region = None
        self._pending_selection_payload = None
        if self._closing:
            return
        self.show()
        self.activate_selector_window()

    @staticmethod
    def _build_circular_preview_bgra(preview_bgr: np.ndarray) -> np.ndarray:
        if preview_bgr is None or not isinstance(preview_bgr, np.ndarray) or preview_bgr.ndim < 2:
            return preview_bgr
        height, width = preview_bgr.shape[:2]
        if preview_bgr.ndim == 2:
            color = cv2.cvtColor(preview_bgr, cv2.COLOR_GRAY2BGR)
        else:
            color = preview_bgr[:, :, :3]
        mask = np.zeros((height, width), dtype=np.uint8)
        radius = max(1, int(round(min(width, height) * 0.5 - 1)))
        cv2.circle(mask, (int(round((width - 1) / 2)), int(round((height - 1) / 2))), radius, 255, -1)
        return np.concatenate((color, mask[:, :, np.newaxis]), axis=2)

    def paintEvent(self, _event) -> None:
        colors = _get_minimap_selector_palette(self.theme_manager)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)

        panel_rect = self._frame_rect().adjusted(0, 0, -1, -1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colors["overlay_tint"])
        painter.drawRoundedRect(panel_rect, 12, 12)

        painter.setPen(QPen(colors["panel_border"], 1.0))
        painter.setBrush(colors["overlay_fill"])
        painter.drawRoundedRect(panel_rect, 12, 12)

        focus_rect = panel_rect.adjusted(2, 2, -2, -2)
        painter.setPen(Qt.PenStyle.NoPen)
        # Do not clear to fully transparent here. On Windows layered widgets,
        # alpha=0 pixels become click-through and the calibration area leaks input.
        focus_fill = QColor(colors["overlay_fill"])
        focus_fill.setAlpha(6 if self.theme_manager.is_dark_mode() else 4)
        painter.setBrush(focus_fill)
        painter.drawEllipse(focus_rect)

        accent_pen = QPen(colors["accent"], 2.0)
        accent_pen.setCosmetic(True)
        painter.setPen(accent_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(focus_rect)

        cross_pen = QPen(colors["guide"], 1.2)
        cross_pen.setCosmetic(True)
        cross_pen.setDashPattern([5.0, 4.0])
        painter.setPen(cross_pen)
        center_x = int(panel_rect.center().x())
        center_y = int(panel_rect.center().y())
        painter.drawLine(int(panel_rect.left()) + 16, center_y, int(panel_rect.right()) - 16, center_y)
        painter.drawLine(center_x, int(panel_rect.top()) + 16, center_x, int(panel_rect.bottom()) - 16)

        painter.setBrush(colors["accent"])
        painter.drawEllipse(QPointF(float(center_x), float(center_y)), 3.5, 3.5)

        self._draw_hint_chip(
            painter,
            text="拖动定位  滚轮缩放",
            rect=QRect(
                int(self._FRAME_SIDE_PADDING),
                6,
                max(40, int(self.width() - self._FRAME_SIDE_PADDING * 2)),
                max(20, int(self._TOP_HINT_HEIGHT - 10)),
            ),
            colors=colors,
        )
        self._draw_hint_chip(
            painter,
            text="回车 / 双击确认",
            rect=QRect(
                int(self._FRAME_SIDE_PADDING),
                int(panel_rect.bottom()) + 8,
                max(40, int(self.width() - self._FRAME_SIDE_PADDING * 2)),
                26,
            ),
            colors=colors,
            align=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
        )
        self._draw_hint_chip(
            painter,
            text=f"{panel_rect.width()} x {panel_rect.height()}",
            rect=QRect(
                int(self._FRAME_SIDE_PADDING),
                int(panel_rect.bottom()) + 38,
                max(40, int(self.width() - self._FRAME_SIDE_PADDING * 2)),
                24,
            ),
            colors=colors,
            align=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
        )
        painter.end()

    def _draw_hint_chip(self, painter: QPainter, *, text: str, rect, colors: dict[str, QColor], align=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter) -> None:
        metrics = painter.fontMetrics()
        text_width = max(72, metrics.horizontalAdvance(text) + 20)
        text_height = metrics.height() + 10

        if align & Qt.AlignmentFlag.AlignRight:
            chip_rect = rect.adjusted(max(0, rect.width() - text_width), 0, 0, 0)
        elif align & Qt.AlignmentFlag.AlignHCenter:
            x_offset = max(0, int((rect.width() - text_width) / 2))
            chip_rect = rect.adjusted(x_offset, 0, -x_offset, 0)
        else:
            chip_rect = rect.adjusted(0, 0, -(max(0, rect.width() - text_width)), 0)

        if align & Qt.AlignmentFlag.AlignBottom:
            chip_rect = chip_rect.adjusted(0, max(0, rect.height() - text_height), 0, 0)
        else:
            chip_rect = chip_rect.adjusted(0, 0, 0, -(max(0, rect.height() - text_height)))

        painter.setPen(QPen(colors["chip_border"], 1.0))
        painter.setBrush(colors["chip_fill"])
        painter.drawRoundedRect(chip_rect, 8, 8)
        painter.setPen(colors["text"])
        painter.drawText(chip_rect, Qt.AlignmentFlag.AlignCenter, text)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            top_left = event.globalPosition().toPoint() - self._drag_offset
            self._apply_frame_geometry(
                int(top_left.x()) + self._FRAME_SIDE_PADDING,
                int(top_left.y()) + self._TOP_HINT_HEIGHT,
                self._frame_size(),
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.prepare_preview()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta > 0:
            self._resize_selector(10)
        elif delta < 0:
            self._resize_selector(-10)
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.prepare_preview()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self._pending_selection_payload = None
        if self.preview_dialog is not None:
            self.preview_dialog.reject()
            self.preview_dialog = None
        super().closeEvent(event)
        if not self._confirmed:
            self.selection_cancelled.emit()


def _select_minimap_region(target_hwnd: int) -> dict[str, object]:
    _app, theme_manager = _ensure_qt_application()
    selector = MinimapMaskSelectorOverlay(theme_manager, target_hwnd=target_hwnd)

    selected_region: dict[str, object] = {}
    cancelled = {"value": False}
    loop = QEventLoop()

    def _handle_selected(region: dict[str, object]) -> None:
        try:
            selected_region.update(region)
        finally:
            loop.quit()

    def _handle_closed() -> None:
        cancelled["value"] = True
        loop.quit()

    selector.selection_confirmed.connect(_handle_selected)
    selector.selection_cancelled.connect(_handle_closed)
    selector.activate_selector_window()
    loop.exec()

    try:
        selector.selection_confirmed.disconnect(_handle_selected)
    except Exception:
        pass
    try:
        selector.selection_cancelled.disconnect(_handle_closed)
    except Exception:
        pass
    selector.deleteLater()

    if not selected_region:
        if cancelled["value"]:
            raise RuntimeError("已取消小地图校准")
        raise RuntimeError("未获取到有效的小地图区域")
    return selected_region


def run_selector_if_needed(force: bool = False) -> None:
    if not force and _has_valid_minimap_config():
        logger.info("[地图导航] 跳过小地图校准，沿用现有配置")
        return

    target_hwnd = _get_target_hwnd()
    bridge.report_status("等待小地图校准")
    region = _select_minimap_region(target_hwnd)
    config.save_minimap_region(region)
    bridge.report_status(
        f"小地图区域已更新: left={region['left']} top={region['top']} width={region['width']} height={region['height']}",
        payload={"minimap": dict(region)},
    )
    logger.info("[地图导航] 小地图校准完成: %s", region)


def _numpy_bgr_to_qimage(image_bgr: np.ndarray) -> QImage:
    if image_bgr.ndim == 3 and image_bgr.shape[2] >= 4:
        rgba = cv2.cvtColor(image_bgr[:, :, :4], cv2.COLOR_BGRA2RGBA)
        height, width = rgba.shape[:2]
        bytes_per_line = rgba.strides[0]
        return QImage(rgba.data, width, height, bytes_per_line, QImage.Format.Format_RGBA8888).copy()
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    height, width = rgb.shape[:2]
    bytes_per_line = rgb.strides[0]
    return QImage(rgb.data, width, height, bytes_per_line, QImage.Format.Format_RGB888).copy()


def _bgr_to_qcolor(color_value: tuple[int, int, int]) -> QColor:
    blue, green, red = color_value
    return QColor(int(red), int(green), int(blue))


class MapSelectorCanvas(QWidget):
    _MAX_SCALE = 4.0

    def __init__(
        self,
        display_map_bgr: np.ndarray,
        logic_map_shape: tuple[int, int],
        theme_manager,
        position_callback,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.position_callback = position_callback
        self.map_qimage = _numpy_bgr_to_qimage(display_map_bgr)
        self.map_width = int(display_map_bgr.shape[1])
        self.map_height = int(display_map_bgr.shape[0])
        logic_height, logic_width = logic_map_shape[:2]
        self.logic_width = max(1, int(logic_width))
        self.logic_height = max(1, int(logic_height))
        self.scale = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self._fit_scale = 1.0
        self._fill_scale = 1.0
        self.last_mouse_pos = None
        self._fit_initialized = False
        self.setMinimumSize(680, 460)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    def apply_theme(self) -> None:
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._fit_initialized:
            QTimer.singleShot(0, self.center_map)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._fit_initialized:
            QTimer.singleShot(0, self.center_map)

    def center_map(self) -> None:
        if self.width() <= 0 or self.height() <= 0:
            return
        self._fit_scale = min(
            self.width() / max(1, self.map_width),
            self.height() / max(1, self.map_height),
        )
        self._fill_scale = max(
            self.width() / max(1, self.map_width),
            self.height() / max(1, self.map_height),
        )
        self.scale = self._fill_scale
        self.offset_x = (self.width() - self.map_width * self.scale) / 2.0
        self.offset_y = (self.height() - self.map_height * self.scale) / 2.0
        self._fit_initialized = True
        self.update()

    def _widget_to_world(self, point: QPointF) -> tuple[float, float]:
        world_x = (point.x() - self.offset_x) / max(self.scale, 1e-6)
        world_y = (point.y() - self.offset_y) / max(self.scale, 1e-6)
        return world_x, world_y

    def _display_to_logic(self, world_x: float, world_y: float) -> tuple[int, int]:
        logic_x = float(world_x) * float(self.logic_width) / max(1.0, float(self.map_width))
        logic_y = float(world_y) * float(self.logic_height) / max(1.0, float(self.map_height))
        return (
            max(0, min(self.logic_width - 1, int(round(logic_x)))),
            max(0, min(self.logic_height - 1, int(round(logic_y)))),
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), self.theme_manager.get_qcolor("canvas"))
        painter.save()
        painter.translate(self.offset_x, self.offset_y)
        painter.scale(self.scale, self.scale)
        painter.drawImage(0, 0, self.map_qimage)
        painter.restore()
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.last_mouse_pos = event.position()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.last_mouse_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.position() - self.last_mouse_pos
            self.offset_x += delta.x()
            self.offset_y += delta.y()
            self.last_mouse_pos = event.position()
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.last_mouse_pos = None
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        delta_y = int(event.angleDelta().y())
        if delta_y == 0:
            event.accept()
            return
        factor = 1.12 if delta_y > 0 else 0.88
        min_scale = max(0.02, min(self._fit_scale, 0.2))
        new_scale = max(min_scale, min(self._MAX_SCALE, self.scale * factor))
        if abs(new_scale - self.scale) < 1e-6:
            event.accept()
            return
        mouse_pos = event.position()
        world_x, world_y = self._widget_to_world(mouse_pos)
        self.scale = new_scale
        self.offset_x = mouse_pos.x() - world_x * self.scale
        self.offset_y = mouse_pos.y() - world_y * self.scale
        self.update()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        world_x, world_y = self._widget_to_world(event.position())
        if 0 <= world_x < self.map_width and 0 <= world_y < self.map_height:
            logic_x, logic_y = self._display_to_logic(world_x, world_y)
            self.position_callback(logic_x, logic_y)
        event.accept()


class RoutePointActionDialog(QDialog):
    def __init__(
        self,
        route_mgr: RouteManager,
        theme_manager,
        entries: list[dict[str, object]],
        *,
        mode: str,
        world_x: int,
        world_y: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.route_mgr = route_mgr
        self.theme_manager = theme_manager
        self.entries = list(entries)
        self.mode = "remove" if str(mode or "").strip().lower() == "remove" else "add"
        self.selected_entry: dict[str, object] | None = None
        self.setObjectName("mapNavRoutePointActionDialog")
        self._section_frames: list[QFrame] = []

        action_text = "删除" if self.mode == "remove" else "添加"
        self.setWindowTitle(f"选择{action_text}资源点")
        self.resize(760, 700)
        self.setModal(True)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        if self.mode == "remove":
            tip_text = (
                f"当前位置 {world_x}, {world_y}。这里只显示当前位置附近可删除的资源点类型，"
                "选择后会删除距离最近的一项。"
            )
        else:
            tip_text = f"当前位置 {world_x}, {world_y}。选择资源点类型后，会立即在该位置添加资源点。"
        self.tip_label = QLabel(tip_text, self)
        self.tip_label.setObjectName("routePointActionTip")
        self.tip_label.setWordWrap(True)
        root_layout.addWidget(self.tip_label)

        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(8)
        root_layout.addLayout(toolbar_layout)

        self.summary_label = QLabel(self)
        self.summary_label.setObjectName("routePointActionSummaryLabel")
        toolbar_layout.addWidget(self.summary_label, 1)

        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.setMinimumWidth(88)
        self.cancel_button.setFixedHeight(30)
        self.cancel_button.clicked.connect(self.reject)
        toolbar_layout.addWidget(self.cancel_button)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("routePointActionScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        root_layout.addWidget(self.scroll_area, 1)

        self.scroll_content = QWidget(self.scroll_area)
        self.scroll_content.setObjectName("routePointActionScrollContent")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(10)
        self.scroll_area.setWidget(self.scroll_content)

        grouped_entries: dict[str, list[dict[str, object]]] = {}
        for entry in self.entries:
            category = str(entry.get("category") or "").strip()
            if not category:
                category = "_uncategorized"
            grouped_entries.setdefault(category, []).append(entry)

        for category in self.route_mgr.categories:
            category_entries = grouped_entries.pop(category, [])
            if not category_entries:
                continue
            self._build_category_section(category, category_entries)

        for category, category_entries in grouped_entries.items():
            if category_entries:
                self._build_category_section(category, category_entries)

        if not self.entries:
            empty_label = QLabel("当前没有可操作的资源点类型", self.scroll_content)
            empty_label.setObjectName("routePointActionEmptyLabel")
            self.scroll_layout.addWidget(empty_label)

        self.scroll_layout.addStretch(1)
        self._refresh_summary()
        self.apply_theme()

    def _build_category_section(self, category: str, entries: list[dict[str, object]]) -> None:
        section_frame = QFrame(self.scroll_content)
        section_frame.setProperty("tableCard", True)
        section_layout = QVBoxLayout(section_frame)
        section_layout.setContentsMargins(12, 10, 12, 12)
        section_layout.setSpacing(8)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        section_layout.addLayout(header_layout)

        title_label = QLabel(self.route_mgr.get_category_label(category), section_frame)
        title_label.setObjectName("routePointActionCategoryTitle")
        header_layout.addWidget(title_label)

        count_label = QLabel(f"{len(entries)} 种", section_frame)
        count_label.setObjectName("routePointActionCategoryCount")
        header_layout.addWidget(count_label)
        header_layout.addStretch(1)

        for entry in entries:
            route_name = str(entry.get("route_name") or entry.get("route_id") or "未命名资源点").strip()
            item_frame = QFrame(section_frame)
            item_layout = QVBoxLayout(item_frame)
            item_layout.setContentsMargins(0, 0, 0, 0)
            item_layout.setSpacing(4)

            action_button = QPushButton(route_name, item_frame)
            action_button.setMinimumHeight(32)
            action_button.clicked.connect(
                lambda _checked=False, payload=dict(entry): self._accept_entry(payload)
            )
            item_layout.addWidget(action_button)

            detail_text = str(entry.get("action_detail") or "").strip()
            if detail_text:
                detail_label = QLabel(detail_text, item_frame)
                detail_label.setObjectName("routePointActionDetailLabel")
                detail_label.setWordWrap(True)
                item_layout.addWidget(detail_label)

            section_layout.addWidget(item_frame)

        self._section_frames.append(section_frame)
        self.scroll_layout.addWidget(section_frame)

    def _accept_entry(self, entry: dict[str, object]) -> None:
        self.selected_entry = dict(entry)
        self.accept()

    def _refresh_summary(self) -> None:
        action_text = "可删除" if self.mode == "remove" else "可添加"
        self.summary_label.setText(
            f"{len(self.entries)} 种资源点类型{action_text}"
        )

    def apply_theme(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        for frame in self._section_frames:
            frame.style().unpolish(frame)
            frame.style().polish(frame)


class LockControlOverlay(QWidget):
    def __init__(self, theme_manager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.setObjectName("mapNavLockOverlay")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(0)

        self.lock_checkbox = QCheckBox("锁定", self)
        self.lock_checkbox.setObjectName("mapNavLockCheckbox")
        self.lock_checkbox.setChecked(True)
        self.lock_checkbox.setFixedHeight(25)
        root_layout.addWidget(self.lock_checkbox)

        self.apply_theme()

    def sync_checked(self, checked: bool) -> None:
        self.lock_checkbox.blockSignals(True)
        self.lock_checkbox.setChecked(bool(checked))
        self.lock_checkbox.blockSignals(False)

    def apply_theme(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        self.lock_checkbox.style().unpolish(self.lock_checkbox)
        self.lock_checkbox.style().polish(self.lock_checkbox)


class MapSelectorDialog(QDialog):
    def __init__(
        self,
        display_map_bgr: np.ndarray,
        logic_map_shape: tuple[int, int],
        theme_manager,
        position_callback,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.position_callback = position_callback
        self.setObjectName("mapNavMapSelectorDialog")
        self.setWindowTitle("地图定位")
        self.resize(1180, 860)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.tip_label = QLabel("操作：滚轮缩放，左键拖拽，双击地图确认当前位置。", self)
        self.tip_label.setObjectName("mapSelectorTip")
        self.tip_label.setWordWrap(True)
        self.tip_label.setVisible(False)
        self.setToolTip(self.tip_label.text())

        self.canvas = MapSelectorCanvas(
            display_map_bgr,
            logic_map_shape,
            self.theme_manager,
            self._handle_position_selected,
            self,
        )
        root_layout.addWidget(self.canvas, 1)
        self.apply_theme()

    def _handle_position_selected(self, x: int, y: int) -> None:
        self.position_callback(x, y)
        self.accept()

    def apply_theme(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        self.canvas.apply_theme()


class NavigationMapView(QWidget):
    def __init__(self, theme_manager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme_manager = theme_manager
        self._pixmap: Optional[QPixmap] = None
        self._transparent_canvas = False
        self.setMinimumSize(520, 320)

    def set_theme_manager(self, theme_manager) -> None:
        self.theme_manager = theme_manager
        self.update()

    def set_transparent_canvas(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._transparent_canvas == enabled:
            return
        self._transparent_canvas = enabled
        self.update()

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        if pixmap is None or pixmap.isNull():
            self._pixmap = None
        else:
            self._pixmap = QPixmap(pixmap)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        draw_rect = self.rect().adjusted(0, 0, -1, -1)
        clip_path = QPainterPath()
        clip_path.addRoundedRect(draw_rect, 6, 6)
        if not self._transparent_canvas:
            painter.fillPath(clip_path, self.theme_manager.get_qcolor("canvas"))

        if self._pixmap is not None and not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                draw_rect.size(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.save()
            painter.setClipPath(clip_path)
            painter.drawPixmap(draw_rect, scaled)
            painter.restore()

        if not self._transparent_canvas:
            painter.setPen(QPen(self.theme_manager.get_qcolor("border_light"), 1))
            painter.drawPath(clip_path)
        painter.end()


class RadarMainWindow(QMainWindow):
    _MENU_ISLAND_HEIGHT = 45
    _MENU_ISLAND_BUTTON_HEIGHT = 32
    _MENU_ISLAND_LAYOUT_MARGINS = (8, 4, 8, 4)
    _DISPLAY_STATUS_FAILURE = "定位失败"
    _DISPLAY_STATUS_SUCCESS = "定位成功"
    _DISPLAY_STATUS_PAUSED = "暂停定位"
    _STATUS_LOG_MAX_ENTRIES = 240

    def __init__(self, theme_manager) -> None:
        super().__init__()
        self.theme_manager = theme_manager
        self.logic_map_bgr = cv2.imread(config.LOGIC_MAP_PATH)
        self.display_map_bgr = cv2.imread(config.DISPLAY_MAP_PATH)
        if self.logic_map_bgr is None:
            raise FileNotFoundError(f"找不到逻辑地图: {config.LOGIC_MAP_PATH}")
        if self.display_map_bgr is None:
            self.display_map_bgr = self.logic_map_bgr.copy()

        self.map_height, self.map_width = self.logic_map_bgr.shape[:2]
        self.state = "MANUAL_RELOCATE"
        self.target_x = float(self.map_width // 2)
        self.target_y = float(self.map_height // 2)
        self.player_x = float(self.target_x)
        self.player_y = float(self.target_y)
        self.camera_x = float(self.target_x)
        self.camera_y = float(self.target_y)
        self.base_search_radius = float(max(64, _coerce_int(config.AI_TRACK_RADIUS, 100)))
        self.current_search_radius = float(self.base_search_radius)
        self.lost_frames = 0
        self.max_lost_frames = max(4, _coerce_int(config.MAX_LOST_FRAMES, 50))
        self.selector_dialog: Optional[MapSelectorDialog] = None
        self.latest_display_crop: Optional[np.ndarray] = None
        self._latest_base_display_crop: Optional[np.ndarray] = None
        self._latest_resource_display_crop: Optional[np.ndarray] = None
        self._latest_base_frame_version = 0
        self._latest_resource_frame_base_version = 0
        self._latest_resource_frame_at = 0.0
        self._pending_status = ""
        self._last_reported_status = ""
        self._status_chip_text = ""
        self._status_chip_until = 0.0
        self._status_log_entries = deque(maxlen=self._STATUS_LOG_MAX_ENTRIES)
        self._last_match_debug = ""
        self._last_debug_snapshot_at = 0.0
        self._has_located_once = False
        self._tracking_paused = False
        self._tracking_pause_reason = ""
        self._hide_tracking_base_map = False
        self._small_motion_frames = 0
        self._small_motion_candidate: Optional[tuple[float, float]] = None
        self._attachment_mode = "window" if _get_target_hwnd() > 0 else "desktop"
        self._pending_selector_open = False
        self._position_initialized = False
        self._attachment_anchor_dirty = True
        self._last_attachment_sync_at = 0.0
        self._attachment_sync_interval = 0.08
        self._last_window_attachment_signature: Optional[tuple[object, ...]] = None
        self._last_effective_attachment_mode = self._attachment_mode
        self.is_running = True
        self.lock = threading.Lock()
        self._resource_render_lock = threading.Lock()
        self._resource_render_event = threading.Event()
        self._resource_render_request_seq = 0
        self._latest_tracking_frame_state: Optional[dict[str, object]] = None
        self._pending_resource_render_state: Optional[dict[str, object]] = None
        self._last_resource_render_request_at = 0.0
        self._last_resource_render_request_signature: Optional[tuple[float, float, float, float, int]] = None
        self._guidance_target_cache: tuple[str | None, int | None, float | None, dict | None] = (None, None, None, None)
        self._guidance_target_cache_at = 0.0
        self._guidance_target_cache_position: Optional[tuple[int, int]] = None
        self._drag_window_offset: Optional[QPoint] = None
        self.lock_overlay: Optional[LockControlOverlay] = None
        self._lock_hotkey_text = "F8"
        self._lock_hotkey_id = 0x14C1
        self._lock_hotkey_vk = 0x77
        self._lock_hotkey_registered = False
        self.view_w = max(420, _coerce_int(config.VIEW_SIZE, 480))
        self.view_h = max(280, _coerce_int(config.VIEW_SIZE, 480))
        self.engine = LoftrEngine()
        self.route_mgr = RouteManager(os.path.join(config.BASE_DIR, "routes"))
        self._restore_resource_visibility_settings()

        self.setWindowTitle("AI导航")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(760, 470)
        self.setWindowOpacity(1.0)
        self.setMinimumSize(560, 360)

        central = QWidget(self)
        central.setObjectName("mapNavCentral")
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(12, 10, 12, 12)
        root_layout.setSpacing(10)

        self.menu_island_frame = QFrame(self)
        self.menu_island_frame.setObjectName("mapNavMenuIsland")
        self.menu_island_frame.setProperty("tableCard", True)
        self.menu_island_frame.setFixedHeight(self._MENU_ISLAND_HEIGHT)
        self.menu_island_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.menu_island_frame.setGraphicsEffect(_create_panel_shadow(self.menu_island_frame))
        menu_island_layout = QHBoxLayout(self.menu_island_frame)
        menu_island_layout.setContentsMargins(*self._MENU_ISLAND_LAYOUT_MARGINS)
        menu_island_layout.setSpacing(8)
        root_layout.addWidget(self.menu_island_frame)

        self.info_panel = QWidget(self.menu_island_frame)
        info_layout = QHBoxLayout(self.info_panel)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(8)

        self.status_info_label = QLabel(self._DISPLAY_STATUS_FAILURE, self.info_panel)
        self.status_info_label.setObjectName("floatingStatusLabel")
        info_layout.addWidget(self.status_info_label)

        self.coordinate_info_label = QLabel("--, --", self.info_panel)
        self.coordinate_info_label.setObjectName("floatingHotkeyLabel")
        info_layout.addWidget(self.coordinate_info_label)

        menu_island_layout.addWidget(self.info_panel, 1)

        self.menu_button = QPushButton("菜单", self.menu_island_frame)
        self.menu_button.setMinimumWidth(72)
        self.menu_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.menu_button.setFixedHeight(self._MENU_ISLAND_BUTTON_HEIGHT)
        self.menu_button.clicked.connect(self._show_main_menu)
        menu_island_layout.addWidget(self.menu_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.add_route_button = QPushButton("添加", self.menu_island_frame)
        self.add_route_button.setMinimumWidth(72)
        self.add_route_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.add_route_button.setFixedHeight(self._MENU_ISLAND_BUTTON_HEIGHT)
        self.add_route_button.clicked.connect(self.open_add_route_dialog)
        menu_island_layout.addWidget(self.add_route_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.remove_route_button = QPushButton("删除", self.menu_island_frame)
        self.remove_route_button.setMinimumWidth(72)
        self.remove_route_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.remove_route_button.setFixedHeight(self._MENU_ISLAND_BUTTON_HEIGHT)
        self.remove_route_button.clicked.connect(self.open_remove_route_dialog)
        menu_island_layout.addWidget(self.remove_route_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.opacity_label = QLabel("透明", self.menu_island_frame)
        self.opacity_label.setObjectName("floatingHotkeyLabel")
        menu_island_layout.addWidget(self.opacity_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal, self.menu_island_frame)
        self.opacity_slider.setObjectName("mapNavOpacitySlider")
        self.opacity_slider.setRange(10, 100)
        self.opacity_slider.setSingleStep(5)
        self.opacity_slider.setPageStep(10)
        self.opacity_slider.setFixedWidth(88)
        self.opacity_slider.setFixedHeight(18)
        self.opacity_slider.setValue(100)
        self.opacity_slider.valueChanged.connect(self._update_opacity)
        menu_island_layout.addWidget(self.opacity_slider, 0, Qt.AlignmentFlag.AlignVCenter)

        self.opacity_value_label = QLabel("100%", self.menu_island_frame)
        self.opacity_value_label.setObjectName("floatingHotkeyLabel")
        self.opacity_value_label.setMinimumWidth(40)
        menu_island_layout.addWidget(self.opacity_value_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.lock_checkbox = QCheckBox("锁定", self.menu_island_frame)
        self.lock_checkbox.setObjectName("mapNavLockCheckbox")
        self.lock_checkbox.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.lock_checkbox.setFixedHeight(self._MENU_ISLAND_BUTTON_HEIGHT)
        self.lock_checkbox.toggled.connect(self.toggle_lock)
        menu_island_layout.addWidget(self.lock_checkbox, 0, Qt.AlignmentFlag.AlignVCenter)

        self.map_card_frame = QFrame(self)
        self.map_card_frame.setObjectName("mapNavMapCard")
        self.map_card_frame.setProperty("tableCard", True)
        self.map_card_frame.setGraphicsEffect(_create_panel_shadow(self.map_card_frame))
        map_card_layout = QVBoxLayout(self.map_card_frame)
        map_card_layout.setContentsMargins(6, 6, 6, 6)
        map_card_layout.setSpacing(0)
        root_layout.addWidget(self.map_card_frame, 1)

        self.view_label = NavigationMapView(self.theme_manager, self.map_card_frame)
        self.view_label.setObjectName("mapNavView")
        map_card_layout.addWidget(self.view_label, 1)

        self.menu_island_frame.installEventFilter(self)
        self.info_panel.installEventFilter(self)
        self.status_info_label.installEventFilter(self)
        self.coordinate_info_label.installEventFilter(self)
        self.map_card_frame.installEventFilter(self)
        self.view_label.installEventFilter(self)

        self.theme_manager.register_theme_change_callback(self.on_theme_changed)
        self.apply_theme()
        self.lock_shortcut = QShortcut(QKeySequence(self._lock_hotkey_text), self)
        self.lock_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.lock_shortcut.activated.connect(self._toggle_lock_from_shortcut)
        self._sync_opacity_controls()
        self._sync_locked_render_mode()
        self._update_lock_checkbox_tooltip()
        self._register_lock_hotkey()
        self._sync_route_controls()
        self._report_status("等待定位")

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.render_frame)
        self.ui_timer.start(max(15, _coerce_int(config.AI_REFRESH_RATE, 50)))

        self.worker_thread = threading.Thread(target=self.ai_worker_loop, daemon=True, name="MapNavigationUiWorker")
        self.worker_thread.start()
        self.resource_render_thread = threading.Thread(
            target=self.resource_render_loop,
            daemon=True,
            name="MapNavigationResourceRender",
        )
        self.resource_render_thread.start()
        QTimer.singleShot(160, self.open_selector_dialog)

    def _collect_route_entries(self) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for category in self.route_mgr.categories:
            category_label = self.route_mgr.get_category_label(category)
            for route in self.route_mgr.route_groups.get(category, []):
                route_id = self.route_mgr._route_id_for(category, route)
                if not route_id:
                    continue
                route_name = str(route.get("display_name") or route_id).strip() or route_id
                points = route.get("points", [])
                point_count = len(points) if isinstance(points, list) else 0
                entries.append(
                    {
                        "route_id": route_id,
                        "route_name": route_name,
                        "category": category,
                        "category_label": category_label,
                        "point_count": int(point_count),
                        "menu_label": f"{route_name}",
                        "tooltip_label": f"{category_label} / {route_name}",
                        "action_detail": f"{category_label} · 当前 {point_count} 个资源点",
                    }
                )
        entries.sort(key=lambda item: (item["category_label"], item["route_name"]))
        return entries

    def _current_world_position(self) -> Optional[tuple[int, int]]:
        if self.state != "LOCAL_TRACK" or self._tracking_paused:
            return None
        return int(round(self.target_x)), int(round(self.target_y))

    def _reset_small_motion_filter(self) -> None:
        self._small_motion_frames = 0
        self._small_motion_candidate = None

    def _should_accept_position_update(self, matched_x: float, matched_y: float, *, strong_match: bool) -> bool:
        hold_pixels = max(0.4, _coerce_float(config.AI_POSITION_HOLD_PIXELS, 0.85))
        still_trigger = max(hold_pixels, _coerce_float(config.AI_STILL_TRIGGER_PIXELS, 1.2))
        jitter_guard = max(still_trigger, _coerce_float(config.AI_JITTER_GUARD_PIXELS, 1.8))
        large_jump_guard = max(
            jitter_guard,
            _coerce_float(config.AI_STEP_LIMIT_MAX_PIXELS, 30.0),
        )
        delta_x = abs(float(matched_x) - float(self.target_x))
        delta_y = abs(float(matched_y) - float(self.target_y))
        axis_pixel_deadband = 1
        rounded_delta_x = abs(int(round(float(matched_x))) - int(round(float(self.target_x))))
        rounded_delta_y = abs(int(round(float(matched_y))) - int(round(float(self.target_y))))

        if not strong_match and rounded_delta_x <= axis_pixel_deadband and rounded_delta_y <= axis_pixel_deadband:
            self._reset_small_motion_filter()
            return False

        if delta_x <= hold_pixels and delta_y <= hold_pixels:
            self._reset_small_motion_filter()
            return False

        if strong_match:
            self._reset_small_motion_filter()
            return True

        if delta_x <= large_jump_guard and delta_y <= large_jump_guard:
            self._reset_small_motion_filter()
            return True
        else:
            candidate = self._small_motion_candidate
            stable_tolerance = max(large_jump_guard * 0.35, 8.0)
            required_frames = max(2, _coerce_int(config.AI_LARGE_OFFSET_CONFIRM_FRAMES, 4))
            if (
                candidate is None
                or abs(float(matched_x) - float(candidate[0])) > stable_tolerance
                or abs(float(matched_y) - float(candidate[1])) > stable_tolerance
            ):
                self._small_motion_candidate = (float(matched_x), float(matched_y))
                self._small_motion_frames = 1
                return False

            self._small_motion_frames = int(self._small_motion_frames) + 1
            if self._small_motion_frames < required_frames:
                return False

            self._reset_small_motion_filter()
            return True

    def _set_tracking_pause_state(self, paused: bool, reason: str = "") -> None:
        if paused:
            self._tracking_paused = True
            self._tracking_pause_reason = str(reason or "").strip()
            self._reset_small_motion_filter()
            return
        self._tracking_paused = False
        self._tracking_pause_reason = ""

    def _find_nearest_route_point(
        self,
        route_ref: str | None,
        world_x: int | float,
        world_y: int | float,
        *,
        tolerance: float = 28.0,
        require_position_inside: bool = False,
    ) -> Optional[dict[str, object]]:
        route_id, _route_name, route = self.route_mgr.resolve_route(route_ref)
        if route is None:
            return None

        points = route.get("points", [])
        if not isinstance(points, list) or not points:
            return None

        target_x = float(world_x)
        target_y = float(world_y)
        base_tolerance = max(12.0, float(tolerance))
        best_key = None
        best_entry = None
        for point_index, point in enumerate(points):
            if not isinstance(point, dict):
                continue
            point_x = float(point.get("x", 0.0))
            point_y = float(point.get("y", 0.0))
            point_radius = float(
                max(0, _coerce_int(point.get("radius", route.get("point_radius", 0)), 0))
            )
            distance = float(np.hypot(point_x - target_x, point_y - target_y))
            if require_position_inside:
                threshold = point_radius if point_radius > 0 else 0.5
            else:
                threshold = max(base_tolerance, point_radius)
            if distance > threshold:
                continue

            candidate_key = (round(distance, 4), int(point_index))
            if best_key is not None and candidate_key >= best_key:
                continue
            best_key = candidate_key
            point_label = str(point.get("label") or point.get("title") or "").strip()
            best_entry = {
                "route_id": route_id,
                "point_index": int(point_index),
                "point": point,
                "point_label": point_label,
                "distance": distance,
                "world_x": point_x,
                "world_y": point_y,
            }
        return best_entry

    def _collect_removable_route_entries(self, world_x: int, world_y: int) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for entry in self._collect_route_entries():
            nearest_point = self._find_nearest_route_point(
                entry.get("route_id"),
                world_x,
                world_y,
                require_position_inside=True,
            )
            if nearest_point is None:
                continue
            point_label = str(nearest_point.get("point_label") or "").strip()
            point_x = int(round(float(nearest_point.get("world_x", 0.0))))
            point_y = int(round(float(nearest_point.get("world_y", 0.0))))
            distance = int(round(float(nearest_point.get("distance", 0.0))))
            point_hint = f"{point_x}, {point_y}"
            if point_label:
                point_hint = f"{point_label} · {point_hint}"
            removable_entry = dict(entry)
            removable_entry.update(nearest_point)
            removable_entry["action_detail"] = f"{entry['category_label']} · 所在点 {point_hint} · 距当前位置 {distance}"
            entries.append(removable_entry)
        entries.sort(key=lambda item: (float(item.get("distance", 0.0)), item["category_label"], item["route_name"]))
        return entries

    def _sync_route_controls(self) -> None:
        entries = self._collect_route_entries()
        total_entries = len(entries)
        current_position = self._current_world_position()

        if not total_entries:
            add_tip = "当前没有可添加的资源点类型"
            remove_tip = "当前没有可删除的资源点类型"
        elif current_position is None:
            add_tip = f"已加载 {len(self.route_mgr.categories)} 类 / 共 {total_entries} 种资源点，需先完成定位后才能添加"
            remove_tip = f"已加载 {len(self.route_mgr.categories)} 类 / 共 {total_entries} 种资源点，需先完成定位后才能删除"
        else:
            position_text = f"{current_position[0]}, {current_position[1]}"
            add_tip = (
                f"当前位置 {position_text}，点击后选择资源点类型并立即添加"
                f"；已加载 {len(self.route_mgr.categories)} 类 / 共 {total_entries} 种资源点"
            )
            remove_tip = f"当前位置 {position_text}，只能删除当前位置所在的资源点"
        controls_enabled = bool(total_entries) and current_position is not None
        self.add_route_button.setEnabled(controls_enabled)
        self.add_route_button.setToolTip(add_tip)
        self.remove_route_button.setEnabled(controls_enabled)
        self.remove_route_button.setToolTip(remove_tip)
        self._update_menu_button_tooltip()

    def _visible_route_summary(self) -> tuple[int, int]:
        total_routes = len(self._collect_route_entries())
        visible_routes = int(self.route_mgr.get_visible_route_count())
        return visible_routes, total_routes

    def _menu_status_summary(self) -> str:
        if self._tracking_paused:
            return self._DISPLAY_STATUS_PAUSED
        if self.state == "LOCAL_TRACK":
            if self.lost_frames > 0:
                return self._DISPLAY_STATUS_FAILURE
            return self._DISPLAY_STATUS_SUCCESS
        return self._DISPLAY_STATUS_FAILURE

    def _update_menu_button_tooltip(self) -> None:
        visible_route_count, total_route_count = self._visible_route_summary()
        tooltip = " | ".join(
            part
            for part in (
                self._build_anchor_text(),
                self._menu_status_summary(),
                "穿透已锁定" if self.lock_checkbox.isChecked() else "",
                f"资源点 {visible_route_count}/{total_route_count}",
            )
            if part
        )
        self.menu_button.setToolTip(tooltip or "打开导航菜单")

    def _refresh_route_visibility_preview(self) -> None:
        self._invalidate_guidance_target_cache()
        if self.state == "LOCAL_TRACK":
            self._request_resource_render(force=True)
        self.render_frame()

    def _restore_resource_visibility_settings(self) -> None:
        try:
            visibility_state = getattr(config, "RESOURCE_VISIBILITY", None)
            self.route_mgr.apply_visibility_state(visibility_state, default_visible=False)
        except Exception as exc:
            logger.warning("[地图导航] 加载资源点显示配置失败，回退为全部隐藏: %s", exc)
            self.route_mgr.set_all_routes_visible(False)

    def _persist_resource_visibility_settings(self) -> None:
        try:
            config.save_resource_visibility(self.route_mgr.get_visibility_state())
        except Exception as exc:
            logger.warning("[地图导航] 保存资源点显示配置失败: %s", exc)

    def _set_all_route_visibility(self, visible: bool) -> None:
        changed = self.route_mgr.set_all_routes_visible(visible)
        visible_route_count, total_route_count = self._visible_route_summary()
        detail = (
            f"已显示全部 {total_route_count} 种资源点"
            if visible
            else f"已隐藏全部 {total_route_count} 种资源点"
        )
        if changed:
            self._persist_resource_visibility_settings()
            self._refresh_route_visibility_preview()
        self._set_status(f"{detail} · 当前可见 {visible_route_count}/{total_route_count}")

    def _set_category_visibility(self, category: str, visible: bool) -> None:
        changed = self.route_mgr.set_category_visible(category, visible)
        category_label = self.route_mgr.get_category_label(category)
        visible_count, total_count = self.route_mgr.get_category_visibility_summary(category)
        detail = (
            f"{category_label} 已显示"
            if visible
            else f"{category_label} 已隐藏"
        )
        if changed:
            self._persist_resource_visibility_settings()
            self._refresh_route_visibility_preview()
        self._set_status(f"{detail} · 当前可见 {visible_count}/{total_count}")

    def _set_route_visibility(self, route_ref: str, visible: bool) -> None:
        changed = self.route_mgr.set_route_visible(route_ref, visible)
        route_name = self._route_display_name(route_ref)
        visible_route_count, total_route_count = self._visible_route_summary()
        detail = f"{route_name} 已显示" if visible else f"{route_name} 已隐藏"
        if changed:
            self._persist_resource_visibility_settings()
            self._refresh_route_visibility_preview()
        self._set_status(f"{detail} · 当前可见 {visible_route_count}/{total_route_count}")

    def _set_lock_checkbox_checked(self, checked: bool) -> None:
        checked = bool(checked)
        if self.lock_checkbox.isChecked() == checked:
            self._sync_locked_render_mode()
            return
        self.lock_checkbox.blockSignals(True)
        self.lock_checkbox.setChecked(checked)
        self.lock_checkbox.blockSignals(False)
        self._sync_locked_render_mode()

    def _update_lock_checkbox_tooltip(self) -> None:
        if self.lock_checkbox.isChecked():
            self.lock_checkbox.setToolTip(f"当前窗口已开启鼠标穿透，顶部锁定控件可点击，按 {self._lock_hotkey_text} 也可解锁")
        else:
            self.lock_checkbox.setToolTip(f"开启后整个导航窗口进入鼠标穿透，顶部保留锁定控件可点击，按 {self._lock_hotkey_text} 可切换")

    def _current_opacity_percent(self) -> int:
        return max(10, min(100, int(self.opacity_slider.value())))

    def _sync_opacity_controls(self) -> None:
        opacity = self._current_opacity_percent()
        self.opacity_slider.setToolTip(f"当前窗口透明度 {opacity}%")
        self.opacity_value_label.setText(f"{opacity}%")

    def _sync_locked_render_mode(self) -> None:
        hide_base_map = bool(self.lock_checkbox.isChecked())
        if bool(self._hide_tracking_base_map) != hide_base_map:
            with self.lock:
                self._latest_base_display_crop = None
                self._latest_resource_display_crop = None
                self.latest_display_crop = None
        self._hide_tracking_base_map = hide_base_map
        self.view_label.set_transparent_canvas(hide_base_map)
        self.map_card_frame.setProperty("mapBaseHidden", hide_base_map)
        card_effect = self.map_card_frame.graphicsEffect()
        if card_effect is not None:
            card_effect.setEnabled(not hide_base_map)
        self.map_card_frame.style().unpolish(self.map_card_frame)
        self.map_card_frame.style().polish(self.map_card_frame)
        if self.state == "LOCAL_TRACK":
            self._request_resource_render(force=True)

    def _toggle_lock_from_shortcut(self) -> None:
        self.lock_checkbox.setChecked(not self.lock_checkbox.isChecked())

    def _ensure_lock_overlay(self) -> LockControlOverlay:
        if self.lock_overlay is None:
            self.lock_overlay = LockControlOverlay(self.theme_manager, self)
            self.lock_overlay.lock_checkbox.toggled.connect(self.lock_checkbox.setChecked)
        return self.lock_overlay

    def _sync_lock_overlay_geometry(self) -> None:
        if self.lock_overlay is None:
            return
        rect = self.lock_checkbox.rect()
        top_left = self.lock_checkbox.mapToGlobal(rect.topLeft())
        self.lock_overlay.setGeometry(
            int(top_left.x()) - 4,
            int(top_left.y()) - 4,
            int(rect.width()) + 8,
            int(rect.height()) + 8,
        )

    def _show_lock_overlay(self) -> None:
        overlay = self._ensure_lock_overlay()
        overlay.sync_checked(True)
        overlay.apply_theme()
        self._sync_lock_overlay_geometry()
        overlay.show()
        overlay.raise_()

    def _hide_lock_overlay(self) -> None:
        if self.lock_overlay is not None:
            self.lock_overlay.hide()

    def _register_lock_hotkey(self) -> None:
        if os.name != "nt" or self._lock_hotkey_registered:
            return
        try:
            if ctypes.windll.user32.RegisterHotKey(None, self._lock_hotkey_id, 0, int(self._lock_hotkey_vk)):
                self._lock_hotkey_registered = True
            else:
                error_code = int(ctypes.windll.kernel32.GetLastError())
                logger.warning("[地图导航] 注册锁定热键失败: %s", error_code)
        except Exception as exc:
            logger.warning("[地图导航] 注册锁定热键异常: %s", exc)

    def _unregister_lock_hotkey(self) -> None:
        if os.name != "nt" or not self._lock_hotkey_registered:
            return
        try:
            ctypes.windll.user32.UnregisterHotKey(None, self._lock_hotkey_id)
        except Exception as exc:
            logger.warning("[地图导航] 注销锁定热键失败: %s", exc)
        finally:
            self._lock_hotkey_registered = False

    def open_add_route_dialog(self) -> None:
        self._open_add_route_point_menu()

    def open_remove_route_dialog(self) -> None:
        self._open_remove_route_point_menu()

    def _open_add_route_point_menu(self) -> None:
        current_position = self._current_world_position()
        if current_position is None:
            self._set_status("请先完成定位后再添加资源点")
            return

        world_x, world_y = current_position
        entries = self._collect_route_entries()
        if not entries:
            self._set_status("当前没有可添加的资源点类型")
            return

        menu = apply_unified_menu_style(QMenu(self), frameless=True)
        header_action = menu.addAction(f"添加到 {world_x}, {world_y}")
        header_action.setEnabled(False)
        menu.addSeparator()

        grouped_entries: dict[str, list[dict[str, object]]] = {}
        for entry in entries:
            category = str(entry.get("category") or "").strip() or "_uncategorized"
            grouped_entries.setdefault(category, []).append(entry)

        def _add_category_menu(category: str, category_entries: list[dict[str, object]]) -> None:
            if not category_entries:
                return
            category_label = self.route_mgr.get_category_label(category)
            category_menu = apply_unified_menu_style(
                QMenu(f"{category_label} ({len(category_entries)} 种)", menu),
                frameless=True,
            )
            menu.addMenu(category_menu)
            for entry in sorted(category_entries, key=lambda item: str(item.get("route_name") or "")):
                route_name = str(entry.get("route_name") or entry.get("route_id") or "未命名资源点").strip()
                action = category_menu.addAction(route_name)
                detail_text = str(entry.get("action_detail") or "").strip()
                if detail_text:
                    action.setToolTip(detail_text)
                action.triggered.connect(
                    lambda _checked=False, payload=dict(entry), x=world_x, y=world_y: (
                        self._add_route_point_at_current_position(payload, x, y)
                    )
                )

        for category in self.route_mgr.categories:
            _add_category_menu(category, grouped_entries.pop(category, []))

        for category, category_entries in grouped_entries.items():
            _add_category_menu(category, category_entries)

        menu.exec(self.add_route_button.mapToGlobal(QPoint(0, self.add_route_button.height() + 8)))

    def _open_remove_route_point_menu(self) -> None:
        current_position = self._current_world_position()
        if current_position is None:
            self._set_status("请先完成定位后再删除资源点")
            return

        world_x, world_y = current_position
        entries = self._collect_removable_route_entries(world_x, world_y)
        if not entries:
            self._set_status("自身位置没有资源点，不能删除")
            return

        menu = apply_unified_menu_style(QMenu(self), frameless=True)
        header_action = menu.addAction(f"删除当前位置资源点 {world_x}, {world_y}")
        header_action.setEnabled(False)
        menu.addSeparator()

        grouped_entries: dict[str, list[dict[str, object]]] = {}
        for entry in entries:
            category = str(entry.get("category") or "").strip() or "_uncategorized"
            grouped_entries.setdefault(category, []).append(entry)

        def _add_category_menu(category: str, category_entries: list[dict[str, object]]) -> None:
            if not category_entries:
                return
            category_label = self.route_mgr.get_category_label(category)
            category_menu = apply_unified_menu_style(
                QMenu(f"{category_label} ({len(category_entries)} 项)", menu),
                frameless=True,
            )
            menu.addMenu(category_menu)
            for entry in sorted(
                category_entries,
                key=lambda item: (float(item.get("distance", 0.0)), str(item.get("route_name") or "")),
            ):
                route_name = str(entry.get("route_name") or entry.get("route_id") or "未命名资源点").strip()
                distance = int(round(float(entry.get("distance", 0.0))))
                action_label = f"{route_name} · {distance}"
                action = category_menu.addAction(action_label)
                detail_text = str(entry.get("action_detail") or "").strip()
                if detail_text:
                    action.setToolTip(detail_text)
                action.triggered.connect(
                    lambda _checked=False, payload=dict(entry): (
                        self._remove_route_point_at_current_position(payload)
                    )
                )

        for category in self.route_mgr.categories:
            _add_category_menu(category, grouped_entries.pop(category, []))

        for category, category_entries in grouped_entries.items():
            _add_category_menu(category, category_entries)

        menu.exec(self.remove_route_button.mapToGlobal(QPoint(0, self.remove_route_button.height() + 8)))

    def _open_route_point_action_dialog(self, mode: str) -> None:
        current_position = self._current_world_position()
        if current_position is None:
            self._set_status("请先完成定位后再添加或删除资源点")
            return

        world_x, world_y = current_position
        if str(mode or "").strip().lower() == "remove":
            entries = self._collect_removable_route_entries(world_x, world_y)
            if not entries:
                self._set_status("自身位置没有资源点，不能删除")
                return
        else:
            entries = self._collect_route_entries()
            if not entries:
                self._set_status("当前没有可添加的资源点类型")
                return

        dialog = RoutePointActionDialog(
            self.route_mgr,
            self.theme_manager,
            entries,
            mode=mode,
            world_x=world_x,
            world_y=world_y,
            parent=self,
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted) or dialog.selected_entry is None:
            return

        if str(mode or "").strip().lower() == "remove":
            self._remove_route_point_at_current_position(dialog.selected_entry)
        else:
            self._add_route_point_at_current_position(dialog.selected_entry, world_x, world_y)

    def _add_route_point_at_current_position(self, entry: dict[str, object], world_x: int, world_y: int) -> None:
        route_id = str(entry.get("route_id") or "").strip()
        route_name = str(entry.get("route_name") or route_id or "资源点").strip() or "资源点"
        if not route_id:
            self._set_status("未找到目标资源点类型")
            return

        point_label = f"{route_name} {world_x}, {world_y}"
        add_result = self.route_mgr.add_point_to_route(
            route_id,
            world_x,
            world_y,
            point_overrides={
                "label": point_label,
                "title": point_label,
            },
        )
        if add_result is None:
            self._set_status(f"添加 {route_name} 失败")
            return

        saved, detail = self.route_mgr.save_route(route_id)
        if not saved:
            self.route_mgr.remove_point_from_route(route_id, int(add_result.get("point_index", -1)))
            self._handle_route_data_changed()
            self._set_status(f"添加 {route_name} 失败：{detail}")
            return

        self._handle_route_data_changed()
        self._set_status(f"已添加 {route_name}：{world_x}, {world_y}")

    def _restore_removed_route_point(self, route_ref: str, removed_entry: dict[str, object]) -> None:
        _route_id, _route_name, route = self.route_mgr.resolve_route(route_ref)
        if route is None:
            return

        points = route.get("points")
        if not isinstance(points, list):
            points = []
            route["points"] = points

        point_payload = removed_entry.get("point")
        if not isinstance(point_payload, dict):
            return

        point_index = max(0, min(len(points), _coerce_int(removed_entry.get("point_index"), len(points))))
        points.insert(point_index, point_payload)
        route["_point_bounds"] = self.route_mgr._compute_route_bounds(points)
        self.route_mgr._invalidate_dynamic_plan_cache()

    def _remove_route_point_at_current_position(self, entry: dict[str, object]) -> None:
        route_id = str(entry.get("route_id") or "").strip()
        route_name = str(entry.get("route_name") or route_id or "资源点").strip() or "资源点"
        if not route_id:
            self._set_status("未找到目标资源点类型")
            return

        point_index = _coerce_int(entry.get("point_index"), -1)
        if point_index < 0:
            self._set_status(f"{route_name} 缺少可删除的资源点")
            return

        removed_entry = self.route_mgr.remove_point_from_route(route_id, point_index)
        if removed_entry is None:
            self._set_status(f"删除 {route_name} 失败")
            return

        saved, detail = self.route_mgr.save_route(route_id)
        if not saved:
            self._restore_removed_route_point(route_id, removed_entry)
            self._handle_route_data_changed()
            self._set_status(f"删除 {route_name} 失败：{detail}")
            return

        removed_point = removed_entry.get("point") if isinstance(removed_entry, dict) else None
        removed_x = int(round(float(removed_point.get("x", 0.0)))) if isinstance(removed_point, dict) else 0
        removed_y = int(round(float(removed_point.get("y", 0.0)))) if isinstance(removed_point, dict) else 0
        self._handle_route_data_changed()
        self._set_status(f"已删除 {route_name}：{removed_x}, {removed_y}")

    def _handle_route_data_changed(self) -> None:
        self._sync_route_controls()
        self._invalidate_guidance_target_cache()
        if self.state == "LOCAL_TRACK":
            self._request_resource_render(force=True)

    def _show_main_menu(self) -> None:
        menu = apply_unified_menu_style(QMenu(self), frameless=True)
        effective_attachment_mode = self._effective_attachment_mode()
        visible_route_count, total_route_count = self._visible_route_summary()

        relocate_action = menu.addAction("重新定位")
        relocate_action.triggered.connect(self.open_selector_dialog)

        attach_window_action = menu.addAction("吸附窗口")
        attach_window_action.setCheckable(True)
        attach_window_action.setChecked(effective_attachment_mode == "window")
        attach_window_action.setEnabled(_get_target_hwnd() > 0)
        attach_window_action.triggered.connect(lambda _checked=False: self._set_attachment_mode("window"))

        attach_desktop_action = menu.addAction("吸附桌面")
        attach_desktop_action.setCheckable(True)
        attach_desktop_action.setChecked(effective_attachment_mode == "desktop")
        attach_desktop_action.triggered.connect(lambda _checked=False: self._set_attachment_mode("desktop"))

        menu.addSeparator()

        resource_menu = apply_unified_menu_style(
            PersistentCheckMenu(f"资源点显示 ({visible_route_count}/{total_route_count})", menu),
            frameless=True,
        )
        menu.addMenu(resource_menu)
        if total_route_count <= 0:
            empty_action = resource_menu.addAction("当前没有可显示的资源点类型")
            empty_action.setEnabled(False)
        else:
            show_all_action = resource_menu.addAction("全部显示")
            show_all_action.setEnabled(visible_route_count < total_route_count)
            show_all_action.triggered.connect(lambda _checked=False: self._set_all_route_visibility(True))

            hide_all_action = resource_menu.addAction("全部隐藏")
            hide_all_action.setEnabled(visible_route_count > 0)
            hide_all_action.triggered.connect(lambda _checked=False: self._set_all_route_visibility(False))

            resource_menu.addSeparator()

            for category in self.route_mgr.categories:
                routes = [
                    route
                    for route in self.route_mgr.route_groups.get(category, [])
                    if self.route_mgr._route_id_for(category, route)
                ]
                if not routes:
                    continue

                category_label = self.route_mgr.get_category_label(category)
                category_visible_count, category_total_count = self.route_mgr.get_category_visibility_summary(category)
                category_menu = apply_unified_menu_style(
                    PersistentCheckMenu(f"{category_label} ({category_visible_count}/{category_total_count})", resource_menu),
                    frameless=True,
                )
                resource_menu.addMenu(category_menu)
                category_is_visible = self.route_mgr.is_category_visible(category)
                route_actions = []

                category_action = category_menu.addAction("显示本类")
                category_action.setCheckable(True)
                category_action.setChecked(category_is_visible)

                category_menu.addSeparator()
                for route in routes:
                    route_id = self.route_mgr._route_id_for(category, route)
                    route_name = str(route.get("display_name") or route_id).strip() or route_id
                    route_action = category_menu.addAction(route_name)
                    route_action.setCheckable(True)
                    route_action.setChecked(self.route_mgr.is_route_visible(category, route))
                    route_action.setEnabled(category_is_visible)
                    route_action.triggered.connect(
                        lambda checked, target_route_id=route_id: self._set_route_visibility(target_route_id, checked)
                    )
                    route_actions.append((route_id, route_action))

                def _sync_category_route_checkboxes(
                    *,
                    target_category=category,
                    target_route_actions=route_actions,
                ) -> None:
                    category_visible_now = self.route_mgr.is_category_visible(target_category)
                    for target_route_id, target_action in target_route_actions:
                        target_action.blockSignals(True)
                        target_action.setEnabled(category_visible_now)
                        target_action.setChecked(self.route_mgr.is_route_visible(target_category, target_route_id))
                        target_action.blockSignals(False)

                def _handle_category_checkbox_toggled(
                    checked: bool,
                    *,
                    target_category=category,
                    sync_route_checkboxes=_sync_category_route_checkboxes,
                ) -> None:
                    self._set_category_visibility(target_category, checked)
                    sync_route_checkboxes()

                category_action.triggered.connect(_handle_category_checkbox_toggled)

        menu.addSeparator()

        close_action = menu.addAction("关闭导航")
        close_action.triggered.connect(self.close)
        menu.exec(self.menu_button.mapToGlobal(QPoint(0, self.menu_button.height() + 8)))

    def _route_display_name(self, route_id: str) -> str:
        _resolved_route_id, route_name, _route = self.route_mgr.resolve_route(route_id)
        return str(route_name or route_id).strip() or route_id

    def _build_coordinate_text(self) -> str:
        if self.state == "LOCAL_TRACK":
            return f"{int(round(self.target_x))}, {int(round(self.target_y))}"
        return "--, --"

    def _build_anchor_text(self) -> str:
        if self.state == "LOCAL_TRACK":
            return f"锚定 {int(round(self.target_x))}, {int(round(self.target_y))}"
        return "等待定位"

    def _resolve_status_chip_text(self) -> str:
        return ""

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._position_initialized:
            self._position_initialized = True
            QTimer.singleShot(0, self._move_to_attachment_anchor)

    def _resolve_window_attachment_position(self) -> Optional[tuple[int, int, tuple[object, ...]]]:
        target_hwnd = _get_target_hwnd()
        if target_hwnd <= 0:
            return None
        window_info = build_window_info(target_hwnd)
        if not isinstance(window_info, dict):
            return None
        window_rect = window_info.get("window_rect")
        if not isinstance(window_rect, tuple) or len(window_rect) != 4:
            return None

        qt_window_rect = native_rect_to_qt_global_rect(tuple(int(value) for value in window_rect))
        if qt_window_rect is not None and not qt_window_rect.isEmpty():
            attachment_window_rect = (
                int(qt_window_rect.x()),
                int(qt_window_rect.y()),
                int(qt_window_rect.x() + qt_window_rect.width()),
                int(qt_window_rect.y() + qt_window_rect.height()),
            )
        else:
            attachment_window_rect = tuple(int(value) for value in window_rect)

        client_qt_rect = get_window_client_qt_global_rect(window_info)
        if client_qt_rect is not None and not client_qt_rect.isEmpty():
            client_origin = (int(client_qt_rect.x()), int(client_qt_rect.y()))
            client_width = max(0, int(client_qt_rect.width()))
            client_height = max(0, int(client_qt_rect.height()))
        else:
            client_origin = None
            client_width = 0
            client_height = 0

        x, y = compute_dynamic_island_position(
            attachment_window_rect,
            client_origin,
            (self.width(), self.height()),
        )
        desktop_rect = get_qt_virtual_desktop_rect()
        if desktop_rect is not None and desktop_rect.width() > 0 and desktop_rect.height() > 0:
            min_x = int(desktop_rect.left()) + 8
            max_x = int(desktop_rect.right()) - int(self.width()) - 7
            min_y = int(desktop_rect.top()) + 8
            max_y = int(desktop_rect.bottom()) - int(self.height()) - 7
            if min_x > max_x:
                max_x = min_x
            if min_y > max_y:
                max_y = min_y
            x = max(min_x, min(int(x), max_x))
            y = max(min_y, min(int(y), max_y))
        signature = (
            "window",
            int(target_hwnd),
            *attachment_window_rect,
            int(client_origin[0]) if client_origin is not None else -1,
            int(client_origin[1]) if client_origin is not None else -1,
            int(client_width),
            int(client_height),
            int(self.width()),
            int(self.height()),
        )
        return int(x), int(y), signature

    def _apply_attachment_position(self, x: int, y: int) -> bool:
        target_x = int(x)
        target_y = int(y)
        if self.x() != target_x or self.y() != target_y:
            self.move(target_x, target_y)
        return True

    def _move_near_target_window(self) -> bool:
        anchor = self._resolve_window_attachment_position()
        if anchor is None:
            return False
        x, y, signature = anchor
        self._apply_attachment_position(x, y)
        self._last_window_attachment_signature = signature
        self._last_attachment_sync_at = time.time()
        self._attachment_anchor_dirty = False
        return True

    def _move_near_desktop(self) -> bool:
        desktop_rect = get_qt_virtual_desktop_rect()
        if desktop_rect is None or desktop_rect.width() <= 0 or desktop_rect.height() <= 0:
            screen = QGuiApplication.primaryScreen()
            if screen is None:
                return False
            desktop_rect = screen.availableGeometry()

        x = int(desktop_rect.left() + (desktop_rect.width() - self.width()) / 2)
        y = int(desktop_rect.top() + 8)
        self._apply_attachment_position(x, y)
        self._last_window_attachment_signature = None
        self._last_attachment_sync_at = time.time()
        self._attachment_anchor_dirty = False
        return True

    def _move_to_attachment_anchor(self) -> bool:
        effective_mode = self._effective_attachment_mode()
        self._last_effective_attachment_mode = effective_mode
        if effective_mode == "window" and self._move_near_target_window():
            return True
        return self._move_near_desktop()

    def _sync_attachment_position(self, *, force: bool = False) -> None:
        effective_mode = self._effective_attachment_mode()
        if effective_mode != self._last_effective_attachment_mode:
            self._attachment_anchor_dirty = True
            self._last_effective_attachment_mode = effective_mode
        if self._drag_window_offset is not None:
            return
        if effective_mode != "window":
            if force or self._attachment_anchor_dirty:
                self._move_near_desktop()
            return

        now = time.time()
        if not force and not self._attachment_anchor_dirty and (now - self._last_attachment_sync_at) < self._attachment_sync_interval:
            return

        anchor = self._resolve_window_attachment_position()
        self._last_attachment_sync_at = now
        if anchor is None:
            self._attachment_anchor_dirty = True
            self._last_window_attachment_signature = None
            return

        x, y, signature = anchor
        if force or self._attachment_anchor_dirty or signature != self._last_window_attachment_signature:
            self._apply_attachment_position(x, y)
            self._last_window_attachment_signature = signature
            self._attachment_anchor_dirty = False

    def _effective_attachment_mode(self) -> str:
        if self._attachment_mode == "window" and _get_target_hwnd() > 0:
            return "window"
        return "desktop"

    def _manual_drag_enabled(self) -> bool:
        return self._effective_attachment_mode() not in {"window", "desktop"}

    def _set_attachment_mode(self, mode: str) -> None:
        normalized_mode = "window" if str(mode or "").strip().lower() == "window" else "desktop"
        if normalized_mode == "window" and _get_target_hwnd() <= 0:
            normalized_mode = "desktop"
        self._attachment_mode = normalized_mode
        self._drag_window_offset = None
        self._attachment_anchor_dirty = True
        self._last_window_attachment_signature = None
        self._move_to_attachment_anchor()

    def _update_opacity(self, value: int) -> None:
        self.setWindowOpacity(max(0.10, min(1.0, float(value) / 100.0)))
        self._sync_opacity_controls()

    def _classify_status_message(self, text: str) -> str | None:
        message = str(text or "").strip()
        if not message:
            return None
        if message in {self._DISPLAY_STATUS_FAILURE, self._DISPLAY_STATUS_SUCCESS, self._DISPLAY_STATUS_PAUSED}:
            return message
        if message in {"追踪中", "已定位"}:
            return self._DISPLAY_STATUS_SUCCESS
        if "暂停" in message or "小地图丢失" in message:
            return self._DISPLAY_STATUS_PAUSED
        if (
            message in {"等待定位", "跟踪丢失，请重新定位"}
            or message.startswith("暂失")
            or any(keyword in message for keyword in ("失败", "异常", "无效", "丢失"))
        ):
            return self._DISPLAY_STATUS_FAILURE
        return None

    def _append_status_log(self, text: str, *, level: int = logging.INFO) -> None:
        message = str(text or "").strip()
        if not message:
            return
        log_key = self._classify_status_message(message) or message
        timestamp = float(time.time())
        if self._status_log_entries and self._status_log_entries[-1].get("key") == log_key:
            last_entry = dict(self._status_log_entries.pop())
            last_entry["count"] = int(last_entry.get("count", 1)) + 1
            last_entry["message"] = message
            last_entry["updated_at"] = timestamp
            self._status_log_entries.append(last_entry)
            return
        self._status_log_entries.append(
            {
                "key": log_key,
                "message": message,
                "count": 1,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        logger.log(int(level), "[地图导航状态日志] %s", message)

    def _set_status(self, text: str) -> None:
        message = str(text or "").strip()
        if not message:
            return
        self._append_status_log(message)

    def _report_status(self, text: str, *, force: bool = False) -> None:
        detail = str(text or "").strip()
        if not detail:
            return
        self._append_status_log(detail)
        display_status = self._menu_status_summary()
        if not force and display_status == self._last_reported_status:
            return
        self._last_reported_status = display_status
        bridge.report_status(display_status)

    def on_theme_changed(self, _theme_name: str | None = None) -> None:
        self.view_label.set_theme_manager(self.theme_manager)
        self.apply_theme()
        if self.selector_dialog is not None and self.selector_dialog.isVisible():
            self.selector_dialog.apply_theme()
        if self.lock_overlay is not None:
            self.lock_overlay.apply_theme()

    def apply_theme(self) -> None:
        central = self.centralWidget()
        if central is not None:
            central.setStyleSheet(
                """
                QWidget#mapNavCentral {
                    background: transparent;
                }
                QFrame#mapNavMenuIsland QPushButton {
                    padding: 0 14px;
                    min-height: 32px;
                    max-height: 32px;
                    border-radius: 8px;
                }
                QFrame#mapNavMenuIsland QCheckBox {
                    min-height: 32px;
                    max-height: 32px;
                }
                QFrame#mapNavMapCard[mapBaseHidden="true"] {
                    background: transparent;
                    border: 0px;
                }
                """
            )
            for widget in (self.menu_island_frame, self.map_card_frame):
                widget.style().unpolish(widget)
                widget.style().polish(widget)
        self.view_label.set_theme_manager(self.theme_manager)

    def eventFilter(self, watched, event) -> bool:
        if watched in {
            self.menu_island_frame,
            self.info_panel,
            self.status_info_label,
            self.coordinate_info_label,
            self.map_card_frame,
            self.view_label,
        }:
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and hasattr(event, "button")
                and event.button() == Qt.MouseButton.LeftButton
                and hasattr(event, "globalPosition")
                and self._manual_drag_enabled()
            ):
                self._drag_window_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            elif (
                event.type() == QEvent.Type.MouseMove
                and self._drag_window_offset is not None
                and hasattr(event, "buttons")
                and event.buttons() & Qt.MouseButton.LeftButton
                and hasattr(event, "globalPosition")
                and self._manual_drag_enabled()
            ):
                if self._effective_attachment_mode() == "window":
                    self._attachment_anchor_dirty = True
                self.move(event.globalPosition().toPoint() - self._drag_window_offset)
                return True
            elif (
                event.type() == QEvent.Type.MouseButtonRelease
                and hasattr(event, "button")
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self._drag_window_offset = None
                if self._effective_attachment_mode() == "window":
                    self._attachment_anchor_dirty = True
                    self._last_attachment_sync_at = 0.0
        return super().eventFilter(watched, event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.is_running = False
        try:
            self.ui_timer.stop()
        except Exception:
            pass
        try:
            self._resource_render_event.set()
        except Exception:
            pass
        if self.lock_overlay is not None:
            try:
                self.lock_overlay.close()
            except Exception:
                pass
        self._unregister_lock_hotkey()
        try:
            self.theme_manager.unregister_theme_change_callback(self.on_theme_changed)
        except Exception:
            pass
        if self.engine is not None:
            self.engine.release()
        super().closeEvent(event)

    def nativeEvent(self, eventType, message):
        handled, result = super().nativeEvent(eventType, message)
        if os.name == "nt" and eventType in (b"windows_generic_MSG", "windows_generic_MSG"):
            try:
                msg = wintypes.MSG.from_address(int(message))
            except Exception:
                return handled, result
            if int(msg.message) == 0x0312 and int(msg.wParam) == int(self._lock_hotkey_id):
                self._toggle_lock_from_shortcut()
                return True, 0
        return handled, result

    def toggle_lock(self, enabled: bool) -> None:
        if os.name != "nt":
            self._set_lock_checkbox_checked(False)
            self._update_lock_checkbox_tooltip()
            self._set_status("当前系统不支持窗口穿透锁定")
            return
        previous_enabled = not bool(enabled)
        try:
            hwnd = int(self.winId())
            gwl_exstyle = -20
            ws_ex_transparent = 0x20
            ws_ex_layered = 0x80000
            style = ctypes.windll.user32.GetWindowLongW(hwnd, gwl_exstyle)
            self._drag_window_offset = None
            if enabled:
                ctypes.windll.user32.SetWindowLongW(hwnd, gwl_exstyle, style | ws_ex_transparent | ws_ex_layered)
                self._show_lock_overlay()
                self._set_status(f"窗口已锁定鼠标穿透，顶部锁定控件可点击，按 {self._lock_hotkey_text} 也可解锁")
            else:
                ctypes.windll.user32.SetWindowLongW(hwnd, gwl_exstyle, style & ~ws_ex_transparent)
                self._hide_lock_overlay()
                self._set_status("已解除窗口鼠标穿透")
        except Exception as exc:
            self._hide_lock_overlay()
            self._set_lock_checkbox_checked(previous_enabled)
            logger.warning("[地图导航] 切换窗口穿透失败: %s", exc)
            self._set_status("切换窗口穿透失败")
        self._sync_locked_render_mode()
        self._update_lock_checkbox_tooltip()
        self._update_menu_button_tooltip()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._sync_lock_overlay_geometry()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._attachment_anchor_dirty = True
        self._sync_lock_overlay_geometry()

    def open_selector_dialog(self) -> None:
        if self.selector_dialog is not None and self.selector_dialog.isVisible():
            self.selector_dialog.raise_()
            self.selector_dialog.activateWindow()
            return
        self.selector_dialog = MapSelectorDialog(
            self.display_map_bgr,
            self.logic_map_bgr.shape[:2],
            self.theme_manager,
            self.on_relocate_done,
            self,
        )
        self.selector_dialog.finished.connect(self._on_selector_closed)
        self.selector_dialog.show()
        self._set_status("等待定位")
        self._report_status("等待定位")

    def _on_selector_closed(self, *_args) -> None:
        self.selector_dialog = None

    def on_relocate_done(self, x: int, y: int) -> None:
        self.target_x = float(x)
        self.target_y = float(y)
        self.player_x = float(x)
        self.player_y = float(y)
        self.camera_x = float(x)
        self.camera_y = float(y)
        self._reset_small_motion_filter()
        self.current_search_radius = float(self.base_search_radius)
        self.lost_frames = 0
        self._set_tracking_pause_state(False)
        self.state = "LOCAL_TRACK"
        self._has_located_once = True
        try:
            current_vw, current_vh = self._get_view_size()
            frame_state = self._capture_tracking_frame_state(current_vw, current_vh)
            preview_frame = self._build_tracking_frame_from_state(frame_state, draw_routes=False)
            self._publish_tracking_base_frame(preview_frame, frame_state)
        except Exception:
            logger.exception("[地图导航] 大地图手动定位后刷新预览失败")
        search_radius = max(64, int(self.current_search_radius))
        x1 = max(0, int(self.target_x) - search_radius)
        y1 = max(0, int(self.target_y) - search_radius)
        x2 = min(self.map_width, int(self.target_x) + search_radius)
        y2 = min(self.map_height, int(self.target_y) + search_radius)
        detail = f"已定位到: {x}, {y}"
        self._set_status("已定位")
        self._report_status(detail, force=True)
        bridge.report_position(
            map_x=int(round(self.target_x)),
            map_y=int(round(self.target_y)),
            locked=True,
            lost_count=0,
            match_mode="ManualSelector",
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2,
            valid_match_count=0,
        )

    def _get_view_size(self) -> tuple[int, int]:
        size = self.view_label.size()
        width = max(320, _coerce_int(size.width(), self.view_w))
        height = max(240, _coerce_int(size.height(), self.view_h))
        self.view_w = width
        self.view_h = height
        return width, height

    def _capture_tracking_frame_state(
        self,
        current_vw: int,
        current_vh: int,
        mini_bgr: Optional[np.ndarray] = None,
    ) -> dict[str, object]:
        return {
            "view_width": int(current_vw),
            "view_height": int(current_vh),
            "camera_x": float(self.camera_x),
            "camera_y": float(self.camera_y),
            "player_x": float(self.player_x),
            "player_y": float(self.player_y),
            "mini_bgr": mini_bgr,
            "hide_base_map": bool(self._hide_tracking_base_map),
        }

    def _build_tracking_frame_from_state(
        self,
        frame_state: dict[str, object],
        *,
        draw_routes: bool,
    ) -> np.ndarray:
        return self._build_tracking_frame(
            _coerce_int(frame_state.get("view_width"), self.view_w),
            _coerce_int(frame_state.get("view_height"), self.view_h),
            frame_state.get("mini_bgr"),
            draw_routes=draw_routes,
            camera_x=_coerce_float(frame_state.get("camera_x"), self.camera_x),
            camera_y=_coerce_float(frame_state.get("camera_y"), self.camera_y),
            player_x=_coerce_float(frame_state.get("player_x"), self.player_x),
            player_y=_coerce_float(frame_state.get("player_y"), self.player_y),
            hide_base_map=bool(frame_state.get("hide_base_map", False)),
        )

    @staticmethod
    def _resource_render_signature(frame_state: dict[str, object]) -> tuple[float, float, float, float, int, bool]:
        return (
            round(_coerce_float(frame_state.get("camera_x"), 0.0), 2),
            round(_coerce_float(frame_state.get("camera_y"), 0.0), 2),
            round(_coerce_float(frame_state.get("player_x"), 0.0), 2),
            round(_coerce_float(frame_state.get("player_y"), 0.0), 2),
            _coerce_int(frame_state.get("base_frame_version"), 0),
            bool(frame_state.get("hide_base_map", False)),
        )

    def _invalidate_guidance_target_cache(self) -> None:
        self._guidance_target_cache = (None, None, None, None)
        self._guidance_target_cache_at = 0.0
        self._guidance_target_cache_position = None

    def _get_cached_guidance_target_info(
        self,
        player_x: int,
        player_y: int,
    ) -> tuple[str | None, int | None, float | None, dict | None]:
        now = time.monotonic()
        cached_position = self._guidance_target_cache_position
        if (
            cached_position is not None
            and (now - float(self._guidance_target_cache_at)) <= 0.28
            and abs(int(player_x) - int(cached_position[0])) <= 18
            and abs(int(player_y) - int(cached_position[1])) <= 18
        ):
            return self._guidance_target_cache

        nearest_point = self.route_mgr.get_nearest_visible_unvisited_point(player_x, player_y)
        if isinstance(nearest_point, dict):
            cached_result = (
                str(nearest_point.get("route_id") or "").strip() or None,
                int(nearest_point.get("point_index", 0)),
                float(nearest_point.get("distance", 0.0)),
                nearest_point.get("route") if isinstance(nearest_point.get("route"), dict) else None,
            )
        else:
            cached_result = (None, None, None, None)

        self._guidance_target_cache = cached_result
        self._guidance_target_cache_at = now
        self._guidance_target_cache_position = (int(player_x), int(player_y))
        return cached_result

    def _request_resource_render(self, frame_state: Optional[dict[str, object]] = None, *, force: bool = False) -> None:
        with self._resource_render_lock:
            latest_state = frame_state if frame_state is not None else self._latest_tracking_frame_state
            if not isinstance(latest_state, dict):
                return
            request_state = dict(latest_state)
            request_signature = self._resource_render_signature(request_state)
            now = time.monotonic()
            if not force and self._last_resource_render_request_signature is not None:
                last_signature = self._last_resource_render_request_signature
                elapsed = now - float(self._last_resource_render_request_at)
                camera_shift = max(
                    abs(float(request_signature[0]) - float(last_signature[0])),
                    abs(float(request_signature[1]) - float(last_signature[1])),
                )
                player_shift = max(
                    abs(float(request_signature[2]) - float(last_signature[2])),
                    abs(float(request_signature[3]) - float(last_signature[3])),
                )
                base_version_gap = max(0, int(request_signature[4]) - int(last_signature[4]))
                if elapsed < 0.10 and camera_shift < 18.0 and player_shift < 14.0 and base_version_gap < 3:
                    return
            self._resource_render_request_seq += 1
            request_state["request_seq"] = int(self._resource_render_request_seq)
            self._pending_resource_render_state = request_state
            self._last_resource_render_request_at = now
            self._last_resource_render_request_signature = request_signature
        self._resource_render_event.set()

    def _publish_tracking_base_frame(
        self,
        frame: Optional[np.ndarray],
        frame_state: Optional[dict[str, object]] = None,
        *,
        request_resource_render: bool = True,
    ) -> None:
        if frame is None:
            return
        next_state: Optional[dict[str, object]] = None
        with self._resource_render_lock:
            self._latest_base_frame_version += 1
            base_frame_version = int(self._latest_base_frame_version)
            if isinstance(frame_state, dict):
                next_state = dict(frame_state)
                next_state["base_frame_version"] = base_frame_version
                self._latest_tracking_frame_state = next_state
            elif isinstance(self._latest_tracking_frame_state, dict):
                next_state = dict(self._latest_tracking_frame_state)
                next_state["base_frame_version"] = base_frame_version
                self._latest_tracking_frame_state = next_state
        with self.lock:
            self._latest_base_display_crop = frame
            self.latest_display_crop = frame
        if request_resource_render and isinstance(next_state, dict):
            self._request_resource_render(next_state)

    def _capture_minimap(self):
        minimap = config.MINIMAP or {}
        if not isinstance(minimap, dict):
            return None
        region = resolve_minimap_capture_region(minimap, target_hwnd=_get_target_hwnd())
        if region is None:
            region = {
                "left": _coerce_int(minimap.get("left"), 0),
                "top": _coerce_int(minimap.get("top"), 0),
                "width": _coerce_int(minimap.get("width"), 0),
                "height": _coerce_int(minimap.get("height"), 0),
            }
        if region["width"] <= 0 or region["height"] <= 0:
            return None
        captured_bgr = None
        try:
            with mss.mss() as screen_capture:
                screenshot = screen_capture.grab(region)
            captured_bgr = np.array(screenshot)[:, :, :3]
        except Exception as exc:
            logger.debug("[地图导航] mss 小地图截图失败，回退到内部截图: %s", exc)
        if captured_bgr is None:
            captured_bgr = capture_region_bgr(region)
        return self._build_minimap_capture_payload(captured_bgr)

    @staticmethod
    def _build_minimap_capture_payload(mini_bgr: Optional[np.ndarray]) -> Optional[dict[str, np.ndarray]]:
        if mini_bgr is None or not isinstance(mini_bgr, np.ndarray) or mini_bgr.ndim < 2:
            return mini_bgr
        height, width = mini_bgr.shape[:2]
        if width <= 0 or height <= 0:
            return mini_bgr
        center_x = (float(width) - 1.0) / 2.0
        center_y = (float(height) - 1.0) / 2.0
        radius = max(1.0, min(float(width), float(height)) * 0.5 - 1.0)
        mask = np.zeros((height, width), dtype=np.uint8)
        cv2.circle(mask, (int(round(center_x)), int(round(center_y))), int(round(radius)), 255, -1)
        if mini_bgr.ndim == 2:
            color = cv2.cvtColor(mini_bgr, cv2.COLOR_GRAY2BGR)
        else:
            color = mini_bgr[:, :, :3]
        match_bgr = color.copy()
        alpha = np.full((height, width, 1), 255, dtype=np.uint8)
        preview_bgra = np.concatenate((color, alpha), axis=2)
        try:
            marker_bgra = RadarMainWindow._build_minimap_marker_bgra(color, mask)
        except Exception as exc:
            logger.debug("[地图导航] 构建定位点 marker 失败，回退圆点显示: %s", exc)
            marker_bgra = None
        gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
        masked_gray = gray[mask > 0]
        gray_std = float(np.std(masked_gray)) if masked_gray.size > 0 else 0.0
        laplacian = cv2.Laplacian(gray, cv2.CV_32F)
        laplacian_std = float(np.std(laplacian[mask > 0])) if masked_gray.size > 0 else 0.0
        marker_alpha_ratio = 0.0
        if (
            isinstance(marker_bgra, np.ndarray)
            and marker_bgra.ndim == 3
            and marker_bgra.shape[2] >= 4
            and marker_bgra.size > 0
        ):
            marker_alpha = marker_bgra[:, :, 3]
            marker_alpha_ratio = float(np.count_nonzero(marker_alpha > 0)) / float(max(1, marker_alpha.size))
        match_ready = True
        return {
            "match_bgr": match_bgr,
            "preview_bgra": preview_bgra,
            "marker_bgra": marker_bgra,
            "match_ready": match_ready,
            "gray_std": gray_std,
            "laplacian_std": laplacian_std,
            "marker_alpha_ratio": marker_alpha_ratio,
        }

    @staticmethod
    def _build_minimap_marker_bgra(
        color_bgr: Optional[np.ndarray],
        valid_mask: Optional[np.ndarray] = None,
    ) -> Optional[np.ndarray]:
        if color_bgr is None or not isinstance(color_bgr, np.ndarray) or color_bgr.ndim < 3:
            return None
        height, width = color_bgr.shape[:2]
        if width <= 0 or height <= 0:
            return None

        side = max(18, int(round(min(float(width), float(height)) * 0.28)))
        side = min(side, width, height)
        center_x = int(round((float(width) - 1.0) / 2.0))
        center_y = int(round((float(height) - 1.0) / 2.0))
        half_side = side // 2
        x1 = max(0, center_x - half_side)
        y1 = max(0, center_y - half_side)
        x2 = min(width, x1 + side)
        y2 = min(height, y1 + side)
        x1 = max(0, x2 - side)
        y1 = max(0, y2 - side)

        marker_color = color_bgr[y1:y2, x1:x2].copy()
        if marker_color.size <= 0:
            return None

        if isinstance(valid_mask, np.ndarray) and valid_mask.shape[:2] == color_bgr.shape[:2]:
            marker_valid_mask = valid_mask[y1:y2, x1:x2].copy()
        else:
            marker_valid_mask = np.full(marker_color.shape[:2], 255, dtype=np.uint8)

        marker_h, marker_w = marker_color.shape[:2]
        yy, xx = np.indices((marker_h, marker_w), dtype=np.float32)
        local_center_x = (float(marker_w) - 1.0) / 2.0
        local_center_y = (float(marker_h) - 1.0) / 2.0
        distance = np.sqrt((xx - local_center_x) ** 2 + (yy - local_center_y) ** 2)
        max_radius = max(1.0, min(float(marker_w), float(marker_h)) * 0.5)

        sample_ring = (
            (distance >= max_radius * 0.42)
            & (distance <= max_radius * 0.92)
            & (marker_valid_mask > 0)
        )
        sample_pixels = marker_color[sample_ring]
        if sample_pixels.size <= 0:
            sample_pixels = marker_color[marker_valid_mask > 0]

        fallback_alpha = np.where(marker_valid_mask > 0, 224, 0).astype(np.uint8)
        if sample_pixels.size <= 0:
            return np.concatenate((marker_color, fallback_alpha[:, :, np.newaxis]), axis=2)

        bg_color = np.median(sample_pixels.reshape(-1, 3), axis=0).astype(np.float32)
        color_delta = np.max(np.abs(marker_color.astype(np.float32) - bg_color), axis=2)

        marker_gray = cv2.cvtColor(marker_color, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if np.any(sample_ring):
            bg_gray = float(np.median(marker_gray[sample_ring]))
        else:
            bg_gray = float(np.median(marker_gray))
        gray_delta = np.abs(marker_gray - bg_gray)

        focus_mask = (distance <= max_radius * 0.82) & (marker_valid_mask > 0)
        marker_alpha = np.zeros((marker_h, marker_w), dtype=np.uint8)
        marker_alpha[((color_delta >= 24.0) | (gray_delta >= 22.0)) & focus_mask] = 255

        kernel = np.ones((3, 3), dtype=np.uint8)
        marker_alpha = cv2.morphologyEx(marker_alpha, cv2.MORPH_OPEN, kernel, iterations=1)
        marker_alpha = cv2.morphologyEx(marker_alpha, cv2.MORPH_CLOSE, kernel, iterations=1)

        nonzero_alpha = int(cv2.countNonZero(marker_alpha))
        if nonzero_alpha > 0:
            num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(marker_alpha, connectivity=8)
            selected_label = 0
            center_ix = max(0, min(marker_w - 1, int(round(local_center_x))))
            center_iy = max(0, min(marker_h - 1, int(round(local_center_y))))
            selected_label = int(labels[center_iy, center_ix])
            if selected_label <= 0:
                best_distance = None
                for label in range(1, num_labels):
                    area = int(stats[label, cv2.CC_STAT_AREA])
                    if area < 6:
                        continue
                    comp_x = float(stats[label, cv2.CC_STAT_LEFT])
                    comp_y = float(stats[label, cv2.CC_STAT_TOP])
                    comp_w = float(stats[label, cv2.CC_STAT_WIDTH])
                    comp_h = float(stats[label, cv2.CC_STAT_HEIGHT])
                    comp_center_x = comp_x + max(0.0, comp_w - 1.0) * 0.5
                    comp_center_y = comp_y + max(0.0, comp_h - 1.0) * 0.5
                    comp_distance = (comp_center_x - local_center_x) ** 2 + (comp_center_y - local_center_y) ** 2
                    if best_distance is None or comp_distance < best_distance:
                        best_distance = comp_distance
                        selected_label = label
            if selected_label > 0:
                marker_alpha = np.where(labels == selected_label, 255, 0).astype(np.uint8)
                marker_alpha = cv2.dilate(marker_alpha, kernel, iterations=1)

        alpha_ratio = float(cv2.countNonZero(marker_alpha)) / float(max(1, cv2.countNonZero(marker_valid_mask)))
        if alpha_ratio < 0.02 or alpha_ratio > 0.72:
            marker_alpha = fallback_alpha

        return np.concatenate((marker_color, marker_alpha[:, :, np.newaxis]), axis=2)

    @staticmethod
    def _minimap_match_image(minimap_payload) -> Optional[np.ndarray]:
        if isinstance(minimap_payload, dict):
            match_bgr = minimap_payload.get("match_bgr")
            if isinstance(match_bgr, np.ndarray):
                return match_bgr
        return minimap_payload if isinstance(minimap_payload, np.ndarray) else None

    @staticmethod
    def _minimap_preview_image(minimap_payload) -> Optional[np.ndarray]:
        if isinstance(minimap_payload, dict):
            preview_bgra = minimap_payload.get("preview_bgra")
            if isinstance(preview_bgra, np.ndarray):
                return preview_bgra
            match_bgr = minimap_payload.get("match_bgr")
            if isinstance(match_bgr, np.ndarray):
                return match_bgr
        return minimap_payload if isinstance(minimap_payload, np.ndarray) else None

    @staticmethod
    def _minimap_marker_image(minimap_payload) -> Optional[np.ndarray]:
        if isinstance(minimap_payload, dict):
            marker_bgra = minimap_payload.get("marker_bgra")
            if isinstance(marker_bgra, np.ndarray):
                return marker_bgra
        return None

    def _build_tracking_frame(
        self,
        current_vw: int,
        current_vh: int,
        mini_bgr: Optional[np.ndarray] = None,
        *,
        draw_routes: bool = True,
        camera_x: float | None = None,
        camera_y: float | None = None,
        player_x: float | None = None,
        player_y: float | None = None,
        hide_base_map: bool = False,
    ) -> np.ndarray:
        render_camera_x = float(self.camera_x if camera_x is None else camera_x)
        render_camera_y = float(self.camera_y if camera_y is None else camera_y)
        render_player_x = float(self.player_x if player_x is None else player_x)
        render_player_y = float(self.player_y if player_y is None else player_y)
        hide_base_map = bool(hide_base_map)
        half_vw = current_vw // 2
        half_vh = current_vh // 2
        cam_x = int(render_camera_x)
        cam_y = int(render_camera_y)

        max_vx1 = max(0, self.map_width - current_vw)
        max_vy1 = max(0, self.map_height - current_vh)
        vx1 = max(0, min(max_vx1, cam_x - half_vw))
        vy1 = max(0, min(max_vy1, cam_y - half_vh))
        vx2 = min(self.map_width, vx1 + current_vw)
        vy2 = min(self.map_height, vy1 + current_vh)

        transparent_key = np.array([1, 2, 3], dtype=np.uint8)
        if hide_base_map:
            crop = np.empty((current_vh, current_vw, 3), dtype=np.uint8)
            crop[:, :] = transparent_key
        else:
            crop = self.display_map_bgr[vy1:vy2, vx1:vx2].copy()
            if crop.shape[1] != current_vw or crop.shape[0] != current_vh:
                padded = np.zeros((current_vh, current_vw, 3), dtype=np.uint8)
                padded[: crop.shape[0], : crop.shape[1]] = crop
                crop = padded

        if draw_routes:
            self.route_mgr.draw_on(
                crop,
                vx1,
                vy1,
                max(current_vw, current_vh),
                int(round(render_player_x)),
                int(round(render_player_y)),
            )

        player_local = (int(round(render_player_x - vx1)), int(round(render_player_y - vy1)))
        marker_drawn = False
        marker_bgra = self._minimap_marker_image(mini_bgr)
        if (
            marker_bgra is not None
            and isinstance(marker_bgra, np.ndarray)
            and marker_bgra.ndim == 3
            and marker_bgra.shape[2] >= 4
            and marker_bgra.size > 0
        ):
            marker_h, marker_w = marker_bgra.shape[:2]
            target_max_side = max(32, min(56, int(round(min(current_vw, current_vh) * 0.088))))
            marker_scale = float(target_max_side) / float(max(1, marker_w, marker_h))
            resized_w = max(1, int(round(marker_w * marker_scale)))
            resized_h = max(1, int(round(marker_h * marker_scale)))
            interpolation = cv2.INTER_LINEAR if marker_scale >= 1.0 else cv2.INTER_AREA
            resized_marker = cv2.resize(marker_bgra, (resized_w, resized_h), interpolation=interpolation)

            left = int(round(player_local[0] - resized_w / 2.0))
            top = int(round(player_local[1] - resized_h / 2.0))
            dst_x1 = max(0, left)
            dst_y1 = max(0, top)
            dst_x2 = min(crop.shape[1], left + resized_w)
            dst_y2 = min(crop.shape[0], top + resized_h)
            if dst_x1 < dst_x2 and dst_y1 < dst_y2:
                src_x1 = max(0, -left)
                src_y1 = max(0, -top)
                src_x2 = src_x1 + (dst_x2 - dst_x1)
                src_y2 = src_y1 + (dst_y2 - dst_y1)
                overlay = resized_marker[src_y1:src_y2, src_x1:src_x2]
                overlay_alpha = overlay[:, :, 3:4].astype(np.float32) / 255.0
                if float(np.max(overlay_alpha)) > 0.0:
                    base_area = crop[dst_y1:dst_y2, dst_x1:dst_x2].astype(np.float32)
                    overlay_color = overlay[:, :, :3].astype(np.float32)
                    blended = overlay_color * overlay_alpha + base_area * (1.0 - overlay_alpha)
                    crop[dst_y1:dst_y2, dst_x1:dst_x2] = blended.astype(np.uint8)
                    marker_drawn = True

        if (
            not hide_base_map
            and not marker_drawn
            and 0 <= player_local[0] < crop.shape[1]
            and 0 <= player_local[1] < crop.shape[0]
        ):
            cv2.circle(crop, player_local, 9, (0, 0, 255), 2)
            cv2.circle(crop, player_local, 3, (255, 255, 255), -1)

        mini_preview = self._minimap_preview_image(mini_bgr)
        if (
            not hide_base_map
            and mini_preview is not None
            and mini_preview.size > 0
            and crop.shape[0] >= 56
            and crop.shape[1] >= 56
        ):
            preview = cv2.resize(mini_preview, (48, 48), interpolation=cv2.INTER_AREA)
            preview_area = crop[8 : 8 + preview.shape[0], 8 : 8 + preview.shape[1]]
            if preview.ndim == 3 and preview.shape[2] >= 4:
                preview_color = preview[:, :, :3].astype(np.float32)
                preview_alpha = (preview[:, :, 3:4].astype(np.float32) / 255.0) * 0.8
                blended = preview_color * preview_alpha + preview_area.astype(np.float32) * (1.0 - preview_alpha)
                preview_area[:] = blended.astype(np.uint8)
            else:
                preview_area[:] = cv2.addWeighted(preview, 0.8, preview_area, 0.2, 0)

        if hide_base_map:
            drawn_mask = np.any(crop != transparent_key, axis=2)
            crop[~drawn_mask] = 0
            alpha = np.where(drawn_mask, 255, 0).astype(np.uint8)
            return np.dstack((crop, alpha))

        info_color = (255, 255, 255) if not self.theme_manager.is_dark_mode() else (224, 224, 224)
        cv2.putText(
            crop,
            f"Backend: {self.engine.backend_label}",
            (8, crop.shape[0] - 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            info_color,
            1,
        )
        return crop

    def _build_bridge_payload(
        self,
        *,
        locked: bool,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        valid_match_count: int,
        failure_reason: str = "",
        match_mode: str = "LKMapTools",
        raw_match_count: int = 0,
        max_confidence: float = 0.0,
        minimap_size: str = "",
        search_size: str = "",
    ) -> None:
        map_x = int(round(self.target_x))
        map_y = int(round(self.target_y))
        route_id, nearest_index, distance_to_next_point, _route = self._get_cached_guidance_target_info(
            map_x,
            map_y,
        )
        bridge.report_position(
            map_x=map_x,
            map_y=map_y,
            locked=bool(locked),
            paused=bool(self._tracking_paused),
            lost_count=int(self.lost_frames),
            match_mode=str(match_mode or "").strip() or "LKMapTools",
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2,
            route_id=route_id,
            nearest_route_index=nearest_index,
            distance_to_next_point=distance_to_next_point,
            failure_reason=failure_reason,
            valid_match_count=int(valid_match_count),
            raw_match_count=int(raw_match_count),
            max_confidence=float(max_confidence),
            minimap_size=minimap_size,
            search_size=search_size,
        )

    def ai_worker_loop(self) -> None:
        while self.is_running:
            if self.state != "LOCAL_TRACK":
                time.sleep(0.05)
                continue

            try:
                current_vw, current_vh = self._get_view_size()
                mini_bgr = self._capture_minimap()
                if mini_bgr is None:
                    detail = "小地图区域无效，无法截图"
                    self.lost_frames = min(self.max_lost_frames + 1, int(self.lost_frames) + 1)
                    self._set_tracking_pause_state(True, detail)
                    self._set_status(detail)
                    self._report_status(detail, force=True)
                    search_radius = max(64, int(self.current_search_radius))
                    max_radius = max(600, _coerce_int(config.AI_SCAN_SIZE, 200) * 3)
                    search_radius = min(search_radius, max_radius)
                    x1 = max(0, int(self.target_x) - search_radius)
                    y1 = max(0, int(self.target_y) - search_radius)
                    x2 = min(self.map_width, int(self.target_x) + search_radius)
                    y2 = min(self.map_height, int(self.target_y) + search_radius)
                    frame_state = self._capture_tracking_frame_state(current_vw, current_vh)
                    preview_frame = self._build_tracking_frame_from_state(frame_state, draw_routes=False)
                    self._publish_tracking_base_frame(preview_frame, frame_state)
                    self._build_bridge_payload(
                        locked=False,
                        x1=x1,
                        y1=y1,
                        x2=x2,
                        y2=y2,
                        valid_match_count=0,
                        failure_reason=detail,
                        minimap_size="--",
                        search_size=f"{max(0, x2 - x1)}x{max(0, y2 - y1)}",
                    )
                    time.sleep(0.2)
                    continue

                search_radius = max(64, int(self.current_search_radius))
                max_radius = max(600, _coerce_int(config.AI_SCAN_SIZE, 200) * 3)
                search_radius = min(search_radius, max_radius)
                x1 = max(0, int(self.target_x) - search_radius)
                y1 = max(0, int(self.target_y) - search_radius)
                x2 = min(self.map_width, int(self.target_x) + search_radius)
                y2 = min(self.map_height, int(self.target_y) + search_radius)
                local_map = self.logic_map_bgr[y1:y2, x1:x2]

                found = False
                valid_match_count = 0
                raw_match_count = 0
                max_confidence = 0.0
                mini_shape_text = ""
                if local_map.size > 0:
                    mini_match_bgr = self._minimap_match_image(mini_bgr)
                    if mini_match_bgr is None or mini_match_bgr.size <= 0:
                        detail = "小地图截图无有效图像，已暂停定位"
                        self.lost_frames = min(self.max_lost_frames + 1, int(self.lost_frames) + 1)
                        self._set_tracking_pause_state(True, detail)
                        self._set_status(detail)
                        self._report_status(detail)
                        frame_state = self._capture_tracking_frame_state(current_vw, current_vh)
                        preview_frame = self._build_tracking_frame_from_state(frame_state, draw_routes=False)
                        self._publish_tracking_base_frame(preview_frame, frame_state)
                        self._build_bridge_payload(
                            locked=False,
                            x1=x1,
                            y1=y1,
                            x2=x2,
                            y2=y2,
                            valid_match_count=0,
                            failure_reason=detail,
                            minimap_size="--",
                            search_size=f"{max(0, x2 - x1)}x{max(0, y2 - y1)}",
                        )
                        time.sleep(0.2)
                        continue
                    mini_shape_text = f"{int(mini_match_bgr.shape[1])}x{int(mini_match_bgr.shape[0])}"
                    now = time.time()
                    if now - self._last_debug_snapshot_at >= 1.0:
                        self._last_debug_snapshot_at = now
                        _write_debug_snapshot("latest_minimap_match.png", mini_match_bgr)
                        _write_debug_snapshot("latest_local_search.png", local_map)
                    corr = self.engine.match(
                        self.engine.preprocess(mini_match_bgr, 0),
                        self.engine.preprocess(local_map, 1),
                    )
                    keypoints0 = np.asarray(corr.get("keypoints0"))
                    keypoints1 = np.asarray(corr.get("keypoints1"))
                    confidence = np.asarray(corr.get("confidence"))
                    mini_input_size = self.engine._get_target_size(0)
                    local_input_size = self.engine._get_target_size(1)
                    if keypoints0.ndim == 2 and keypoints0.shape[0] > 0 and mini_input_size is not None:
                        input_w, input_h = mini_input_size
                        if input_w > 0 and input_h > 0:
                            keypoints0 = keypoints0.astype(np.float32, copy=False)
                            keypoints0[:, 0] *= float(mini_match_bgr.shape[1]) / float(input_w)
                            keypoints0[:, 1] *= float(mini_match_bgr.shape[0]) / float(input_h)
                    if keypoints1.ndim == 2 and keypoints1.shape[0] > 0 and local_input_size is not None:
                        input_w, input_h = local_input_size
                        if input_w > 0 and input_h > 0:
                            keypoints1 = keypoints1.astype(np.float32, copy=False)
                            keypoints1[:, 0] *= float(local_map.shape[1]) / float(input_w)
                            keypoints1[:, 1] *= float(local_map.shape[0]) / float(input_h)
                    raw_match_count = int(len(keypoints0))
                    confidence_threshold = _coerce_float(config.AI_CONFIDENCE_THRESHOLD, 0.50)
                    if confidence.ndim > 0:
                        if confidence.size > 0:
                            max_confidence = float(np.max(confidence))
                        valid_idx = confidence > confidence_threshold
                        keypoints0 = keypoints0[valid_idx]
                        keypoints1 = keypoints1[valid_idx]
                    valid_match_count = int(len(keypoints0))
                    min_match_count = max(4, _coerce_int(config.AI_MIN_MATCH_COUNT, 9))
                    if valid_match_count >= min_match_count:
                        matrix, _homography_mask = cv2.findHomography(
                            keypoints0,
                            keypoints1,
                            cv2.RANSAC,
                            _coerce_float(config.AI_RANSAC_THRESHOLD, 8.0),
                        )
                        if matrix is not None:
                            mini_h, mini_w = mini_match_bgr.shape[:2]
                            center = cv2.perspectiveTransform(
                                np.float32([[[mini_w / 2.0, mini_h / 2.0]]]),
                                matrix,
                            )
                            rx = x1 + float(center[0][0][0])
                            ry = y1 + float(center[0][0][1])
                            if 0 <= rx < self.map_width and 0 <= ry < self.map_height:
                                if self._should_accept_position_update(rx, ry, strong_match=False):
                                    self.target_x = float(rx)
                                    self.target_y = float(ry)
                                found = True

                self._last_match_debug = (
                    f"raw={raw_match_count} valid={valid_match_count} "
                    f"conf={max_confidence:.2f} mini={mini_shape_text or '--'} "
                    f"search={max(0, x2 - x1)}x{max(0, y2 - y1)}"
                )
                search_size_text = f"{max(0, x2 - x1)}x{max(0, y2 - y1)}"

                if found:
                    self.lost_frames = 0
                    self.current_search_radius = float(self.base_search_radius)
                    self._set_tracking_pause_state(False)
                else:
                    self.lost_frames = min(self.max_lost_frames + 1, int(self.lost_frames) + 1)
                    lost_step = 20.0
                    self.current_search_radius = float(
                        min(
                            max_radius,
                            max(
                                self.base_search_radius * 6.0,
                                float(search_radius) + lost_step,
                                self.base_search_radius + float(self.lost_frames) * lost_step,
                            ),
                        )
                    )
                    self._set_tracking_pause_state(False)

                player_lerp = 0.6
                camera_lerp = 0.22
                hold_pixels = max(0.4, _coerce_float(config.AI_POSITION_HOLD_PIXELS, 0.85))
                player_delta_x = abs(float(self.target_x) - float(self.player_x))
                player_delta_y = abs(float(self.target_y) - float(self.player_y))
                player_rounded_delta_x = abs(int(round(float(self.target_x))) - int(round(float(self.player_x))))
                player_rounded_delta_y = abs(int(round(float(self.target_y))) - int(round(float(self.player_y))))
                if (
                    player_rounded_delta_x <= 1
                    and player_rounded_delta_y <= 1
                ) or (player_delta_x <= hold_pixels and player_delta_y <= hold_pixels):
                    self.player_x = float(self.target_x)
                    self.player_y = float(self.target_y)
                else:
                    self.player_x += (self.target_x - self.player_x) * player_lerp
                    self.player_y += (self.target_y - self.player_y) * player_lerp
                camera_delta_x = abs(float(self.player_x) - float(self.camera_x))
                camera_delta_y = abs(float(self.player_y) - float(self.camera_y))
                camera_rounded_delta_x = abs(int(round(float(self.player_x))) - int(round(float(self.camera_x))))
                camera_rounded_delta_y = abs(int(round(float(self.player_y))) - int(round(float(self.camera_y))))
                if (
                    camera_rounded_delta_x <= 1
                    and camera_rounded_delta_y <= 1
                ) or (camera_delta_x <= hold_pixels and camera_delta_y <= hold_pixels):
                    self.camera_x = float(self.player_x)
                    self.camera_y = float(self.player_y)
                else:
                    self.camera_x += (self.player_x - self.camera_x) * camera_lerp
                    self.camera_y += (self.player_y - self.camera_y) * camera_lerp

                if found:
                    self._set_status("追踪中")
                    self._build_bridge_payload(
                        locked=True,
                        x1=x1,
                        y1=y1,
                        x2=x2,
                        y2=y2,
                        valid_match_count=valid_match_count,
                        raw_match_count=raw_match_count,
                        max_confidence=max_confidence,
                        minimap_size=mini_shape_text,
                        search_size=search_size_text,
                    )
                else:
                    self._set_status(f"暂失 {self.lost_frames}/{self.max_lost_frames} · {self._last_match_debug}")
                    self._build_bridge_payload(
                        locked=False,
                        x1=x1,
                        y1=y1,
                        x2=x2,
                        y2=y2,
                        valid_match_count=valid_match_count,
                        raw_match_count=raw_match_count,
                        max_confidence=max_confidence,
                        minimap_size=mini_shape_text,
                        search_size=search_size_text,
                        failure_reason="当前帧未找到足够匹配点",
                    )

                frame_state = self._capture_tracking_frame_state(current_vw, current_vh, mini_bgr)
                preview_frame = self._build_tracking_frame_from_state(frame_state, draw_routes=False)
                self._publish_tracking_base_frame(preview_frame, frame_state)

                if self.lost_frames > self.max_lost_frames:
                    detail = "跟踪丢失，已暂停定位，请重新定位"
                    self._set_tracking_pause_state(True, detail)
                    self.state = "MANUAL_RELOCATE"
                    self._pending_selector_open = False
                    self._set_status(detail)
                    self._report_status(detail, force=True)
                    self._build_bridge_payload(
                        locked=False,
                        x1=x1,
                        y1=y1,
                        x2=x2,
                        y2=y2,
                        valid_match_count=valid_match_count,
                        raw_match_count=raw_match_count,
                        max_confidence=max_confidence,
                        minimap_size=mini_shape_text,
                        search_size=search_size_text,
                        failure_reason=detail,
                    )

                time.sleep(max(0.01, _coerce_int(config.AI_REFRESH_RATE, 50) / 1000.0))
            except Exception as exc:
                detail = f"地图导航运行异常: {exc}"
                logger.exception("[地图导航] 运行循环异常")
                self._set_status(detail)
                self._report_status(detail)
                time.sleep(0.2)

    def resource_render_loop(self) -> None:
        while self.is_running:
            triggered = self._resource_render_event.wait(0.25)
            if not self.is_running:
                break
            if not triggered:
                continue
            self._resource_render_event.clear()
            with self._resource_render_lock:
                pending_state = self._pending_resource_render_state
                latest_request_seq = int(self._resource_render_request_seq)
            if not isinstance(pending_state, dict):
                continue
            request_state = dict(pending_state)
            request_seq = _coerce_int(request_state.get("request_seq"), 0)
            if request_seq != latest_request_seq or self.state != "LOCAL_TRACK":
                continue
            try:
                rendered_frame = self._build_tracking_frame_from_state(request_state, draw_routes=True)
                with self._resource_render_lock:
                    if request_seq != int(self._resource_render_request_seq):
                        continue
                with self.lock:
                    self._latest_resource_display_crop = rendered_frame
                    self._latest_resource_frame_base_version = _coerce_int(
                        request_state.get("base_frame_version"),
                        0,
                    )
                    self._latest_resource_frame_at = time.monotonic()
                    self.latest_display_crop = rendered_frame
            except Exception:
                logger.exception("[地图导航] 资源层渲染线程异常")
                time.sleep(0.05)

    def render_frame(self) -> None:
        status_chip_text = self._resolve_status_chip_text()
        self._sync_route_controls()
        self._sync_attachment_position()

        status_text = status_chip_text or self._menu_status_summary()
        if self.status_info_label.text() != status_text:
            self.status_info_label.setText(status_text)
        self.status_info_label.setToolTip(f"状态: {status_text}")

        coordinate_text = self._build_coordinate_text()
        if self.coordinate_info_label.text() != coordinate_text:
            self.coordinate_info_label.setText(coordinate_text)
        self.coordinate_info_label.setToolTip(f"坐标: {coordinate_text}")

        if self._pending_selector_open:
            self._pending_selector_open = False
            self.open_selector_dialog()

        frame = None
        base_frame = None
        resource_frame = None
        base_frame_version = 0
        resource_frame_version = 0
        resource_frame_at = 0.0
        with self.lock:
            if self._latest_base_display_crop is not None:
                base_frame = self._latest_base_display_crop.copy()
            if self._latest_resource_display_crop is not None:
                resource_frame = self._latest_resource_display_crop.copy()
            base_frame_version = int(self._latest_base_frame_version)
            resource_frame_version = int(self._latest_resource_frame_base_version)
            resource_frame_at = float(self._latest_resource_frame_at)

        if resource_frame is not None:
            version_gap = max(0, int(base_frame_version) - int(resource_frame_version))
            resource_age = max(0.0, time.monotonic() - float(resource_frame_at))
            if bool(self._hide_tracking_base_map) or base_frame is None or version_gap <= 2 or resource_age <= 0.20:
                frame = resource_frame
        if frame is None and base_frame is not None:
            frame = base_frame

        if frame is None:
            draw_w, draw_h = self._get_view_size()
            if bool(self._hide_tracking_base_map):
                frame = np.zeros((draw_h, draw_w, 4), dtype=np.uint8)
                image = _numpy_bgr_to_qimage(frame)
                pixmap = QPixmap.fromImage(image)
                self.view_label.set_pixmap(pixmap)
                return
            frame = np.zeros((draw_h, draw_w, 3), dtype=np.uint8)
            hint = "WAITING" if self.state == "MANUAL_RELOCATE" else "INITIALIZING"
            text_size = cv2.getTextSize(hint, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 1)[0]
            text_x = max(12, (draw_w - text_size[0]) // 2)
            text_y = max(40, (draw_h + text_size[1]) // 2)
            info_color = (0, 200, 0) if not self.theme_manager.is_dark_mode() else (160, 220, 160)
            cv2.putText(frame, hint, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, info_color, 1)

        image = _numpy_bgr_to_qimage(frame)
        pixmap = QPixmap.fromImage(image)
        self.view_label.set_pixmap(pixmap)


def run_bootstrapper(force_selector: bool = True) -> None:
    app, theme_manager = _ensure_qt_application()
    logger.info("[地图导航] 启动内部 Qt 导航窗口: force_selector=%s target_hwnd=%s", bool(force_selector), _get_target_hwnd())
    run_selector_if_needed(force=force_selector)
    window = RadarMainWindow(theme_manager)
    window.show()
    bridge.report_status("地图导航窗口已显示", payload={"ui": "qt"})
    app.exec()


def run_selector_main() -> int:
    run_selector_if_needed(force=True)
    return 0


def main() -> int:
    run_bootstrapper(force_selector=True)
    return 0
