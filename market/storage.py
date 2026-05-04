# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import uuid
from pathlib import Path

from market.package_identity import to_safe_storage_segment
from utils.app_paths import (
    get_market_cache_dir,
    get_market_installed_dir,
    get_market_packages_dir,
    get_market_root,
    get_market_runtime_dir,
    get_market_user_overrides_dir,
)


def _safe_segment(value: str) -> str:
    return to_safe_storage_segment(value)


def get_market_packages_root() -> Path:
    return Path(get_market_packages_dir())


def get_market_installed_root() -> Path:
    return Path(get_market_installed_dir())


def get_market_runtime_root() -> Path:
    return Path(get_market_runtime_dir())


def get_market_cache_root() -> Path:
    return Path(get_market_cache_dir())


def get_market_user_overrides_root() -> Path:
    return Path(get_market_user_overrides_dir())


def get_market_auth_state_path() -> Path:
    root = Path(get_market_root())
    root.mkdir(parents=True, exist_ok=True)
    return root / "author_auth.json"


def get_package_archive_dir(package_id: str, version: str) -> Path:
    path = get_market_packages_root() / _safe_segment(package_id) / _safe_segment(version)
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_package_install_dir(package_id: str, version: str) -> Path:
    path = get_market_installed_root() / _safe_segment(package_id) / _safe_segment(version)
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_package_runtime_dir(package_id: str) -> Path:
    path = get_market_runtime_root() / _safe_segment(package_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_package_user_override_path(package_id: str) -> Path:
    root = get_market_user_overrides_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{_safe_segment(package_id)}.json"


def get_package_archive_path(package_id: str, version: str, filename: str = "package.lca_market.zip") -> Path:
    return get_package_archive_dir(package_id, version) / filename


def get_market_cache_download_path(package_id: str, version: str, filename: str = "package.lca_market.zip") -> Path:
    path = get_market_cache_root() / _safe_segment(package_id) / _safe_segment(version)
    path.mkdir(parents=True, exist_ok=True)
    return path / str(filename or "package.lca_market.zip").strip().strip("/")


def get_installed_manifest_path(package_id: str, version: str) -> Path:
    return get_package_install_dir(package_id, version) / "manifest.json"


def path_exists(path: os.PathLike | str) -> bool:
    try:
        return Path(path).exists()
    except Exception:
        return False



def get_package_runtime_version_dir(package_id: str, version: str, access_mode: str = "run") -> Path:
    path = get_market_runtime_root() / _safe_segment(access_mode or "run") / _safe_segment(package_id) / _safe_segment(version)
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_package_runtime_session_dir(package_id: str, version: str, access_mode: str = "run") -> Path:
    session_dir = get_package_runtime_version_dir(package_id, version, access_mode=access_mode) / uuid.uuid4().hex
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir



def resolve_runtime_session_dir(path_value: os.PathLike | str) -> Path | None:
    if not path_value:
        return None
    try:
        runtime_root = get_market_runtime_root().resolve()
        target_path = Path(path_value).resolve()
        relative = target_path.relative_to(runtime_root)
    except Exception:
        return None
    parts = relative.parts
    if len(parts) < 4:
        return None
    return runtime_root.joinpath(*parts[:4])
