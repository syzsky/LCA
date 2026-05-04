# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List, Optional


def extract_text_from_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            text = extract_text_from_message_content(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    if isinstance(content, dict):
        item_type = str(content.get("type") or "").strip().lower()
        if item_type in {"input_text", "text", "output_text"}:
            text_value = content.get("text")
            if isinstance(text_value, dict):
                return extract_text_from_message_content(text_value)
            return str(text_value or "").strip()
        for key in ("text", "value", "content", "output_text"):
            nested = extract_text_from_message_content(content.get(key))
            if nested:
                return nested
    return ""


def normalize_text_messages(messages: Any) -> List[Dict[str, str]]:
    if not isinstance(messages, list):
        return []
    normalized: List[Dict[str, str]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"system", "user", "assistant"}:
            continue
        text = extract_text_from_message_content(item.get("content"))
        if not text:
            continue
        normalized.append({"role": role, "content": text})
    return normalized


def build_chat_request_messages(
    prompt_text: str,
    role_prompt: str = "",
    import_prompt_text: str = "",
    history_messages: Optional[List[Dict[str, Any]]] = None,
    import_prompt_rule: str = "",
) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    role_text = str(role_prompt or "").strip()
    if role_text:
        messages.append({"role": "system", "content": role_text})

    doc_text = str(import_prompt_text or "").strip()
    if doc_text:
        rule_text = str(import_prompt_rule or "").strip()
        if rule_text:
            doc_text = f"{rule_text}\n{doc_text}"
        messages.append({"role": "system", "content": doc_text})

    messages.extend(normalize_text_messages(history_messages or []))

    user_text = str(prompt_text or "").strip()
    if user_text:
        messages.append({"role": "user", "content": user_text})
    return messages


def _ensure_messages_with_user(prompt: str, messages: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    normalized = normalize_text_messages(messages or [])
    if normalized:
        return normalized
    return [{"role": "user", "content": str(prompt or "").strip()}]


def build_responses_input(
    prompt: str,
    image_base64: str,
    image_mime_type: str,
    messages: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    normalized = _ensure_messages_with_user(prompt, messages)
    last_user_index = max((idx for idx, item in enumerate(normalized) if item.get("role") == "user"), default=-1)
    if last_user_index < 0:
        normalized.append({"role": "user", "content": str(prompt or "").strip()})
        last_user_index = len(normalized) - 1

    built: List[Dict[str, Any]] = []
    image_url = f"data:{image_mime_type};base64,{image_base64}"
    for idx, item in enumerate(normalized):
        content: List[Dict[str, Any]] = []
        text = str(item.get("content") or "").strip()
        if text:
            content.append({"type": "input_text", "text": text})
        if idx == last_user_index:
            content.append({"type": "input_image", "image_url": image_url, "detail": "high"})
        built.append({"role": item["role"], "content": content})
    return built


def build_chat_completions_messages(
    prompt: str,
    image_base64: str,
    image_mime_type: str,
    messages: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    normalized = _ensure_messages_with_user(prompt, messages)
    last_user_index = max((idx for idx, item in enumerate(normalized) if item.get("role") == "user"), default=-1)
    if last_user_index < 0:
        normalized.append({"role": "user", "content": str(prompt or "").strip()})
        last_user_index = len(normalized) - 1

    built: List[Dict[str, Any]] = []
    image_url = f"data:{image_mime_type};base64,{image_base64}"
    for idx, item in enumerate(normalized):
        role = item["role"]
        text = str(item.get("content") or "").strip()
        if idx == last_user_index:
            content: List[Dict[str, Any]] = []
            if text:
                content.append({"type": "text", "text": text})
            content.append({"type": "image_url", "image_url": {"url": image_url, "detail": "high"}})
            built.append({"role": role, "content": content})
            continue
        built.append({"role": role, "content": text})
    return built
