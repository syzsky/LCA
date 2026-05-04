import base64
import io
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import httpx

# Ensure project root is on sys.path when running from services/mcp
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

try:
    import win32con
    import win32gui
    import win32api
except Exception:
    win32con = None
    win32gui = None
    win32api = None

from utils.screenshot_helper import take_window_screenshot
from services.ai.message_builder import build_chat_completions_messages, build_responses_input
from services.ai.provider_config import (
    OPENAI_API_PROTOCOL_CHAT_COMPLETIONS,
    OPENAI_API_URL_MODE_ENDPOINT,
    normalize_ai_api_protocol as _normalize_ai_api_protocol,
    normalize_ai_api_url_mode as _normalize_ai_api_url_mode,
    normalize_ai_provider_mode as _normalize_ai_provider_mode,
    resolve_ai_api_base_url as _resolve_ai_api_base_url,
)
from services.ai.response_utils import (
    detect_scale_base as _detect_scale_base,
    extract_bbox as _extract_bbox,
    extract_output_text as _extract_output_text,
    extract_point as _extract_point,
    parse_json_from_text as _parse_json_from_text,
)


SERVER_NAME = "lca-openai-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"
_STREAM_ONLY_ENDPOINT_CACHE: Dict[str, bool] = {}


def _eprint(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def _send(obj: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _rpc_result(req_id: Any, result: Dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _rpc_error(req_id: Any, message: str, code: int = -32000) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _format_exception_chain(exc: Exception) -> str:
    parts: List[str] = []
    seen: set[int] = set()
    current: Optional[BaseException] = exc

    while current is not None:
        current_id = id(current)
        if current_id in seen:
            break
        seen.add(current_id)

        type_name = type(current).__name__
        message = str(current).strip()
        parts.append(f"{type_name}: {message}" if message else type_name)
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)

    if not parts:
        return str(exc)
    return " <- ".join(parts)


def _normalize_image_mime_type(image_mime_type: Optional[str]) -> str:
    mime_type = str(image_mime_type or "").strip().lower()
    if mime_type in ("image/jpg", "image/jpeg"):
        return "image/jpeg"
    if mime_type in ("image/png", "image/webp", "image/bmp"):
        return mime_type
    return "image/png"


def _build_openai_headers(api_key: str) -> Dict[str, str]:
    token = str(api_key or "").strip()
    if not token:
        raise RuntimeError("missing api_key")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _build_openai_endpoint(
    api_base_url: str,
    api_protocol: str,
    provider_mode: Optional[str] = None,
    api_url_mode: Optional[str] = None,
) -> str:
    request_url = str(api_base_url or "").strip().rstrip("/")
    if not request_url:
        raise RuntimeError("missing api_base_url")
    normalized_provider_mode = _normalize_ai_provider_mode(provider_mode)
    normalized_protocol = _normalize_ai_api_protocol(
        api_protocol,
        provider_mode=normalized_provider_mode,
        api_base_url=request_url,
    )
    normalized_url_mode = _normalize_ai_api_url_mode(
        api_url_mode,
        provider_mode=normalized_provider_mode,
        api_base_url=request_url,
    )
    if normalized_url_mode != OPENAI_API_URL_MODE_ENDPOINT:
        if normalized_protocol == OPENAI_API_PROTOCOL_CHAT_COMPLETIONS:
            if request_url.lower().endswith("/chat/completions"):
                return request_url
            return f"{request_url}/chat/completions"
        if request_url.lower().endswith("/responses"):
            return request_url
        return f"{request_url}/responses"
    return request_url


def _requires_stream_response(exc: Exception) -> bool:
    lower_msg = str(exc or "").strip().lower()
    return "only support stream" in lower_msg or "stream only" in lower_msg


def _build_stream_only_cache_key(
    api_base_url: str,
    api_protocol: str,
    provider_mode: Optional[str],
    api_url_mode: Optional[str],
    model: str,
) -> str:
    return "|".join(
        [
            str(api_base_url or "").strip().rstrip("/").lower(),
            str(
                _normalize_ai_api_protocol(
                    api_protocol,
                    provider_mode=provider_mode,
                    api_base_url=api_base_url,
                ) or ""
            ).strip().lower(),
            str(_normalize_ai_provider_mode(provider_mode) or "").strip().lower(),
            str(
                _normalize_ai_api_url_mode(
                    api_url_mode,
                    provider_mode=provider_mode,
                    api_base_url=api_base_url,
                ) or ""
            ).strip().lower(),
            str(model or "").strip(),
        ]
    )


def _raise_http_error(response: httpx.Response) -> None:
    if response.is_success:
        return

    detail = ""
    try:
        payload = response.json()
    except Exception:
        payload = None

    if isinstance(payload, dict):
        error_obj = payload.get("error")
        if isinstance(error_obj, dict):
            detail = str(error_obj.get("message") or "").strip()
        if not detail:
            detail = str(payload.get("message") or "").strip()

    if not detail:
        try:
            detail = response.text.strip()
        except Exception:
            detail = ""

    request_url = ""
    try:
        request = getattr(response, "request", None)
        request_url = str(getattr(request, "url", "") or "").strip()
    except Exception:
        request_url = ""

    status_line = f"HTTP {response.status_code}"
    if request_url:
        status_line = f"{status_line} for url '{request_url}'"
    if detail:
        raise RuntimeError(f"{status_line}: {detail}")
    raise RuntimeError(status_line)


def _summarize_response_body(response: httpx.Response, limit: int = 160) -> str:
    try:
        raw_text = response.text
    except Exception:
        raw_text = ""
    clean_text = " ".join(str(raw_text or "").split()).strip()
    if not clean_text:
        return "empty response body"
    if len(clean_text) > limit:
        clean_text = clean_text[: limit - 3].rstrip() + "..."
    return clean_text


def _compact_json_text(value: Any, limit: int = 240) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        text = str(value)
    clean_text = " ".join(str(text or "").split()).strip()
    if len(clean_text) > limit:
        clean_text = clean_text[: limit - 3].rstrip() + "..."
    return clean_text


def _describe_empty_model_output(payload: Dict[str, Any]) -> str:
    details: List[str] = []
    if isinstance(payload, dict):
        root_keys = [str(key) for key in payload.keys()]
        if root_keys:
            details.append("root_keys=" + ",".join(root_keys[:12]))

        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            details.append("first_choice=" + _compact_json_text(choices[0], 160))

        output_items = payload.get("output")
        if isinstance(output_items, list):
            details.append(f"output_items={len(output_items)}")
            if output_items:
                details.append("first_output=" + _compact_json_text(output_items[0], 160))

        usage = payload.get("usage")
        if isinstance(usage, dict):
            output_tokens = usage.get("completion_tokens")
            if output_tokens is None:
                output_tokens = usage.get("output_tokens")
            if output_tokens is not None:
                details.append(f"output_tokens={output_tokens}")

    return "; ".join(item for item in details if item)


def _post_openai_json(
    *,
    api_base_url: str,
    api_key: str,
    api_protocol: str,
    provider_mode: Optional[str],
    api_url_mode: Optional[str],
    payload: Dict[str, Any],
    timeout_seconds: float,
) -> Dict[str, Any]:
    timeout_value = max(5.0, float(timeout_seconds or 0))
    endpoint = _build_openai_endpoint(
        api_base_url=api_base_url,
        api_protocol=api_protocol,
        provider_mode=provider_mode,
        api_url_mode=api_url_mode,
    )
    with httpx.Client(timeout=timeout_value, follow_redirects=True, trust_env=False) as http_client:
        response = http_client.post(
            endpoint,
            headers=_build_openai_headers(api_key),
            json=payload,
        )
        _raise_http_error(response)
        try:
            return response.json()
        except Exception as exc:
            body_preview = _summarize_response_body(response)
            raise RuntimeError(
                f"invalid json response for url '{endpoint}': {body_preview}"
            ) from exc


def _collect_stream_text_parts(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        texts: List[str] = []
        for item in value:
            texts.extend(_collect_stream_text_parts(item))
        return texts
    if isinstance(value, dict):
        for key in ("output_text", "text", "value", "content", "reasoning_content", "delta"):
            texts = _collect_stream_text_parts(value.get(key))
            if texts:
                return texts
    return []


def _extract_stream_event_text(event_payload: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    delta_parts: List[str] = []
    final_parts: List[str] = []
    if not isinstance(event_payload, dict):
        return delta_parts, final_parts

    choices = event_payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            delta_parts.extend(_collect_stream_text_parts(delta.get("content")))
            delta_parts.extend(_collect_stream_text_parts(delta.get("reasoning_content")))
            message = choice.get("message") or {}
            final_parts.extend(_collect_stream_text_parts(message.get("content")))
            final_parts.extend(_collect_stream_text_parts(message.get("reasoning_content")))

    event_type = str(event_payload.get("type") or "").strip()
    if event_type == "response.output_text.delta":
        delta_parts.extend(_collect_stream_text_parts(event_payload.get("delta")))
    elif event_type == "response.output_text.done":
        final_parts.extend(_collect_stream_text_parts(event_payload.get("text")))
    elif event_type == "response.content_part.done":
        part = event_payload.get("part") or {}
        final_parts.extend(_collect_stream_text_parts(part.get("text")))
    elif event_type == "response.output_item.done":
        item = event_payload.get("item") or {}
        final_parts.extend(_collect_stream_text_parts(item.get("content")))

    item = event_payload.get("item")
    if isinstance(item, dict):
        final_parts.extend(_collect_stream_text_parts(item.get("content")))

    part = event_payload.get("part")
    if isinstance(part, dict):
        final_parts.extend(_collect_stream_text_parts(part.get("text")))

    return delta_parts, final_parts


def _post_openai_stream_text(
    *,
    api_base_url: str,
    api_key: str,
    api_protocol: str,
    provider_mode: Optional[str],
    api_url_mode: Optional[str],
    payload: Dict[str, Any],
    timeout_seconds: float,
) -> str:
    timeout_value = max(5.0, float(timeout_seconds or 0))
    endpoint = _build_openai_endpoint(
        api_base_url=api_base_url,
        api_protocol=api_protocol,
        provider_mode=provider_mode,
        api_url_mode=api_url_mode,
    )
    stream_payload = dict(payload or {})
    stream_payload["stream"] = True
    delta_parts: List[str] = []
    final_parts: List[str] = []
    with httpx.Client(timeout=timeout_value, follow_redirects=True, trust_env=False) as http_client:
        with http_client.stream(
            "POST",
            endpoint,
            headers=_build_openai_headers(api_key),
            json=stream_payload,
        ) as response:
            _raise_http_error(response)
            for line in response.iter_lines():
                if not line:
                    continue
                clean_line = str(line).strip()
                if not clean_line or not clean_line.startswith("data:"):
                    continue
                data_text = clean_line[5:].strip()
                if not data_text or data_text == "[DONE]":
                    continue
                try:
                    event_payload = json.loads(data_text)
                except Exception:
                    continue
                event_delta_parts, event_final_parts = _extract_stream_event_text(event_payload)
                if event_delta_parts:
                    delta_parts.extend(event_delta_parts)
                if event_final_parts:
                    final_parts.extend(event_final_parts)

    if delta_parts:
        return "".join(delta_parts).strip()
    if final_parts:
        return "\n".join(part for part in final_parts if part).strip()
    return ""


def _list_windows() -> List[Dict[str, Any]]:
    if win32gui is None:
        raise RuntimeError("pywin32 is not available")
    results: List[Dict[str, Any]] = []

    def _enum_cb(hwnd: int, _ctx: Any) -> None:
        try:
            title = win32gui.GetWindowText(hwnd) or ""
            if not title.strip():
                return
            results.append(
                {
                    "hwnd": int(hwnd),
                    "title": title,
                    "class_name": win32gui.GetClassName(hwnd),
                    "visible": bool(win32gui.IsWindowVisible(hwnd)),
                }
            )
        except Exception:
            return

    win32gui.EnumWindows(_enum_cb, None)
    return results


def _capture_window(hwnd: int, client_area_only: bool = True) -> Dict[str, Any]:
    try:
        from app_core.plugin_bridge import is_plugin_enabled, plugin_capture

        if is_plugin_enabled():
            if win32gui is None:
                raise RuntimeError("pywin32 is not available")
            hwnd_int = int(hwnd)
            if client_area_only:
                rect = win32gui.GetClientRect(hwnd_int)
                width = int(rect[2] - rect[0])
                height = int(rect[3] - rect[1])
            else:
                rect = win32gui.GetWindowRect(hwnd_int)
                width = int(rect[2] - rect[0])
                height = int(rect[3] - rect[1])
            if width <= 0 or height <= 0:
                raise RuntimeError("capture target size invalid")
            plugin_img = plugin_capture(hwnd=hwnd_int, x1=0, y1=0, x2=width, y2=height)
            if plugin_img is None:
                raise RuntimeError("capture failed")
            ok, encoded = cv2.imencode(".png", plugin_img)
            if not ok:
                raise RuntimeError("png encode failed")
            b64 = base64.b64encode(encoded.tobytes()).decode("ascii")
            return {"width": int(plugin_img.shape[1]), "height": int(plugin_img.shape[0]), "image_base64": b64}
    except ImportError:
        pass

    img = take_window_screenshot(hwnd, client_area_only=client_area_only, return_format="pil")
    if img is None:
        raise RuntimeError("capture failed")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return {"width": img.size[0], "height": img.size[1], "image_base64": b64}


def _openai_protocol_probe(
    api_base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: float,
    provider_mode: Optional[str] = None,
    api_protocol: Optional[str] = None,
    api_url_mode: Optional[str] = None,
) -> Dict[str, Any]:
    timeout_value = max(5.0, float(timeout_seconds or 0))
    provider_mode = _normalize_ai_provider_mode(provider_mode)
    api_protocol = _normalize_ai_api_protocol(
        api_protocol,
        provider_mode=provider_mode,
        api_base_url=api_base_url,
    )
    stream_cache_key = _build_stream_only_cache_key(
        api_base_url=api_base_url,
        api_protocol=api_protocol,
        provider_mode=provider_mode,
        api_url_mode=api_url_mode,
        model=model,
    )
    if api_protocol == OPENAI_API_PROTOCOL_CHAT_COMPLETIONS:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
        }
    else:
        payload = {
            "model": model,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "ping"}],
                }
            ],
        }

    response_payload: Optional[Dict[str, Any]] = None
    if _STREAM_ONLY_ENDPOINT_CACHE.get(stream_cache_key):
        _post_openai_stream_text(
            api_base_url=api_base_url,
            api_key=api_key,
            api_protocol=api_protocol,
            provider_mode=provider_mode,
            api_url_mode=api_url_mode,
            payload=payload,
            timeout_seconds=timeout_value,
        )
        return {
            "ok": True,
            "api_protocol": api_protocol,
            "model": model,
            "response_id": "",
        }
    try:
        response_payload = _post_openai_json(
            api_base_url=api_base_url,
            api_key=api_key,
            api_protocol=api_protocol,
            provider_mode=provider_mode,
            api_url_mode=api_url_mode,
            payload=payload,
            timeout_seconds=timeout_value,
        )
    except Exception as exc:
        if not _requires_stream_response(exc):
            raise
        _STREAM_ONLY_ENDPOINT_CACHE[stream_cache_key] = True
        _post_openai_stream_text(
            api_base_url=api_base_url,
            api_key=api_key,
            api_protocol=api_protocol,
            provider_mode=provider_mode,
            api_url_mode=api_url_mode,
            payload=payload,
            timeout_seconds=timeout_value,
        )
    response_id = str((response_payload or {}).get("id") or "").strip()
    return {
        "ok": True,
        "api_protocol": api_protocol,
        "model": model,
        "response_id": response_id,
    }


