# -*- coding: utf-8 -*-
"""
点击执行适配器
将不同输入后端适配为 click_action_executor 所需接口：
- click(x, y, button, clicks, interval)
- mouse_down(x, y, button)
- mouse_up(x, y, button)
"""

from __future__ import annotations

from typing import Any, Optional

from .task_utils import precise_sleep


class ForegroundDriverSimulatorAdapter:
    """前台驱动适配器（click_mouse/mouse_down/mouse_up）。"""
    supports_atomic_click_hold = True

    def __init__(self, driver: Any):
        self._driver = driver

    def move_mouse(self, x: int, y: int, absolute: bool = True) -> bool:
        if not hasattr(self._driver, "move_mouse"):
            raise AttributeError("驱动不支持move_mouse方法")
        return bool(self._driver.move_mouse(int(x), int(y), absolute=bool(absolute)))

    def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
        interval: float = 0.0,
        duration: float = 0.0,
    ) -> bool:
        if not hasattr(self._driver, "click_mouse"):
            raise AttributeError("驱动不支持click_mouse方法")
        try:
            safe_duration = max(0.0, float(duration))
        except Exception:
            safe_duration = 0.0
        return bool(
            self._driver.click_mouse(
                x=int(x),
                y=int(y),
                button=button,
                clicks=int(clicks),
                interval=float(interval),
                duration=safe_duration,
            )
        )

    def mouse_down(self, x: int, y: int, button: str = "left") -> bool:
        if not hasattr(self._driver, "mouse_down"):
            raise AttributeError("驱动不支持mouse_down方法")
        return bool(
            self._driver.mouse_down(
                x=int(x),
                y=int(y),
                button=button,
            )
        )

    def mouse_up(self, x: int, y: int, button: str = "left") -> bool:
        if not hasattr(self._driver, "mouse_up"):
            raise AttributeError("驱动不支持mouse_up方法")
        return bool(
            self._driver.mouse_up(
                x=int(x),
                y=int(y),
                button=button,
            )
        )


class PluginSimulatorAdapter:
    """插件接口适配器（plugin.execute + PluginCapability）。"""
    supports_atomic_click_hold = False

    def __init__(
        self,
        plugin: Any,
        plugin_capability: Any,
        mouse_move_with_trajectory: bool,
        hwnd: Optional[int],
    ):
        self._plugin = plugin
        self._capability = plugin_capability
        self._mouse_move_with_trajectory = bool(mouse_move_with_trajectory)
        self._hwnd = hwnd

    def move_mouse(self, x: int, y: int, absolute: bool = True) -> bool:
        capability = getattr(self._capability, "MOUSE_MOVE", None)
        if capability is None:
            return False
        return bool(
            self._plugin.execute(
                capability,
                "mouse_move",
                int(x),
                int(y),
                mouse_move_with_trajectory=self._mouse_move_with_trajectory,
                hwnd=self._hwnd,
            )
        )

    def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
        interval: float = 0.0,
        duration: float = 0.0,
    ) -> bool:
        try:
            if float(duration) > 0:
                return False
        except Exception:
            pass
        try:
            safe_clicks = max(1, int(clicks))
        except Exception:
            safe_clicks = 1
        try:
            safe_interval = max(0.0, float(interval))
        except Exception:
            safe_interval = 0.0

        all_success = True
        for i in range(safe_clicks):
            click_ok = bool(
                self._plugin.execute(
                    self._capability.MOUSE_CLICK,
                    "mouse_click",
                    int(x),
                    int(y),
                    button,
                    mouse_move_with_trajectory=self._mouse_move_with_trajectory,
                    hwnd=self._hwnd,
                )
            )
            if not click_ok:
                all_success = False
            if i < safe_clicks - 1 and safe_interval > 0:
                precise_sleep(safe_interval)
        return all_success

    def mouse_down(self, x: int, y: int, button: str = "left") -> bool:
        return bool(
            self._plugin.execute(
                self._capability.MOUSE_DOWN,
                "mouse_down",
                int(x),
                int(y),
                button,
                mouse_move_with_trajectory=self._mouse_move_with_trajectory,
                hwnd=self._hwnd,
            )
        )

    def mouse_up(self, x: int, y: int, button: str = "left") -> bool:
        return bool(
            self._plugin.execute(
                self._capability.MOUSE_UP,
                "mouse_up",
                int(x),
                int(y),
                button,
                mouse_move_with_trajectory=self._mouse_move_with_trajectory,
                hwnd=self._hwnd,
            )
        )
