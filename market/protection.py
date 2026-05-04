# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


PAYLOAD_FILENAME = "payload.bin"
LOCAL_SECRET_SUFFIX = ".market_secret.json"
_PROTECTION_AAD_VERSION = "market-protected-payload-v1"


def _b64encode_bytes(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64decode_text(text: str) -> bytes:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("empty_base64_value")
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("utf-8"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if chunk:
                digest.update(chunk)
    return digest.hexdigest()


def _normalize_member_path(member_path: Any) -> str:
    normalized = str(member_path or "").replace("\\", "/").strip("/")
    if not normalized:
        return ""
    parts = []
    for part in normalized.split("/"):
        clean_part = str(part or "").strip()
        if not clean_part or clean_part == ".":
            continue
        if clean_part == "..":
            raise ValueError("invalid_member_path")
        parts.append(clean_part)
    return "/".join(parts)


def is_manifest_protected(manifest: Any) -> bool:
    protection = {}
    if hasattr(manifest, "protection"):
        protection = getattr(manifest, "protection")
        if hasattr(protection, "enabled"):
            return bool(getattr(protection, "enabled", False))
    if isinstance(manifest, dict):
        protection = manifest.get("protection") if isinstance(manifest.get("protection"), dict) else {}
    if isinstance(protection, dict):
        return bool(protection.get("enabled"))
    return False


def get_archive_secret_path(archive_path: str | Path) -> Path:
    archive = Path(archive_path).expanduser().resolve()
    return archive.with_name(f"{archive.name}{LOCAL_SECRET_SUFFIX}")


def save_archive_secret(archive_path: str | Path, payload: Dict[str, Any]) -> Path:
    secret_path = get_archive_secret_path(archive_path)
    secret_path.write_text(json.dumps(payload or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    return secret_path


def load_archive_secret(archive_path: str | Path) -> Dict[str, Any]:
    secret_path = get_archive_secret_path(archive_path)
    if not secret_path.exists():
        return {}
    try:
        payload = json.loads(secret_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_payload_aad(package_id: str, version: str) -> bytes:
    payload = {
        "tag": _PROTECTION_AAD_VERSION,
        "package_id": str(package_id or "").strip(),
        "version": str(version or "").strip(),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def protect_staging_directory(staging_root: str | Path, manifest) -> Dict[str, Any]:
    root = Path(staging_root).resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"staging_root_not_found: {root}")

    public_files = []
    cover_image = _normalize_member_path(getattr(manifest, "cover_image", ""))
    if cover_image:
        public_files.append(cover_image)

    manifest_name = "manifest.json"
    payload_members = []
    payload_buffer = BytesIO()
    with zipfile.ZipFile(payload_buffer, "w", compression=zipfile.ZIP_DEFLATED) as payload_zip:
        for file_path in sorted(root.rglob("*")):
            if not file_path.is_file():
                continue
            relative_path = file_path.relative_to(root).as_posix()
            if relative_path == manifest_name or relative_path in public_files:
                continue
            payload_zip.write(file_path, relative_path)
            payload_members.append(relative_path)

    if not payload_members:
        raise ValueError("protected_payload_empty")

    key_bytes = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    payload_plain = payload_buffer.getvalue()
    cipher = AESGCM(key_bytes)
    payload_encrypted = nonce + cipher.encrypt(
        nonce,
        payload_plain,
        _build_payload_aad(getattr(manifest, "package_id", ""), getattr(manifest, "version", "")),
    )

    payload_path = root / PAYLOAD_FILENAME
    payload_path.write_bytes(payload_encrypted)

    for relative_path in payload_members:
        file_path = root / Path(relative_path)
        if file_path.exists() and file_path.is_file():
            file_path.unlink()

    for directory in sorted((item for item in root.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
        try:
            if directory == root:
                continue
            if any(directory.iterdir()):
                continue
            directory.rmdir()
        except Exception:
            continue

    protection = getattr(manifest, "protection")
    protection.enabled = True
    protection.scheme = "aes256gcm"
    protection.payload_path = PAYLOAD_FILENAME
    protection.payload_sha256 = _sha256_bytes(payload_encrypted)
    protection.payload_size = len(payload_encrypted)
    protection.requires_online_key = True
    protection.edit_requires_owner_auth = True
    protection.public_files = list(public_files)

    manifest.file_hashes = {
        PAYLOAD_FILENAME: protection.payload_sha256,
    }
    for relative_path in public_files:
        public_path = root / Path(relative_path)
        if public_path.exists() and public_path.is_file():
            manifest.file_hashes[relative_path] = _sha256_file(public_path)

    return {
        "payload_key": _b64encode_bytes(key_bytes),
        "payload_sha256": protection.payload_sha256,
        "payload_size": protection.payload_size,
        "payload_path": PAYLOAD_FILENAME,
        "public_files": list(public_files),
    }


def extract_protected_payload(install_dir: str | Path, manifest, payload_key: str, session_dir: str | Path) -> Path:
    install_root = Path(install_dir).resolve()
    target_root = Path(session_dir).resolve()
    if target_root.exists():
        shutil.rmtree(target_root, ignore_errors=True)
    target_root.mkdir(parents=True, exist_ok=True)

    protection = getattr(manifest, "protection", None)
    if protection is None or not getattr(protection, "enabled", False):
        raise ValueError("manifest_not_protected")

    payload_member = _normalize_member_path(getattr(protection, "payload_path", "") or PAYLOAD_FILENAME)
    payload_path = (install_root / Path(payload_member)).resolve()
    try:
        payload_path.relative_to(install_root)
    except Exception as exc:
        raise RuntimeError(f"payload_path_out_of_root: {payload_member}") from exc
    if not payload_path.exists() or not payload_path.is_file():
        raise FileNotFoundError(f"payload_not_found: {payload_path}")

    encrypted_payload = payload_path.read_bytes()
    expected_sha256 = str(getattr(protection, "payload_sha256", "") or "").strip()
    if expected_sha256 and _sha256_bytes(encrypted_payload) != expected_sha256:
        raise RuntimeError("protected_payload_sha256_mismatch")
    if len(encrypted_payload) <= 12:
        raise RuntimeError("protected_payload_invalid")

    nonce = encrypted_payload[:12]
    ciphertext = encrypted_payload[12:]
    key_bytes = _b64decode_text(payload_key)
    cipher = AESGCM(key_bytes)
    plain_zip = cipher.decrypt(
        nonce,
        ciphertext,
        _build_payload_aad(getattr(manifest, "package_id", ""), getattr(manifest, "version", "")),
    )

    with zipfile.ZipFile(BytesIO(plain_zip), "r") as payload_zip:
        for member in payload_zip.infolist():
            member_name = _normalize_member_path(member.filename)
            if not member_name:
                continue
            target_path = (target_root / Path(member_name)).resolve()
            try:
                target_path.relative_to(target_root)
            except Exception as exc:
                raise RuntimeError(f"payload_member_out_of_root: {member.filename}") from exc
            if member.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with payload_zip.open(member, "r") as source_handle, target_path.open("wb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle)

    manifest_path = install_root / "manifest.json"
    if manifest_path.exists():
        shutil.copy2(manifest_path, target_root / "manifest.json")

    for relative_path in list(getattr(protection, "public_files", []) or []):
        safe_relative = _normalize_member_path(relative_path)
        if not safe_relative:
            continue
        source_path = (install_root / Path(safe_relative)).resolve()
        try:
            source_path.relative_to(install_root)
        except Exception:
            continue
        if not source_path.exists() or not source_path.is_file():
            continue
        target_path = (target_root / Path(safe_relative)).resolve()
        try:
            target_path.relative_to(target_root)
        except Exception:
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)

    return target_root
