from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any, Dict, Tuple

from utils.app_paths import get_runtime_data_dir

MAP_NAVIGATION_SUBPROCESS_FLAG = "--map-navigation-worker"
MAP_NAVIGATION_SUBPROCESS_STANDALONE_FLAG = "--map-navigation-worker-standalone"
MAP_NAVIGATION_SUBPROCESS_EXE_NAME = "map_navigation_worker.exe"
MAP_NAVIGATION_SUBPROCESS_RELATIVE_DIR = os.path.join("workers", "map_navigation_worker")
MAP_NAVIGATION_CARD_REQUEST_SOURCE = "map_navigation_card"
_RUNTIME_DIR_NAME = "map_navigation_subprocess"
_SAFE_TOKEN_PATTERN = re.compile(r"[^0-9A-Za-z_-]+")


def get_map_navigation_subprocess_runtime_dir() -> str:
    runtime_dir = os.path.join(get_runtime_data_dir("LCA"), _RUNTIME_DIR_NAME)
    os.makedirs(runtime_dir, exist_ok=True)
    return runtime_dir


def _sanitize_token(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "default"
    sanitized = _SAFE_TOKEN_PATTERN.sub("_", text).strip("_")
    return sanitized or "default"


def create_map_navigation_subprocess_io_paths(
    workflow_id: str,
    card_id: int,
) -> Tuple[str, str]:
    runtime_dir = get_map_navigation_subprocess_runtime_dir()
    timestamp = int(time.time() * 1000)
    workflow_token = _sanitize_token(workflow_id)
    card_token = max(0, int(card_id))
    unique_token = uuid.uuid4().hex
    base_name = f"{workflow_token}_{card_token}_{timestamp}_{unique_token}"
    input_path = os.path.join(runtime_dir, f"{base_name}_input.json")
    output_path = os.path.join(runtime_dir, f"{base_name}_output.json")
    return input_path, output_path


def write_map_navigation_subprocess_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def read_map_navigation_subprocess_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("地图导航子程序协议文件格式无效")
    return payload


def cleanup_map_navigation_subprocess_files(*paths: str) -> None:
    for raw_path in paths:
        path = str(raw_path or "").strip()
        if not path:
            continue
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def build_map_navigation_card_launch_context(
    workflow_id: str,
    card_id: int,
    *,
    action: str,
) -> Dict[str, Any]:
    normalized_workflow_id = str(workflow_id or "").strip() or "default"
    normalized_card_id = int(card_id or 0)
    if normalized_card_id <= 0:
        raise ValueError("地图导航卡片上下文无效")
    normalized_action = str(action or "").strip() or "execute_task"
    return {
        "source": MAP_NAVIGATION_CARD_REQUEST_SOURCE,
        "workflow_id": normalized_workflow_id,
        "card_id": normalized_card_id,
        "action": normalized_action,
    }


def normalize_map_navigation_subprocess_request(
    request_payload: Dict[str, Any],
    *,
    require_card_origin: bool = False,
) -> Dict[str, Any]:
    if not isinstance(request_payload, dict):
        raise ValueError("地图导航子程序请求无效")

    workflow_id = str(request_payload.get("workflow_id", "") or "").strip() or "default"

    try:
        card_id = int(request_payload.get("card_id", 0) or 0)
    except Exception as exc:
        raise ValueError("地图导航子程序卡片信息无效") from exc

    try:
        target_hwnd = int(request_payload.get("target_hwnd", 0) or 0)
    except Exception as exc:
        raise ValueError("地图导航子程序窗口句柄无效") from exc

    params = request_payload.get("params")
    if not isinstance(params, dict):
        raise ValueError("地图导航子程序参数无效")

    normalized_request = {
        "workflow_id": workflow_id,
        "card_id": card_id,
        "target_hwnd": target_hwnd,
        "params": dict(params),
    }

    launch_context = request_payload.get("launch_context")
    if launch_context is None:
        if require_card_origin:
            raise ValueError("地图导航只能由地图导航卡片启动")
        return normalized_request

    if not isinstance(launch_context, dict):
        raise ValueError("地图导航子程序启动上下文无效")

    launch_source = str(launch_context.get("source", "") or "").strip()
    launch_workflow_id = str(launch_context.get("workflow_id", "") or "").strip() or workflow_id
    launch_action = str(launch_context.get("action", "") or "").strip() or "execute_task"
    try:
        launch_card_id = int(launch_context.get("card_id", card_id) or 0)
    except Exception as exc:
        raise ValueError("地图导航子程序启动卡片信息无效") from exc

    normalized_launch_context = {
        "source": launch_source,
        "workflow_id": launch_workflow_id,
        "card_id": launch_card_id,
        "action": launch_action,
    }
    normalized_request["launch_context"] = normalized_launch_context

    if not require_card_origin:
        return normalized_request

    if launch_source != MAP_NAVIGATION_CARD_REQUEST_SOURCE:
        raise ValueError("地图导航只能由地图导航卡片启动")
    if card_id <= 0 or launch_card_id <= 0:
        raise ValueError("地图导航缺少有效的卡片上下文")
    if launch_workflow_id != workflow_id or launch_card_id != card_id:
        raise ValueError("地图导航子程序启动上下文不匹配")

    return normalized_request
