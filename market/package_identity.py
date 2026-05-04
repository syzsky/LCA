# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import re


SAFE_SEGMENT_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
SAFE_SEGMENT_PATTERN = re.compile(r"^[0-9A-Za-z._-]+$")


def _is_safe_ascii(char: str) -> bool:
    return len(char) == 1 and char in SAFE_SEGMENT_CHARS


def validate_segment(value: str, field_label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_label}不能为空")
    if not SAFE_SEGMENT_PATTERN.fullmatch(text):
        raise ValueError(f"{field_label}只能包含字母、数字、点、下划线和中划线")
    return text


def validate_package_id(package_id: str) -> str:
    return validate_segment(package_id, "包ID")


def validate_version(version: str) -> str:
    return validate_segment(version, "版本号")


def validate_package_identity(package_id: str, version: str) -> tuple[str, str]:
    return validate_package_id(package_id), validate_version(version)


def suggest_package_id(raw_text: str, fallback_prefix: str = "pkg") -> str:
    text = str(raw_text or "").strip().lower()
    if not text:
        return ""
    normalized: list[str] = []
    previous_separator = False
    for char in text:
        if "a" <= char <= "z" or "0" <= char <= "9":
            normalized.append(char)
            previous_separator = False
            continue
        if char in {".", "_", "-"}:
            if previous_separator:
                continue
            normalized.append(char)
            previous_separator = True
            continue
        if previous_separator:
            continue
        normalized.append("-")
        previous_separator = True
    suggested = "".join(normalized).strip("._-")
    if suggested:
        return suggested
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    return f"{fallback_prefix}-{digest}"


def to_safe_storage_segment(value: str, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    cleaned = [char if _is_safe_ascii(char) else "_" for char in text]
    return "".join(cleaned).strip("._") or fallback
