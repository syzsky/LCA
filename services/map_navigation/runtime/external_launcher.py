# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

_EXTERNAL_APP_RELATIVE_DIR = Path("services") / "map_navigation" / "external_app" / "AIMapTracker4.0"
_TRACKER_EXE_NAME = "AIMapTracker.exe"
_MINIMAP_SETUP_EXE_NAME = "MinimapSetup.exe"
_CONFIG_FILE_NAME = "config.json"
_ROUTES_DIR_NAME = "routes"
_MODEL_FILE_NAME = "loftr_model.onnx"
_INTERNAL_DIR_NAME = "_internal"
_DEFAULT_MAP_FILE_NAMES = ("big_map.png", "big_map-1.png")


def get_external_app_dir() -> Path:
    app_dir = (Path(__file__).resolve().parents[3] / _EXTERNAL_APP_RELATIVE_DIR).resolve(strict=False)
    if not app_dir.is_dir():
        raise FileNotFoundError(f"外部地图导航程序目录不存在: {app_dir}")
    return app_dir


def get_tracker_executable_path() -> Path:
    executable_path = get_external_app_dir() / _TRACKER_EXE_NAME
    if not executable_path.is_file():
        raise FileNotFoundError(f"缺少地图导航主程序: {executable_path}")
    return executable_path


def get_minimap_setup_executable_path() -> Path:
    executable_path = get_external_app_dir() / _MINIMAP_SETUP_EXE_NAME
    if not executable_path.is_file():
        raise FileNotFoundError(f"缺少小地图校准程序: {executable_path}")
    return executable_path


def _normalize_bundle_dir(bundle_dir: Any) -> Path | None:
    text = str(bundle_dir or "").strip()
    if not text:
        return None
    normalized = Path(os.path.abspath(os.path.expanduser(text))).resolve(strict=False)
    if normalized.is_file():
        normalized = normalized.parent
    if not normalized.is_dir():
        raise FileNotFoundError(f"地图资源目录不存在: {normalized}")
    return normalized


def _ensure_path_within_app_dir(path: Path) -> Path:
    app_dir = get_external_app_dir()
    candidate = path.resolve(strict=False)
    if os.path.commonpath([str(candidate), str(app_dir)]) != str(app_dir):
        raise ValueError(f"目标路径超出外部地图导航目录: {candidate}")
    return candidate


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _copy_file(source_path: Path, destination_path: Path) -> None:
    if not source_path.is_file():
        return
    safe_destination = _ensure_path_within_app_dir(destination_path)
    safe_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, safe_destination)


def _replace_directory(source_dir: Path, destination_dir: Path) -> None:
    if not source_dir.is_dir():
        return
    safe_destination = _ensure_path_within_app_dir(destination_dir)
    if safe_destination.exists():
        shutil.rmtree(safe_destination)
    shutil.copytree(source_dir, safe_destination)


def _collect_map_relative_paths(bundle_root: Path, config_payload: dict[str, Any]) -> list[Path]:
    collected: list[Path] = []
    seen: set[str] = set()

    def _append_path(candidate: Path) -> None:
        normalized_key = candidate.as_posix().lower()
        if normalized_key in seen:
            return
        seen.add(normalized_key)
        collected.append(candidate)

    for config_key in ("LOGIC_MAP_PATH", "DISPLAY_MAP_PATH"):
        raw_value = str(config_payload.get(config_key, "") or "").strip()
        if not raw_value:
            continue
        configured_path = Path(raw_value)
        source_path = configured_path if configured_path.is_absolute() else (bundle_root / configured_path)
        resolved_source_path = source_path.resolve(strict=False)
        if not resolved_source_path.is_file():
            continue
        try:
            relative_path = resolved_source_path.relative_to(bundle_root.resolve(strict=False))
        except ValueError:
            relative_path = Path(resolved_source_path.name)
        _append_path(relative_path)

    if collected:
        return collected

    for file_name in _DEFAULT_MAP_FILE_NAMES:
        if (bundle_root / file_name).is_file():
            _append_path(Path(file_name))
    return collected


def prepare_external_app(bundle_dir: Any = None) -> Path:
    app_dir = get_external_app_dir()
    normalized_bundle_dir = _normalize_bundle_dir(bundle_dir)
    if normalized_bundle_dir is None:
        return app_dir
    if normalized_bundle_dir == app_dir:
        return app_dir

    logger.info("[地图导航外部UI] 开始同步资源: source=%s target=%s", normalized_bundle_dir, app_dir)
    bundle_config_path = normalized_bundle_dir / _CONFIG_FILE_NAME
    bundle_config_payload = _load_json_file(bundle_config_path)

    _copy_file(bundle_config_path, app_dir / _CONFIG_FILE_NAME)
    for relative_path in _collect_map_relative_paths(normalized_bundle_dir, bundle_config_payload):
        _copy_file(normalized_bundle_dir / relative_path, app_dir / relative_path)

    _replace_directory(
        normalized_bundle_dir / _ROUTES_DIR_NAME,
        app_dir / _ROUTES_DIR_NAME,
    )
    _copy_file(
        normalized_bundle_dir / _MODEL_FILE_NAME,
        app_dir / _INTERNAL_DIR_NAME / _MODEL_FILE_NAME,
    )
    logger.info("[地图导航外部UI] 资源同步完成: source=%s", normalized_bundle_dir)
    return app_dir


def has_valid_minimap_config(bundle_dir: Any = None) -> bool:
    app_dir = prepare_external_app(bundle_dir)
    config_payload = _load_json_file(app_dir / _CONFIG_FILE_NAME)
    minimap_payload = config_payload.get("MINIMAP")
    if not isinstance(minimap_payload, dict):
        return False
    try:
        left = int(minimap_payload.get("left", 0) or 0)
        top = int(minimap_payload.get("top", 0) or 0)
        width = int(minimap_payload.get("width", 0) or 0)
        height = int(minimap_payload.get("height", 0) or 0)
    except Exception:
        return False
    return left >= 0 and top >= 0 and width > 0 and height > 0


def _run_external_executable(executable_path: Path) -> int:
    app_dir = get_external_app_dir()
    logger.info("[地图导航外部UI] 启动程序: %s", executable_path)
    process = subprocess.Popen(
        [str(executable_path)],
        cwd=str(app_dir),
        env=os.environ.copy(),
    )
    return int(process.wait())


def run_minimap_setup(bundle_dir: Any = None) -> int:
    prepare_external_app(bundle_dir)
    exit_code = _run_external_executable(get_minimap_setup_executable_path())
    logger.info("[地图导航外部UI] 小地图校准程序已退出: exit_code=%s", exit_code)
    return exit_code


def run_tracker_app(bundle_dir: Any = None) -> int:
    prepare_external_app(bundle_dir)
    exit_code = _run_external_executable(get_tracker_executable_path())
    logger.info("[地图导航外部UI] 地图导航主程序已退出: exit_code=%s", exit_code)
    return exit_code


__all__ = [
    "get_external_app_dir",
    "get_tracker_executable_path",
    "get_minimap_setup_executable_path",
    "prepare_external_app",
    "has_valid_minimap_config",
    "run_minimap_setup",
    "run_tracker_app",
]
