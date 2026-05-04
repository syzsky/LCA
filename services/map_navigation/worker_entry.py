from __future__ import annotations

import importlib
import json
import logging
import os
import sys
from typing import Sequence

from services.map_navigation.bundle_runtime import DEFAULT_BUNDLE_NAME, get_default_bundle_dir
from services.map_navigation.subprocess_protocol import MAP_NAVIGATION_SUBPROCESS_STANDALONE_FLAG
from utils.dpi_awareness import enable_process_dpi_awareness
from utils.worker_entry import ensure_project_main_runtime

_VERIFY_RUNTIME_FLAG = "--verify-runtime"
_VERIFY_OUTPUT_FLAG = "--verify-output"


def _has_flag(args: Sequence[str], flag: str) -> bool:
    target = str(flag or "").strip()
    if not target:
        return False
    prefix = f"{target}="
    return any(str(item or "").strip() == target or str(item or "").strip().startswith(prefix) for item in args)


def _extract_flag_value(args: Sequence[str], flag: str) -> str:
    target = str(flag or "").strip()
    if not target:
        return ""
    prefix = f"{target}="
    values = list(args)
    for index, item in enumerate(values):
        text = str(item or "").strip()
        if text.startswith(prefix):
            return text.split("=", 1)[1].strip()
        if text == target and index + 1 < len(values):
            return str(values[index + 1] or "").strip()
    return ""


def _write_json_file(path: str, payload: dict) -> None:
    normalized_path = os.path.abspath(str(path or "").strip())
    if not normalized_path:
        return
    os.makedirs(os.path.dirname(normalized_path), exist_ok=True)
    temp_path = f"{normalized_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    os.replace(temp_path, normalized_path)


def _load_bundle_map_paths(bundle_root: str) -> tuple[str, str]:
    config_path = os.path.join(bundle_root, "config.json")
    bundle_config = {}
    try:
        with open(config_path, "r", encoding="utf-8") as file:
            payload = json.load(file)
        if isinstance(payload, dict):
            bundle_config = payload
    except Exception:
        bundle_config = {}

    logic_map_name = str(bundle_config.get("LOGIC_MAP_PATH") or "big_map.png").strip() or "big_map.png"
    display_map_name = str(bundle_config.get("DISPLAY_MAP_PATH") or logic_map_name).strip() or logic_map_name
    logic_map_path = os.path.join(bundle_root, logic_map_name)
    display_map_path = os.path.join(bundle_root, display_map_name)
    if not os.path.exists(display_map_path):
        display_map_path = logic_map_path
    return logic_map_path, display_map_path


def _resolve_app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _run_runtime_self_check(args: Sequence[str], logger: logging.Logger) -> int:
    output_path = _extract_flag_value(args, _VERIFY_OUTPUT_FLAG)
    app_root = _resolve_app_root()
    bundle_root = str(get_default_bundle_dir(DEFAULT_BUNDLE_NAME) or "").strip()
    if not bundle_root:
        bundle_root = os.path.join(app_root, "resources", "map_navigation_bundles", DEFAULT_BUNDLE_NAME)
    theme_root = os.path.join(app_root, "themes")
    route_root = os.path.join(bundle_root, "routes")
    logic_map_path, display_map_path = _load_bundle_map_paths(bundle_root)
    result = {
        "success": False,
        "detail": "",
        "app_root": app_root,
        "modules": [],
        "paths": {},
        "route_file_count": 0,
    }
    required_paths = {
        "bundle_root": bundle_root,
        "bundle_config": os.path.join(bundle_root, "config.json"),
        "bundle_logic_map": logic_map_path,
        "bundle_display_map": display_map_path,
        "bundle_model": os.path.join(bundle_root, "loftr_model.onnx"),
        "bundle_routes": route_root,
        "theme_light": os.path.join(theme_root, "light.qss"),
        "theme_dark": os.path.join(theme_root, "dark.qss"),
        "theme_icon_check": os.path.join(theme_root, "icons", "check-white.svg"),
    }
    try:
        for name, path in required_paths.items():
            result["paths"][name] = {
                "path": path,
                "exists": os.path.exists(path),
            }

        missing_paths = [name for name, payload in result["paths"].items() if not bool(payload.get("exists"))]
        route_file_count = 0
        if os.path.isdir(route_root):
            for _root, _dirs, files in os.walk(route_root):
                route_file_count += sum(1 for name in files if str(name).lower().endswith(".json"))
        result["route_file_count"] = route_file_count
        if route_file_count <= 0:
            missing_paths.append("bundle_route_json")

        module_names = [
            "services.map_navigation.subprocess_runner",
            "services.map_navigation.runtime.runtime",
            "services.map_navigation.runtime.main_ai",
            "services.map_navigation.runtime.qt_ui",
            "services.map_navigation.runtime.screen_capture",
            "services.map_navigation.runtime.tracker_engine",
            "themes.theme_manager",
            "ui.widgets.custom_title_bar",
        ]
        for module_name in module_names:
            importlib.import_module(module_name)
            result["modules"].append(module_name)

        from themes import get_theme_manager

        theme_manager = get_theme_manager()
        if not theme_manager.load_stylesheet("light"):
            missing_paths.append("theme_light_stylesheet_load")
        if not theme_manager.load_stylesheet("dark"):
            missing_paths.append("theme_dark_stylesheet_load")

        if missing_paths:
            raise FileNotFoundError(f"地图导航运行时缺少依赖: {', '.join(missing_paths)}")

        result["success"] = True
        result["detail"] = "地图导航运行时依赖检查通过"
        logger.info("[地图导航独立程序] 运行时自检通过")
        return 0
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        result["success"] = False
        result["detail"] = detail
        logger.exception("[地图导航独立程序] 运行时自检失败")
        return 1
    finally:
        _write_json_file(output_path, result)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv) if argv is not None else list(sys.argv[1:])
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - [pid=%(process)d] - [%(module)s:%(lineno)d] - %(message)s",
    )
    logger = logging.getLogger(__name__)
    logger.info("[地图导航独立程序] 启动: argv=%s", args)
    ensure_project_main_runtime(
        entry_file=__file__,
        argv=args,
        logger=logger,
        runtime_label="地图导航独立程序",
    )
    enable_process_dpi_awareness()

    if _has_flag(args, _VERIFY_RUNTIME_FLAG):
        return _run_runtime_self_check(args, logger)

    from services.map_navigation.subprocess_runner import main as runner_main

    if MAP_NAVIGATION_SUBPROCESS_STANDALONE_FLAG not in args:
        args = [MAP_NAVIGATION_SUBPROCESS_STANDALONE_FLAG, *args]

    result = int(runner_main(args))
    logger.info("[地图导航独立程序] 退出: code=%s", result)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
