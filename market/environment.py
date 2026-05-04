# -*- coding: utf-8 -*-

from __future__ import annotations

import ctypes
import json
from pathlib import Path
from typing import Any, Dict, Optional

from app_core.app_config import APP_VERSION
from utils.app_paths import get_config_path

from .models import EnvironmentSnapshot

try:
    import win32gui
    _WIN32GUI_AVAILABLE = True
except Exception:
    win32gui = None
    _WIN32GUI_AVAILABLE = False


def load_current_config_data(config_path: Optional[str] = None) -> Dict[str, Any]:
    path = Path(config_path or get_config_path())
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _get_first_enabled_bound_window(config_data: Dict[str, Any]) -> Dict[str, Any]:
    bound_windows = config_data.get("bound_windows")
    if not isinstance(bound_windows, list):
        return {}
    for item in bound_windows:
        if isinstance(item, dict) and item.get("enabled", True):
            return item
    return {}


def _read_window_client_size(hwnd: int) -> tuple[Optional[int], Optional[int]]:
    if not _WIN32GUI_AVAILABLE or not hwnd:
        return None, None
    try:
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        return max(0, int(right - left)), max(0, int(bottom - top))
    except Exception:
        return None, None


def _read_window_class_name(hwnd: int) -> str:
    if not _WIN32GUI_AVAILABLE or not hwnd:
        return ""
    try:
        return str(win32gui.GetClassName(hwnd) or "").strip()
    except Exception:
        return ""


def _read_window_dpi(hwnd: int, fallback_dpi: Optional[int] = None) -> Optional[int]:
    if hwnd:
        try:
            user32 = ctypes.windll.user32
            get_dpi = getattr(user32, "GetDpiForWindow", None)
            if get_dpi:
                dpi_value = int(get_dpi(hwnd))
                if dpi_value > 0:
                    return dpi_value
        except Exception:
            pass
    return fallback_dpi


def capture_environment_snapshot(config_data: Optional[Dict[str, Any]] = None) -> EnvironmentSnapshot:
    current_config = dict(config_data or load_current_config_data())
    plugin_settings = current_config.get("plugin_settings") if isinstance(current_config.get("plugin_settings"), dict) else {}
    bound_window = _get_first_enabled_bound_window(current_config)
    hwnd = 0
    try:
        hwnd = int(bound_window.get("hwnd") or 0)
    except Exception:
        hwnd = 0

    dpi_info = bound_window.get("dpi_info") if isinstance(bound_window.get("dpi_info"), dict) else {}
    fallback_dpi = None
    try:
        fallback_dpi = int(dpi_info.get("dpi")) if dpi_info.get("dpi") is not None else None
    except Exception:
        fallback_dpi = None

    client_width, client_height = _read_window_client_size(hwnd)
    dpi_value = _read_window_dpi(hwnd, fallback_dpi=fallback_dpi)
    scale_factor = dpi_info.get("scale_factor")
    try:
        scale_factor = float(scale_factor) if scale_factor is not None else None
    except Exception:
        scale_factor = None

    return EnvironmentSnapshot(
        app_version=APP_VERSION,
        execution_mode=str(current_config.get("execution_mode") or "").strip(),
        screenshot_engine=str(current_config.get("screenshot_engine") or "").strip(),
        plugin_enabled=bool(plugin_settings.get("enabled", False)),
        preferred_plugin=str(plugin_settings.get("preferred_plugin") or "").strip(),
        plugin_settings=dict(plugin_settings),
        bound_window_title=str(bound_window.get("title") or "").strip(),
        bound_window_class_name=_read_window_class_name(hwnd),
        bound_window_client_width=client_width,
        bound_window_client_height=client_height,
        bound_window_dpi=dpi_value,
        bound_window_scale_factor=scale_factor,
        raw_config=current_config,
    )
