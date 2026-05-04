from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import struct
import time
import zipfile
from pathlib import Path

from utils.app_paths import get_app_root, get_resource_path, get_runtime_data_dir

DEFAULT_BUNDLE_NAME = "lkmaptools_ai_v2"
ARCHIVE_SUFFIX = ".lcares"
ARCHIVE_MAGIC = "LCA_MAP_BUNDLE_V1"
READY_MARKER_NAME = ".lca_bundle_ready.json"
MANAGED_CACHE_DIRNAME = "map_navigation_bundles"

REQUIRED_FILES = (
    "config.json",
    "big_map.png",
    "loftr_model.onnx",
    "manifest.json",
    "poi_catalog.json",
)

SAFE_MANIFEST_KEYS = (
    "name",
    "logic_map_path",
    "display_map_path",
    "routes_dir",
    "poi_catalog_path",
    "icons_dir",
    "route_categories",
    "default_region",
)
STRIPPED_JSON_KEYS = {
    "source",
    "page_url",
    "icon_url",
    "generated_at",
}


def _to_path(path_value: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.fspath(path_value))).resolve(strict=False)


def _decode_b64x2_secret(raw_text: str) -> bytes:
    first_pass = base64.b64decode(str(raw_text or "").strip().encode("utf-8"))
    second_pass = base64.b64decode(first_pass)
    return second_pass


def _load_secret_key(
    *,
    bundle_name: str = DEFAULT_BUNDLE_NAME,
    secret_file: str | os.PathLike[str] | None = None,
) -> bytes:
    if secret_file is None:
        secret_path = Path(get_app_root()) / "config" / "build_auth_secret.b64x2"
    else:
        secret_path = _to_path(secret_file)

    if not secret_path.is_file():
        raise FileNotFoundError(f"缺少构建密钥文件: {secret_path}")

    raw_text = secret_path.read_text(encoding="utf-8").strip()
    if not raw_text:
        raise ValueError(f"构建密钥文件为空: {secret_path}")

    decoded_secret = _decode_b64x2_secret(raw_text)
    return hashlib.sha256(
        b"LCA::MAP_NAV_BUNDLE::" + str(bundle_name or DEFAULT_BUNDLE_NAME).encode("utf-8") + b"::" + decoded_secret
    ).digest()


def _xor_bytes(payload: bytes, key: bytes) -> bytes:
    if not payload:
        return b""
    if not key:
        raise ValueError("地图资源密钥无效")

    key_length = len(key)
    output = bytearray(len(payload))
    for index, value in enumerate(payload):
        output[index] = value ^ key[index % key_length]
    return bytes(output)


def _bundle_archive_path(bundle_name: str = DEFAULT_BUNDLE_NAME) -> str:
    return os.path.abspath(get_resource_path("map_navigation_bundles", f"{bundle_name}{ARCHIVE_SUFFIX}"))


def _legacy_bundle_dirs(bundle_name: str = DEFAULT_BUNDLE_NAME) -> tuple[str, ...]:
    app_root = get_app_root()
    return (
        os.path.abspath(get_resource_path("map_navigation_bundles", bundle_name)),
        os.path.abspath(
            os.path.join(
                app_root,
                "workers",
                "map_navigation_worker",
                "resources",
                "map_navigation_bundles",
                bundle_name,
            )
        ),
    )


def _get_cache_root() -> Path:
    return _to_path(os.path.join(get_runtime_data_dir("LCA"), MANAGED_CACHE_DIRNAME))


def _ready_marker_path(bundle_dir: Path) -> Path:
    return bundle_dir / READY_MARKER_NAME


def _count_files(root_dir: Path, pattern: str) -> int:
    return sum(1 for _ in root_dir.rglob(pattern))


def validate_bundle_dir(bundle_dir: str | os.PathLike[str]) -> dict:
    root = _to_path(bundle_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"地图资源目录不存在: {root}")

    missing: list[str] = []
    for name in REQUIRED_FILES:
        if not (root / name).is_file():
            missing.append(name)

    route_count = _count_files(root / "routes", "*.json") if (root / "routes").is_dir() else 0
    icon_count = _count_files(root / "icons", "*.png") if (root / "icons").is_dir() else 0

    if route_count <= 0:
        missing.append("routes/*.json")
    if icon_count <= 0:
        missing.append("icons/*.png")

    if missing:
        joined = ", ".join(missing)
        raise FileNotFoundError(f"地图资源目录缺少必要文件: {joined}")

    return {
        "bundle_dir": str(root),
        "route_file_count": int(route_count),
        "icon_file_count": int(icon_count),
    }


def _sanitize_json_payload(payload):
    if isinstance(payload, dict):
        sanitized = {}
        for key, value in payload.items():
            normalized_key = str(key or "").strip()
            if normalized_key in STRIPPED_JSON_KEYS:
                continue
            sanitized[normalized_key] = _sanitize_json_payload(value)
        return sanitized
    if isinstance(payload, list):
        return [_sanitize_json_payload(item) for item in payload]
    return payload


