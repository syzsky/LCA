# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import glob
import json
import os
import sys
import threading
import time

import cv2
import numpy as np


class RouteManager:
    _DEFAULT_CATEGORY_LABELS = {
        "zhiwu": "植物",
        "diquluxian": "路线",
        "qita": "其他",
        "anqiufenlei": "精灵球材料",
    }

    def __init__(self, base_folder: str = "routes") -> None:
        env_base_dir = str(os.environ.get("LCA_LKMAPTOOLS_BASE_DIR", "") or "").strip()
        if os.path.isabs(base_folder):
            self.base_folder = os.path.abspath(base_folder)
        elif env_base_dir:
            self.base_folder = os.path.join(os.path.abspath(env_base_dir), base_folder)
        elif getattr(sys, "frozen", False):
            self.base_folder = os.path.join(os.path.dirname(sys.executable), base_folder)
        else:
            self.base_folder = os.path.abspath(base_folder)

        self.bundle_root = os.path.dirname(self.base_folder)
        self.manifest = self._load_manifest()
        self.category_display_names = {}
        self.categories = self._discover_categories()
        self.route_groups = {cat: [] for cat in self.categories}
        self._hidden_categories: set[str] = set()
        self._hidden_route_ids: set[str] = set()
        self.colors = [
            (0, 255, 0),
            (255, 165, 0),
            (0, 255, 255),
            (255, 0, 255),
            (0, 128, 255),
        ]
        self._icon_cache = {}
        self._dynamic_plan_cache: dict[tuple[object, ...], dict[str, object]] = {}
        self._visited_revision = 0
        self._cache_lock = threading.Lock()
        self._load_all_routes()

    def _load_manifest(self) -> dict:
        manifest_path = os.path.join(self.bundle_root, "manifest.json")
        if not os.path.isfile(manifest_path):
            return {}
        try:
            with open(manifest_path, "r", encoding="utf-8") as file:
                payload = json.load(file)
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        return {}

    def _discover_categories(self) -> list[str]:
        categories = []
        route_categories = self.manifest.get("route_categories")
        if isinstance(route_categories, list):
            for item in route_categories:
                if not isinstance(item, dict):
                    continue
                category_id = str(item.get("id") or "").strip()
                if not category_id or category_id in categories:
                    continue
                categories.append(category_id)
                label = str(item.get("label") or category_id).strip() or category_id
                self.category_display_names[category_id] = label

        if os.path.isdir(self.base_folder):
            dynamic_categories = sorted(
                name
                for name in os.listdir(self.base_folder)
                if os.path.isdir(os.path.join(self.base_folder, name)) and not str(name or "").startswith(".")
            )
            for category_id in dynamic_categories:
                if category_id not in categories:
                    categories.append(category_id)

        if not categories:
            categories = list(self._DEFAULT_CATEGORY_LABELS.keys())

        for category_id in categories:
            self.category_display_names.setdefault(
                category_id,
                self._DEFAULT_CATEGORY_LABELS.get(category_id, category_id),
            )
        return categories

    def _iter_routes(self):
        for cat in self.categories:
            for route in self.route_groups[cat]:
                yield cat, route

    def get_category_label(self, category: str) -> str:
        return str(self.category_display_names.get(category, category) or category)

    def is_category_visible(self, category: str | None) -> bool:
        category_id = str(category or "").strip()
        if not category_id:
            return True
        return category_id not in self._hidden_categories

    def is_route_visible(self, category: str | None, route_ref) -> bool:
        category_id = str(category or "").strip()
        route_id = ""
        route = None
        if isinstance(route_ref, dict):
            route = route_ref
            if not category_id:
                category_id = str(route.get("_category") or "").strip()
            route_id = self._route_id_for(category_id, route)
        else:
            route_id, _route_name, route = self.resolve_route(route_ref)
            route_id = str(route_id or "").strip()
            if route is not None and not category_id:
                category_id = str(route.get("_category") or "").strip()

        if not self.is_category_visible(category_id):
            return False
        if not route_id:
            return True
        return route_id not in self._hidden_route_ids

    def set_category_visible(self, category: str | None, visible: bool) -> bool:
        category_id = str(category or "").strip()
        if not category_id:
            return False
        visible = bool(visible)
        changed = False
        if visible:
            if category_id in self._hidden_categories:
                self._hidden_categories.remove(category_id)
                changed = True
        else:
            if category_id not in self._hidden_categories:
                self._hidden_categories.add(category_id)
                changed = True
        if changed:
            self._invalidate_dynamic_plan_cache()
        return changed

    def set_route_visible(self, route_ref, visible: bool) -> bool:
        route_id, _route_name, _route = self.resolve_route(route_ref)
        route_id = str(route_id or "").strip()
        if not route_id:
            return False
        visible = bool(visible)
        changed = False
        if visible:
            if route_id in self._hidden_route_ids:
                self._hidden_route_ids.remove(route_id)
                changed = True
        else:
            if route_id not in self._hidden_route_ids:
                self._hidden_route_ids.add(route_id)
                changed = True
        if changed:
            self._invalidate_dynamic_plan_cache()
        return changed

    def set_all_routes_visible(self, visible: bool) -> bool:
        visible = bool(visible)
        changed = False
        if visible:
            if self._hidden_categories or self._hidden_route_ids:
                self._hidden_categories.clear()
                self._hidden_route_ids.clear()
                changed = True
        else:
            target_hidden_categories = set(self.categories)
            if self._hidden_categories != target_hidden_categories or self._hidden_route_ids:
                self._hidden_categories = target_hidden_categories
                self._hidden_route_ids.clear()
                changed = True
        if changed:
            self._invalidate_dynamic_plan_cache()
        return changed

    def get_visibility_state(self) -> dict:
        visible_categories = [
            category
            for category in self.categories
            if category not in self._hidden_categories
        ]
        visible_route_ids: list[str] = []
        for category, route in self._iter_routes():
            route_id = self._route_id_for(category, route)
            if route_id and route_id not in self._hidden_route_ids:
                visible_route_ids.append(route_id)
        return {
            "visible_categories": visible_categories,
            "visible_route_ids": visible_route_ids,
        }

    def apply_visibility_state(self, visibility_state, *, default_visible: bool = True) -> bool:
        all_route_ids = {
            route_id
            for category, route in self._iter_routes()
            for route_id in [self._route_id_for(category, route)]
            if route_id
        }
        target_hidden_categories = set()
        target_hidden_route_ids = set()
        if not bool(default_visible):
            target_hidden_categories = set(self.categories)
            target_hidden_route_ids = set(all_route_ids)

        if isinstance(visibility_state, dict):
            visible_categories = visibility_state.get("visible_categories")
            if isinstance(visible_categories, (list, tuple, set)):
                for category in visible_categories:
                    category_id = str(category or "").strip()
                    if category_id in target_hidden_categories:
                        target_hidden_categories.remove(category_id)

            visible_route_ids = visibility_state.get("visible_route_ids")
            if isinstance(visible_route_ids, (list, tuple, set)):
                for route_ref in visible_route_ids:
                    route_id = str(route_ref or "").strip()
                    if route_id in target_hidden_route_ids:
                        target_hidden_route_ids.remove(route_id)

        changed = (
            target_hidden_categories != self._hidden_categories
            or target_hidden_route_ids != self._hidden_route_ids
        )
        self._hidden_categories = target_hidden_categories
        self._hidden_route_ids = target_hidden_route_ids
        if changed:
            self._invalidate_dynamic_plan_cache()
        return changed

    def get_category_visibility_summary(self, category: str | None) -> tuple[int, int]:
        category_id = str(category or "").strip()
        routes = self.route_groups.get(category_id, [])
        total_routes = 0
        visible_routes = 0
        for route in routes:
            route_id = self._route_id_for(category_id, route)
            if not route_id:
                continue
            total_routes += 1
            if self.is_route_visible(category_id, route):
                visible_routes += 1
        return int(visible_routes), int(total_routes)

    def get_visible_route_count(self) -> int:
        count = 0
        for _category, _route in self.iter_visible_routes():
            count += 1
        return int(count)

    def _route_id_for(self, category: str, route: dict) -> str:
        route_id = str(route.get("route_id") or "").strip()
        if route_id:
            return route_id
        name = str(route.get("display_name") or "").strip()
        if category and name:
            return f"{category}/{name}"
        return name

    @staticmethod
    def _coerce_bool(value, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if not text:
            return bool(default)
        if text in {"1", "true", "yes", "y", "on", "开启", "是"}:
            return True
        if text in {"0", "false", "no", "n", "off", "关闭", "否"}:
            return False
        return bool(default)

    @staticmethod
    def _coerce_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return int(default)

    @staticmethod
    def _normalize_bgr_color(value, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            try:
                blue = int(value[0])
                green = int(value[1])
                red = int(value[2])
                return (
                    max(0, min(255, blue)),
                    max(0, min(255, green)),
                    max(0, min(255, red)),
                )
            except Exception:
                return fallback

        text = str(value or "").strip()
        if text.startswith("#") and len(text) == 7:
            try:
                red = int(text[1:3], 16)
                green = int(text[3:5], 16)
                blue = int(text[5:7], 16)
                return blue, green, red
            except Exception:
                return fallback
        return fallback

    def _get_route_color(self, route: dict, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
        return self._normalize_bgr_color(route.get("color"), fallback)

    def _get_point_color(self, route: dict, fallback: tuple[int, int, int], *, visited: bool) -> tuple[int, int, int]:
        key = "visited_color" if visited else "point_color"
        if key in route:
            return self._normalize_bgr_color(route.get(key), fallback)
        if visited:
            return (128, 128, 128)
        return fallback

    def _should_connect_route(self, route: dict) -> bool:
        return self._coerce_bool(route.get("connect_points"), True)

    def _should_track_visited(self, route: dict) -> bool:
        if "disable_auto_visited" in route:
            return not self._coerce_bool(route.get("disable_auto_visited"), False)
        return True

    def _resolve_icon_path(self, route: dict) -> str:
        candidates = [route.get("icon_path")]
        points = route.get("points", [])
        if points:
            candidates.append(points[0].get("icon_path"))

        for value in candidates:
            text = str(value or "").strip()
            if not text:
                continue
            if os.path.isabs(text):
                candidate = os.path.abspath(text)
            else:
                candidate = os.path.abspath(os.path.join(self.bundle_root, text))
            if os.path.isfile(candidate):
                return candidate
        return ""

    @staticmethod
    def _build_visited_icon(icon: np.ndarray) -> np.ndarray:
        if icon is None or icon.ndim != 3 or icon.shape[2] < 3:
            return icon

        gray = cv2.cvtColor(icon[:, :, :3], cv2.COLOR_BGR2GRAY)
        gray_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        blended_bgr = cv2.addWeighted(gray_bgr, 0.82, np.full_like(gray_bgr, 150), 0.18, 0)
        if icon.shape[2] == 4:
            return np.dstack((blended_bgr, icon[:, :, 3]))
        return blended_bgr

    def _get_icon_image(self, route: dict, icon_size: int, *, visited: bool = False):
        resolved_icon_size = max(1, self._coerce_int(icon_size, 0))
        icon_path = self._resolve_icon_path(route)
        if not icon_path or resolved_icon_size <= 0:
            return None

        cache_key = (icon_path, resolved_icon_size, bool(visited))
        if cache_key in self._icon_cache:
            return self._icon_cache[cache_key]

        try:
            icon = cv2.imread(icon_path, cv2.IMREAD_UNCHANGED)
            if icon is None:
                self._icon_cache[cache_key] = None
                return None
            if icon.ndim == 2:
                icon = cv2.cvtColor(icon, cv2.COLOR_GRAY2BGRA)
            elif icon.shape[2] == 3:
                icon = cv2.cvtColor(icon, cv2.COLOR_BGR2BGRA)
            elif icon.shape[2] != 4:
                self._icon_cache[cache_key] = None
                return None

            src_height, src_width = icon.shape[:2]
            if src_height <= 0 or src_width <= 0:
                self._icon_cache[cache_key] = None
                return None

            scale = float(resolved_icon_size) / float(max(src_height, src_width))
            target_width = max(1, int(round(src_width * scale)))
            target_height = max(1, int(round(src_height * scale)))
            interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            resized_icon = cv2.resize(icon, (target_width, target_height), interpolation=interpolation)
            if visited:
                resized_icon = self._build_visited_icon(resized_icon)
            self._icon_cache[cache_key] = resized_icon
            return resized_icon
        except Exception:
            self._icon_cache[cache_key] = None
            return None

    @staticmethod
    def _draw_highlight_glow(
        canvas,
        center_point: tuple[int, int],
        color: tuple[int, int, int],
        *,
        icon_size: int,
        point_radius: int,
    ) -> None:
        pulse = 0.5 + 0.5 * np.sin(time.monotonic() * 3.6)
        center_x = int(center_point[0])
        center_y = int(center_point[1])
        glow_radius = max(point_radius + 8, int(round(icon_size * (0.84 + pulse * 0.08))))
        outer_radius = glow_radius + max(5, int(round(icon_size * (0.16 + pulse * 0.04))))
        padding = outer_radius + max(4, int(round(icon_size * 0.12)))
        canvas_height, canvas_width = canvas.shape[:2]
        left = max(0, center_x - padding)
        top = max(0, center_y - padding)
        right = min(canvas_width, center_x + padding + 1)
        bottom = min(canvas_height, center_y + padding + 1)
        if left >= right or top >= bottom:
            return

        local_center = (center_x - left, center_y - top)
        base_region = canvas[top:bottom, left:right]
        overlay_region = base_region.copy()
        inner_glow_color = tuple(int(round(channel * 0.82 + 255 * 0.18)) for channel in color)
        ring_color = tuple(int(round(channel * 0.7 + 255 * 0.3)) for channel in color)
        cv2.circle(overlay_region, local_center, outer_radius, color, -1, cv2.LINE_AA)
        cv2.circle(
            overlay_region,
            local_center,
            max(point_radius + 5, glow_radius - max(2, int(round(icon_size * 0.08)))),
            inner_glow_color,
            -1,
            cv2.LINE_AA,
        )
        blend_alpha = 0.10 + float(pulse) * 0.10
        cv2.addWeighted(overlay_region, blend_alpha, base_region, 1.0 - blend_alpha, 0, base_region)
        cv2.circle(
            canvas,
            (center_x, center_y),
            outer_radius,
            ring_color,
            max(2, int(round(icon_size * 0.08))),
            cv2.LINE_AA,
        )
        cv2.circle(
            canvas,
            (center_x, center_y),
            max(point_radius + 3, glow_radius - max(3, int(round(icon_size * 0.12)))),
            ring_color,
            1,
            cv2.LINE_AA,
        )

    @staticmethod
    def _overlay_icon(canvas, icon, center_point: tuple[int, int]) -> bool:
        if canvas is None or icon is None:
            return False

        icon_height, icon_width = icon.shape[:2]
        if icon_height <= 0 or icon_width <= 0:
            return False

        center_x = int(center_point[0])
        center_y = int(center_point[1])
        left = int(round(center_x - icon_width / 2.0))
        top = int(round(center_y - icon_height / 2.0))
        right = left + icon_width
        bottom = top + icon_height

        canvas_height, canvas_width = canvas.shape[:2]
        clipped_left = max(0, left)
        clipped_top = max(0, top)
        clipped_right = min(canvas_width, right)
        clipped_bottom = min(canvas_height, bottom)
        if clipped_left >= clipped_right or clipped_top >= clipped_bottom:
            return False

        icon_left = clipped_left - left
        icon_top = clipped_top - top
        icon_right = icon_left + (clipped_right - clipped_left)
        icon_bottom = icon_top + (clipped_bottom - clipped_top)
        icon_region = icon[icon_top:icon_bottom, icon_left:icon_right]
        if icon_region.shape[2] < 4:
            canvas[clipped_top:clipped_bottom, clipped_left:clipped_right] = icon_region[:, :, :3]
            return True

        alpha = icon_region[:, :, 3:4].astype(np.float32) / 255.0
        if not np.any(alpha > 0):
            return False

        background = canvas[clipped_top:clipped_bottom, clipped_left:clipped_right].astype(np.float32)
        foreground = icon_region[:, :, :3].astype(np.float32)
        blended = foreground * alpha + background * (1.0 - alpha)
        canvas[clipped_top:clipped_bottom, clipped_left:clipped_right] = blended.astype(np.uint8)
        return True

    def draw_point_marker(
        self,
        canvas,
        local_point: tuple[int, int],
        route: dict,
        fallback_color: tuple[int, int, int],
        *,
        visited: bool = False,
        highlighted: bool = False,
        point_radius: int | None = None,
        icon_size: int | None = None,
    ) -> None:
        requested_icon_size = icon_size if icon_size is not None else route.get("icon_size")
        resolved_icon_size = max(0, self._coerce_int(requested_icon_size, 0))
        resolved_point_radius = max(
            2,
            self._coerce_int(point_radius if point_radius is not None else route.get("point_radius"), 5),
        )
        if highlighted:
            self._draw_highlight_glow(
                canvas,
                local_point,
                self._get_route_color(route, fallback_color),
                icon_size=max(resolved_icon_size, resolved_point_radius * 2),
                point_radius=resolved_point_radius,
            )

        if resolved_icon_size > 0:
            icon = self._get_icon_image(route, resolved_icon_size, visited=bool(visited and not highlighted))
            if icon is not None and self._overlay_icon(canvas, icon, local_point):
                return

        point_color = self._get_point_color(route, fallback_color, visited=visited)
        if highlighted:
            point_color = self._get_route_color(route, fallback_color)
        cv2.circle(canvas, (int(local_point[0]), int(local_point[1])), resolved_point_radius, point_color, -1)

    def iter_visible_routes(self):
        for cat, route in self._iter_routes():
            if self.is_route_visible(cat, route):
                yield cat, route

    def resolve_route(self, route_ref: str | None):
        text = str(route_ref or "").strip().replace("\\", "/").strip("/")
        if not text:
            return None, None, None

        short_name = text.rsplit("/", 1)[-1]
        for cat, route in self._iter_routes():
            name = str(route.get("display_name") or "").strip()
            if not name:
                continue
            full_id = self._route_id_for(cat, route)
            if text == full_id or text == name or short_name == name:
                return full_id, name, route
        return None, None, None

    def iter_visible_route_names(self):
        for cat, route in self.iter_visible_routes():
            route_id = self._route_id_for(cat, route)
            if route_id:
                yield route_id

    @staticmethod
    def _distance_sq(start_point: tuple[float, float], end_point: tuple[float, float]) -> float:
        dx = float(end_point[0]) - float(start_point[0])
        dy = float(end_point[1]) - float(start_point[1])
        return float(dx * dx + dy * dy)

    @staticmethod
    def _is_point_in_viewport(
        point_x: float,
        point_y: float,
        viewport_bounds: tuple[int, int, int, int] | None,
    ) -> bool:
        if not viewport_bounds:
            return True

        left, top, right, bottom = viewport_bounds
        return (
            float(left) <= float(point_x) <= float(right)
            and float(top) <= float(point_y) <= float(bottom)
        )

    @staticmethod
    def _normalize_viewport_bounds(
        viewport_bounds: tuple[int, int, int, int] | None,
    ) -> tuple[int, int, int, int] | None:
        if not viewport_bounds:
            return None
        if len(viewport_bounds) != 4:
            return None
        left, top, right, bottom = [int(value) for value in viewport_bounds]
        return (
            int(min(left, right)),
            int(min(top, bottom)),
            int(max(left, right)),
            int(max(top, bottom)),
        )

    @staticmethod
    def _compute_route_bounds(points: list[dict] | tuple[dict, ...] | None) -> tuple[int, int, int, int] | None:
        if not isinstance(points, (list, tuple)) or not points:
            return None

        min_x = None
        min_y = None
        max_x = None
        max_y = None
        for point in points:
            if not isinstance(point, dict):
                continue
            try:
                point_x = float(point.get("x", 0.0))
                point_y = float(point.get("y", 0.0))
            except Exception:
                continue
            if min_x is None:
                min_x = point_x
                min_y = point_y
                max_x = point_x
                max_y = point_y
                continue
            min_x = min(min_x, point_x)
            min_y = min(min_y, point_y)
            max_x = max(max_x, point_x)
            max_y = max(max_y, point_y)

        if min_x is None or min_y is None or max_x is None or max_y is None:
            return None
        return (
            int(np.floor(min_x)),
            int(np.floor(min_y)),
            int(np.ceil(max_x)),
            int(np.ceil(max_y)),
        )

    @staticmethod
    def _viewport_intersects_bounds(
        viewport_bounds: tuple[int, int, int, int] | None,
        content_bounds: tuple[int, int, int, int] | None,
    ) -> bool:
        if not viewport_bounds or not content_bounds:
            return True

        viewport_left, viewport_top, viewport_right, viewport_bottom = viewport_bounds
        content_left, content_top, content_right, content_bottom = content_bounds
        return not (
            content_right < viewport_left
            or content_left > viewport_right
            or content_bottom < viewport_top
            or content_top > viewport_bottom
        )

    def _get_route_bounds(self, route: dict) -> tuple[int, int, int, int] | None:
        if not isinstance(route, dict):
            return None
        cached_bounds = route.get("_point_bounds")
        if isinstance(cached_bounds, tuple) and len(cached_bounds) == 4:
            return tuple(int(value) for value in cached_bounds)
        if isinstance(cached_bounds, list) and len(cached_bounds) == 4:
            resolved_bounds = tuple(int(value) for value in cached_bounds)
            route["_point_bounds"] = resolved_bounds
            return resolved_bounds

        resolved_bounds = self._compute_route_bounds(route.get("points", []))
        route["_point_bounds"] = resolved_bounds
        return resolved_bounds

    @staticmethod
    def _build_persistable_route_payload(route: dict) -> dict:
        if not isinstance(route, dict):
            return {}
        payload: dict = {}
        for key, value in route.items():
            key_text = str(key or "")
            if key_text.startswith("_"):
                continue
            payload[key] = copy.deepcopy(value)

        points = payload.get("points")
        if isinstance(points, list):
            normalized_points: list[object] = []
            for point in points:
                if not isinstance(point, dict):
                    normalized_points.append(point)
                    continue
                point_payload = dict(point)
                # visited 为运行时状态，避免持久化到资源文件。
                point_payload.pop("visited", None)
                normalized_points.append(point_payload)
            payload["points"] = normalized_points
        return payload

    def find_point_at_world(
        self,
        world_x: int | float | None,
        world_y: int | float | None,
        *,
        tolerance: float = 20.0,
        include_visited: bool = True,
        visible_only: bool = False,
    ) -> dict | None:
        if world_x is None or world_y is None:
            return None

        target_x = float(world_x)
        target_y = float(world_y)
        base_tolerance = max(1.0, float(tolerance))
        best_key = None
        best_entry = None

        for category, route in self._iter_routes():
            route_id = self._route_id_for(category, route)
            if not route_id:
                continue
            if visible_only and not self.is_route_visible(category, route):
                continue

            points = route.get("points", [])
            if not isinstance(points, list):
                continue
            for point_index, point in enumerate(points):
                if not isinstance(point, dict):
                    continue
                visited = bool(point.get("visited", False))
                if not include_visited and visited:
                    continue
                point_x = float(point.get("x", 0.0))
                point_y = float(point.get("y", 0.0))
                distance = float(np.hypot(point_x - target_x, point_y - target_y))
                point_radius = float(max(0, self._coerce_int(point.get("radius"), 0)))
                threshold = max(base_tolerance, point_radius)
                if distance > threshold:
                    continue

                candidate_key = (
                    round(distance, 4),
                    str(route_id),
                    int(point_index),
                )
                if best_key is None or candidate_key < best_key:
                    best_key = candidate_key
                    best_entry = {
                        "category": category,
                        "category_label": self.get_category_label(category),
                        "route_id": route_id,
                        "route_name": str(route.get("display_name") or route_id).strip() or route_id,
                        "route": route,
                        "point_index": int(point_index),
                        "point": point,
                        "distance": distance,
                    }
        return best_entry

    def add_point_to_route(
        self,
        route_ref: str | None,
        world_x: int | float,
        world_y: int | float,
        *,
        point_overrides: dict | None = None,
    ) -> dict | None:
        route_id, _route_name, route = self.resolve_route(route_ref)
        if route is None:
            return None

        points = route.get("points")
        if not isinstance(points, list):
            points = []
            route["points"] = points

        point_x = round(float(world_x), 2)
        point_y = round(float(world_y), 2)
        label_text = f"新增点 {int(round(point_x))}, {int(round(point_y))}"
        new_point = {
            "point_id": int(time.time_ns() // 1000),
            "x": point_x,
            "y": point_y,
            "radius": max(20, self._coerce_int(route.get("point_radius"), 24)),
            "label": label_text,
            "title": label_text,
            "description": "",
            "category_id": route.get("category_id"),
            "category_title": route.get("category_title"),
            "group_id": route.get("group_id"),
            "group_title": route.get("group_title"),
            "icon_path": route.get("icon_path"),
        }
        if isinstance(point_overrides, dict):
            for key, value in point_overrides.items():
                new_point[key] = value
        new_point.pop("visited", None)
        points.append(new_point)

        route["_point_bounds"] = self._compute_route_bounds(points)
        self._invalidate_dynamic_plan_cache()
        return {
            "route_id": route_id,
            "route_name": str(route.get("display_name") or route_id or "").strip(),
            "point_index": int(len(points) - 1),
            "point": new_point,
        }

    def remove_point_from_route(self, route_ref: str | None, point_index: int) -> dict | None:
        route_id, _route_name, route = self.resolve_route(route_ref)
        if route is None:
            return None

        points = route.get("points")
        if not isinstance(points, list):
            return None
        try:
            resolved_index = int(point_index)
        except Exception:
            return None
        if resolved_index < 0 or resolved_index >= len(points):
            return None

        removed_point = points.pop(resolved_index)
        route["_point_bounds"] = self._compute_route_bounds(points)
        self._invalidate_dynamic_plan_cache()
        return {
            "route_id": route_id,
            "route_name": str(route.get("display_name") or route_id or "").strip(),
            "point_index": int(resolved_index),
            "point": removed_point,
        }

    def save_route(self, route_ref: str | None) -> tuple[bool, str]:
        route_id, _route_name, route = self.resolve_route(route_ref)
        if route is None:
            return False, "未找到目标资源种类"

        file_path = str(route.get("_file_path") or "").strip()
        if not file_path:
            return False, f"资源文件路径缺失：{route_id or route_ref or ''}"

        try:
            parent_dir = os.path.dirname(file_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            payload = self._build_persistable_route_payload(route)
            with open(file_path, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.write("\n")
            return True, file_path
        except Exception as exc:
            return False, str(exc)

    def _invalidate_dynamic_plan_cache(self) -> None:
        with self._cache_lock:
            self._dynamic_plan_cache.clear()

    def _bump_visited_revision(self) -> None:
        with self._cache_lock:
            self._visited_revision += 1
            self._dynamic_plan_cache.clear()

    def _build_dynamic_route_candidates(
        self,
        *,
        viewport_bounds: tuple[int, int, int, int] | None = None,
    ) -> list[dict]:
        normalized_viewport_bounds = self._normalize_viewport_bounds(viewport_bounds)
        candidates: list[dict] = []
        for category, route in self.iter_visible_routes():
            route_id = self._route_id_for(category, route)
            if not route_id:
                continue
            if not self._viewport_intersects_bounds(normalized_viewport_bounds, self._get_route_bounds(route)):
                continue

            points = route.get("points", [])
            if not isinstance(points, list):
                continue

            for point_index, point in enumerate(points):
                if not isinstance(point, dict) or bool(point.get("visited", False)):
                    continue
                point_x = float(point.get("x", 0.0))
                point_y = float(point.get("y", 0.0))
                if not self._is_point_in_viewport(point_x, point_y, normalized_viewport_bounds):
                    continue
                candidates.append(
                    {
                        "route_id": route_id,
                        "route": route,
                        "point": point,
                        "point_index": int(point_index),
                        "x": point_x,
                        "y": point_y,
                    }
                )
        return candidates

    def _build_nearest_neighbor_route_plan(
        self,
        candidates: list[dict],
        *,
        start_point: tuple[float, float],
        preferred_route_ref: str | None = None,
        plan_limit: int | None = None,
    ) -> list[dict]:
        preferred_route_id = None
        if preferred_route_ref:
            preferred_route_id, _preferred_name, _preferred_route = self.resolve_route(preferred_route_ref)

        remaining = list(candidates)
        ordered: list[dict] = []
        current_point = (float(start_point[0]), float(start_point[1]))
        target_count = len(remaining) if plan_limit is None else max(1, min(int(plan_limit), len(remaining)))
        while remaining and len(ordered) < target_count:
            best_index = 0
            best_key = None
            for candidate_index, candidate in enumerate(remaining):
                target_point = (float(candidate["x"]), float(candidate["y"]))
                distance_key = round(self._distance_sq(current_point, target_point), 6)
                preferred_key = 0 if preferred_route_id and candidate.get("route_id") == preferred_route_id else 1
                candidate_key = (
                    distance_key,
                    preferred_key,
                    str(candidate.get("route_id", "")),
                    int(candidate.get("point_index", 0)),
                )
                if best_key is None or candidate_key < best_key:
                    best_key = candidate_key
                    best_index = int(candidate_index)

            selected = remaining.pop(best_index)
            ordered.append(selected)
            current_point = (float(selected["x"]), float(selected["y"]))
        return ordered

    @staticmethod
    def _compute_route_plan_distance(start_point: tuple[float, float], ordered_points: list[dict]) -> float:
        if not ordered_points:
            return 0.0

        total_distance = 0.0
        cursor = (float(start_point[0]), float(start_point[1]))
        for entry in ordered_points:
            next_point = (float(entry["x"]), float(entry["y"]))
            total_distance += float(np.hypot(next_point[0] - cursor[0], next_point[1] - cursor[1]))
            cursor = next_point
        return float(total_distance)

    def _optimize_route_plan_with_two_opt(
        self,
        ordered_points: list[dict],
        *,
        start_point: tuple[float, float],
        anchor_count: int = 0,
        max_points: int = 80,
        max_passes: int = 4,
    ) -> list[dict]:
        if len(ordered_points) < 4 or len(ordered_points) > max_points:
            return ordered_points

        resolved_anchor_count = max(0, min(int(anchor_count), len(ordered_points)))
        anchored_prefix = list(ordered_points[:resolved_anchor_count])
        best_plan = list(ordered_points[resolved_anchor_count:])
        if len(best_plan) < 4:
            return ordered_points

        optimization_start_point = (
            (float(anchored_prefix[-1]["x"]), float(anchored_prefix[-1]["y"]))
            if anchored_prefix
            else (float(start_point[0]), float(start_point[1]))
        )
        best_distance = self._compute_route_plan_distance(optimization_start_point, best_plan)
        pass_index = 0
        improved = True
        while improved and pass_index < max(1, int(max_passes)):
            improved = False
            pass_index += 1
            plan_length = len(best_plan)
            for left_index in range(0, plan_length - 2):
                for right_index in range(left_index + 1, plan_length):
                    candidate_plan = (
                        best_plan[:left_index]
                        + list(reversed(best_plan[left_index:right_index + 1]))
                        + best_plan[right_index + 1:]
                    )
                    candidate_distance = self._compute_route_plan_distance(
                        optimization_start_point,
                        candidate_plan,
                    )
                    if candidate_distance + 1e-6 < best_distance:
                        best_plan = candidate_plan
                        best_distance = candidate_distance
                        improved = True
                        break
                if improved:
                    break
        return anchored_prefix + best_plan

    def build_dynamic_route_plan(
        self,
        player_x: int | None,
        player_y: int | None,
        *,
        preferred_route_ref: str | None = None,
        viewport_bounds: tuple[int, int, int, int] | None = None,
        plan_limit: int | None = None,
    ) -> list[dict]:
        if player_x is None or player_y is None:
            return []

        normalized_viewport_bounds = self._normalize_viewport_bounds(viewport_bounds)
        preferred_route_id = None
        if preferred_route_ref:
            preferred_route_id, _preferred_name, _preferred_route = self.resolve_route(preferred_route_ref)

        start_point = (float(player_x), float(player_y))
        cache_key = (
            normalized_viewport_bounds,
            str(preferred_route_id or ""),
            None if plan_limit is None else int(max(1, int(plan_limit))),
            int(self._visited_revision),
        )
        with self._cache_lock:
            cached_entry = self._dynamic_plan_cache.get(cache_key)
        if isinstance(cached_entry, dict):
            cached_start_point = cached_entry.get("start_point")
            cached_plan = cached_entry.get("plan")
        else:
            cached_start_point = None
            cached_plan = None
        if isinstance(cached_start_point, tuple) and len(cached_start_point) == 2 and isinstance(cached_plan, list):
            reuse_distance = 96.0
            if plan_limit is not None:
                reuse_distance = max(reuse_distance, min(320.0, float(int(plan_limit)) * 6.0))
            moved_distance = float(
                np.hypot(
                    float(start_point[0]) - float(cached_start_point[0]),
                    float(start_point[1]) - float(cached_start_point[1]),
                )
            )
            if moved_distance <= reuse_distance:
                return list(cached_plan)

        candidates = self._build_dynamic_route_candidates(viewport_bounds=normalized_viewport_bounds)
        if not candidates:
            with self._cache_lock:
                self._dynamic_plan_cache.pop(cache_key, None)
            return []

        initial_plan = self._build_nearest_neighbor_route_plan(
            candidates,
            start_point=start_point,
            preferred_route_ref=preferred_route_ref,
            plan_limit=plan_limit,
        )
        # 首个目标保持为最近未访问资源，只优化后续路径，避免箭头和动态线路偏离最近目标。
        optimized_plan = self._optimize_route_plan_with_two_opt(
            initial_plan,
            start_point=start_point,
            anchor_count=1,
        )
        with self._cache_lock:
            self._dynamic_plan_cache[cache_key] = {
                "start_point": start_point,
                "plan": list(optimized_plan),
            }
            if len(self._dynamic_plan_cache) > 8:
                retained_key = cache_key
                retained_entry = self._dynamic_plan_cache.get(retained_key)
                self._dynamic_plan_cache.clear()
                if retained_entry is not None:
                    self._dynamic_plan_cache[retained_key] = retained_entry
        return optimized_plan

    @staticmethod
    def _draw_dynamic_route_lines(
        canvas,
        start_point: tuple[int, int],
        ordered_points: list[dict],
        *,
        vx1: int,
        vy1: int,
    ) -> None:
        if canvas is None or not ordered_points:
            return

        local_points = [tuple(int(value) for value in start_point)]
        for entry in ordered_points:
            local_points.append(
                (
                    int(float(entry.get("x", 0.0)) - int(vx1)),
                    int(float(entry.get("y", 0.0)) - int(vy1)),
                )
            )

        if len(local_points) < 2:
            return

        shadow_color = (12, 18, 24)
        lead_color = (0, 220, 255)
        follow_color = (64, 180, 255)
        for segment_index in range(len(local_points) - 1):
            point_a = local_points[segment_index]
            point_b = local_points[segment_index + 1]
            if point_a == point_b:
                continue
            color = lead_color if segment_index == 0 else follow_color
            thickness = 3 if segment_index == 0 else 2
            cv2.line(canvas, point_a, point_b, shadow_color, thickness + 2, cv2.LINE_AA)
            cv2.line(canvas, point_a, point_b, color, thickness, cv2.LINE_AA)

    def _get_nearest_point_from_route(
        self,
        route_id: str,
        route: dict,
        player_x: int | None,
        player_y: int | None,
        *,
        include_visited: bool = True,
    ) -> tuple[str | None, int | None, float | None]:
        if route is None or player_x is None or player_y is None:
            return route_id, None, None

        points = route.get("points", [])
        if not points:
            return route_id, None, None

        player = np.array((float(player_x), float(player_y)))
        nearest_index = None
        nearest_distance = None
        for index, point in enumerate(points):
            if not include_visited and bool(point.get("visited", False)):
                continue
            point_x = float(point.get("x", 0.0))
            point_y = float(point.get("y", 0.0))
            distance = float(np.linalg.norm(np.array((point_x, point_y)) - player))
            if nearest_distance is None or distance < nearest_distance:
                nearest_index = int(index)
                nearest_distance = distance

        return route_id, nearest_index, nearest_distance

    def get_guidance_target_info(
        self,
        player_x: int | None,
        player_y: int | None,
        *,
        preferred_route_ref: str | None = None,
        viewport_bounds: tuple[int, int, int, int] | None = None,
        plan_limit: int | None = None,
    ) -> tuple[str | None, int | None, float | None, dict | None]:
        dynamic_plan = self.build_dynamic_route_plan(
            player_x,
            player_y,
            preferred_route_ref=preferred_route_ref,
            viewport_bounds=viewport_bounds,
            plan_limit=plan_limit,
        )
        if not dynamic_plan or player_x is None or player_y is None:
            return None, None, None, None

        next_target = dynamic_plan[0]
        distance = float(
            np.hypot(
                float(next_target.get("x", 0.0)) - float(player_x),
                float(next_target.get("y", 0.0)) - float(player_y),
            )
        )
        return (
            str(next_target.get("route_id") or "").strip() or None,
            int(next_target.get("point_index", 0)),
            distance,
            next_target.get("route"),
        )

    @staticmethod
    def _draw_directional_arrow_dash(
        canvas,
        center_point: tuple[float, float],
        direction_unit: tuple[float, float],
        *,
        arrow_length: float,
        arrow_width: float,
    ) -> None:
        ux, uy = float(direction_unit[0]), float(direction_unit[1])
        normal_x, normal_y = -uy, ux
        center_x, center_y = float(center_point[0]), float(center_point[1])
        head_length = max(6.0, float(arrow_length) * 0.48)
        tail_length = max(4.0, float(arrow_length) - head_length)

        def _arrow_polygon(scale: float) -> np.ndarray:
            scaled_length = float(arrow_length) * scale
            scaled_width = float(arrow_width) * scale
            scaled_head_length = max(4.0, head_length * scale)
            scaled_tail_length = max(3.0, min(tail_length * scale, max(1.0, scaled_length - 3.0)))
            tip_x = center_x + ux * (scaled_length / 2.0)
            tip_y = center_y + uy * (scaled_length / 2.0)
            tail_center_x = center_x - ux * (scaled_head_length / 2.0)
            tail_center_y = center_y - uy * (scaled_head_length / 2.0)
            tail_end_x = tail_center_x + ux * (scaled_tail_length / 2.0)
            tail_end_y = tail_center_y + uy * (scaled_tail_length / 2.0)
            tail_start_x = tail_center_x - ux * (scaled_tail_length / 2.0)
            tail_start_y = tail_center_y - uy * (scaled_tail_length / 2.0)
            half_width = scaled_width / 2.0
            points = np.array(
                [
                    (tail_start_x + normal_x * half_width, tail_start_y + normal_y * half_width),
                    (tail_end_x + normal_x * half_width, tail_end_y + normal_y * half_width),
                    (tip_x, tip_y),
                    (tail_end_x - normal_x * half_width, tail_end_y - normal_y * half_width),
                    (tail_start_x - normal_x * half_width, tail_start_y - normal_y * half_width),
                ],
                dtype=np.float32,
            )
            return np.round(points).astype(np.int32)

        layered_styles = (
            (1.0, (0, 168, 255)),
            (0.72, (0, 220, 255)),
            (0.42, (170, 250, 255)),
        )
        for scale, color in layered_styles:
            polygon = _arrow_polygon(scale)
            cv2.fillConvexPoly(canvas, polygon, color, lineType=cv2.LINE_AA)

    @classmethod
    def _draw_animated_direction_path(
        cls,
        canvas,
        start_point: tuple[int, int],
        end_point: tuple[int, int],
    ) -> None:
        dx = float(end_point[0] - start_point[0])
        dy = float(end_point[1] - start_point[1])
        distance = float(np.hypot(dx, dy))
        if distance < 18.0:
            return

        unit = (dx / distance, dy / distance)
        arrow_length = max(14.0, min(26.0, distance * 0.12))
        arrow_width = max(8.0, arrow_length * 0.56)
        gap = max(10.0, arrow_length * 0.52)
        spacing = arrow_length + gap
        start_padding = max(14.0, arrow_width * 0.8)
        end_padding = max(18.0, arrow_length * 0.9)
        if distance <= start_padding + end_padding:
            return

        phase = (time.monotonic() * 140.0) % spacing
        cursor = start_padding + phase
        while cursor < distance - end_padding:
            center = (
                float(start_point[0]) + unit[0] * cursor,
                float(start_point[1]) + unit[1] * cursor,
            )
            cls._draw_directional_arrow_dash(
                canvas,
                center,
                unit,
                arrow_length=arrow_length,
                arrow_width=arrow_width,
            )
            cursor += spacing

    def get_nearest_point_info(
        self,
        route_ref: str | None,
        player_x: int | None,
        player_y: int | None,
    ) -> tuple[str | None, int | None, float | None]:
        full_id, _route_name, route = self.resolve_route(route_ref)
        if route is None or player_x is None or player_y is None:
            return full_id, None, None

        return self._get_nearest_point_from_route(
            full_id or "",
            route,
            player_x,
            player_y,
            include_visited=False,
        )

    def get_visible_points_in_bounds(
        self,
        *,
        world_bounds: tuple[int, int, int, int] | None = None,
        player_x: int | None = None,
        player_y: int | None = None,
        max_points: int | None = None,
    ) -> list[dict]:
        normalized_bounds = self._normalize_viewport_bounds(world_bounds)
        player_point = None if player_x is None or player_y is None else (float(player_x), float(player_y))
        collected_points: list[dict] = []
        color_idx = 0
        for category, route in self.iter_visible_routes():
            route_id = self._route_id_for(category, route)
            if not route_id:
                continue
            if not self._viewport_intersects_bounds(normalized_bounds, self._get_route_bounds(route)):
                continue

            route_color = self._get_route_color(route, self.colors[color_idx % len(self.colors)])
            color_idx += 1
            points = route.get("points", [])
            if not isinstance(points, list):
                continue

            for point_index, point in enumerate(points):
                if not isinstance(point, dict):
                    continue
                point_x = float(point.get("x", 0.0))
                point_y = float(point.get("y", 0.0))
                if not self._is_point_in_viewport(point_x, point_y, normalized_bounds):
                    continue

                visited = bool(point.get("visited", False))
                distance = 0.0
                if player_point is not None:
                    distance = float(np.hypot(point_x - player_point[0], point_y - player_point[1]))
                collected_points.append(
                    {
                        "route_id": route_id,
                        "route": route,
                        "point": point,
                        "point_index": int(point_index),
                        "x": point_x,
                        "y": point_y,
                        "visited": visited,
                        "distance": distance,
                        "route_color": route_color,
                        "point_color": self._get_point_color(route, route_color, visited=visited),
                    }
                )

        if max_points is not None and len(collected_points) > int(max_points):
            collected_points.sort(
                key=lambda item: (
                    bool(item.get("visited", False)),
                    float(item.get("distance", 0.0)),
                    str(item.get("route_id", "")),
                    int(item.get("point_index", 0)),
                )
            )
            return collected_points[: max(1, int(max_points))]
        return collected_points

    def get_nearest_visible_unvisited_point(
        self,
        player_x: int | None,
        player_y: int | None,
        *,
        viewport_bounds: tuple[int, int, int, int] | None = None,
    ) -> dict | None:
        if player_x is None or player_y is None:
            return None

        normalized_bounds = self._normalize_viewport_bounds(viewport_bounds)
        best_key = None
        best_entry = None
        player_point = (float(player_x), float(player_y))
        for category, route in self.iter_visible_routes():
            route_id = self._route_id_for(category, route)
            if not route_id:
                continue
            if not self._viewport_intersects_bounds(normalized_bounds, self._get_route_bounds(route)):
                continue

            points = route.get("points", [])
            if not isinstance(points, list):
                continue

            for point_index, point in enumerate(points):
                if not isinstance(point, dict) or bool(point.get("visited", False)):
                    continue
                point_x = float(point.get("x", 0.0))
                point_y = float(point.get("y", 0.0))
                if not self._is_point_in_viewport(point_x, point_y, normalized_bounds):
                    continue

                distance_sq = self._distance_sq(player_point, (point_x, point_y))
                candidate_key = (
                    round(distance_sq, 6),
                    str(route_id),
                    int(point_index),
                )
                if best_key is not None and candidate_key >= best_key:
                    continue
                best_key = candidate_key
                best_entry = {
                    "route_id": route_id,
                    "route": route,
                    "point": point,
                    "point_index": int(point_index),
                    "x": point_x,
                    "y": point_y,
                    "distance": float(np.hypot(point_x - player_point[0], point_y - player_point[1])),
                }
        return best_entry

    def _sample_visible_points_for_rendering(
        self,
        render_entries: list[tuple[dict, tuple[int, int, int], list[tuple[int, tuple[int, int], dict, bool]], set[int]]],
        *,
        canvas_width: int,
        canvas_height: int,
        total_visible_points: int,
        local_player_pt: tuple[int, int] | None = None,
    ) -> list[tuple[dict, tuple[int, int, int], list[tuple[int, tuple[int, int], dict, bool]], set[int]]]:
        if not render_entries or total_visible_points <= 0:
            return render_entries

        viewport_area = max(1, int(canvas_width) * int(canvas_height))
        target_points = max(220, min(720, int(round(float(viewport_area) / 650.0))))
        if total_visible_points <= target_points:
            return render_entries

        density_ratio = float(total_visible_points) / float(max(1, target_points))
        base_cell_size = max(12, int(round(np.sqrt(float(viewport_area) / float(max(1, target_points))))))
        cell_size = max(
            14,
            min(
                40,
                int(round(float(base_cell_size) * min(2.2, max(1.0, float(np.sqrt(density_ratio)))))),
            ),
        )
        bucket_limit = 2 if density_ratio < 2.2 else 1
        route_budget = max(
            1,
            min(18, int(round(float(target_points) / float(max(1, len(render_entries))) * 1.5))),
        )

        sampled_points_by_route: list[list[tuple[int, tuple[int, int], dict, bool]]] = []
        route_candidates: list[list[tuple[bool, float, int, tuple[int, int], dict, bool]]] = []
        for route, _color, visible_points, active_indices in render_entries:
            sampled_points: list[tuple[int, tuple[int, int], dict, bool]] = []
            normal_candidates: list[tuple[bool, float, int, tuple[int, int], dict, bool]] = []
            for point_index, local_point, point, visited in visible_points:
                if point_index in active_indices:
                    sampled_points.append((point_index, local_point, point, visited))
                    continue

                distance_sq = 0.0
                if local_player_pt is not None:
                    dx = float(local_point[0] - local_player_pt[0])
                    dy = float(local_point[1] - local_player_pt[1])
                    distance_sq = float(dx * dx + dy * dy)
                normal_candidates.append(
                    (
                        bool(visited),
                        distance_sq,
                        int(point_index),
                        local_point,
                        point,
                        bool(visited),
                    )
                )

            normal_candidates.sort(key=lambda item: (item[0], item[1], item[2]))
            sampled_points_by_route.append(sampled_points)
            route_candidates.append(normal_candidates)

        route_bucket_sets = [set() for _ in render_entries]
        global_bucket_counts: dict[tuple[int, int], int] = {}
        route_positions = [0 for _ in render_entries]
        route_selected_counts = [0 for _ in render_entries]
        selected_total = sum(len(points) for points in sampled_points_by_route)

        while selected_total < target_points:
            progress = False
            for route_index, candidates in enumerate(route_candidates):
                if route_selected_counts[route_index] >= route_budget:
                    continue

                while route_positions[route_index] < len(candidates):
                    _visited_key, _distance_sq, point_index, local_point, point, visited = candidates[route_positions[route_index]]
                    route_positions[route_index] += 1
                    bucket = (int(local_point[0]) // cell_size, int(local_point[1]) // cell_size)
                    if bucket in route_bucket_sets[route_index]:
                        continue
                    if global_bucket_counts.get(bucket, 0) >= bucket_limit:
                        continue

                    sampled_points_by_route[route_index].append((point_index, local_point, point, visited))
                    route_bucket_sets[route_index].add(bucket)
                    global_bucket_counts[bucket] = global_bucket_counts.get(bucket, 0) + 1
                    route_selected_counts[route_index] += 1
                    selected_total += 1
                    progress = True
                    break

                if selected_total >= target_points:
                    break

            if not progress:
                break

        sampled_render_entries: list[
            tuple[dict, tuple[int, int, int], list[tuple[int, tuple[int, int], dict, bool]], set[int]]
        ] = []
        for route_index, (route, color, _visible_points, active_indices) in enumerate(render_entries):
            sampled_points = sampled_points_by_route[route_index]
            if not sampled_points:
                continue
            sampled_points.sort(key=lambda item: item[0])
            sampled_render_entries.append((route, color, sampled_points, active_indices))
        return sampled_render_entries

    def draw_on(
        self,
        canvas,
        vx1: int,
        vy1: int,
        view_size: int,
        player_x: int | None = None,
        player_y: int | None = None,
        preferred_route_ref: str | None = None,
    ) -> None:
        color_idx = 0
        local_player_pt = None
        canvas_height, canvas_width = canvas.shape[:2]
        player_world_x = float(player_x) if player_x is not None else None
        player_world_y = float(player_y) if player_y is not None else None
        viewport_bounds = self._normalize_viewport_bounds(
            (
                int(vx1),
                int(vy1),
                int(vx1 + max(1, canvas_width - 1)),
                int(vy1 + max(1, canvas_height - 1)),
            )
        )
        if player_x is not None and player_y is not None:
            local_player_pt = (int(player_x - vx1), int(player_y - vy1))
        close_threshold = 20
        overlay_icon_size = max(24, min(36, self._coerce_int(round(float(view_size) * 0.06), 24)))
        overlay_point_radius = max(8, min(12, self._coerce_int(round(float(overlay_icon_size) * 0.34), 8)))
        render_entries: list[tuple[dict, tuple[int, int, int], list[tuple[int, tuple[int, int], dict, bool]], set[int]]] = []
        total_visible_points = 0
        visited_changed = False

        for cat, route in self.iter_visible_routes():
            route_id = self._route_id_for(cat, route)
            if not route_id:
                continue
            if not self._viewport_intersects_bounds(viewport_bounds, self._get_route_bounds(route)):
                continue

            pts = route.get("points", [])
            color = self._get_route_color(route, self.colors[color_idx % len(self.colors)])
            color_idx += 1

            active_indices: set[int] = set()
            visible_points: list[tuple[int, tuple[int, int], dict, bool]] = []
            track_visited = bool(local_player_pt and self._should_track_visited(route))
            for point_index, point in enumerate(pts):
                if not isinstance(point, dict):
                    continue

                point_x = float(point.get("x", 0.0))
                point_y = float(point.get("y", 0.0))
                visited = bool(point.get("visited", False))
                if track_visited and not visited and player_world_x is not None and player_world_y is not None:
                    radius = float(max(close_threshold, self._coerce_int(point.get("radius"), close_threshold)))
                    delta_x = point_x - player_world_x
                    delta_y = point_y - player_world_y
                    if abs(delta_x) <= radius and abs(delta_y) <= radius:
                        if float(np.hypot(delta_x, delta_y)) <= radius:
                            point["visited"] = True
                            visited = True
                            visited_changed = True
                            active_indices.add(int(point_index))

                local_point = (
                    int(point_x - vx1),
                    int(point_y - vy1),
                )
                if 0 <= local_point[0] < canvas_width and 0 <= local_point[1] < canvas_height:
                    visible_points.append((int(point_index), local_point, point, visited))

            if not visible_points:
                continue
            total_visible_points += len(visible_points)
            render_entries.append((route, color, visible_points, active_indices))

        if visited_changed:
            self._bump_visited_revision()

        dynamic_plan_limit = None
        if total_visible_points >= 720:
            dynamic_plan_limit = min(48, max(24, total_visible_points // 18))
        elif total_visible_points >= 420:
            dynamic_plan_limit = min(72, max(30, total_visible_points // 10))
        elif total_visible_points >= 240:
            dynamic_plan_limit = min(96, max(36, total_visible_points // 6))
        dynamic_route_plan = self.build_dynamic_route_plan(
            player_x,
            player_y,
            preferred_route_ref=preferred_route_ref,
            viewport_bounds=(
                int(vx1),
                int(vy1),
                int(vx1 + max(1, canvas.shape[1] - 1)),
                int(vy1 + max(1, canvas.shape[0] - 1)),
            ),
            plan_limit=dynamic_plan_limit,
        )
        if local_player_pt is not None and dynamic_route_plan:
            self._draw_dynamic_route_lines(
                canvas,
                local_player_pt,
                dynamic_route_plan,
                vx1=vx1,
                vy1=vy1,
            )

        simplify_threshold = max(160, self._coerce_int(round(float(view_size) * 0.40), 160))
        simplified_rendering = total_visible_points >= simplify_threshold
        simplified_point_radius = max(3, min(6, int(round(float(overlay_point_radius) * 0.55))))
        render_density_threshold = max(simplify_threshold + 80, 260)
        sampled_render_entries = render_entries
        if total_visible_points >= render_density_threshold:
            sampled_render_entries = self._sample_visible_points_for_rendering(
                render_entries,
                canvas_width=canvas_width,
                canvas_height=canvas_height,
                total_visible_points=total_visible_points,
                local_player_pt=local_player_pt,
            )
        for route, color, visible_points, active_indices in sampled_render_entries:
            for point_index, local_point, point, visited in visible_points:
                highlighted = point_index in active_indices
                marker_icon_size = overlay_icon_size if (not simplified_rendering or highlighted) else 0
                marker_point_radius = overlay_point_radius if (not simplified_rendering or highlighted) else simplified_point_radius
                self.draw_point_marker(
                    canvas,
                    local_point,
                    route,
                    color,
                    visited=bool(visited),
                    highlighted=highlighted,
                    point_radius=marker_point_radius,
                    icon_size=marker_icon_size,
                )

        if local_player_pt is None:
            return

        if not dynamic_route_plan or player_x is None or player_y is None:
            return

        next_target = dynamic_route_plan[0]
        guidance_route = next_target.get("route")
        target_point = next_target.get("point")
        if not isinstance(guidance_route, dict) or not isinstance(target_point, dict):
            return

        guidance_distance = float(
            np.hypot(
                float(next_target.get("x", 0.0)) - float(player_x),
                float(next_target.get("y", 0.0)) - float(player_y),
            )
        )
        target_local_point = (
            int(float(target_point.get("x", 0.0)) - vx1),
            int(float(target_point.get("y", 0.0)) - vy1),
        )
        target_radius = max(close_threshold, self._coerce_int(target_point.get("radius"), close_threshold))
        if float(guidance_distance) <= float(target_radius):
            return

        self._draw_animated_direction_path(canvas, local_player_pt, target_local_point)

        if 0 <= target_local_point[0] <= view_size and 0 <= target_local_point[1] <= view_size:
            route_color = self._get_route_color(guidance_route, self.colors[0])
            self.draw_point_marker(
                canvas,
                target_local_point,
                guidance_route,
                route_color,
                visited=bool(target_point.get("visited", False)),
                highlighted=False,
                point_radius=overlay_point_radius,
                icon_size=overlay_icon_size,
            )

    def _load_all_routes(self) -> None:
        if not os.path.exists(self.base_folder):
            return

        for cat in self.categories:
            cat_path = os.path.join(self.base_folder, cat)
            if not os.path.exists(cat_path):
                try:
                    os.makedirs(cat_path)
                except Exception:
                    pass
                continue

            for path in glob.glob(os.path.join(cat_path, "*.json")):
                try:
                    file_name = os.path.basename(path)
                    route_name = os.path.splitext(file_name)[0]
                    with open(path, "r", encoding="utf-8") as file:
                        data = json.load(file)
                    display_name = (
                        str(data.get("display_name") or data.get("name") or route_name).strip() or route_name
                    )
                    data["display_name"] = display_name
                    data["route_id"] = self._route_id_for(cat, data)
                    data["_category"] = cat
                    data["_file_path"] = os.path.abspath(path)
                    data["_point_bounds"] = self._compute_route_bounds(data.get("points", []))
                    self.route_groups[cat].append(data)
                except Exception:
                    continue
