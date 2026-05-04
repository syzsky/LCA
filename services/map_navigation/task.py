from __future__ import annotations

import gc
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

from services.map_navigation.bundle_runtime import (
    cleanup_managed_bundle_cache,
    get_default_bundle_dir as get_packaged_default_bundle_dir,
    resolve_bundle_dir as resolve_packaged_bundle_dir,
)
from services.map_navigation.subprocess_protocol import (
    build_map_navigation_card_launch_context,
    read_map_navigation_subprocess_json,
)
from services.map_navigation.subprocess_client import (
    cleanup_map_navigation_subprocesses,
    launch_map_navigation_subprocess,
)
from task_workflow.workflow_context import get_workflow_context
from tasks.task_utils import (
    get_recorded_region_binding_mismatch_detail,
    get_standard_action_params,
    get_standard_next_step_delay_params,
    handle_failure_action,
    handle_success_action,
    merge_params_definitions,
    resolve_region_selection_params,
)

TASK_TYPE = "地图导航"
TASK_NAME = "地图导航"
ROUTE_AUTO_OPTION = "不指定路线"
ORT_PROVIDER_OPTIONS = ["auto", "cpu", "dml", "cuda"]
MINIMAP_REGION_MODE_RUNTIME = "运行时校准"
MINIMAP_REGION_MODE_SELECTED = "指定区域"
logger = logging.getLogger(__name__)


def requires_input_lock(_params: Dict[str, Any]) -> bool:
    return False


def _resolve_workflow_id(**kwargs) -> str:
    executor = kwargs.get("executor")
    workflow_id = ""
    if executor is not None:
        workflow_id = str(getattr(executor, "workflow_id", "") or "").strip()
    if not workflow_id:
        workflow_id = str(kwargs.get("workflow_id", "") or "").strip()
    return workflow_id or "default"


def _resolve_card_id(card_id: Optional[int], **kwargs) -> int:
    if card_id is not None:
        return int(card_id)
    parameter_panel = kwargs.get("parameter_panel")
    panel_card_id = getattr(parameter_panel, "current_card_id", None)
    if panel_card_id is not None:
        return int(panel_card_id)
    return 0


def _get_parameter_host(**kwargs):
    return kwargs.get("parameter_panel") or kwargs.get("parameter_dialog")


def _set_host_text_value(host, name: str, value: str) -> None:
    if host is None or not hasattr(host, "widgets"):
        return
    widget = host.widgets.get(name)
    if widget is None:
        return
    if hasattr(widget, "setText"):
        widget.setText(str(value))


def _sync_host_changes(host, changes: Dict[str, Any]) -> None:
    if host is None or not changes:
        return
    if hasattr(host, "current_parameters"):
        host.current_parameters.update(changes)
    current_card_id = getattr(host, "current_card_id", None)
    if current_card_id is not None and hasattr(host, "parameters_changed"):
        try:
            host.parameters_changed.emit(current_card_id, dict(changes))
        except Exception:
            pass
    apply_fn = getattr(host, "_apply_parameters", None)
    if callable(apply_fn):
        try:
            apply_fn(auto_close=False)
        except Exception:
            pass


def _normalize_bundle_dir(bundle_path: Any) -> str:
    return resolve_packaged_bundle_dir(bundle_path)


def _iter_bundled_bundle_candidates() -> tuple[str, ...]:
    resolved_default_dir = get_packaged_default_bundle_dir()
    if not resolved_default_dir:
        return ()
    return (os.path.abspath(resolved_default_dir),)


def _get_default_bundle_dir() -> str:
    return str(get_packaged_default_bundle_dir() or "").strip()


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


