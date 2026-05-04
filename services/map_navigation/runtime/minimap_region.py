from __future__ import annotations

from typing import Any, Optional

from utils.window_coordinate_common import (
    build_window_info,
    client_relative_to_qt_global,
    get_window_client_native_rect,
    get_window_client_qt_global_rect,
    native_rect_to_qt_global_rect,
    normalize_region_binding_hwnd,
    overlay_local_rect_to_client_relative,
    qt_global_to_native_point,
)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def _rect_to_xywh(rect: Any) -> Optional[tuple[int, int, int, int]]:
    if rect is None:
        return None

    if isinstance(rect, tuple) and len(rect) == 4:
        try:
            x, y, width, height = [int(value) for value in rect]
        except Exception:
            return None
        if width <= 0 or height <= 0:
            return None
        return x, y, width, height

    try:
        x = int(rect.x())
        y = int(rect.y())
        width = int(rect.width())
        height = int(rect.height())
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


def _extract_relative_region(payload: Any) -> Optional[tuple[int, int, int, int]]:
    if not isinstance(payload, dict):
        return None

    width = _coerce_int(payload.get("region_width", 0), 0)
    height = _coerce_int(payload.get("region_height", 0), 0)
    if width <= 0 or height <= 0:
        return None

    return (
        _coerce_int(payload.get("region_x", 0), 0),
        _coerce_int(payload.get("region_y", 0), 0),
        width,
        height,
    )


def _extract_absolute_region(payload: Any) -> Optional[tuple[int, int, int, int]]:
    if not isinstance(payload, dict):
        return None

    width = _coerce_int(payload.get("width", 0), 0)
    height = _coerce_int(payload.get("height", 0), 0)
    if width <= 0 or height <= 0:
        return None

    return (
        _coerce_int(payload.get("left", 0), 0),
        _coerce_int(payload.get("top", 0), 0),
        width,
        height,
    )


def has_valid_minimap_region_payload(payload: Any) -> bool:
    return _extract_relative_region(payload) is not None or _extract_absolute_region(payload) is not None


def resolve_minimap_bound_window_info(target_hwnd: int, payload: Any = None) -> Optional[dict[str, Any]]:
    payload_dict = payload if isinstance(payload, dict) else {}

    fallback_hwnd = _coerce_int(payload_dict.get("region_hwnd", 0), 0)
    base_hwnd = _coerce_int(target_hwnd, 0) or fallback_hwnd
    if base_hwnd <= 0:
        return None

    normalized_hwnd, normalized_title, normalized_class, normalized_client_width, normalized_client_height = (
        normalize_region_binding_hwnd(
            base_hwnd,
            title_hint=_coerce_text(payload_dict.get("region_window_title")),
            class_hint=_coerce_text(payload_dict.get("region_window_class")),
            client_width=_coerce_int(payload_dict.get("region_client_width", 0), 0),
            client_height=_coerce_int(payload_dict.get("region_client_height", 0), 0),
        )
    )
    if normalized_hwnd <= 0:
        return None

    window_info = build_window_info(normalized_hwnd)
    if not isinstance(window_info, dict):
        return None

    normalized_info = dict(window_info)
    normalized_info["region_hwnd"] = int(normalized_hwnd)
    normalized_info["region_window_title"] = normalized_title
    normalized_info["region_window_class"] = normalized_class
    normalized_info["region_client_width"] = int(normalized_client_width)
    normalized_info["region_client_height"] = int(normalized_client_height)
    return normalized_info


