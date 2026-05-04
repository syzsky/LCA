# -*- coding: utf-8 -*-
"""OLA 运行时公共配置。"""

from __future__ import annotations

import os
import sys
import threading
from typing import Dict, Optional, Tuple


_DEFAULT_OLA_USER_CODE = "80c9733043fb41458c05c1c31df67323"
_DEFAULT_OLA_SOFT_CODE = "51fbb519a65b458a9cb31e0d1eebd906"
_DEFAULT_OLA_FEATURE_LIST = "OLA|OLAPlus"

DEFAULT_OLA_AUTH_SETTINGS: Dict[str, str] = {
    "user_code": "",
    "soft_code": "",
    "feature_list": "",
}

_runtime_lock = threading.RLock()
_runtime_user_code = _DEFAULT_OLA_USER_CODE
_runtime_soft_code = _DEFAULT_OLA_SOFT_CODE
_runtime_feature_list = _DEFAULT_OLA_FEATURE_LIST
_runtime_sdk_dir: Optional[str] = None


def _normalize_optional_string(value: object) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


def clone_ola_auth_settings() -> Dict[str, str]:
    return dict(DEFAULT_OLA_AUTH_SETTINGS)


def normalize_ola_auth_settings(config: Optional[dict]) -> Dict[str, str]:
    result = clone_ola_auth_settings()
    if not isinstance(config, dict):
        return result

    for key in result.keys():
        result[key] = _normalize_optional_string(config.get(key)) or ""

    return result


def _resolve_sdk_dir_override(config: Optional[dict]) -> Optional[str]:
    if not config:
        return None

    sdk_dir = _normalize_optional_string(config.get("sdk_dir"))
    if sdk_dir:
        return os.path.abspath(sdk_dir)

    dll_path = _normalize_optional_string(config.get("dll_path"))
    if not dll_path:
        return None

    normalized_path = os.path.abspath(dll_path)
    if os.path.isdir(normalized_path):
        return normalized_path
    return os.path.dirname(normalized_path)


def configure_ola_runtime(config: Optional[dict] = None) -> None:
    """同步 OLA 运行时配置，保证各链路读取同一份配置。"""
    auth_settings = normalize_ola_auth_settings(config)
    sdk_dir = _resolve_sdk_dir_override(config)

    global _runtime_user_code, _runtime_soft_code, _runtime_feature_list, _runtime_sdk_dir
    with _runtime_lock:
        _runtime_user_code = auth_settings["user_code"] or _DEFAULT_OLA_USER_CODE
        _runtime_soft_code = auth_settings["soft_code"] or _DEFAULT_OLA_SOFT_CODE
        _runtime_feature_list = auth_settings["feature_list"] or _DEFAULT_OLA_FEATURE_LIST
        _runtime_sdk_dir = sdk_dir


def get_ola_registration_info() -> Tuple[str, str, str]:
    with _runtime_lock:
        return _runtime_user_code, _runtime_soft_code, _runtime_feature_list


def get_ola_sdk_dir() -> str:
    with _runtime_lock:
        if _runtime_sdk_dir:
            return _runtime_sdk_dir

    if getattr(sys, "frozen", False):
        exe_path = os.path.abspath(sys.executable)
        try:
            exe_path = os.path.realpath(exe_path)
        except Exception:
            pass
        return os.path.join(os.path.dirname(exe_path), "OLA")

    current_file = os.path.abspath(__file__)
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
    )
    return os.path.join(project_root, "OLA")
