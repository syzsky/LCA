# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sys


BASE_DIR_ENV_NAME = "LCA_LKMAPTOOLS_BASE_DIR"
MINIMAP_REGION_ENV_NAME = "LCA_LKMAPTOOLS_MINIMAP_REGION"

_ENV_BASE_DIR = str(os.environ.get(BASE_DIR_ENV_NAME, "") or "").strip()

if _ENV_BASE_DIR:
    BASE_DIR = os.path.abspath(_ENV_BASE_DIR)
elif getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "MINIMAP": {"top": 212, "left": 1659, "width": 120, "height": 120},
    "RESOURCE_VISIBILITY": {"visible_categories": [], "visible_route_ids": []},
    "WINDOW_GEOMETRY": "400x400+1500+100",
    "VIEW_SIZE": 400,
    "LOGIC_MAP_PATH": "big_map.png",
    "DISPLAY_MAP_PATH": "big_map.png",
    "MAX_LOST_FRAMES": 50,
    "SIFT_REFRESH_RATE": 50,
    "SIFT_CLAHE_LIMIT": 3.0,
    "SIFT_MATCH_RATIO": 0.9,
    "SIFT_MIN_MATCH_COUNT": 5,
    "SIFT_RANSAC_THRESHOLD": 8.0,
    "AI_REFRESH_RATE": 50,
    "AI_CONFIDENCE_THRESHOLD": 0.50,
    "AI_MIN_MATCH_COUNT": 9,
    "AI_RANSAC_THRESHOLD": 8.0,
    "AI_SCAN_SIZE": 200,
    "AI_SCAN_STEP": 100,
    "AI_TRACK_RADIUS": 100,
    "AI_SMOOTH_ALPHA_MIN": 0.35,
    "AI_SMOOTH_ALPHA_MAX": 0.9,
    "AI_SMOOTH_DISTANCE_MIN": 4.0,
    "AI_SMOOTH_DISTANCE_MAX": 28.0,
    "AI_LEAD_TIME_MS": 45,
    "AI_LEAD_MAX_PIXELS": 14,
    "AI_LEAD_MIN_MATCHES": 12,
    "AI_LEAD_TRIGGER_PIXELS": 2.0,
    "AI_STILL_TRIGGER_PIXELS": 1.2,
    "AI_POSITION_HOLD_PIXELS": 0.85,
    "AI_JITTER_GUARD_PIXELS": 1.8,
    "AI_LOCAL_MATCH_BASE_DISTANCE": 96.0,
    "AI_LOCAL_MATCH_LOST_STEP": 32.0,
    "AI_LOCAL_MATCH_MAX_DISTANCE": 260.0,
    "AI_STEP_LIMIT_MIN_PIXELS": 8.0,
    "AI_STEP_LIMIT_MAX_PIXELS": 30.0,
    "AI_GLOBAL_SCAN_MIN_LOST_FRAMES": 2,
    "AI_GLOBAL_SCAN_RETRY_GAP": 2,
    "AI_RECOVERY_SNAP_LOST_FRAMES": 2,
    "AI_LARGE_OFFSET_MIN_LOST_FRAMES": 8,
    "AI_LARGE_OFFSET_CONFIRM_FRAMES": 4,
}


def _normalize_resource_visibility_settings(payload) -> dict:
    normalized = {
        "visible_categories": [],
        "visible_route_ids": [],
    }
    if not isinstance(payload, dict):
        return normalized

    for key in ("visible_categories", "visible_route_ids"):
        raw_values = payload.get(key)
        if not isinstance(raw_values, (list, tuple, set)):
            continue
        seen: set[str] = set()
        cleaned_values: list[str] = []
        for value in raw_values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            cleaned_values.append(text)
        normalized[key] = cleaned_values
    return normalized


def _to_absolute_path(path_value: str) -> str:
    text = str(path_value or "").strip()
    if not text:
        return ""
    if os.path.isabs(text):
        return os.path.abspath(text)
    return os.path.abspath(os.path.join(BASE_DIR, text))


def _resolve_display_map_path(settings_payload: dict) -> str:
    logic_map_path = _to_absolute_path(settings_payload.get("LOGIC_MAP_PATH"))
    display_map_path = _to_absolute_path(settings_payload.get("DISPLAY_MAP_PATH") or settings_payload.get("LOGIC_MAP_PATH"))
    if display_map_path and os.path.exists(display_map_path):
        return display_map_path
    return logic_map_path


