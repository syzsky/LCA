# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.ai.message_builder import build_chat_request_messages
from services.ai.provider_config import (
    OPENAI_API_PROTOCOL_CHAT_COMPLETIONS,
    normalize_ai_api_protocol as _normalize_ai_api_protocol,
    normalize_ai_api_url_mode as _normalize_ai_api_url_mode,
    normalize_ai_provider_mode as _normalize_ai_provider_mode,
)
from services.ai.response_utils import extract_output_text as _extract_output_text
from services.mcp import mcp_openai_server as _mcp_openai_server


def _build_responses_text_input(messages: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    built: List[Dict[str, Any]] = []
    for item in messages:
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        built.append(
            {
                "role": str(item.get("role") or "user").strip() or "user",
                "content": [{"type": "input_text", "text": text}],
            }
        )
    return built


def request_text_response(
    *,
    prompt_text: str,
    api_base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: float,
    provider_mode: Optional[str] = None,
    api_protocol: Optional[str] = None,
    api_url_mode: Optional[str] = None,
    history_messages: Optional[List[Dict[str, Any]]] = None,
    role_prompt: str = "",
    import_prompt_text: str = "",
    import_prompt_rule: str = "",
    temperature: float = 0.2,
) -> Dict[str, Any]:
    provider_mode = _normalize_ai_provider_mode(provider_mode)
    api_protocol = _normalize_ai_api_protocol(
        api_protocol,
        provider_mode=provider_mode,
        api_base_url=api_base_url,
    )
    api_url_mode = _normalize_ai_api_url_mode(
        api_url_mode,
        provider_mode=provider_mode,
        api_base_url=api_base_url,
    )
    messages = build_chat_request_messages(
        prompt_text=prompt_text,
        role_prompt=role_prompt,
        import_prompt_text=import_prompt_text,
        history_messages=history_messages,
        import_prompt_rule=import_prompt_rule,
    )
    stream_cache_key = _mcp_openai_server._build_stream_only_cache_key(
        api_base_url=api_base_url,
        api_protocol=api_protocol,
        provider_mode=provider_mode,
        api_url_mode=api_url_mode,
        model=model,
    )
    if api_protocol == OPENAI_API_PROTOCOL_CHAT_COMPLETIONS:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": float(temperature),
        }
    else:
        payload = {
            "model": model,
            "input": _build_responses_text_input(messages),
            "temperature": float(temperature),
        }

    response_payload: Optional[Dict[str, Any]] = None
    output_text = ""
    use_stream_only = bool(_mcp_openai_server._STREAM_ONLY_ENDPOINT_CACHE.get(stream_cache_key))
    if not use_stream_only:
        try:
            response_payload = _mcp_openai_server._post_openai_json(
                api_base_url=api_base_url,
                api_key=api_key,
                api_protocol=api_protocol,
                provider_mode=provider_mode,
                api_url_mode=api_url_mode,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )
            output_text = _extract_output_text(response_payload)
        except Exception as exc:
            if not _mcp_openai_server._requires_stream_response(exc):
                raise
            _mcp_openai_server._STREAM_ONLY_ENDPOINT_CACHE[stream_cache_key] = True
            use_stream_only = True
    if use_stream_only or not str(output_text or "").strip():
        streamed_output_text = _mcp_openai_server._post_openai_stream_text(
            api_base_url=api_base_url,
            api_key=api_key,
            api_protocol=api_protocol,
            provider_mode=provider_mode,
            api_url_mode=api_url_mode,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        if str(streamed_output_text or "").strip():
            output_text = streamed_output_text
        else:
            detail = _mcp_openai_server._describe_empty_model_output(response_payload or {})
            if detail and response_payload is not None:
                raise RuntimeError(f"empty model output: {detail}")
            raise RuntimeError("empty model output")
    return {"output_text": str(output_text or "").strip()}
