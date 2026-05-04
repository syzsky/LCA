# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Dict, Optional
from urllib.parse import parse_qs, quote, unquote, urlparse


_MARKET_REF_SCHEME = "market"
_MARKET_REF_NETLOC = "workflow"


def build_market_workflow_ref(package_id: str, version: str, entry_workflow: str = "workflow/main.json") -> str:
    safe_package_id = quote(str(package_id or "").strip(), safe="")
    safe_version = quote(str(version or "").strip(), safe="")
    safe_entry = quote(str(entry_workflow or "workflow/main.json").strip().replace("\\", "/"), safe="")
    return f"{_MARKET_REF_SCHEME}://{_MARKET_REF_NETLOC}/{safe_package_id}/{safe_version}?entry={safe_entry}"


def parse_market_workflow_ref(value: str) -> Optional[Dict[str, str]]:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme != _MARKET_REF_SCHEME or parsed.netloc != _MARKET_REF_NETLOC:
        return None
    segments = [unquote(item) for item in parsed.path.split("/") if item]
    if len(segments) < 2:
        return None
    query = parse_qs(parsed.query or "")
    entry = str(query.get("entry", ["workflow/main.json"])[0] or "workflow/main.json").strip().replace("\\", "/")
    return {
        "package_id": str(segments[0] or "").strip(),
        "version": str(segments[1] or "").strip(),
        "entry_workflow": entry or "workflow/main.json",
        "ref": text,
    }


def is_market_workflow_ref(value: str) -> bool:
    return parse_market_workflow_ref(value) is not None
