# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

OPENAI_PROVIDER_MODE_OFFICIAL = "OpenAI官方"
OPENAI_PROVIDER_MODE_COMPATIBLE = "自定义OpenAI兼容"
OPENAI_PROVIDER_MODE_OPTIONS = [OPENAI_PROVIDER_MODE_COMPATIBLE]

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"

OPENAI_API_PROTOCOL_AUTO = "自动"
OPENAI_API_PROTOCOL_RESPONSES = "responses"
OPENAI_API_PROTOCOL_CHAT_COMPLETIONS = "chat_completions"
OPENAI_API_PROTOCOL_OPTIONS = [
    OPENAI_API_PROTOCOL_RESPONSES,
    OPENAI_API_PROTOCOL_CHAT_COMPLETIONS,
]

OPENAI_API_URL_MODE_AUTO = "自动"
OPENAI_API_URL_MODE_ENDPOINT = "完整请求地址"
OPENAI_API_URL_MODE_BASE = "接口基地址"
OPENAI_API_URL_MODE_OPTIONS = [
    OPENAI_API_URL_MODE_BASE,
    OPENAI_API_URL_MODE_ENDPOINT,
]

_PROVIDER_MODE_OFFICIAL_ALIASES = {
    "",
    "openai",
    "openai_official",
    "official",
    OPENAI_PROVIDER_MODE_OFFICIAL,
    "OpenAI官方",
    "OpenAI官方模式",
    "OpenAI瀹樻柟",
    "OpenAI??",
}

_PROVIDER_MODE_COMPATIBLE_ALIASES = {
    "openai_compatible",
    "compatible",
    "custom",
    "custom_openai_compatible",
    OPENAI_PROVIDER_MODE_COMPATIBLE,
    "自定义OpenAI兼容",
    "兼容",
    "鑷畾涔塐penAI鍏煎",
    "???OpenAI??",
}

_API_URL_MODE_ENDPOINT_ALIASES = {
    "",
    OPENAI_API_URL_MODE_AUTO,
    OPENAI_API_URL_MODE_ENDPOINT,
    "auto",
    "endpoint",
    "request_url",
    "request",
    "full",
    "full_url",
}

_API_URL_MODE_BASE_ALIASES = {
    OPENAI_API_URL_MODE_BASE,
    "base",
    "base_url",
    "provider_base_url",
}


def normalize_ai_provider_mode(value: Any) -> str:
    mode = str(value or "").strip()
    if not mode:
        return OPENAI_PROVIDER_MODE_COMPATIBLE

    lowered = mode.lower()
    if mode in _PROVIDER_MODE_COMPATIBLE_ALIASES or lowered in _PROVIDER_MODE_COMPATIBLE_ALIASES:
        return OPENAI_PROVIDER_MODE_COMPATIBLE
    if "兼容" in mode or "鍏煎" in mode or "compatible" in lowered or "custom" in lowered:
        return OPENAI_PROVIDER_MODE_COMPATIBLE

    if mode in _PROVIDER_MODE_OFFICIAL_ALIASES or lowered in _PROVIDER_MODE_OFFICIAL_ALIASES:
        return OPENAI_PROVIDER_MODE_OFFICIAL
    if "官方" in mode or "瀹樻柟" in mode or "official" in lowered:
        return OPENAI_PROVIDER_MODE_OFFICIAL

    return OPENAI_PROVIDER_MODE_COMPATIBLE


def _infer_ai_api_protocol_from_url(api_base_url: Any) -> Optional[str]:
    url_text = str(api_base_url or "").strip().rstrip("/").lower()
    if not url_text:
        return None
    if url_text.endswith("/responses"):
        return OPENAI_API_PROTOCOL_RESPONSES
    if url_text.endswith("/chat/completions"):
        return OPENAI_API_PROTOCOL_CHAT_COMPLETIONS
    return None


def normalize_ai_api_protocol(value: Any, provider_mode: Any = None, api_base_url: Any = None) -> str:
    protocol = str(value or "").strip().lower()
    if protocol in {"", "auto"} or str(value or "").strip() == OPENAI_API_PROTOCOL_AUTO:
        inferred_protocol = _infer_ai_api_protocol_from_url(api_base_url)
        if inferred_protocol:
            return inferred_protocol
        return OPENAI_API_PROTOCOL_RESPONSES
    if protocol in {OPENAI_API_PROTOCOL_RESPONSES, "response", "responses_api"}:
        return OPENAI_API_PROTOCOL_RESPONSES
    if protocol in {
        OPENAI_API_PROTOCOL_CHAT_COMPLETIONS,
        "chat",
        "chat_completion",
        "chat.completions",
        "chat/completions",
    }:
        return OPENAI_API_PROTOCOL_CHAT_COMPLETIONS

    inferred_protocol = _infer_ai_api_protocol_from_url(api_base_url)
    if inferred_protocol:
        return inferred_protocol
    return OPENAI_API_PROTOCOL_RESPONSES


def _looks_like_openai_endpoint_url(base_url: Any) -> bool:
    url_text = str(base_url or "").strip().rstrip("/").lower()
    if not url_text:
        return False
    return url_text.endswith("/responses") or url_text.endswith("/chat/completions")


def normalize_ai_api_url_mode(value: Any, provider_mode: Any = None, api_base_url: Any = None) -> str:
    mode = str(value or "").strip()
    lowered = mode.lower()
    if mode in _API_URL_MODE_BASE_ALIASES or lowered in _API_URL_MODE_BASE_ALIASES:
        return OPENAI_API_URL_MODE_BASE
    if mode == OPENAI_API_URL_MODE_ENDPOINT:
        return OPENAI_API_URL_MODE_ENDPOINT
    if lowered in {"endpoint", "request_url", "request", "full", "full_url"}:
        return OPENAI_API_URL_MODE_ENDPOINT
    if mode in {"", OPENAI_API_URL_MODE_AUTO} or lowered == "auto":
        if _looks_like_openai_endpoint_url(api_base_url):
            return OPENAI_API_URL_MODE_ENDPOINT
        return OPENAI_API_URL_MODE_BASE
    if "基地址" in mode or "base" in lowered:
        return OPENAI_API_URL_MODE_BASE
    if "请求地址" in mode or "endpoint" in lowered or "request" in lowered or "full" in lowered:
        return OPENAI_API_URL_MODE_ENDPOINT

    if _looks_like_openai_endpoint_url(api_base_url):
        return OPENAI_API_URL_MODE_ENDPOINT
    return OPENAI_API_URL_MODE_BASE


def is_valid_api_base_url(base_url: str) -> bool:
    parsed = urlparse(str(base_url or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_official_openai_base_url(base_url: str) -> bool:
    parsed = urlparse(str(base_url or "").strip())
    return parsed.scheme == "https" and parsed.netloc.lower() == "api.openai.com"


def resolve_ai_api_base_url(params: Dict[str, Any], env_base_url: Optional[str] = None) -> Tuple[str, str]:
    param_base_url = str(params.get("api_base_url") or "").strip()
    resolved_env_base_url = str(
        env_base_url if env_base_url is not None else os.getenv("OPENAI_BASE_URL") or ""
    ).strip()
    base_url = param_base_url or resolved_env_base_url or OPENAI_DEFAULT_BASE_URL

    if not is_valid_api_base_url(base_url):
        return "", "API 地址无效，必须是完整的 http(s) 地址。"
    return base_url.rstrip("/"), ""
