from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any, Dict, Sequence

from services.map_navigation.runtime.runtime import run_lkmaptools_runtime
from services.map_navigation.subprocess_protocol import (
    MAP_NAVIGATION_SUBPROCESS_STANDALONE_FLAG,
    get_map_navigation_subprocess_runtime_dir,
    normalize_map_navigation_subprocess_request,
    read_map_navigation_subprocess_json,
    write_map_navigation_subprocess_json,
)


logger = logging.getLogger(__name__)


def _build_error_response(detail: str) -> Dict[str, Any]:
    text = str(detail or "").strip() or "地图导航子程序执行失败"
    return {
        "success": False,
        "detail": text,
        "payload": {
            "success": False,
            "error": text,
        },
    }


def _validate_runtime_protocol_path(path: str, *, label: str) -> str:
    normalized_path = os.path.abspath(str(path or "").strip())
    if not normalized_path:
        raise ValueError(f"地图导航子程序{label}文件路径无效")

    runtime_dir = os.path.abspath(get_map_navigation_subprocess_runtime_dir())
    try:
        in_runtime_dir = os.path.commonpath([normalized_path, runtime_dir]) == runtime_dir
    except ValueError:
        in_runtime_dir = False
    if not in_runtime_dir:
        raise ValueError(f"地图导航子程序{label}文件必须位于运行目录内")
    return normalized_path


def run_map_navigation_subprocess_standalone(input_path: str, output_path: str) -> None:
    response: Dict[str, Any]
    safe_output_path = ""
    try:
        safe_input_path = _validate_runtime_protocol_path(input_path, label="输入")
        safe_output_path = _validate_runtime_protocol_path(output_path, label="输出")
        logger.info("[地图导航子进程Runner] 读取输入: input=%s output=%s", safe_input_path, safe_output_path)
        request = normalize_map_navigation_subprocess_request(
            read_map_navigation_subprocess_json(safe_input_path),
            require_card_origin=True,
        )
        params = dict(request["params"])
        logger.info(
            "[地图导航子进程Runner] 请求有效: workflow=%s card=%s hwnd=%s bundle=%s",
            str(request.get("workflow_id", "") or "").strip() or "default",
            int(request.get("card_id", 0) or 0),
            int(request.get("target_hwnd", 0) or 0),
            str(params.get("bundle_path", "") or "").strip(),
        )
        response = run_lkmaptools_runtime(request, safe_output_path)
        logger.info(
            "[地图导航子进程Runner] 运行完成: success=%s detail=%s",
            bool(response.get("success")),
            str(response.get("detail", "") or "").strip(),
        )
    except Exception as exc:
        logger.exception("[地图导航子进程Runner] 执行失败")
        response = _build_error_response(str(exc))

    if not safe_output_path:
        logger.warning("[地图导航子进程Runner] 输出路径未通过校验，跳过写入响应")
        return

    logger.info("[地图导航子进程Runner] 写入输出: %s", safe_output_path)
    write_map_navigation_subprocess_json(safe_output_path, response)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the standalone map navigation subprocess.")
    parser.add_argument(MAP_NAVIGATION_SUBPROCESS_STANDALONE_FLAG, action="store_true")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    resolved_argv = tuple(sys.argv[1:] if argv is None else argv)
    args = _parse_args(resolved_argv)
    standalone_flag_name = MAP_NAVIGATION_SUBPROCESS_STANDALONE_FLAG.lstrip("-").replace("-", "_")
    if not bool(getattr(args, standalone_flag_name, False)):
        return 2
    run_map_navigation_subprocess_standalone(args.input, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
