# -*- coding: utf-8 -*-

from __future__ import annotations

import copy
import ctypes
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .environment import _read_window_class_name, _read_window_client_size, _read_window_dpi
from .models import MarketPackageManifest, PrecheckReport, TargetWindowRequirement

try:
    import psutil
except Exception:
    psutil = None

try:
    import win32con
    import win32gui
    import win32process
    _WIN32_AVAILABLE = True
except Exception:
    win32con = None
    win32gui = None
    win32process = None
    _WIN32_AVAILABLE = False


@dataclass
class AutoAdjustWindowInfo:
    hwnd: int
    title: str = ""
    class_name: str = ""
    process_name: str = ""
    client_width: Optional[int] = None
    client_height: Optional[int] = None
    dpi: Optional[int] = None
    scale_factor: Optional[float] = None
    parent_hwnd: int = 0
    parent_title: str = ""
    is_child_window: bool = False


@dataclass
class MarketAutoAdjustResult:
    updated_config: Dict[str, Any]
    changed: bool = False
    applied_items: List[str] = field(default_factory=list)
    skipped_items: List[str] = field(default_factory=list)

    def add_applied(self, text: str) -> None:
        message = str(text or "").strip()
        if message and message not in self.applied_items:
            self.applied_items.append(message)
            self.changed = True

    def add_skipped(self, text: str) -> None:
        message = str(text or "").strip()
        if message and message not in self.skipped_items:
            self.skipped_items.append(message)


