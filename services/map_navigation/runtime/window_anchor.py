from __future__ import annotations

from typing import Optional, Tuple


DEFAULT_DYNAMIC_ISLAND_VERTICAL_GAP = 0
DEFAULT_DYNAMIC_ISLAND_EDGE_MARGIN = 8


def _clamp(value: int, minimum: int, maximum: int) -> int:
    if minimum > maximum:
        maximum = minimum
    return max(int(minimum), min(int(value), int(maximum)))


def compute_dynamic_island_position(
    window_rect: Tuple[int, int, int, int],
    client_origin: Optional[Tuple[int, int]],
    overlay_size: Tuple[int, int],
    *,
    vertical_gap: int = DEFAULT_DYNAMIC_ISLAND_VERTICAL_GAP,
    screen_rect: Optional[Tuple[int, int, int, int]] = None,
    edge_margin: int = DEFAULT_DYNAMIC_ISLAND_EDGE_MARGIN,
) -> Tuple[int, int]:
    left, top, right, _bottom = [int(value) for value in window_rect]
    overlay_width, overlay_height = [max(1, int(value)) for value in overlay_size]

    anchor_y_source = int(client_origin[1]) if client_origin is not None else int(top)
    if anchor_y_source < int(top):
        anchor_y_source = int(top)

    x = int((left + right - overlay_width) / 2)
    y = int(anchor_y_source + int(vertical_gap))

    if screen_rect is None:
        return x, y

    screen_left, screen_top, screen_right, screen_bottom = [int(value) for value in screen_rect]
    x = _clamp(
        x,
        screen_left + int(edge_margin),
        screen_right - overlay_width - int(edge_margin),
    )
    y = _clamp(
        y,
        screen_top + int(edge_margin),
        screen_bottom - overlay_height - int(edge_margin),
    )
    return x, y


def compute_dynamic_island_window_size(
    *,
    expanded: bool,
    top_bar_width: int,
    top_bar_height: int,
    preview_height: int,
    expanded_width: int,
    expanded_height: int,
    collapsed_min_width: int,
    collapsed_min_height: int,
    collapsed_width_padding: int,
    vertical_spacing: int = 0,
) -> Tuple[int, int]:
    normalized_top_bar_width = max(0, int(top_bar_width))
    normalized_top_bar_height = max(0, int(top_bar_height))
    normalized_preview_height = max(0, int(preview_height))
    base_width = max(
        int(expanded_width),
        normalized_top_bar_width,
        int(collapsed_min_width),
    )

    if expanded:
        content_height = normalized_top_bar_height
        if normalized_preview_height > 0:
            content_height += int(vertical_spacing) + normalized_preview_height
        target_height = max(int(expanded_height), content_height)
        return base_width, target_height

    target_height = max(int(collapsed_min_height), normalized_top_bar_height)
    return base_width, target_height


def compute_tracking_search_radius(
    current_radius: int,
    *,
    base_radius: int,
    max_radius: int,
    step: int,
    reset: bool = False,
) -> int:
    normalized_base = max(1, int(base_radius))
    normalized_max = max(normalized_base, int(max_radius))
    normalized_step = max(1, int(step))
    normalized_current = max(normalized_base, int(current_radius))

    if reset:
        return normalized_base
    if normalized_current >= normalized_max:
        return normalized_max
    return min(normalized_max, normalized_current + normalized_step)