def _normalize_ort_provider(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "auto"
    if text in ORT_PROVIDER_OPTIONS:
        return text
    if text == "cpuexecutionprovider":
        return "cpu"
    if text == "cudaexecutionprovider":
        return "cuda"
    if text in {"directml", "dmlexecutionprovider"}:
        return "dml"
    return "auto"


def _format_ort_provider_display(value: Any) -> str:
    normalized = _normalize_ort_provider(value)
    labels = {
        "auto": "自动(auto)",
        "cpu": "纯 CPU(cpu)",
        "dml": "DirectML(dml)",
        "cuda": "CUDA(cuda)",
    }
    return labels.get(normalized, "自动(auto)")


def _normalize_window_region(
    window_region: Optional[Tuple[int, int, int, int]],
) -> Optional[Tuple[int, int, int, int]]:
    if not isinstance(window_region, tuple) or len(window_region) != 4:
        return None
    try:
        x, y, width, height = [int(value) for value in window_region]
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


def _prepare_runtime_params(
    params: Dict[str, Any],
    *,
    target_hwnd: int,
    window_region: Optional[Tuple[int, int, int, int]] = None,
) -> Dict[str, Any]:
    runtime_params = dict(params or {})
    # 已移除“工作流结束时关闭导航”配置项，兼容旧工作流时忽略该参数。
    runtime_params.pop("close_on_workflow_finish", None)
    runtime_params["ort_provider"] = _normalize_ort_provider(runtime_params.get("ort_provider"))
    raw_region_mode = str(runtime_params.get("region_mode", "") or "").strip()
    region_mode, region_x, region_y, region_width, region_height = resolve_region_selection_params(
        runtime_params,
        default_mode=MINIMAP_REGION_MODE_RUNTIME,
    )
    explicit_region_selected = region_width > 0 and region_height > 0
    legacy_region_selected = not raw_region_mode and explicit_region_selected
    use_region = (
        _coerce_bool(runtime_params.get("use_region"), False)
        or (
            (
                str(region_mode or "").strip() == MINIMAP_REGION_MODE_SELECTED
                or legacy_region_selected
            )
            and explicit_region_selected
        )
    )

    if use_region and (region_width <= 0 or region_height <= 0):
        normalized_window_region = _normalize_window_region(window_region)
        if normalized_window_region is not None:
            region_x, region_y, region_width, region_height = normalized_window_region

    if raw_region_mode and str(region_mode or "").strip() != MINIMAP_REGION_MODE_SELECTED:
        use_region = False

    if not use_region:
        runtime_params.pop("force_selector", None)
        runtime_params["use_region"] = False
        return runtime_params

    if region_width <= 0 or region_height <= 0:
        raise ValueError("已启用小地图区域，但尚未框选有效区域")

    binding_mismatch_detail = get_recorded_region_binding_mismatch_detail(runtime_params, target_hwnd)
    if binding_mismatch_detail:
        raise ValueError(binding_mismatch_detail)

    recorded_region_hwnd = _coerce_int(runtime_params.get("region_hwnd"), 0)

    runtime_params.update(
        {
            "use_region": True,
            "region_x": int(region_x),
            "region_y": int(region_y),
            "region_width": int(region_width),
            "region_height": int(region_height),
            "region_hwnd": int(recorded_region_hwnd or target_hwnd or 0),
            "force_selector": False,
        }
    )
    return runtime_params


def _build_payload(
    *,
    success: bool,
    target_hwnd: int,
    bundle_path: str,
    launched: bool,
    launch_info: Optional[Dict[str, Any]] = None,
    detail: str = "",
) -> Dict[str, Any]:
    launch_info = dict(launch_info or {})
    return {
        "success": bool(success),
        "launched": bool(launched),
        "map_x": None,
        "map_y": None,
        "confidence": 0.0,
        "match_mode": "LKMapTools",
        "locked": False,
        "lost_count": 0,
        "x1": None,
        "y1": None,
        "x2": None,
        "y2": None,
        "route_id": None,
        "nearest_route_index": None,
        "distance_to_next_point": None,
        "window_hwnd": int(target_hwnd or 0),
        "bundle_path": str(bundle_path or "").strip(),
        "subprocess_pid": launch_info.get("pid"),
        "subprocess_key": launch_info.get("process_key"),
        "replaced_count": int(launch_info.get("replaced_count", 0) or 0),
        "error": str(detail or "").strip(),
    }


def _store_payload(workflow_id: str, card_id: int, payload: Dict[str, Any]) -> None:
    if card_id <= 0:
        return
    context = get_workflow_context(workflow_id)
    setter = getattr(context, "set_map_navigation_result", None)
    if callable(setter):
        setter(card_id, payload)


def _try_wait_initial_response(
    workflow_id: str,
    card_id: int,
    launch_info: Optional[Dict[str, Any]],
    *,
    timeout_seconds: float = 1.2,
) -> Optional[Dict[str, Any]]:
    _ = workflow_id
    _ = card_id
    output_path = str((launch_info or {}).get("output_path") or "").strip()
    if not output_path:
        return None

    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    while time.monotonic() < deadline:
        try:
            if os.path.exists(output_path):
                response = read_map_navigation_subprocess_json(output_path)
                if isinstance(response, dict):
                    return dict(response)
        except Exception:
            pass
        time.sleep(0.1)
    return None


def _try_wait_initial_result(
    workflow_id: str,
    card_id: int,
    launch_info: Optional[Dict[str, Any]],
    *,
    timeout_seconds: float = 2.0,
) -> Optional[Dict[str, Any]]:
    response = _try_wait_initial_response(
        workflow_id,
        card_id,
        launch_info,
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(response, dict):
        return None
    payload = response.get("payload")
    if (
        isinstance(payload, dict)
        and payload.get("map_x") is not None
        and payload.get("map_y") is not None
    ):
        return dict(payload)
    return None


def get_map_navigation_route_options(_bundle_path: str) -> list[str]:
    return [ROUTE_AUTO_OPTION]


def select_map_navigation_bundle(params: Dict[str, Any], **kwargs) -> Optional[bool]:
    try:
        from PySide6.QtWidgets import QFileDialog
    except Exception:
        logger.exception("[地图导航] 导入 QFileDialog 失败，无法选择地图资源目录")
        return False

    host = _get_parameter_host(**kwargs)
    parent = kwargs.get("main_window") or host
    current_path = str((params or {}).get("bundle_path") or "").strip()
    start_dir = current_path
    if start_dir and not os.path.isdir(start_dir):
        start_dir = os.path.dirname(start_dir)
    if not start_dir:
        bundled_dir = _get_default_bundle_dir()
        start_dir = os.path.dirname(bundled_dir) if bundled_dir else "."

    logger.info(
        "[地图导航] 打开地图资源目录选择器: start_dir=%s parent=%s",
        start_dir,
        type(parent).__name__ if parent is not None else "None",
    )

    selected_dir = QFileDialog.getExistingDirectory(
        parent,
        "选择地图资源目录",
        start_dir,
        QFileDialog.ShowDirsOnly,
    )
    if not selected_dir:
        logger.info("[地图导航] 用户取消选择地图资源目录")
        return None

    normalized_path = str(selected_dir).strip()
    _set_host_text_value(host, "bundle_path", normalized_path)
    _sync_host_changes(host, {"bundle_path": normalized_path})
    logger.info("[地图导航] 已选择地图资源目录: %s", normalized_path)
    return True


def execute_map_navigation_request(
    params: Dict[str, Any],
    *,
    target_hwnd: Optional[int],
    card_id: int,
    workflow_id: str,
) -> Tuple[bool, Dict[str, Any], str]:
    _ = card_id
    _ = workflow_id
    hwnd = int(target_hwnd or 0)
    try:
        bundle_dir = _normalize_bundle_dir(params.get("bundle_path"))
        if hwnd <= 0:
            raise ValueError("没有有效的窗口句柄")
        _prepare_runtime_params(params, target_hwnd=hwnd)
        payload = _build_payload(
            success=True,
            target_hwnd=hwnd,
            bundle_path=bundle_dir,
            launched=False,
        )
        return True, payload, ""
    except Exception as exc:
        detail = str(exc).strip() or "地图导航执行失败"
        payload = _build_payload(
            success=False,
            target_hwnd=hwnd,
            bundle_path=str(params.get("bundle_path", "") or "").strip(),
            launched=False,
            detail=detail,
        )
        return False, payload, detail


def get_params_definition() -> Dict[str, Dict[str, Any]]:
    params = {
        "---bundle---": {"type": "separator", "label": "地图资源"},
        "bundle_path": {
            "label": "地图资源目录",
            "type": "str",
            "default": _get_default_bundle_dir(),
            "required": True,
            "tooltip": "选择参考项目所需的整套资源目录",
        },
        "select_bundle_path_button": {
            "label": "选择地图资源",
            "type": "button",
            "button_text": "选择地图资源目录",
            "action": "select_map_navigation_bundle",
        },
    }
    return merge_params_definitions(
        params,
        get_standard_action_params(),
        get_standard_next_step_delay_params(),
    )


def _validate_launch_request(params: Dict[str, Any], target_hwnd: Optional[int]) -> tuple[int, str]:
    hwnd = int(target_hwnd or 0)
    if hwnd <= 0:
        raise ValueError("没有有效的窗口句柄")
    bundle_dir = _normalize_bundle_dir(params.get("bundle_path"))
    return hwnd, bundle_dir


def _prepare_launch_request(
    params: Dict[str, Any],
    *,
    target_hwnd: Optional[int],
    window_region: Optional[Tuple[int, int, int, int]] = None,
) -> tuple[int, str, Dict[str, Any]]:
    hwnd, bundle_dir = _validate_launch_request(params, target_hwnd)
    runtime_params = _prepare_runtime_params(
        dict(params or {}, bundle_path=bundle_dir),
        target_hwnd=hwnd,
        window_region=window_region,
    )
    runtime_params["bundle_path"] = bundle_dir
    return hwnd, bundle_dir, runtime_params


def _build_subprocess_request_payload(
    *,
    workflow_id: str,
    card_id: int,
    target_hwnd: int,
    params: Dict[str, Any],
    action: str,
) -> Dict[str, Any]:
    return {
        "workflow_id": workflow_id,
        "card_id": int(card_id or 0),
        "target_hwnd": int(target_hwnd or 0),
        "params": dict(params or {}),
        "launch_context": build_map_navigation_card_launch_context(
            workflow_id,
            int(card_id or 0),
            action=action,
        ),
    }


def execute_task(
    params: Dict[str, Any],
    counters: Dict[str, int],
    execution_mode: str,
    target_hwnd: Optional[int],
    window_region: Optional[Tuple[int, int, int, int]],
    card_id: Optional[int] = None,
    **kwargs,
):
    _ = counters
    _ = execution_mode
    workflow_id = _resolve_workflow_id(**kwargs)
    current_card_id = _resolve_card_id(card_id, **kwargs)
    stop_checker = kwargs.get("stop_checker")

    try:
        if callable(stop_checker) and stop_checker():
            raise RuntimeError("地图导航已停止")

        hwnd, bundle_dir, runtime_params = _prepare_launch_request(
            params,
            target_hwnd=target_hwnd,
            window_region=window_region,
        )
        launch_info = launch_map_navigation_subprocess(
            _build_subprocess_request_payload(
                workflow_id=workflow_id,
                card_id=current_card_id,
                target_hwnd=hwnd,
                params=runtime_params,
                action="execute_task",
            )
        )
        initial_response = _try_wait_initial_response(workflow_id, current_card_id, launch_info)
        if isinstance(initial_response, dict):
            initial_payload = initial_response.get("payload")
            initial_error = ""
            if isinstance(initial_payload, dict):
                initial_error = str(initial_payload.get("error", "") or "").strip()
            if initial_error:
                raise RuntimeError(initial_error)
        payload = _build_payload(
            success=True,
            target_hwnd=hwnd,
            bundle_path=bundle_dir,
            launched=True,
            launch_info=launch_info,
        )
        initial_payload = _try_wait_initial_result(workflow_id, current_card_id, launch_info)
        if isinstance(initial_payload, dict):
            initial_payload.setdefault("subprocess_pid", launch_info.get("pid"))
            initial_payload.setdefault("subprocess_key", launch_info.get("process_key"))
            initial_payload.setdefault("launched", True)
            payload = initial_payload
        _store_payload(workflow_id, current_card_id, payload)
        result = handle_success_action(params, current_card_id, stop_checker)
        return result[0], result[1], result[2], ""
    except Exception as exc:
        detail = str(exc).strip() or "地图导航执行失败"
        payload = _build_payload(
            success=False,
            target_hwnd=int(target_hwnd or 0),
            bundle_path=str(params.get("bundle_path", "") or "").strip(),
            launched=False,
            detail=detail,
        )
        _store_payload(workflow_id, current_card_id, payload)
        result = handle_failure_action(params, current_card_id, stop_checker)
        return result[0], result[1], result[2], detail


def execute_card(*args, **kwargs):
    return execute_task(*args, **kwargs)


def _show_test_message(title: str, text: str) -> None:
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance()
        if app is None:
            return

        def show_message():
            QMessageBox.information(None, title, text)

        QTimer.singleShot(0, app, show_message)
    except Exception:
        pass


def test_map_navigation(params: Dict[str, Any], target_hwnd: Optional[int] = None, **kwargs) -> bool:
    current_card_id = _resolve_card_id(None, **kwargs)
    workflow_id = _resolve_workflow_id(**kwargs)
    try:
        logger.info(
            "[map_navigation] test launch request: workflow=%s card=%s hwnd=%s bundle=%s ort_provider=%s",
            workflow_id,
            current_card_id,
            int(target_hwnd or 0),
            str((params or {}).get("bundle_path") or "").strip(),
            _normalize_ort_provider((params or {}).get("ort_provider")),
        )
        logger.info(
            "[地图导航] 准备启动测试: card=%s workflow=%s hwnd=%s bundle=%s",
            current_card_id,
            workflow_id,
            int(target_hwnd or 0),
            str((params or {}).get("bundle_path") or "").strip(),
        )
        hwnd, bundle_dir, runtime_params = _prepare_launch_request(
            params,
            target_hwnd=target_hwnd,
        )
        launch_info = launch_map_navigation_subprocess(
            _build_subprocess_request_payload(
                workflow_id=workflow_id,
                card_id=current_card_id,
                target_hwnd=hwnd,
                params=runtime_params,
                action="test_map_navigation",
            )
        )
        initial_response = _try_wait_initial_response(workflow_id, current_card_id, launch_info)
        if isinstance(initial_response, dict):
            initial_payload = initial_response.get("payload")
            initial_error = ""
            if isinstance(initial_payload, dict):
                initial_error = str(initial_payload.get("error", "") or "").strip()
            if initial_error:
                raise RuntimeError(initial_error)
        payload = _build_payload(
            success=True,
            target_hwnd=hwnd,
            bundle_path=bundle_dir,
            launched=True,
            launch_info=launch_info,
        )
        _store_payload(workflow_id, current_card_id, payload)
        logger.info(
            "[地图导航] 测试启动成功: card=%s hwnd=%s pid=%s bundle=%s",
            current_card_id,
            hwnd,
            int(launch_info.get("pid", 0) or 0),
            bundle_dir,
        )
        backend_text = _format_ort_provider_display(runtime_params.get("ort_provider"))
        if _coerce_bool(runtime_params.get("use_region"), False):
            message = (
                f"参考项目地图导航已启动，当前后端：{backend_text}。\n"
                "如果界面显示“待手动定位”或预览停在等待状态，请在弹出的大地图窗口中双击当前位置。"
            )
        else:
            message = (
                f"参考项目地图导航已启动，当前后端：{backend_text}。\n"
                "接下来会出现“小地图校准”和“大地图定位”窗口，完成后才会开始自动追踪。"
            )
        _show_test_message("地图导航", message)
        return True
    except Exception as exc:
        logger.exception("[地图导航] 测试启动失败")
        _show_test_message("地图导航", str(exc).strip() or "地图导航启动失败")
        return False


def cleanup_map_navigation_runtime_state(
    release_bundle_cache: bool = False,
    *,
    workflow_id: Optional[str] = None,
    target_hwnd: Optional[int] = None,
    auto_close_only: bool = False,
    include_orphans: bool = True,
) -> bool:
    success = True
    normalized_workflow_id = str(workflow_id or "").strip() or None
    normalized_target_hwnd = None if target_hwnd is None else int(target_hwnd or 0)

    try:
        cleanup_map_navigation_subprocesses(
            main_pid=os.getpid() if include_orphans and not normalized_workflow_id and not auto_close_only else None,
            workflow_id=normalized_workflow_id,
            target_hwnd=normalized_target_hwnd,
            auto_close_only=auto_close_only,
            include_orphans=include_orphans,
        )
    except Exception:
        success = False

    try:
        if normalized_workflow_id:
            from task_workflow.workflow_context import clear_all_map_navigation_data

            clear_all_map_navigation_data(workflow_id=normalized_workflow_id)
        else:
            from task_workflow.workflow_context import clear_all_map_navigation_runtime_data

            clear_all_map_navigation_runtime_data()
    except Exception:
        success = False

    if release_bundle_cache:
        try:
            cleanup_managed_bundle_cache()
        except Exception:
            success = False

    try:
        gc.collect()
    except Exception:
        pass
    return success


run = execute_task
