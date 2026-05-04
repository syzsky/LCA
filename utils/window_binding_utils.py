# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional


DEFAULT_PLUGIN_OLA_BINDING: Dict[str, Any] = {
    "display_mode": "normal",
    "mouse_mode": "normal",
    "keypad_mode": "normal",
    "mode": 0,
    "mouse_move_with_trajectory": False,
    "input_lock": False,
    "sim_mode_type": 0,
    "pubstr": "",
}


def clone_default_plugin_ola_binding() -> Dict[str, Any]:
    return copy.deepcopy(DEFAULT_PLUGIN_OLA_BINDING)


def normalize_plugin_ola_binding(
    data: Optional[Dict[str, Any]],
    fallback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    base = clone_default_plugin_ola_binding()
    if isinstance(fallback, dict):
        base.update(copy.deepcopy(fallback))
    if isinstance(data, dict):
        base.update(copy.deepcopy(data))
    try:
        base["mode"] = max(0, int(base.get("mode", 0)))
    except Exception:
        base["mode"] = 0
    base["mouse_move_with_trajectory"] = bool(base.get("mouse_move_with_trajectory", False))
    base["input_lock"] = bool(base.get("input_lock", False))
    try:
        base["sim_mode_type"] = int(base.get("sim_mode_type", 0))
    except Exception:
        base["sim_mode_type"] = 0
    base["pubstr"] = str(base.get("pubstr", "") or "").strip()
    for key in ("display_mode", "mouse_mode", "keypad_mode"):
        base[key] = str(base.get(key, "normal") or "normal").strip().lower() or "normal"
    return base


def get_plugin_default_ola_binding(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    plugin_settings = config.get("plugin_settings", {}) if isinstance(config, dict) else {}
    ola_binding = plugin_settings.get("ola_binding", {}) if isinstance(plugin_settings, dict) else {}
    return normalize_plugin_ola_binding(ola_binding)


def normalize_plugin_bound_window(
    window_info: Optional[Dict[str, Any]],
    default_binding: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    item = copy.deepcopy(window_info) if isinstance(window_info, dict) else {}
    item.setdefault("title", "")
    item["enabled"] = bool(item.get("enabled", True))
    try:
        hwnd = int(item.get("hwnd", 0) or 0)
    except Exception:
        hwnd = 0
    if hwnd:
        item["hwnd"] = hwnd
    elif "hwnd" in item:
        item["hwnd"] = 0
    item["ola_binding"] = normalize_plugin_ola_binding(item.get("ola_binding"), fallback=default_binding)
    return item


def normalize_plugin_bound_windows(
    windows: Optional[List[Dict[str, Any]]],
    default_binding: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    if not isinstance(windows, list):
        return []
    return [normalize_plugin_bound_window(item, default_binding=default_binding) for item in windows if isinstance(item, dict)]


def is_plugin_runtime_enabled(config: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(config, dict):
        return False
    plugin_settings = config.get("plugin_settings", {})
    if not isinstance(plugin_settings, dict):
        return False
    return bool(plugin_settings.get("enabled", False))


def get_native_bound_windows(config: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(config, dict):
        return []
    windows = config.get("bound_windows", [])
    return windows if isinstance(windows, list) else []


def get_plugin_bound_windows(config: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(config, dict):
        return []
    return normalize_plugin_bound_windows(
        config.get("plugin_bound_windows", []),
        default_binding=get_plugin_default_ola_binding(config),
    )


def get_bound_windows_for_mode(
    config: Optional[Dict[str, Any]],
    plugin_mode: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    use_plugin = is_plugin_runtime_enabled(config) if plugin_mode is None else bool(plugin_mode)
    return get_plugin_bound_windows(config) if use_plugin else get_native_bound_windows(config)


def get_window_binding_mode_for_mode(
    config: Optional[Dict[str, Any]],
    plugin_mode: Optional[bool] = None,
) -> str:
    if not isinstance(config, dict):
        return "single"
    use_plugin = is_plugin_runtime_enabled(config) if plugin_mode is None else bool(plugin_mode)
    key = "plugin_window_binding_mode" if use_plugin else "window_binding_mode"
    mode = str(config.get(key, "single") or "single").strip().lower()
    return "multiple" if mode == "multiple" else "single"


def get_active_bound_windows(config: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if isinstance(config, dict):
        active = config.get("active_bound_windows")
        if isinstance(active, list):
            return active
    return get_bound_windows_for_mode(config)


def get_first_enabled_bound_window(
    windows: Optional[List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    first_valid = None
    if not isinstance(windows, list):
        return None
    for item in windows:
        if not isinstance(item, dict):
            continue
        if first_valid is None:
            first_valid = item
        if item.get("enabled", True):
            return item
    return first_valid


def get_active_bound_window(config: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return get_first_enabled_bound_window(get_active_bound_windows(config))


def get_active_bound_window_hwnd(config: Optional[Dict[str, Any]]) -> Optional[int]:
    window_info = get_active_bound_window(config)
    if not isinstance(window_info, dict):
        return None
    try:
        hwnd = int(window_info.get("hwnd", 0) or 0)
    except Exception:
        return None
    return hwnd or None


def get_active_window_binding_mode(config: Optional[Dict[str, Any]]) -> str:
    if isinstance(config, dict):
        mode = str(config.get("active_window_binding_mode", "") or "").strip().lower()
        if mode in {"single", "multiple"}:
            return mode
    return get_window_binding_mode_for_mode(config)


def get_active_target_window_title(config: Optional[Dict[str, Any]]) -> Optional[str]:
    window_info = get_active_bound_window(config)
    if isinstance(window_info, dict):
        title = str(window_info.get("title", "") or "").strip()
        if title:
            return title
    if isinstance(config, dict):
        title = str(config.get("target_window_title", "") or "").strip()
        if title:
            return title
    return None


def resolve_plugin_ola_binding(
    config: Optional[Dict[str, Any]],
    hwnd: Optional[int] = None,
    window_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    default_binding = get_plugin_default_ola_binding(config if isinstance(config, dict) else {})
    if isinstance(window_info, dict):
        return normalize_plugin_ola_binding(window_info.get("ola_binding"), fallback=default_binding)
    if hwnd:
        for item in get_plugin_bound_windows(config):
            if int(item.get("hwnd", 0) or 0) == int(hwnd):
                return normalize_plugin_ola_binding(item.get("ola_binding"), fallback=default_binding)
    return default_binding


def get_plugin_bind_args(
    config: Optional[Dict[str, Any]],
    hwnd: Optional[int] = None,
    window_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    binding = resolve_plugin_ola_binding(config, hwnd=hwnd, window_info=window_info)
    trajectory_config = binding.get("trajectory_config", {})
    if not isinstance(trajectory_config, dict):
        trajectory_config = {}
    return {
        "display_mode": binding.get("display_mode", "normal"),
        "mouse_mode": binding.get("mouse_mode", "normal"),
        "keypad_mode": binding.get("keypad_mode", "normal"),
        "bind_mode": binding.get("mode", 0),
        "input_lock": binding.get("input_lock", False),
        "mouse_move_with_trajectory": binding.get("mouse_move_with_trajectory", False),
        "sim_mode_type": binding.get("sim_mode_type", 0),
        "pubstr": binding.get("pubstr", ""),
        "trajectory_config": copy.deepcopy(trajectory_config),
    }


def get_plugin_binding_foreground_state(
    config: Optional[Dict[str, Any]],
    hwnd: Optional[int] = None,
    window_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    binding = resolve_plugin_ola_binding(config, hwnd=hwnd, window_info=window_info)
    display_mode = str(binding.get("display_mode", "normal") or "normal").strip().lower()
    mouse_mode = str(binding.get("mouse_mode", "normal") or "normal").strip().lower()
    keypad_mode = str(binding.get("keypad_mode", "normal") or "normal").strip().lower()
    display_foreground = display_mode == "normal"
    mouse_foreground = mouse_mode == "normal"
    keypad_foreground = keypad_mode == "normal"
    return {
        "display_mode": display_mode,
        "mouse_mode": mouse_mode,
        "keypad_mode": keypad_mode,
        "display_foreground": display_foreground,
        "mouse_foreground": mouse_foreground,
        "keypad_foreground": keypad_foreground,
        "any_foreground": display_foreground or mouse_foreground or keypad_foreground,
    }


def is_plugin_binding_foreground(
    config: Optional[Dict[str, Any]],
    hwnd: Optional[int] = None,
    window_info: Optional[Dict[str, Any]] = None,
) -> bool:
    state = get_plugin_binding_foreground_state(config, hwnd=hwnd, window_info=window_info)
    return bool(state.get("any_foreground", False))


def sync_runtime_window_binding_state(config: Optional[Dict[str, Any]]) -> None:
    if not isinstance(config, dict):
        return
    use_plugin = is_plugin_runtime_enabled(config)
    config["active_bound_windows"] = get_bound_windows_for_mode(config, plugin_mode=use_plugin)
    config["active_window_binding_mode"] = get_window_binding_mode_for_mode(config, plugin_mode=use_plugin)
    config["active_target_window_title"] = get_active_target_window_title(config)