def _openai_raw(
    image_base64: str,
    prompt: str,
    api_base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: float,
    provider_mode: Optional[str] = None,
    api_protocol: Optional[str] = None,
    api_url_mode: Optional[str] = None,
    image_mime_type: Optional[str] = None,
    messages: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    timeout_value = max(5.0, float(timeout_seconds or 0))
    mime_type = _normalize_image_mime_type(image_mime_type)
    provider_mode = _normalize_ai_provider_mode(provider_mode)
    api_protocol = _normalize_ai_api_protocol(
        api_protocol,
        provider_mode=provider_mode,
        api_base_url=api_base_url,
    )
    stream_cache_key = _build_stream_only_cache_key(
        api_base_url=api_base_url,
        api_protocol=api_protocol,
        provider_mode=provider_mode,
        api_url_mode=api_url_mode,
        model=model,
    )
    if api_protocol == OPENAI_API_PROTOCOL_CHAT_COMPLETIONS:
        payload = {
            "model": model,
            "messages": build_chat_completions_messages(
                prompt=prompt,
                image_base64=image_base64,
                image_mime_type=mime_type,
                messages=messages,
            ),
            "temperature": 0.2,
        }
    else:
        payload = {
            "model": model,
            "input": build_responses_input(
                prompt=prompt,
                image_base64=image_base64,
                image_mime_type=mime_type,
                messages=messages,
            ),
            "temperature": 0.2,
        }

    response_payload: Optional[Dict[str, Any]] = None
    output_text = ""
    use_stream_only = bool(_STREAM_ONLY_ENDPOINT_CACHE.get(stream_cache_key))
    if not use_stream_only:
        try:
            response_payload = _post_openai_json(
                api_base_url=api_base_url,
                api_key=api_key,
                api_protocol=api_protocol,
                provider_mode=provider_mode,
                api_url_mode=api_url_mode,
                payload=payload,
                timeout_seconds=timeout_value,
            )
            output_text = _extract_output_text(response_payload)
        except Exception as exc:
            if not _requires_stream_response(exc):
                raise
            _STREAM_ONLY_ENDPOINT_CACHE[stream_cache_key] = True
            use_stream_only = True
    if use_stream_only or not str(output_text or "").strip():
        streamed_output_text = _post_openai_stream_text(
            api_base_url=api_base_url,
            api_key=api_key,
            api_protocol=api_protocol,
            provider_mode=provider_mode,
            api_url_mode=api_url_mode,
            payload=payload,
            timeout_seconds=timeout_value,
        )
        if str(streamed_output_text or "").strip():
            output_text = streamed_output_text
        else:
            detail = _describe_empty_model_output(response_payload or {})
            if detail and response_payload is not None:
                raise RuntimeError(f"empty model output: {detail}")
            raise RuntimeError("empty model output")
    return {"output_text": output_text}


def _openai_call(
    image_base64: str,
    prompt: str,
    image_width: int,
    image_height: int,
    api_base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: float,
    provider_mode: Optional[str] = None,
    api_protocol: Optional[str] = None,
    api_url_mode: Optional[str] = None,
    image_mime_type: Optional[str] = None,
    messages: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    raw_result = _openai_raw(
        image_base64=image_base64,
        prompt=prompt,
        api_base_url=api_base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        provider_mode=provider_mode,
        api_protocol=api_protocol,
        api_url_mode=api_url_mode,
        image_mime_type=image_mime_type,
        messages=messages,
    )
    output_text = raw_result.get("output_text", "")
    result_obj = _parse_json_from_text(output_text)
    if not result_obj:
        return {
            "raw_text": output_text,
            "error": "failed to parse json from model output",
        }
    scale_base = _detect_scale_base(result_obj, image_width, image_height)
    if scale_base is None:
        values = []
        for key in ("x", "x1", "x2", "left", "y", "y1", "y2", "top", "width", "height"):
            val = result_obj.get(key)
            if val is None:
                continue
            try:
                values.append(float(val))
            except Exception:
                continue
        center_val = result_obj.get("center")
        if isinstance(center_val, (list, tuple)) and len(center_val) >= 2:
            try:
                values.append(float(center_val[0]))
            except Exception:
                pass
            try:
                values.append(float(center_val[1]))
            except Exception:
                pass
        if values:
            max_dim = max(image_width, image_height)
            try:
                max_val = max(values)
            except Exception:
                max_val = None
            if max_val is not None and max_dim >= 1200 and max_val <= 1024:
                ratio = max_val / float(max_dim) if max_dim > 0 else 1.0
                if max_val <= 1000 and ratio < 0.80:
                    scale_base = 1000
                elif max_val <= 1024 and ratio < 0.80:
                    scale_base = 1024
    point = _extract_point(result_obj, image_width, image_height, scale_base)
    bbox = _extract_bbox(result_obj, image_width, image_height, scale_base)
    if not point:
        return {
            "raw_text": output_text,
            "error": "missing point in model output",
        }
    x, y = point
    out: Dict[str, Any] = {
        "x": int(round(x)),
        "y": int(round(y)),
        "raw_text": output_text,
        "scale_base": scale_base,
    }
    if bbox:
        x1, y1, x2, y2 = bbox
        out.update(
            {
                "x1": int(round(x1)),
                "y1": int(round(y1)),
                "x2": int(round(x2)),
                "y2": int(round(y2)),
            }
        )
    if isinstance(result_obj.get("confidence"), (int, float)):
        out["confidence"] = float(result_obj.get("confidence"))
    return out


def _resolve_ai_request_config(args: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    provider_mode = _normalize_ai_provider_mode(args.get("provider_mode"))
    api_url_mode = _normalize_ai_api_url_mode(
        args.get("api_url_mode"),
        provider_mode=provider_mode,
        api_base_url=args.get("api_base_url"),
    )
    api_base_url, base_url_error = _resolve_ai_api_base_url(
        {
            "provider_mode": provider_mode,
            "api_base_url": args.get("api_base_url"),
        },
        env_base_url=os.getenv("OPENAI_BASE_URL") or "",
    )
    if base_url_error:
        raise RuntimeError(base_url_error)

    api_key = (args.get("api_key") or os.getenv("OPENAI_API_KEY") or "").strip()
    model = (args.get("model") or os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()
    if not api_key:
        raise RuntimeError("missing api_key (set OPENAI_API_KEY)")
    return provider_mode, api_url_mode, api_base_url, api_key, model


def _click_client(hwnd: int, x: int, y: int, button: str = "left", clicks: int = 1) -> Dict[str, Any]:
    if win32gui is None or win32con is None or win32api is None:
        raise RuntimeError("pywin32 is not available")
    if not win32gui.IsWindow(hwnd):
        raise RuntimeError("invalid hwnd")
    button = (button or "left").lower()
    if button not in ("left", "right", "middle"):
        raise RuntimeError("unsupported button")
    if button == "left":
        down_msg, up_msg, wparam = win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP, win32con.MK_LBUTTON
    elif button == "right":
        down_msg, up_msg, wparam = win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP, win32con.MK_RBUTTON
    else:
        down_msg, up_msg, wparam = win32con.WM_MBUTTONDOWN, win32con.WM_MBUTTONUP, win32con.MK_MBUTTON
    lparam = win32api.MAKELONG(int(x), int(y))
    for _ in range(max(1, int(clicks))):
        win32gui.SendMessage(hwnd, down_msg, wparam, lparam)
        time.sleep(0.01)
        win32gui.SendMessage(hwnd, up_msg, 0, lparam)
        time.sleep(0.02)
    return {"ok": True, "x": int(x), "y": int(y), "button": button, "clicks": int(clicks)}


def _tools_list() -> List[Dict[str, Any]]:
    return [
        {
            "name": "list_windows",
            "description": "List top-level windows with title and hwnd.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "capture_window",
            "description": "Capture a window client area and return PNG base64.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "hwnd": {"type": "integer"},
                    "client_area_only": {"type": "boolean", "default": True},
                },
                "required": ["hwnd"],
                "additionalProperties": False,
            },
        },
        {
            "name": "openai_raw",
            "description": "Call OpenAI official or compatible API and return raw output text.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "image_base64": {"type": "string"},
                    "image_mime_type": {"type": "string"},
                    "prompt": {"type": "string"},
                    "messages": {"type": "array"},
                    "provider_mode": {"type": "string"},
                    "api_protocol": {"type": "string", "enum": ["responses", "chat_completions"]},
                    "api_url_mode": {"type": "string"},
                    "api_base_url": {"type": "string"},
                    "api_key": {"type": "string"},
                    "model": {"type": "string"},
                    "timeout_seconds": {"type": "number", "default": 20.0},
                },
                "required": ["image_base64", "prompt"],
                "additionalProperties": False,
            },
        },
        {
            "name": "openai_find",
            "description": "Call OpenAI official or compatible API on a base64 image and return target coordinates.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "image_base64": {"type": "string"},
                    "image_mime_type": {"type": "string"},
                    "prompt": {"type": "string"},
                    "messages": {"type": "array"},
                    "image_width": {"type": "integer"},
                    "image_height": {"type": "integer"},
                    "provider_mode": {"type": "string"},
                    "api_protocol": {"type": "string", "enum": ["responses", "chat_completions"]},
                    "api_url_mode": {"type": "string"},
                    "api_base_url": {"type": "string"},
                    "api_key": {"type": "string"},
                    "model": {"type": "string"},
                    "timeout_seconds": {"type": "number", "default": 20.0},
                },
                "required": ["image_base64", "prompt", "image_width", "image_height"],
                "additionalProperties": False,
            },
        },
        {
            "name": "openai_find_in_window",
            "description": "Capture a window and call OpenAI official or compatible API. Returns coordinates in client space.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "hwnd": {"type": "integer"},
                    "prompt": {"type": "string"},
                    "messages": {"type": "array"},
                    "client_area_only": {"type": "boolean", "default": True},
                    "provider_mode": {"type": "string"},
                    "api_protocol": {"type": "string", "enum": ["responses", "chat_completions"]},
                    "api_url_mode": {"type": "string"},
                    "api_base_url": {"type": "string"},
                    "api_key": {"type": "string"},
                    "model": {"type": "string"},
                    "timeout_seconds": {"type": "number", "default": 20.0},
                },
                "required": ["hwnd", "prompt"],
                "additionalProperties": False,
            },
        },
        {
            "name": "openai_protocol_probe",
            "description": "Probe whether the configured provider supports the selected OpenAI protocol.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "provider_mode": {"type": "string"},
                    "api_protocol": {"type": "string", "enum": ["responses", "chat_completions"]},
                    "api_url_mode": {"type": "string"},
                    "api_base_url": {"type": "string"},
                    "api_key": {"type": "string"},
                    "model": {"type": "string"},
                    "timeout_seconds": {"type": "number", "default": 10.0}
                },
                "required": ["api_protocol", "api_base_url", "api_key", "model"],
                "additionalProperties": False
            }
        },
        {
            "name": "click_client",
            "description": "Send a background click to a window using client coordinates.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "hwnd": {"type": "integer"},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {"type": "string", "default": "left"},
                    "clicks": {"type": "integer", "default": 1},
                },
                "required": ["hwnd", "x", "y"],
                "additionalProperties": False,
            },
        },
    ]


