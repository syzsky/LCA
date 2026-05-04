# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


def select_best_result(items: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(items, list):
        return None
    dict_items = [item for item in items if isinstance(item, dict)]
    if not dict_items:
        return None
    best = None
    best_conf = None
    for item in dict_items:
        conf = item.get("confidence")
        try:
            conf_val = float(conf)
        except Exception:
            conf_val = None
        if best is None:
            best = item
            best_conf = conf_val
            continue
        if conf_val is None:
            continue
        if best_conf is None or conf_val > best_conf:
            best = item
            best_conf = conf_val
    return best


def strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
    return cleaned.strip()


def parse_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    cleaned = strip_code_fences(text)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return select_best_result(parsed)
    except Exception:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        candidate = cleaned[start:end + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


def extract_output_text(payload: Dict[str, Any]) -> str:
    if not payload:
        return ""

    def _collect_text_parts(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, list):
            texts: List[str] = []
            for item in value:
                texts.extend(_collect_text_parts(item))
            return texts
        if isinstance(value, dict):
            for key in (
                "output_text",
                "text",
                "value",
                "content",
                "reasoning_content",
                "answer",
                "response",
                "result",
            ):
                texts = _collect_text_parts(value.get(key))
                if texts:
                    return texts
        return []

    output_text = "\n".join(_collect_text_parts(payload.get("output_text")))
    if output_text:
        return output_text

    output_text = "\n".join(_collect_text_parts(payload.get("output")))
    if output_text:
        return output_text

    if isinstance(payload.get("choices"), list) and payload["choices"]:
        choice = payload["choices"][0] or {}
        message = choice.get("message") or {}
        output_text = "\n".join(_collect_text_parts(message.get("content")))
        if output_text:
            return output_text
        output_text = "\n".join(_collect_text_parts(message.get("reasoning_content")))
        if output_text:
            return output_text
        function_call = message.get("function_call") or {}
        output_text = "\n".join(_collect_text_parts(function_call.get("arguments")))
        if output_text:
            return output_text
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                function_obj = (tool_call or {}).get("function") or {}
                output_text = "\n".join(_collect_text_parts(function_obj.get("arguments")))
                if output_text:
                    return output_text
        output_text = "\n".join(_collect_text_parts(choice.get("text")))
        if output_text:
            return output_text
        output_text = "\n".join(_collect_text_parts(choice.get("delta")))
        if output_text:
            return output_text

    output_text = "\n".join(
        _collect_text_parts(
            payload.get("message")
            or payload.get("response")
            or payload.get("result")
            or payload.get("answer")
        )
    )
    if output_text:
        return output_text
    return ""


def extract_confidence(result: Dict[str, Any]) -> Optional[float]:
    if not result:
        return None
    conf = result.get("confidence")
    try:
        return float(conf)
    except Exception:
        return None


def normalize_value(value: Any, max_value: int, scale_base: Optional[int] = None) -> Optional[float]:
    try:
        number = float(value)
    except Exception:
        return None
    if scale_base and scale_base > 0:
        return number / float(scale_base) * float(max_value)
    return number


def adjust_bbox_center(center_x: float, center_y: float, box_w: float, box_h: float,
                       width: int, height: int) -> Tuple[float, float]:
    if box_w <= 0 or box_h <= 0:
        return center_x, center_y
    if box_w > width * 0.85 and box_h > height * 0.85:
        return center_x, center_y
    adjust_y = min(box_h * 0.18, max(4.0, height * 0.015))
    adjusted_y = center_y - adjust_y
    return center_x, max(0.0, adjusted_y)


def detect_scale_base(result: Dict[str, Any], width: int, height: int) -> Optional[int]:
    if not result:
        return None

    scale = result.get("scale")
    if scale is not None:
        try:
            scale_val = float(scale)
            if scale_val > 0:
                if scale_val <= 1.0:
                    return 1
                return int(scale_val)
        except Exception:
            if str(scale).strip().lower() in ("px", "pixel", "pixels"):
                return None

    values: List[float] = []
    for key in ("x", "y", "x1", "y1", "x2", "y2", "left", "top", "width", "height"):
        val = result.get(key)
        if val is None:
            continue
        try:
            values.append(float(val))
        except Exception:
            continue

    center_val = result.get("center")
    if isinstance(center_val, (list, tuple)) and len(center_val) >= 2:
        try:
            values.append(float(center_val[0]))
        except Exception:
            pass
        try:
            values.append(float(center_val[1]))
        except Exception:
            pass

    if not values:
        return None

    max_val = max(values)
    min_val = min(values)
    if 0.0 <= min_val and max_val <= 1.0:
        return 1

    max_dim = max(width, height)
    if max_dim <= 0:
        return None

    ratio = max_val / float(max_dim)
    if max_dim >= 1200 and max_val <= 1000 and ratio < 0.80:
        return 1000
    if max_dim >= 1200 and max_val <= 1024 and ratio < 0.80:
        return 1024
    if max_val <= 1000 and max_dim < 1000:
        return 1000
    if max_val <= 1024 and max_dim < 1024:
        return 1024
    return None


def extract_point(result: Dict[str, Any], width: int, height: int,
                  scale_base: Optional[int] = None) -> Optional[Tuple[int, int]]:
    if not result:
        return None

    if all(k in result for k in ("x1", "y1", "x2", "y2")):
        x1 = normalize_value(result.get("x1"), width, scale_base)
        y1 = normalize_value(result.get("y1"), height, scale_base)
        x2 = normalize_value(result.get("x2"), width, scale_base)
        y2 = normalize_value(result.get("y2"), height, scale_base)
        if None in (x1, y1, x2, y2):
            return None
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        box_w = abs(x2 - x1)
        box_h = abs(y2 - y1)
        cx, cy = adjust_bbox_center(cx, cy, box_w, box_h, width, height)
        return int(round(cx)), int(round(cy))

    if all(k in result for k in ("left", "top", "width", "height")):
        left = normalize_value(result.get("left"), width, scale_base)
        top = normalize_value(result.get("top"), height, scale_base)
        box_w = normalize_value(result.get("width"), width, scale_base)
        box_h = normalize_value(result.get("height"), height, scale_base)
        if None in (left, top, box_w, box_h):
            return None
        cx = left + box_w / 2.0
        cy = top + box_h / 2.0
        cx, cy = adjust_bbox_center(cx, cy, box_w, box_h, width, height)
        return int(round(cx)), int(round(cy))

    if "x" in result and "y" in result:
        x = normalize_value(result.get("x"), width, scale_base)
        y = normalize_value(result.get("y"), height, scale_base)
        if x is None or y is None:
            return None
        return int(round(x)), int(round(y))

    if "center" in result and isinstance(result.get("center"), (list, tuple)) and len(result["center"]) >= 2:
        x = normalize_value(result["center"][0], width, scale_base)
        y = normalize_value(result["center"][1], height, scale_base)
        if x is None or y is None:
            return None
        return int(round(x)), int(round(y))

    return None


def extract_bbox(result: Dict[str, Any], width: int, height: int,
                 scale_base: Optional[int] = None) -> Optional[Tuple[float, float, float, float]]:
    if not result:
        return None

    if all(k in result for k in ("x1", "y1", "x2", "y2")):
        x1 = normalize_value(result.get("x1"), width, scale_base)
        y1 = normalize_value(result.get("y1"), height, scale_base)
        x2 = normalize_value(result.get("x2"), width, scale_base)
        y2 = normalize_value(result.get("y2"), height, scale_base)
        if None in (x1, y1, x2, y2):
            return None
        return float(x1), float(y1), float(x2), float(y2)

    if all(k in result for k in ("left", "top", "width", "height")):
        left = normalize_value(result.get("left"), width, scale_base)
        top = normalize_value(result.get("top"), height, scale_base)
        box_w = normalize_value(result.get("width"), width, scale_base)
        box_h = normalize_value(result.get("height"), height, scale_base)
        if None in (left, top, box_w, box_h):
            return None
        return float(left), float(top), float(left + box_w), float(top + box_h)

    bbox_val = result.get("bbox")
    if isinstance(bbox_val, (list, tuple)) and bbox_val:
        points = []
        if isinstance(bbox_val[0], (list, tuple)):
            for pt in bbox_val:
                if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                    x = normalize_value(pt[0], width, scale_base)
                    y = normalize_value(pt[1], height, scale_base)
                    if x is not None and y is not None:
                        points.append((x, y))
        elif len(bbox_val) >= 8 and len(bbox_val) % 2 == 0:
            for idx in range(0, len(bbox_val) - 1, 2):
                x = normalize_value(bbox_val[idx], width, scale_base)
                y = normalize_value(bbox_val[idx + 1], height, scale_base)
                if x is not None and y is not None:
                    points.append((x, y))
        elif len(bbox_val) >= 4:
            x1 = normalize_value(bbox_val[0], width, scale_base)
            y1 = normalize_value(bbox_val[1], height, scale_base)
            x2 = normalize_value(bbox_val[2], width, scale_base)
            y2 = normalize_value(bbox_val[3], height, scale_base)
            if None not in (x1, y1, x2, y2):
                points = [(x1, y1), (x2, y2)]

        if points:
            xs = [pt[0] for pt in points]
            ys = [pt[1] for pt in points]
            return float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))

    return None
