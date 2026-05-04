# -*- coding: utf-8 -*-

from __future__ import annotations

import os
from pathlib import Path

from market.package_identity import to_safe_storage_segment
from market.refs import build_market_workflow_ref, parse_market_workflow_ref
from utils.app_paths import get_market_installed_dir, get_market_runtime_dir



def _normalize_path(path_value: os.PathLike | str) -> Path | None:
    text = str(path_value or '').strip()
    if not text:
        return None
    try:
        return Path(text).expanduser().resolve(strict=False)
    except TypeError:
        return Path(os.path.abspath(os.path.expanduser(text)))
    except Exception:
        try:
            return Path(os.path.abspath(os.path.expanduser(text)))
        except Exception:
            return None



def _is_relative_to(path_value: Path, parent_path: Path) -> bool:
    try:
        path_value.relative_to(parent_path)
        return True
    except Exception:
        return False



def _collect_package_version_keys(package_id: str, version: str) -> tuple[list[str], list[str]]:
    package_keys: list[str] = []
    version_keys: list[str] = []

    raw_package_id = str(package_id or '').strip()
    raw_version = str(version or '').strip()

    for value in (to_safe_storage_segment(raw_package_id), raw_package_id):
        value = str(value or '').strip()
        if value and value not in package_keys:
            package_keys.append(value)

    for value in (to_safe_storage_segment(raw_version), raw_version):
        value = str(value or '').strip()
        if value and value not in version_keys:
            version_keys.append(value)

    return package_keys, version_keys



def package_scope_matches_ref(value: str, package_id: str, version: str) -> bool:
    ref_info = parse_market_workflow_ref(value)
    if ref_info is None:
        return False
    return (
        str(ref_info.get('package_id') or '').strip() == str(package_id or '').strip()
        and str(ref_info.get('version') or '').strip() == str(version or '').strip()
    )



def package_scope_matches_path(path_value: os.PathLike | str, package_id: str, version: str) -> bool:
    target_path = _normalize_path(path_value)
    package_keys, version_keys = _collect_package_version_keys(package_id, version)
    if target_path is None or not package_keys or not version_keys:
        return False

    installed_root = _normalize_path(get_market_installed_dir())
    if installed_root is not None:
        for package_key in package_keys:
            for version_key in version_keys:
                installed_dir = _normalize_path(installed_root / package_key / version_key)
                if installed_dir is not None and _is_relative_to(target_path, installed_dir):
                    return True

    runtime_root = _normalize_path(get_market_runtime_dir())
    if runtime_root is None:
        return False
    try:
        relative_path = target_path.relative_to(runtime_root)
    except Exception:
        return False
    parts = relative_path.parts
    return len(parts) >= 4 and parts[1] in package_keys and parts[2] in version_keys



def package_scope_matches_value(value: os.PathLike | str, package_id: str, version: str) -> bool:
    return package_scope_matches_ref(str(value or '').strip(), package_id, version) or package_scope_matches_path(
        value,
        package_id,
        version,
    )


def resolve_market_workflow_ref_from_value(value: os.PathLike | str) -> str:
    text = str(value or '').strip()
    if not text:
        return ''

    ref_info = parse_market_workflow_ref(text)
    if ref_info:
        return build_market_workflow_ref(
            str(ref_info.get('package_id') or '').strip(),
            str(ref_info.get('version') or '').strip(),
            str(ref_info.get('entry_workflow') or 'workflow/main.json').strip(),
        )

    target_path = _normalize_path(text)
    if target_path is None:
        return ''

    installed_root = _normalize_path(get_market_installed_dir())
    if installed_root is not None:
        try:
            relative_path = target_path.relative_to(installed_root)
        except Exception:
            relative_path = None
        if relative_path is not None:
            parts = relative_path.parts
            if len(parts) >= 3:
                entry_workflow = Path(*parts[2:]).as_posix()
                if entry_workflow and entry_workflow.lower().endswith('.json') and not entry_workflow.startswith('backups/'):
                    return build_market_workflow_ref(parts[0], parts[1], entry_workflow)

    runtime_root = _normalize_path(get_market_runtime_dir())
    if runtime_root is None:
        return ''
    try:
        relative_path = target_path.relative_to(runtime_root)
    except Exception:
        return ''
    parts = relative_path.parts
    if len(parts) < 5:
        return ''

    entry_workflow = Path(*parts[4:]).as_posix()
    if not entry_workflow or not entry_workflow.lower().endswith('.json') or entry_workflow.startswith('backups/'):
        return ''
    return build_market_workflow_ref(parts[1], parts[2], entry_workflow)
