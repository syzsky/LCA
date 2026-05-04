from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Optional

import numpy as np

from utils.dxgi_capture import capture_screen_dxgi, get_dxgi_monitors, is_dxgi_available
from utils.gdi_capture import capture_screen_gdi, is_gdi_available
from utils.multi_monitor_manager import get_virtual_screen_bounds


def _normalize_region(region) -> Optional[tuple[int, int, int, int]]:
    if isinstance(region, Mapping):
        left = region.get("left", 0)
        top = region.get("top", 0)
        width = region.get("width", 0)
        height = region.get("height", 0)
    elif isinstance(region, Sequence) and not isinstance(region, (str, bytes)) and len(region) == 4:
        left, top, width, height = region
    else:
        return None

    try:
        normalized = (int(left), int(top), int(width), int(height))
    except Exception:
        return None

    if normalized[2] <= 0 or normalized[3] <= 0:
        return None
    return normalized


def _resolve_dxgi_region(
    absolute_region: tuple[int, int, int, int],
) -> Optional[tuple[int, tuple[int, int, int, int]]]:
    left, top, width, height = absolute_region
    right = left + width
    bottom = top + height

    for monitor in get_dxgi_monitors():
        mon_left = int(getattr(monitor, "left", 0))
        mon_top = int(getattr(monitor, "top", 0))
        mon_width = int(getattr(monitor, "width", 0))
        mon_height = int(getattr(monitor, "height", 0))
        mon_right = mon_left + mon_width
        mon_bottom = mon_top + mon_height
        if left < mon_left or top < mon_top:
            continue
        if right > mon_right or bottom > mon_bottom:
            continue
        return (
            int(getattr(monitor, "index", 0)),
            (left - mon_left, top - mon_top, width, height),
        )
    return None


def _is_valid_frame(frame: Optional[np.ndarray], expected_region: tuple[int, int, int, int]) -> bool:
    if frame is None or not isinstance(frame, np.ndarray):
        return False
    if frame.ndim < 2:
        return False
    return frame.shape[0] == expected_region[3] and frame.shape[1] == expected_region[2]


def _clip_region_to_virtual_screen(
    absolute_region: tuple[int, int, int, int],
) -> Optional[tuple[int, int, int, int]]:
    left, top, width, height = absolute_region
    virtual_left, virtual_top, virtual_width, virtual_height = get_virtual_screen_bounds()
    if virtual_width <= 0 or virtual_height <= 0:
        return None

    virtual_right = int(virtual_left + virtual_width)
    virtual_bottom = int(virtual_top + virtual_height)
    clipped_left = max(int(left), int(virtual_left))
    clipped_top = max(int(top), int(virtual_top))
    clipped_right = min(int(left + width), virtual_right)
    clipped_bottom = min(int(top + height), virtual_bottom)
    if clipped_right <= clipped_left or clipped_bottom <= clipped_top:
        return None
    return (
        clipped_left,
        clipped_top,
        int(clipped_right - clipped_left),
        int(clipped_bottom - clipped_top),
    )


def _pad_frame_to_requested_region(
    frame: Optional[np.ndarray],
    *,
    requested_region: tuple[int, int, int, int],
    captured_region: tuple[int, int, int, int],
) -> Optional[np.ndarray]:
    if frame is None or not isinstance(frame, np.ndarray) or frame.ndim < 2:
        return None

    requested_left, requested_top, requested_width, requested_height = requested_region
    captured_left, captured_top, _captured_width, _captured_height = captured_region
    if requested_width <= 0 or requested_height <= 0:
        return None

    shape_suffix = tuple(frame.shape[2:]) if frame.ndim > 2 else ()
    padded = np.zeros((requested_height, requested_width, *shape_suffix), dtype=frame.dtype)
    offset_x = max(0, int(captured_left - requested_left))
    offset_y = max(0, int(captured_top - requested_top))
    copy_width = min(int(frame.shape[1]), max(0, requested_width - offset_x))
    copy_height = min(int(frame.shape[0]), max(0, requested_height - offset_y))
    if copy_width <= 0 or copy_height <= 0:
        return None

    if frame.ndim == 2:
        padded[offset_y:offset_y + copy_height, offset_x:offset_x + copy_width] = frame[:copy_height, :copy_width]
    else:
        padded[offset_y:offset_y + copy_height, offset_x:offset_x + copy_width, ...] = (
            frame[:copy_height, :copy_width, ...]
        )
    return padded


def capture_region_bgr(region) -> Optional[np.ndarray]:
    absolute_region = _normalize_region(region)
    if absolute_region is None:
        return None

    if is_dxgi_available():
        dxgi_region = _resolve_dxgi_region(absolute_region)
        if dxgi_region is not None:
            monitor_index, relative_region = dxgi_region
            frame = capture_screen_dxgi(monitor_index=monitor_index, region=relative_region)
            if _is_valid_frame(frame, absolute_region):
                return frame

    if is_gdi_available():
        frame = capture_screen_gdi(region=absolute_region)
        if _is_valid_frame(frame, absolute_region):
            return frame
        clipped_region = _clip_region_to_virtual_screen(absolute_region)
        if clipped_region is not None and clipped_region != absolute_region:
            clipped_frame = capture_screen_gdi(region=clipped_region)
            adjusted_frame = _pad_frame_to_requested_region(
                clipped_frame,
                requested_region=absolute_region,
                captured_region=clipped_region,
            )
            if _is_valid_frame(adjusted_frame, absolute_region):
                return adjusted_frame

    return None


def capture_region_bgra(region) -> Optional[np.ndarray]:
    frame = capture_region_bgr(region)
    if frame is None:
        return None
    alpha = np.full((frame.shape[0], frame.shape[1], 1), 255, dtype=frame.dtype)
    return np.concatenate((frame, alpha), axis=2)
