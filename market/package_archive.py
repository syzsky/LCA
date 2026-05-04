# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import List, Tuple

from .models import MarketPackageManifest
from .package_identity import validate_package_identity
from .protection import PAYLOAD_FILENAME


MARKET_PACKAGE_MANIFEST_NAME = "manifest.json"


def load_manifest_from_archive(archive_path: str | Path) -> MarketPackageManifest:
    archive = Path(archive_path)
    with zipfile.ZipFile(archive, "r") as package_zip:
        with package_zip.open(MARKET_PACKAGE_MANIFEST_NAME, "r") as manifest_file:
            raw = json.loads(manifest_file.read().decode("utf-8"))
    return MarketPackageManifest.from_dict(raw)


def list_archive_files(archive_path: str | Path) -> List[str]:
    archive = Path(archive_path)
    with zipfile.ZipFile(archive, "r") as package_zip:
        return sorted(package_zip.namelist())


def validate_archive_basic_structure(archive_path: str | Path) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    try:
        manifest = load_manifest_from_archive(archive_path)
    except KeyError:
        return False, ["缺少 manifest.json"]
    except Exception as exc:
        return False, [f"读取共享平台包失败: {exc}"]

    try:
        validate_package_identity(manifest.package_id, manifest.version)
    except ValueError as exc:
        errors.append(f"manifest 身份无效: {exc}")
    if not manifest.title:
        errors.append("manifest.title 不能为空")
    if not manifest.entry_workflow:
        errors.append("manifest.entry_workflow 不能为空")

    archive_files = set(list_archive_files(archive_path))
    if getattr(manifest.protection, "enabled", False):
        payload_path = str(getattr(manifest.protection, "payload_path", "") or PAYLOAD_FILENAME).strip().replace("\\", "/")
        if payload_path not in archive_files:
            errors.append(f"缺少加密载荷文件: {payload_path}")
    else:
        if manifest.entry_workflow not in archive_files:
            errors.append(f"缺少入口工作流文件: {manifest.entry_workflow}")

    return len(errors) == 0, errors