def _load_sanitized_manifest(source_dir: Path) -> bytes:
    manifest_path = source_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"缺少地图资源 manifest.json: {manifest_path}")

    payload = _sanitize_json_payload(json.loads(manifest_path.read_text(encoding="utf-8")))
    if not isinstance(payload, dict):
        raise ValueError("地图资源 manifest.json 格式无效")

    sanitized = {}
    for key in SAFE_MANIFEST_KEYS:
        if key in payload:
            sanitized[key] = payload[key]

    if not isinstance(sanitized.get("route_categories"), list):
        sanitized["route_categories"] = []
    sanitized["name"] = "LCA 地图导航资源包"

    return json.dumps(sanitized, ensure_ascii=False, indent=2).encode("utf-8")


def _load_sanitized_json_bytes(file_path: Path, source_dir: Path) -> bytes:
    relative_name = file_path.relative_to(source_dir).as_posix()
    if relative_name == "manifest.json":
        return _load_sanitized_manifest(source_dir)

    payload = _sanitize_json_payload(json.loads(file_path.read_text(encoding="utf-8")))
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _build_archive_header(
    *,
    bundle_name: str,
    zip_payload: bytes,
    file_count: int,
    route_file_count: int,
    icon_file_count: int,
) -> bytes:
    header = {
        "magic": ARCHIVE_MAGIC,
        "bundle_name": str(bundle_name or DEFAULT_BUNDLE_NAME),
        "zip_sha256": hashlib.sha256(zip_payload).hexdigest(),
        "file_count": int(file_count),
        "route_file_count": int(route_file_count),
        "icon_file_count": int(icon_file_count),
    }
    return json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def build_secure_bundle_archive(
    *,
    source_dir: str | os.PathLike[str],
    output_file: str | os.PathLike[str],
    secret_file: str | os.PathLike[str] | None = None,
    bundle_name: str = DEFAULT_BUNDLE_NAME,
) -> dict:
    source_root = _to_path(source_dir)
    output_path = _to_path(output_file)
    bundle_meta = validate_bundle_dir(source_root)
    all_files = sorted(path for path in source_root.rglob("*") if path.is_file())
    file_count = len(all_files)

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file_path in all_files:
            relative_name = file_path.relative_to(source_root).as_posix()
            if file_path.suffix.lower() == ".json":
                archive.writestr(relative_name, _load_sanitized_json_bytes(file_path, source_root))
                continue
            archive.write(file_path, relative_name)

    zip_payload = memory_file.getvalue()
    secret_key = _load_secret_key(bundle_name=bundle_name, secret_file=secret_file)
    encrypted_payload = _xor_bytes(zip_payload, secret_key)
    header_bytes = _build_archive_header(
        bundle_name=bundle_name,
        zip_payload=zip_payload,
        file_count=file_count,
        route_file_count=int(bundle_meta["route_file_count"]),
        icon_file_count=int(bundle_meta["icon_file_count"]),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_bytes(struct.pack(">I", len(header_bytes)) + header_bytes + encrypted_payload)
    os.replace(temp_path, output_path)

    return {
        "output_file": str(output_path),
        "bundle_name": str(bundle_name),
        "file_count": int(file_count),
        "route_file_count": int(bundle_meta["route_file_count"]),
        "icon_file_count": int(bundle_meta["icon_file_count"]),
    }


def _read_archive_header_and_payload(archive_path: Path) -> tuple[dict, bytes]:
    raw_payload = archive_path.read_bytes()
    if len(raw_payload) < 4:
        raise ValueError(f"地图资源归档格式无效: {archive_path}")

    header_length = struct.unpack(">I", raw_payload[:4])[0]
    header_start = 4
    header_end = header_start + header_length
    if header_length <= 0 or header_end > len(raw_payload):
        raise ValueError(f"地图资源归档头部无效: {archive_path}")

    header = json.loads(raw_payload[header_start:header_end].decode("utf-8"))
    if not isinstance(header, dict) or header.get("magic") != ARCHIVE_MAGIC:
        raise ValueError(f"地图资源归档标识无效: {archive_path}")

    encrypted_payload = raw_payload[header_end:]
    if not encrypted_payload:
        raise ValueError(f"地图资源归档内容为空: {archive_path}")
    return header, encrypted_payload


def _managed_bundle_dir(bundle_name: str, zip_sha256: str) -> Path:
    safe_digest = str(zip_sha256 or "").strip().lower()[:16]
    if not safe_digest:
        raise ValueError("地图资源归档摘要无效")
    return _get_cache_root() / f"{bundle_name}_{safe_digest}"


def _cleanup_other_bundle_versions(bundle_name: str, keep_dir: Path | None = None) -> None:
    cache_root = _get_cache_root()
    if not cache_root.is_dir():
        return

    prefix = f"{bundle_name}_"
    keep_text = os.path.normcase(str(keep_dir)) if keep_dir is not None else ""
    for child in cache_root.iterdir():
        if not child.is_dir() or not child.name.startswith(prefix):
            continue
        if keep_text and os.path.normcase(str(child)) == keep_text:
            continue
        shutil.rmtree(child, ignore_errors=True)


def _ensure_archive_extracted(
    archive_path: Path,
    *,
    bundle_name: str = DEFAULT_BUNDLE_NAME,
    secret_file: str | os.PathLike[str] | None = None,
) -> str:
    header, encrypted_payload = _read_archive_header_and_payload(archive_path)
    normalized_bundle_name = str(header.get("bundle_name") or bundle_name or DEFAULT_BUNDLE_NAME).strip() or DEFAULT_BUNDLE_NAME
    target_dir = _managed_bundle_dir(normalized_bundle_name, str(header.get("zip_sha256") or ""))
    ready_marker = _ready_marker_path(target_dir)

    if ready_marker.is_file():
        validate_bundle_dir(target_dir)
        _cleanup_other_bundle_versions(normalized_bundle_name, keep_dir=target_dir)
        return str(target_dir)

    secret_key = _load_secret_key(bundle_name=normalized_bundle_name, secret_file=secret_file)
    zip_payload = _xor_bytes(encrypted_payload, secret_key)
    actual_digest = hashlib.sha256(zip_payload).hexdigest()
    expected_digest = str(header.get("zip_sha256") or "").strip().lower()
    if actual_digest != expected_digest:
        raise ValueError(f"地图资源归档校验失败: {archive_path}")

    temp_dir = target_dir.parent / f".{target_dir.name}_tmp_{os.getpid()}_{int(time.time() * 1000)}"
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(io.BytesIO(zip_payload), "r") as archive:
            archive.extractall(temp_dir)
        validate_bundle_dir(temp_dir)
        _ready_marker_path(temp_dir).write_text(
            json.dumps(
                {
                    "bundle_name": normalized_bundle_name,
                    "zip_sha256": actual_digest,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        if target_dir.exists():
            try:
                validate_bundle_dir(target_dir)
                shutil.rmtree(temp_dir, ignore_errors=True)
                _cleanup_other_bundle_versions(normalized_bundle_name, keep_dir=target_dir)
                return str(target_dir)
            except Exception:
                shutil.rmtree(target_dir, ignore_errors=True)

        shutil.move(str(temp_dir), str(target_dir))
        _cleanup_other_bundle_versions(normalized_bundle_name, keep_dir=target_dir)
        return str(target_dir)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def get_default_bundle_dir(bundle_name: str = DEFAULT_BUNDLE_NAME) -> str:
    for candidate in _legacy_bundle_dirs(bundle_name):
        if not os.path.isdir(candidate):
            continue
        try:
            validate_bundle_dir(candidate)
            return os.path.abspath(candidate)
        except Exception:
            continue

    archive_path = Path(_bundle_archive_path(bundle_name))
    if archive_path.is_file():
        return _ensure_archive_extracted(archive_path, bundle_name=bundle_name)
    return ""


def resolve_bundle_dir(bundle_path: str | os.PathLike[str] | None, bundle_name: str = DEFAULT_BUNDLE_NAME) -> str:
    text = str(bundle_path or "").strip()
    if not text:
        default_dir = get_default_bundle_dir(bundle_name)
        if default_dir:
            return default_dir
        raise ValueError("未配置地图资源目录")

    normalized = _to_path(os.path.expanduser(text))
    if normalized.is_dir():
        validate_bundle_dir(normalized)
        return str(normalized)

    if normalized.is_file() and normalized.suffix.lower() == ARCHIVE_SUFFIX:
        return _ensure_archive_extracted(normalized, bundle_name=bundle_name)

    cache_root = _get_cache_root()
    if os.path.normcase(str(normalized)).startswith(os.path.normcase(str(cache_root))):
        default_dir = get_default_bundle_dir(bundle_name)
        if default_dir:
            return default_dir

    raise ValueError(f"地图资源目录不存在: {normalized}")


def cleanup_managed_bundle_cache(bundle_name: str | None = DEFAULT_BUNDLE_NAME) -> int:
    cache_root = _get_cache_root()
    if not cache_root.is_dir():
        return 0

    removed_count = 0
    prefix = f"{bundle_name}_" if bundle_name else ""
    for child in list(cache_root.iterdir()):
        if not child.is_dir():
            continue
        if prefix and not child.name.startswith(prefix):
            continue
        shutil.rmtree(child, ignore_errors=True)
        removed_count += 1
    return removed_count