def _handle_tool_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if name == "list_windows":
        return {"windows": _list_windows()}
    if name == "capture_window":
        return _capture_window(int(args["hwnd"]), bool(args.get("client_area_only", True)))
    if name == "openai_protocol_probe":
        provider_mode, api_url_mode, api_base_url, api_key, model = _resolve_ai_request_config(args)
        api_protocol = _normalize_ai_api_protocol(
            args.get("api_protocol"),
            provider_mode=provider_mode,
            api_base_url=api_base_url,
        )
        return _openai_protocol_probe(
            api_base_url=api_base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=float(args.get("timeout_seconds", 10.0)),
            provider_mode=provider_mode,
            api_protocol=api_protocol,
            api_url_mode=api_url_mode,
        )
    if name == "openai_find":
        provider_mode, api_url_mode, api_base_url, api_key, model = _resolve_ai_request_config(args)
        api_protocol = _normalize_ai_api_protocol(
            args.get("api_protocol"),
            provider_mode=provider_mode,
            api_base_url=api_base_url,
        )
        return _openai_call(
            image_base64=args["image_base64"],
            prompt=args["prompt"],
            image_width=int(args["image_width"]),
            image_height=int(args["image_height"]),
            api_base_url=api_base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=float(args.get("timeout_seconds", 20.0)),
            provider_mode=provider_mode,
            api_protocol=api_protocol,
            api_url_mode=api_url_mode,
            image_mime_type=args.get("image_mime_type"),
            messages=args.get("messages"),
        )
    if name == "openai_raw":
        provider_mode, api_url_mode, api_base_url, api_key, model = _resolve_ai_request_config(args)
        api_protocol = _normalize_ai_api_protocol(
            args.get("api_protocol"),
            provider_mode=provider_mode,
            api_base_url=api_base_url,
        )
        return _openai_raw(
            image_base64=args["image_base64"],
            prompt=args["prompt"],
            api_base_url=api_base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=float(args.get("timeout_seconds", 20.0)),
            provider_mode=provider_mode,
            api_protocol=api_protocol,
            api_url_mode=api_url_mode,
            image_mime_type=args.get("image_mime_type"),
            messages=args.get("messages"),
        )
    if name == "openai_find_in_window":
        provider_mode, api_url_mode, api_base_url, api_key, model = _resolve_ai_request_config(args)
        api_protocol = _normalize_ai_api_protocol(
            args.get("api_protocol"),
            provider_mode=provider_mode,
            api_base_url=api_base_url,
        )
        snap = _capture_window(int(args["hwnd"]), bool(args.get("client_area_only", True)))
        return _openai_call(
            image_base64=snap["image_base64"],
            prompt=args["prompt"],
            image_width=int(snap["width"]),
            image_height=int(snap["height"]),
            api_base_url=api_base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=float(args.get("timeout_seconds", 20.0)),
            provider_mode=provider_mode,
            api_protocol=api_protocol,
            api_url_mode=api_url_mode,
            image_mime_type=args.get("image_mime_type"),
            messages=args.get("messages"),
        )
    if name == "click_client":
        return _click_client(
            int(args["hwnd"]),
            int(args["x"]),
            int(args["y"]),
            str(args.get("button", "left")),
            int(args.get("clicks", 1)),
        )
    raise RuntimeError(f"unknown tool: {name}")


def main() -> None:
    _eprint(f"{SERVER_NAME} starting (protocol {PROTOCOL_VERSION})")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as exc:
            _eprint(f"invalid json: {exc}")
            continue

        method = msg.get("method")
        req_id = msg.get("id", None)

        if method == "initialize":
            _rpc_result(
                req_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            )
            continue

        if method == "ping":
            if req_id is not None:
                _rpc_result(req_id, {})
            continue

        if method == "tools/list":
            _rpc_result(req_id, {"tools": _tools_list()})
            continue

        if method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if not name:
                _rpc_error(req_id, "missing tool name")
                continue
            try:
                result = _handle_tool_call(name, args)
                _rpc_result(req_id, {"content": [{"type": "text", "text": json.dumps(result)}]})
            except Exception as exc:
                _rpc_error(req_id, _format_exception_chain(exc))
            continue

        # Notifications or unsupported methods
        if req_id is not None:
            _rpc_error(req_id, f"unsupported method: {method}")


if __name__ == "__main__":
    main()