def _load_minimap_env_override() -> dict:
    raw_value = str(os.environ.get(MINIMAP_REGION_ENV_NAME, "") or "").strip()
    if not raw_value:
        return {}
    try:
        payload = json.loads(raw_value)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    try:
        left = int(payload.get("left", 0) or 0)
        top = int(payload.get("top", 0) or 0)
        width = int(payload.get("width", 0) or 0)
        height = int(payload.get("height", 0) or 0)
    except Exception:
        return {}
    if width <= 0 or height <= 0:
        return {}
    return {
        "left": left,
        "top": top,
        "width": width,
        "height": height,
    }


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as file:
                json.dump(DEFAULT_CONFIG, file, indent=4, ensure_ascii=False)
        except Exception:
            pass
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as file:
            user_config = json.load(file)
        merged_config = DEFAULT_CONFIG.copy()
        if isinstance(user_config, dict):
            merged_config.update(user_config)
        return merged_config
    except Exception:
        return DEFAULT_CONFIG.copy()


def _refresh_runtime_settings() -> dict:
    global settings
    global MINIMAP, RESOURCE_VISIBILITY, WINDOW_GEOMETRY, VIEW_SIZE, LOGIC_MAP_PATH, DISPLAY_MAP_PATH, MAX_LOST_FRAMES
    global SIFT_REFRESH_RATE, SIFT_CLAHE_LIMIT, SIFT_MATCH_RATIO, SIFT_MIN_MATCH_COUNT, SIFT_RANSAC_THRESHOLD
    global AI_REFRESH_RATE, AI_CONFIDENCE_THRESHOLD, AI_MIN_MATCH_COUNT, AI_RANSAC_THRESHOLD
    global AI_SCAN_SIZE, AI_SCAN_STEP, AI_TRACK_RADIUS
    global AI_SMOOTH_ALPHA_MIN, AI_SMOOTH_ALPHA_MAX, AI_SMOOTH_DISTANCE_MIN, AI_SMOOTH_DISTANCE_MAX
    global AI_LEAD_TIME_MS, AI_LEAD_MAX_PIXELS, AI_LEAD_MIN_MATCHES, AI_LEAD_TRIGGER_PIXELS
    global AI_STILL_TRIGGER_PIXELS, AI_POSITION_HOLD_PIXELS, AI_JITTER_GUARD_PIXELS
    global AI_LOCAL_MATCH_BASE_DISTANCE, AI_LOCAL_MATCH_LOST_STEP, AI_LOCAL_MATCH_MAX_DISTANCE
    global AI_STEP_LIMIT_MIN_PIXELS, AI_STEP_LIMIT_MAX_PIXELS
    global AI_GLOBAL_SCAN_MIN_LOST_FRAMES, AI_GLOBAL_SCAN_RETRY_GAP
    global AI_RECOVERY_SNAP_LOST_FRAMES, AI_LARGE_OFFSET_MIN_LOST_FRAMES, AI_LARGE_OFFSET_CONFIRM_FRAMES

    settings = load_config()
    env_minimap_override = _load_minimap_env_override()
    if env_minimap_override:
        settings["MINIMAP"] = env_minimap_override

    MINIMAP = settings.get("MINIMAP")
    RESOURCE_VISIBILITY = _normalize_resource_visibility_settings(settings.get("RESOURCE_VISIBILITY"))
    settings["RESOURCE_VISIBILITY"] = dict(RESOURCE_VISIBILITY)
    WINDOW_GEOMETRY = settings.get("WINDOW_GEOMETRY")
    VIEW_SIZE = settings.get("VIEW_SIZE")
    LOGIC_MAP_PATH = _to_absolute_path(settings.get("LOGIC_MAP_PATH"))
    DISPLAY_MAP_PATH = _resolve_display_map_path(settings)
    MAX_LOST_FRAMES = settings.get("MAX_LOST_FRAMES")

    SIFT_REFRESH_RATE = settings.get("SIFT_REFRESH_RATE")
    SIFT_CLAHE_LIMIT = settings.get("SIFT_CLAHE_LIMIT")
    SIFT_MATCH_RATIO = settings.get("SIFT_MATCH_RATIO")
    SIFT_MIN_MATCH_COUNT = settings.get("SIFT_MIN_MATCH_COUNT")
    SIFT_RANSAC_THRESHOLD = settings.get("SIFT_RANSAC_THRESHOLD")

    AI_REFRESH_RATE = settings.get("AI_REFRESH_RATE")
    AI_CONFIDENCE_THRESHOLD = settings.get("AI_CONFIDENCE_THRESHOLD")
    AI_MIN_MATCH_COUNT = settings.get("AI_MIN_MATCH_COUNT")
    AI_RANSAC_THRESHOLD = settings.get("AI_RANSAC_THRESHOLD")
    AI_SCAN_SIZE = settings.get("AI_SCAN_SIZE")
    AI_SCAN_STEP = settings.get("AI_SCAN_STEP")
    AI_TRACK_RADIUS = settings.get("AI_TRACK_RADIUS")
    AI_SMOOTH_ALPHA_MIN = settings.get("AI_SMOOTH_ALPHA_MIN")
    AI_SMOOTH_ALPHA_MAX = settings.get("AI_SMOOTH_ALPHA_MAX")
    AI_SMOOTH_DISTANCE_MIN = settings.get("AI_SMOOTH_DISTANCE_MIN")
    AI_SMOOTH_DISTANCE_MAX = settings.get("AI_SMOOTH_DISTANCE_MAX")
    AI_LEAD_TIME_MS = settings.get("AI_LEAD_TIME_MS")
    AI_LEAD_MAX_PIXELS = settings.get("AI_LEAD_MAX_PIXELS")
    AI_LEAD_MIN_MATCHES = settings.get("AI_LEAD_MIN_MATCHES")
    AI_LEAD_TRIGGER_PIXELS = settings.get("AI_LEAD_TRIGGER_PIXELS")
    AI_STILL_TRIGGER_PIXELS = settings.get("AI_STILL_TRIGGER_PIXELS")
    AI_POSITION_HOLD_PIXELS = settings.get("AI_POSITION_HOLD_PIXELS")
    AI_JITTER_GUARD_PIXELS = settings.get("AI_JITTER_GUARD_PIXELS")
    AI_LOCAL_MATCH_BASE_DISTANCE = settings.get("AI_LOCAL_MATCH_BASE_DISTANCE")
    AI_LOCAL_MATCH_LOST_STEP = settings.get("AI_LOCAL_MATCH_LOST_STEP")
    AI_LOCAL_MATCH_MAX_DISTANCE = settings.get("AI_LOCAL_MATCH_MAX_DISTANCE")
    AI_STEP_LIMIT_MIN_PIXELS = settings.get("AI_STEP_LIMIT_MIN_PIXELS")
    AI_STEP_LIMIT_MAX_PIXELS = settings.get("AI_STEP_LIMIT_MAX_PIXELS")
    AI_GLOBAL_SCAN_MIN_LOST_FRAMES = settings.get("AI_GLOBAL_SCAN_MIN_LOST_FRAMES")
    AI_GLOBAL_SCAN_RETRY_GAP = settings.get("AI_GLOBAL_SCAN_RETRY_GAP")
    AI_RECOVERY_SNAP_LOST_FRAMES = settings.get("AI_RECOVERY_SNAP_LOST_FRAMES")
    AI_LARGE_OFFSET_MIN_LOST_FRAMES = settings.get("AI_LARGE_OFFSET_MIN_LOST_FRAMES")
    AI_LARGE_OFFSET_CONFIRM_FRAMES = settings.get("AI_LARGE_OFFSET_CONFIRM_FRAMES")
    return dict(settings)


