# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

from services.map_navigation.bundle_runtime import DEFAULT_BUNDLE_NAME, get_default_bundle_dir
from services.map_navigation.runtime import bridge
from services.map_navigation.runtime.minimap_region import resolve_minimap_capture_region
from utils.dpi_awareness import enable_process_dpi_awareness


_MINIMAP_REGION_ENV_NAME = "LCA_LKMAPTOOLS_MINIMAP_REGION"
_TARGET_HWND_ENV_NAME = "LCA_LKMAPTOOLS_TARGET_HWND"
_ORT_PROVIDER_ENV_NAME = "LCA_LKMAPTOOLS_ORT_PROVIDER"
_ORT_PROVIDER_ALIASES = {
    "": "auto",
    "auto": "auto",
    "cpu": "cpu",
    "cpuexecutionprovider": "cpu",
    "cuda": "cuda",
    "cudaexecutionprovider": "cuda",
    "dml": "dml",
    "directml": "dml",
    "dmlexecutionprovider": "dml",
}

logger = logging.getLogger(__name__)


def _resolve_bundle_base_dir(bundle_path: Any) -> str:
    text = str(bundle_path or "").strip()
    if not text:
        fallback_dir = str(get_default_bundle_dir(DEFAULT_BUNDLE_NAME) or "").strip()
        if not fallback_dir:
            fallback_dir = os.getcwd()
        logger.info("[地图导航运行时] 未配置地图资源目录，回退到默认资源目录: %s", fallback_dir)
        return fallback_dir
    normalized = os.path.abspath(os.path.expanduser(text))
    if os.path.isfile(normalized):
        normalized = os.path.dirname(normalized)
    if not os.path.isdir(normalized):
        raise ValueError(f"地图资源目录不存在: {normalized}")
    return normalized


def _build_absolute_minimap_region(params: Dict[str, Any], *, target_hwnd: int) -> dict[str, int]:
    absolute_region = resolve_minimap_capture_region(params, target_hwnd=target_hwnd)
    if absolute_region is None:
        raise ValueError("小地图区域参数无效")
    return absolute_region


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "y", "on", "开启", "是"}:
        return True
    if text in {"0", "false", "no", "n", "off", "关闭", "否"}:
        return False
    return bool(default)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _resolve_ort_provider(params: Dict[str, Any]) -> str:
    value = ""
    if isinstance(params, dict):
        value = str(params.get("ort_provider", "") or "").strip().lower()
    return _ORT_PROVIDER_ALIASES.get(value, "auto")


def _request_uses_minimap_region(params: Dict[str, Any]) -> bool:
    if not isinstance(params, dict):
        return False
    return _coerce_bool(params.get("use_region"), False)


def run_lkmaptools_runtime(request: Dict[str, Any], output_path: str) -> Dict[str, Any]:
    params = request.get("params")
    if not isinstance(params, dict):
        raise ValueError("地图导航参数无效")

    enable_process_dpi_awareness()

    logger.info(
        "[地图导航运行时] 启动请求: workflow=%s card=%s hwnd=%s output=%s bundle=%s",
        str(request.get("workflow_id", "") or "").strip() or "default",
        int(request.get("card_id", 0) or 0),
        int(request.get("target_hwnd", 0) or 0),
        str(output_path or "").strip(),
        str(params.get("bundle_path", "") or "").strip(),
    )

    base_dir = _resolve_bundle_base_dir(params.get("bundle_path"))
    force_selector = _coerce_bool(params.get("force_selector"), True)
    ort_provider = _resolve_ort_provider(params)
    request_uses_region = _request_uses_minimap_region(params)
    if request_uses_region:
        if force_selector:
            logger.info("[地图导航运行时] 检测到工作流已传入小地图区域，跳过校准器并在运行时直接解析")
        force_selector = False
    previous_cwd = os.getcwd()
    previous_base_dir = os.environ.get("LCA_LKMAPTOOLS_BASE_DIR")
    previous_minimap_region = os.environ.get(_MINIMAP_REGION_ENV_NAME)
    previous_target_hwnd = os.environ.get(_TARGET_HWND_ENV_NAME)
    previous_ort_provider = os.environ.get(_ORT_PROVIDER_ENV_NAME)

    logger.info(
        "[地图导航运行时] 环境准备: base_dir=%s force_selector=%s ort_provider=%s request_uses_region=%s target_hwnd=%s",
        base_dir,
        force_selector,
        ort_provider,
        request_uses_region,
        int(request.get("target_hwnd", 0) or 0),
    )

    os.environ["LCA_LKMAPTOOLS_BASE_DIR"] = base_dir
    os.environ[_TARGET_HWND_ENV_NAME] = str(int(request.get("target_hwnd", 0) or 0))
    os.environ[_ORT_PROVIDER_ENV_NAME] = ort_provider
    os.environ.pop(_MINIMAP_REGION_ENV_NAME, None)

    bridge.configure_bridge(output_path, request, base_dir=base_dir)
    bridge.report_status("地图导航子程序已启动")

    try:
        os.chdir(base_dir)
        logger.info("[地图导航运行时] 已切换工作目录: %s", base_dir)

        if request_uses_region:
            absolute_region = _build_absolute_minimap_region(
                params,
                target_hwnd=int(request.get("target_hwnd", 0) or 0),
            )
            os.environ[_MINIMAP_REGION_ENV_NAME] = json.dumps(absolute_region, ensure_ascii=False)
            logger.info("[地图导航运行时] 已写入运行时小地图区域覆盖: %s", absolute_region)
        else:
            os.environ.pop(_MINIMAP_REGION_ENV_NAME, None)

        from services.map_navigation.runtime import main_ai

        logger.info("[地图导航运行时] 开始进入主运行循环")
        main_ai.run_bootstrapper(force_selector=force_selector)
        logger.info("[地图导航运行时] 主运行循环已退出")
        return bridge.build_final_response(exit_reason="窗口已关闭")
    except Exception:
        logger.exception("[地图导航运行时] 主运行循环异常退出")
        raise
    finally:
        os.chdir(previous_cwd)
        if previous_base_dir is None:
            os.environ.pop("LCA_LKMAPTOOLS_BASE_DIR", None)
        else:
            os.environ["LCA_LKMAPTOOLS_BASE_DIR"] = previous_base_dir
        if previous_minimap_region is None:
            os.environ.pop(_MINIMAP_REGION_ENV_NAME, None)
        else:
            os.environ[_MINIMAP_REGION_ENV_NAME] = previous_minimap_region
        if previous_target_hwnd is None:
            os.environ.pop(_TARGET_HWND_ENV_NAME, None)
        else:
            os.environ[_TARGET_HWND_ENV_NAME] = previous_target_hwnd
        if previous_ort_provider is None:
            os.environ.pop(_ORT_PROVIDER_ENV_NAME, None)
        else:
            os.environ[_ORT_PROVIDER_ENV_NAME] = previous_ort_provider
        logger.info("[地图导航运行时] 已恢复现场并退出: cwd=%s", previous_cwd)
