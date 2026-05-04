# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import threading
from copy import deepcopy
from typing import Any, Dict, Optional

from services.map_navigation.subprocess_protocol import write_map_navigation_subprocess_json

_STATE_LOCK = threading.RLock()
_OUTPUT_PATH = ""
_REQUEST: Dict[str, Any] = {}
_BASE_DIR = ""
_LAST_RESPONSE: Dict[str, Any] = {}

logger = logging.getLogger(__name__)


def _normalize_detail(value: Any) -> str:
    return str(value or "").strip()


def _build_base_payload() -> Dict[str, Any]:
    request = dict(_REQUEST or {})
    params = request.get("params")
    if not isinstance(params, dict):
        params = {}
    return {
        "success": False,
        "launched": True,
        "window_hwnd": int(request.get("target_hwnd", 0) or 0),
        "bundle_path": str(params.get("bundle_path", "") or "").strip(),
        "subprocess_pid": int(os.getpid()),
        "workflow_id": str(request.get("workflow_id", "") or "").strip() or "default",
        "card_id": int(request.get("card_id", 0) or 0),
        "runtime_base_dir": _BASE_DIR,
    }


def configure_bridge(output_path: str, request: Dict[str, Any], *, base_dir: str = "") -> None:
    global _OUTPUT_PATH, _REQUEST, _BASE_DIR, _LAST_RESPONSE
    with _STATE_LOCK:
        _OUTPUT_PATH = str(output_path or "").strip()
        _REQUEST = dict(request or {})
        _BASE_DIR = str(base_dir or "").strip()
        _LAST_RESPONSE = {
            "success": False,
            "detail": "",
            "payload": _build_base_payload(),
        }
    logger.info(
        "[地图导航桥接] 已配置: workflow=%s card=%s hwnd=%s output=%s base_dir=%s",
        str(_REQUEST.get("workflow_id", "") or "").strip() or "default",
        int(_REQUEST.get("card_id", 0) or 0),
        int(_REQUEST.get("target_hwnd", 0) or 0),
        _OUTPUT_PATH,
        _BASE_DIR,
    )


def get_request_params() -> Dict[str, Any]:
    with _STATE_LOCK:
        params = (_REQUEST or {}).get("params")
        return dict(params) if isinstance(params, dict) else {}


def _store_response(response: Dict[str, Any]) -> Dict[str, Any]:
    global _LAST_RESPONSE
    normalized = {
        "success": bool(response.get("success")),
        "detail": _normalize_detail(response.get("detail")),
        "payload": dict(response.get("payload") or {}),
    }
    _LAST_RESPONSE = normalized
    output_path = str(_OUTPUT_PATH or "").strip()
    if output_path:
        write_map_navigation_subprocess_json(output_path, normalized)
    return deepcopy(normalized)


def emit_response(success: bool, payload: Optional[Dict[str, Any]] = None, detail: str = "") -> Dict[str, Any]:
    merged_payload = _build_base_payload()
    if isinstance(payload, dict):
        merged_payload.update(payload)
    merged_payload["success"] = bool(success)
    response = {
        "success": bool(success),
        "detail": _normalize_detail(detail),
        "payload": merged_payload,
    }
    with _STATE_LOCK:
        return _store_response(response)


def _get_previous_payload() -> Dict[str, Any]:
    with _STATE_LOCK:
        previous_payload = _LAST_RESPONSE.get("payload") or {}
    return dict(previous_payload) if isinstance(previous_payload, dict) else {}