def reload_runtime_config() -> dict:
    return _refresh_runtime_settings()


def save_runtime_settings(updated_settings: dict) -> dict:
    payload = DEFAULT_CONFIG.copy()
    if isinstance(updated_settings, dict):
        payload.update(updated_settings)
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=4, ensure_ascii=False)
    return _refresh_runtime_settings()


def save_minimap_region(minimap_payload: dict) -> dict:
    if not isinstance(minimap_payload, dict):
        raise ValueError("小地图区域配置无效")
    try:
        left = int(minimap_payload.get("left", 0) or 0)
        top = int(minimap_payload.get("top", 0) or 0)
        width = int(minimap_payload.get("width", minimap_payload.get("region_width", 0)) or 0)
        height = int(minimap_payload.get("height", minimap_payload.get("region_height", 0)) or 0)
        region_x = int(minimap_payload.get("region_x", 0) or 0)
        region_y = int(minimap_payload.get("region_y", 0) or 0)
        region_width = int(minimap_payload.get("region_width", 0) or 0)
        region_height = int(minimap_payload.get("region_height", 0) or 0)
        region_hwnd = int(minimap_payload.get("region_hwnd", 0) or 0)
        region_client_width = int(minimap_payload.get("region_client_width", 0) or 0)
        region_client_height = int(minimap_payload.get("region_client_height", 0) or 0)
    except Exception as exc:
        raise ValueError("小地图区域配置无效") from exc
    if width <= 0 or height <= 0:
        raise ValueError("小地图区域尺寸无效")

    current_settings = load_config()
    saved_payload = {
        "left": left,
        "top": top,
        "width": width,
        "height": height,
    }
    if region_width > 0 and region_height > 0:
        saved_payload.update(
            {
                "region_x": region_x,
                "region_y": region_y,
                "region_width": region_width,
                "region_height": region_height,
                "region_hwnd": region_hwnd,
                "region_window_title": str(minimap_payload.get("region_window_title", "") or "").strip(),
                "region_window_class": str(minimap_payload.get("region_window_class", "") or "").strip(),
                "region_client_width": region_client_width,
                "region_client_height": region_client_height,
            }
        )
    current_settings["MINIMAP"] = saved_payload
    return save_runtime_settings(current_settings)


def save_resource_visibility(visibility_payload: dict) -> dict:
    current_settings = load_config()
    current_settings["RESOURCE_VISIBILITY"] = _normalize_resource_visibility_settings(visibility_payload)
    return save_runtime_settings(current_settings)


_refresh_runtime_settings()