class MarketPrecheckAutoAdjuster:
    def apply(
        self,
        manifest: MarketPackageManifest,
        report: PrecheckReport,
        config_data: Optional[Dict[str, Any]] = None,
    ) -> MarketAutoAdjustResult:
        updated_config = copy.deepcopy(config_data or {})
        result = MarketAutoAdjustResult(updated_config=updated_config)

        issue_codes = {str(issue.code or "").strip() for issue in report.issues}
        requirement = manifest.runtime_requirement
        target_window = requirement.target_window

        self._apply_execution_mode(requirement.execution_mode, issue_codes, updated_config, result)
        self._apply_screenshot_engine(requirement.screenshot_engine, issue_codes, updated_config, result)
        self._apply_plugin_settings(requirement, issue_codes, updated_config, result)
        self._apply_window_adjustment(target_window, issue_codes, updated_config, result)
        self._apply_unfixable_items(issue_codes, result)

        return result

    @staticmethod
    def _apply_execution_mode(
        execution_mode: str,
        issue_codes: set[str],
        updated_config: Dict[str, Any],
        result: MarketAutoAdjustResult,
    ) -> None:
        mode = str(execution_mode or "").strip()
        if not mode or "execution_mode_mismatch" not in issue_codes:
            return
        if str(updated_config.get("execution_mode") or "").strip() == mode:
            return
        updated_config["execution_mode"] = mode
        result.add_applied(f"执行模式已切换为 {mode}")

    @staticmethod
    def _apply_screenshot_engine(
        screenshot_engine: str,
        issue_codes: set[str],
        updated_config: Dict[str, Any],
        result: MarketAutoAdjustResult,
    ) -> None:
        engine = str(screenshot_engine or "").strip().lower()
        if not engine or "screenshot_engine_mismatch" not in issue_codes:
            return
        if str(updated_config.get("screenshot_engine") or "").strip().lower() == engine:
            return
        updated_config["screenshot_engine"] = engine
        result.add_applied(f"截图引擎已切换为 {engine}")

    @staticmethod
    def _normalize_plugin_settings(
        requirement,
        updated_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        template = requirement.plugin_settings_template
        normalized_template = dict(template) if isinstance(template, dict) else {}
        current_settings = updated_config.get("plugin_settings")
        normalized_current = dict(current_settings) if isinstance(current_settings, dict) else {}

        merged = dict(normalized_template)
        current_binding = normalized_current.get("ola_binding")
        if isinstance(current_binding, dict):
            merged_binding = dict(normalized_template.get("ola_binding") or {})
            merged_binding.update(current_binding)
            merged["ola_binding"] = merged_binding

        merged.update(normalized_current)
        if requirement.plugin_required:
            merged["enabled"] = True
        if requirement.plugin_id:
            merged["preferred_plugin"] = str(requirement.plugin_id or "").strip()
        return merged

    def _apply_plugin_settings(
        self,
        requirement,
        issue_codes: set[str],
        updated_config: Dict[str, Any],
        result: MarketAutoAdjustResult,
    ) -> None:
        needs_plugin_fix = bool({"plugin_required", "plugin_id_mismatch"} & issue_codes)
        if not needs_plugin_fix:
            return

        normalized_settings = self._normalize_plugin_settings(requirement, updated_config)
        previous_settings = updated_config.get("plugin_settings")
        if previous_settings == normalized_settings:
            return

        updated_config["plugin_settings"] = normalized_settings
        plugin_name = str(normalized_settings.get("preferred_plugin") or "插件模式").strip() or "插件模式"
        if normalized_settings.get("enabled"):
            result.add_applied(f"插件模式已调整为 {plugin_name}")
        else:
            result.add_applied("插件配置已同步")

    def _apply_window_adjustment(
        self,
        requirement: TargetWindowRequirement,
        issue_codes: set[str],
        updated_config: Dict[str, Any],
        result: MarketAutoAdjustResult,
    ) -> None:
        window_issue_codes = {
            "bound_window_missing",
            "window_title_mismatch",
            "window_class_mismatch",
            "client_width_exact",
            "client_height_exact",
            "client_width_range",
            "client_height_range",
            "window_dpi_mismatch",
            "window_scale_factor_mismatch",
        }
        if not (issue_codes & window_issue_codes):
            return
        if not _WIN32_AVAILABLE:
            result.add_skipped("当前环境无法自动调整窗口绑定和分辨率")
            return

        target_window = self._find_best_window(requirement)
        if target_window is None:
            result.add_skipped("未找到符合要求的目标窗口，无法自动绑定")
            return

        resized = self._try_resize_window(target_window, requirement)
        refreshed = self._read_window_info(target_window.hwnd)
        if refreshed is not None:
            target_window = refreshed

        display_title = str(target_window.title or target_window.parent_title or "").strip()
        updated_config["bound_windows"] = [self._build_bound_window_entry(target_window)]
        updated_config["target_window_title"] = display_title or None
        updated_config["window_binding_mode"] = "single"
        if target_window.client_width is not None:
            updated_config["custom_width"] = int(target_window.client_width)
        if target_window.client_height is not None:
            updated_config["custom_height"] = int(target_window.client_height)

        result.add_applied(f"已自动绑定窗口：{target_window.title or target_window.hwnd}")
        if resized:
            result.add_applied(
                f"已自动调整窗口客户区为 {target_window.client_width or '?'}x{target_window.client_height or '?'}"
            )

        if "window_dpi_mismatch" in issue_codes and requirement.dpi is not None:
            if target_window.dpi != requirement.dpi:
                result.add_skipped("窗口 DPI 仍不匹配，需手动调整系统或模拟器缩放")
        if "window_scale_factor_mismatch" in issue_codes and requirement.scale_factor is not None:
            if target_window.scale_factor is None or abs(target_window.scale_factor - requirement.scale_factor) > 0.01:
                result.add_skipped("窗口缩放仍不匹配，需手动调整系统或模拟器缩放")

    @staticmethod
    def _apply_unfixable_items(issue_codes: set[str], result: MarketAutoAdjustResult) -> None:
        if "client_version_too_low" in issue_codes:
            result.add_skipped("客户端版本过低，需先更新软件")
        if "client_version_too_high" in issue_codes:
            result.add_skipped("\u5ba2\u6237\u7aef\u7248\u672c\u9ad8\u4e8e\u811a\u672c\u58f0\u660e\u8303\u56f4\uff0c\u9700\u786e\u8ba4\u517c\u5bb9\u6027")

    def _find_best_window(self, requirement: TargetWindowRequirement) -> Optional[AutoAdjustWindowInfo]:
        windows = self._enumerate_windows()
        if not windows:
            return None

        best_window: Optional[AutoAdjustWindowInfo] = None
        best_score: Optional[tuple] = None
        for item in windows:
            score = self._score_window(item, requirement)
            if score is None:
                continue
            if best_score is None or score < best_score:
                best_score = score
                best_window = item
        return best_window

    def _enumerate_windows(self) -> List[AutoAdjustWindowInfo]:
        if not _WIN32_AVAILABLE:
            return []

        items: List[AutoAdjustWindowInfo] = []
        seen_hwnds: set[int] = set()

        def _append_info(hwnd: int, parent_hwnd: int = 0, parent_title: str = "") -> None:
            try:
                if not win32gui.IsWindow(hwnd):
                    return
                info = self._read_window_info(hwnd, parent_hwnd=parent_hwnd, parent_title=parent_title)
                if info is None:
                    return
                if info.hwnd in seen_hwnds:
                    return
                if not info.title and not info.parent_title and not info.class_name:
                    return
                seen_hwnds.add(info.hwnd)
                items.append(info)
            except Exception:
                return

        def _enum_child_proc(child_hwnd, parent_hwnd):
            try:
                if not win32gui.IsWindow(child_hwnd) or not win32gui.IsWindowVisible(child_hwnd):
                    return True
                parent_title = ""
                try:
                    parent_title = str(win32gui.GetWindowText(parent_hwnd) or "").strip()
                except Exception:
                    parent_title = ""
                _append_info(int(child_hwnd), parent_hwnd=int(parent_hwnd or 0), parent_title=parent_title)
            except Exception:
                return True
            return True

        def _enum_proc(hwnd, _lparam):
            try:
                if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                    return True
                _append_info(int(hwnd))
                try:
                    win32gui.EnumChildWindows(hwnd, _enum_child_proc, hwnd)
                except Exception:
                    pass
            except Exception:
                return True
            return True

        try:
            win32gui.EnumWindows(_enum_proc, None)
        except Exception:
            return []
        return items

    def _read_window_info(self, hwnd: int, parent_hwnd: int = 0, parent_title: str = "") -> Optional[AutoAdjustWindowInfo]:
        if not _WIN32_AVAILABLE or not hwnd:
            return None
        try:
            title = str(win32gui.GetWindowText(hwnd) or "").strip()
        except Exception:
            title = ""
        try:
            class_name = _read_window_class_name(hwnd)
        except Exception:
            class_name = ""
        try:
            client_width, client_height = _read_window_client_size(hwnd)
        except Exception:
            client_width, client_height = None, None
        try:
            dpi = _read_window_dpi(hwnd, fallback_dpi=None)
        except Exception:
            dpi = None

        process_name = ""
        if psutil is not None and win32process is not None:
            try:
                _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
                if process_id:
                    process_name = str(psutil.Process(process_id).name() or "").strip()
            except Exception:
                process_name = ""

        scale_factor = None
        if dpi:
            try:
                scale_factor = round(float(dpi) / 96.0, 4)
            except Exception:
                scale_factor = None

        normalized_parent_title = str(parent_title or "").strip()
        normalized_parent_hwnd = int(parent_hwnd or 0)
        return AutoAdjustWindowInfo(
            hwnd=int(hwnd),
            title=title,
            class_name=class_name,
            process_name=process_name,
            client_width=client_width,
            client_height=client_height,
            dpi=dpi,
            scale_factor=scale_factor,
            parent_hwnd=normalized_parent_hwnd,
            parent_title=normalized_parent_title,
            is_child_window=bool(normalized_parent_hwnd),
        )

    @staticmethod
    def _score_window(item: AutoAdjustWindowInfo, requirement: TargetWindowRequirement) -> Optional[tuple]:
        title = str(item.title or "").lower()
        parent_title = str(item.parent_title or "").lower()
        class_name = str(item.class_name or "").lower()
        process_name = str(item.process_name or "").lower()

        if requirement.title_keywords:
            keywords = [str(keyword or "").strip().lower() for keyword in requirement.title_keywords if str(keyword or "").strip()]
            if keywords and not any(keyword in title or keyword in parent_title for keyword in keywords):
                return None

        if requirement.class_names:
            class_names = {str(name or "").strip().lower() for name in requirement.class_names if str(name or "").strip()}
            if class_names and class_name not in class_names:
                return None

        if requirement.process_names:
            process_names = {str(name or "").strip().lower() for name in requirement.process_names if str(name or "").strip()}
            if process_names and process_name not in process_names:
                return None

        width_penalty = MarketPrecheckAutoAdjuster._dimension_penalty(
            item.client_width,
            requirement.client_width,
            requirement.min_client_width,
            requirement.max_client_width,
        )
        height_penalty = MarketPrecheckAutoAdjuster._dimension_penalty(
            item.client_height,
            requirement.client_height,
            requirement.min_client_height,
            requirement.max_client_height,
        )

        dpi_penalty = 0 if requirement.dpi is None or item.dpi is None else abs(int(item.dpi) - int(requirement.dpi))
        scale_penalty = 0.0
        if requirement.scale_factor is not None and item.scale_factor is not None:
            scale_penalty = abs(float(item.scale_factor) - float(requirement.scale_factor))

        title_length_penalty = len(item.title or item.parent_title or "")
        child_penalty = 0 if item.is_child_window else 1
        return (width_penalty + height_penalty, dpi_penalty, scale_penalty, child_penalty, title_length_penalty)

    @staticmethod
    def _dimension_penalty(
        current_value: Optional[int],
        expected_value: Optional[int],
        min_value: Optional[int],
        max_value: Optional[int],
    ) -> int:
        if current_value is None:
            return 100000
        current = int(current_value)
        if expected_value is not None:
            return abs(current - int(expected_value))
        if min_value is not None and current < int(min_value):
            return int(min_value) - current
        if max_value is not None and current > int(max_value):
            return current - int(max_value)
        return 0

    def _try_resize_window(self, item: AutoAdjustWindowInfo, requirement: TargetWindowRequirement) -> bool:
        target_width = self._resolve_target_dimension(
            item.client_width,
            requirement.client_width,
            requirement.min_client_width,
            requirement.max_client_width,
        )
        target_height = self._resolve_target_dimension(
            item.client_height,
            requirement.client_height,
            requirement.min_client_height,
            requirement.max_client_height,
        )
        if target_width is None or target_height is None:
            return False
        if item.client_width == target_width and item.client_height == target_height:
            return False
        if self._set_window_client_size(item.hwnd, target_width, target_height):
            return self._wait_for_client_size(item.hwnd, target_width, target_height)
        if self._set_window_client_size_with_manager(item.hwnd, target_width, target_height):
            return self._wait_for_client_size(item.hwnd, target_width, target_height)
        return False

    @staticmethod
    def _window_size_still_mismatch(item: AutoAdjustWindowInfo, requirement: TargetWindowRequirement) -> bool:
        width = item.client_width
        height = item.client_height
        if requirement.client_width is not None and width != int(requirement.client_width):
            return True
        if requirement.client_height is not None and height != int(requirement.client_height):
            return True
        if requirement.min_client_width is not None and (width is None or width < int(requirement.min_client_width)):
            return True
        if requirement.max_client_width is not None and (width is None or width > int(requirement.max_client_width)):
            return True
        if requirement.min_client_height is not None and (height is None or height < int(requirement.min_client_height)):
            return True
        if requirement.max_client_height is not None and (height is None or height > int(requirement.max_client_height)):
            return True
        return False

    @staticmethod
    def _resolve_target_dimension(
        current_value: Optional[int],
        expected_value: Optional[int],
        min_value: Optional[int],
        max_value: Optional[int],
    ) -> Optional[int]:
        if expected_value is not None:
            return int(expected_value)
        if min_value is None and max_value is None:
            return current_value
        if current_value is None:
            if min_value is not None:
                return int(min_value)
            if max_value is not None:
                return int(max_value)
            return None
        current = int(current_value)
        if min_value is not None and current < int(min_value):
            return int(min_value)
        if max_value is not None and current > int(max_value):
            return int(max_value)
        return current

    def _wait_for_client_size(self, hwnd: int, width: int, height: int, attempts: int = 8, delay: float = 0.12) -> bool:
        if not hwnd or width <= 0 or height <= 0:
            return False
        for _ in range(max(1, int(attempts))):
            current_width, current_height = _read_window_client_size(hwnd)
            if current_width == width and current_height == height:
                return True
            time.sleep(max(0.02, float(delay)))
        final_width, final_height = _read_window_client_size(hwnd)
        return final_width == width and final_height == height

    def _set_window_client_size_with_manager(self, hwnd: int, width: int, height: int) -> bool:
        if not hwnd or width <= 0 or height <= 0:
            return False
        try:
            from utils.universal_window_manager import get_universal_window_manager
        except Exception:
            return False
        try:
            manager = get_universal_window_manager()
            result = manager.adjust_single_window(hwnd, width, height, async_mode=False)
        except Exception:
            return False
        if not getattr(result, 'success', False):
            return False
        after_size = tuple(getattr(result, 'after_size', ()) or ())
        if len(after_size) == 2 and after_size[0] == width and after_size[1] == height:
            return True
        return self._wait_for_client_size(hwnd, width, height, attempts=10, delay=0.15)

    def _set_window_client_size(self, hwnd: int, width: int, height: int) -> bool:
        if not _WIN32_AVAILABLE or not hwnd or width <= 0 or height <= 0:
            return False

        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        except Exception:
            pass

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        outer_width = max(1, int(right - left))
        outer_height = max(1, int(bottom - top))

        for _ in range(3):
            current_client_width, current_client_height = _read_window_client_size(hwnd)
            if current_client_width == width and current_client_height == height:
                return True

            frame_width = max(0, outer_width - int(current_client_width or 0))
            frame_height = max(0, outer_height - int(current_client_height or 0))
            target_outer_width = max(1, int(width + frame_width))
            target_outer_height = max(1, int(height + frame_height))

            try:
                win32gui.MoveWindow(hwnd, left, top, target_outer_width, target_outer_height, True)
            except Exception:
                return False

            time.sleep(0.08)
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            outer_width = max(1, int(right - left))
            outer_height = max(1, int(bottom - top))

        final_width, final_height = _read_window_client_size(hwnd)
        return final_width == width and final_height == height

    @staticmethod
    def _build_bound_window_entry(item: AutoAdjustWindowInfo) -> Dict[str, Any]:
        display_title = str(item.title or item.parent_title or item.hwnd).strip()
        return {
            "title": display_title,
            "enabled": True,
            "hwnd": int(item.hwnd),
            "is_child_window": bool(item.is_child_window),
            "parent_hwnd": int(item.parent_hwnd or 0),
            "parent_title": str(item.parent_title or "").strip(),
            "dpi_info": {
                "dpi": item.dpi,
                "scale_factor": item.scale_factor,
                "method": "market_auto_adjust",
                "recorded_at": time.time(),
            },
        }