def report_status(detail: str, *, success: bool = False, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    logger.info("[地图导航状态] success=%s detail=%s", bool(success), _normalize_detail(detail))
    merged_payload = _get_previous_payload()
    merged_payload.pop("error", None)
    if isinstance(payload, dict):
        merged_payload.update(payload)
    return emit_response(success=success, payload=merged_payload, detail=detail)


def report_position(
    *,
    map_x: Optional[int],
    map_y: Optional[int],
    locked: bool,
    paused: bool = False,
    lost_count: int,
    match_mode: str,
    x1: Optional[int] = None,
    y1: Optional[int] = None,
    x2: Optional[int] = None,
    y2: Optional[int] = None,
    route_id: Optional[str] = None,
    nearest_route_index: Optional[int] = None,
    distance_to_next_point: Optional[float] = None,
    failure_reason: Optional[str] = None,
    valid_match_count: Optional[int] = None,
    raw_match_count: Optional[int] = None,
    max_confidence: Optional[float] = None,
    minimap_size: Optional[str] = None,
    search_size: Optional[str] = None,
) -> Dict[str, Any]:
    previous_locked = False
    previous_map_x = None
    previous_map_y = None
    with _STATE_LOCK:
        previous_payload = _LAST_RESPONSE.get("payload") or {}
        if isinstance(previous_payload, dict):
            previous_locked = bool(previous_payload.get("locked"))
            previous_map_x = previous_payload.get("map_x")
            previous_map_y = previous_payload.get("map_y")

    payload = {
        "map_x": map_x,
        "map_y": map_y,
        "confidence": 1.0 if locked and map_x is not None and map_y is not None else 0.0,
        "match_mode": str(match_mode or "").strip() or "LKMapTools",
        "locked": bool(locked),
        "lost_count": int(lost_count),
        "paused": bool(paused),
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "nearest_route_index": nearest_route_index,
        "distance_to_next_point": distance_to_next_point,
        "route_id": route_id,
        "failure_reason": _normalize_detail(failure_reason),
        "valid_match_count": None if valid_match_count is None else int(valid_match_count),
        "raw_match_count": None if raw_match_count is None else int(raw_match_count),
        "max_confidence": None if max_confidence is None else float(max_confidence),
        "minimap_size": _normalize_detail(minimap_size),
        "search_size": _normalize_detail(search_size),
    }
    if bool(paused):
        detail = "暂停定位"
    elif locked and map_x is not None and map_y is not None:
        detail = "定位成功"
    else:
        detail = "定位失败"

    if locked and not previous_locked:
        logger.info(
            "[地图导航定位] 已恢复: map=(%s,%s) lost_count=%s mode=%s search=(%s,%s)-(%s,%s)",
            map_x,
            map_y,
            int(lost_count),
            str(match_mode or "").strip() or "LKMapTools",
            x1,
            y1,
            x2,
            y2,
        )
    elif not locked and previous_locked:
        logger.warning(
            "[地图导航定位] 已丢失: last_map=(%s,%s) lost_count=%s mode=%s",
            previous_map_x,
            previous_map_y,
            int(lost_count),
            str(match_mode or "").strip() or "LKMapTools",
        )

    return emit_response(
        success=bool(locked and map_x is not None and map_y is not None),
        payload=payload,
        detail=detail,
    )


def report_error(detail: str, *, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged_payload = _get_previous_payload()
    if isinstance(payload, dict):
        merged_payload.update(payload)
    merged_payload["error"] = _normalize_detail(detail)
    logger.error("[地图导航错误] %s", _normalize_detail(detail))
    return emit_response(False, payload=merged_payload, detail=detail)


def get_last_response() -> Dict[str, Any]:
    with _STATE_LOCK:
        return deepcopy(_LAST_RESPONSE)


def build_final_response(exit_reason: str = "") -> Dict[str, Any]:
    with _STATE_LOCK:
        response = get_last_response()
    payload = response.get("payload") or {}
    if not isinstance(payload, dict):
        payload = _build_base_payload()
    payload["runtime_exit_reason"] = _normalize_detail(exit_reason)
    if exit_reason and not response.get("detail"):
        response["detail"] = _normalize_detail(exit_reason)
    response["payload"] = payload
    logger.info(
        "[地图导航桥接] 构建最终响应: success=%s detail=%s exit_reason=%s",
        bool(response.get("success")),
        _normalize_detail(response.get("detail")),
        _normalize_detail(exit_reason),
    )
    with _STATE_LOCK:
        return _store_response(response)