def resolve_minimap_capture_region(payload: Any, *, target_hwnd: int) -> Optional[dict[str, int]]:
    relative_region = _extract_relative_region(payload)
    if relative_region is not None:
        window_info = resolve_minimap_bound_window_info(target_hwnd, payload)
        client_native_rect = get_window_client_native_rect(window_info)
        if not client_native_rect:
            return None

        region_x, region_y, region_width, region_height = relative_region
        client_left, client_top, client_right, client_bottom = [int(value) for value in client_native_rect]
        client_width = max(1, int(client_right - client_left))
        client_height = max(1, int(client_bottom - client_top))

        left = max(0, min(int(region_x), client_width - 1))
        top = max(0, min(int(region_y), client_height - 1))
        width = max(1, min(int(region_width), client_width - left))
        height = max(1, min(int(region_height), client_height - top))
        return {
            "left": int(client_left + left),
            "top": int(client_top + top),
            "width": int(width),
            "height": int(height),
        }

    absolute_region = _extract_absolute_region(payload)
    if absolute_region is None:
        return None

    left, top, width, height = absolute_region
    return {
        "left": int(left),
        "top": int(top),
        "width": int(width),
        "height": int(height),
    }


def resolve_minimap_qt_rect(payload: Any, *, target_hwnd: int) -> Optional[tuple[int, int, int, int]]:
    relative_region = _extract_relative_region(payload)
    if relative_region is not None:
        window_info = resolve_minimap_bound_window_info(target_hwnd, payload)
        if not isinstance(window_info, dict):
            return None

        region_x, region_y, region_width, region_height = relative_region
        qt_left, qt_top = client_relative_to_qt_global(window_info, region_x, region_y)
        qt_right, qt_bottom = client_relative_to_qt_global(
            window_info,
            region_x + region_width,
            region_y + region_height,
        )
        x = min(int(qt_left), int(qt_right))
        y = min(int(qt_top), int(qt_bottom))
        width = max(1, abs(int(qt_right) - int(qt_left)))
        height = max(1, abs(int(qt_bottom) - int(qt_top)))
        return x, y, width, height

    absolute_region = _extract_absolute_region(payload)
    if absolute_region is None:
        return None

    left, top, width, height = absolute_region
    qt_rect = native_rect_to_qt_global_rect((left, top, left + width, top + height))
    rect_metrics = _rect_to_xywh(qt_rect)
    if rect_metrics is None:
        return None
    return rect_metrics


def build_minimap_region_payload_from_qt_rect(qt_rect: Any, *, target_hwnd: int) -> Optional[dict[str, Any]]:
    rect_metrics = _rect_to_xywh(qt_rect)
    if rect_metrics is None:
        return None

    qt_left, qt_top, qt_width, qt_height = rect_metrics
    native_left, native_top = qt_global_to_native_point(qt_left, qt_top)
    native_right, native_bottom = qt_global_to_native_point(qt_left + qt_width, qt_top + qt_height)
    absolute_payload: dict[str, Any] = {
        "left": int(min(native_left, native_right)),
        "top": int(min(native_top, native_bottom)),
        "width": int(max(1, abs(native_right - native_left))),
        "height": int(max(1, abs(native_bottom - native_top))),
    }

    window_info = resolve_minimap_bound_window_info(target_hwnd)
    if not isinstance(window_info, dict):
        return absolute_payload

    target_qt_rect = get_window_client_qt_global_rect(window_info)
    relative_rect = overlay_local_rect_to_client_relative(window_info, target_qt_rect, rect_metrics)
    if not relative_rect:
        return absolute_payload

    region_x, region_y, region_width, region_height = [int(value) for value in relative_rect]
    normalized_hwnd, normalized_title, normalized_class, normalized_client_width, normalized_client_height = (
        normalize_region_binding_hwnd(
            window_info.get("hwnd", 0),
            title_hint=window_info.get("region_window_title", ""),
            class_hint=window_info.get("region_window_class", ""),
            client_width=window_info.get("region_client_width", 0),
            client_height=window_info.get("region_client_height", 0),
        )
    )

    absolute_payload.update(
        {
            "region_x": int(region_x),
            "region_y": int(region_y),
            "region_width": int(region_width),
            "region_height": int(region_height),
            "region_hwnd": int(normalized_hwnd or window_info.get("hwnd", 0) or 0),
            "region_window_title": normalized_title,
            "region_window_class": normalized_class,
            "region_client_width": int(normalized_client_width),
            "region_client_height": int(normalized_client_height),
        }
    )
    return absolute_payload
