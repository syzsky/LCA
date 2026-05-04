# -*- coding: utf-8 -*-

"""
字库识别任务模块
使用OLA插件的字库OCR功能进行文字识别
仅在插件模式下可用
"""

import logging
import math
import sys
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from tasks.task_utils import (
    get_recorded_region_binding_mismatch_detail,
    resolve_region_selection_params,
)
from utils.app_paths import get_config_path, get_user_data_dir
from utils.window_binding_utils import get_bound_windows_for_mode, get_plugin_bind_args

logger = logging.getLogger(__name__)

# 任务类型标识
TASK_TYPE = "字库识别"
TASK_NAME = "字库识别"



def _get_effective_ola_binding_config(manager: Any, hwnd: Optional[int]) -> Dict[str, Any]:
    """Prefer the existing window binding config, otherwise fall back to main config."""
    default_config = {
        'display_mode': 'normal',
        'mouse_mode': 'normal',
        'keypad_mode': 'normal',
        'mode': 0,
        'input_lock': False,
        'mouse_move_with_trajectory': False,
        'pubstr': '',
    }

    if manager and hwnd:
        try:
            cached_config = manager.get_window_config(hwnd)
            if cached_config:
                merged_config = default_config.copy()
                merged_config.update(cached_config)
                return merged_config
        except Exception:
            pass

    try:
        from app_core.plugin_bridge import get_cached_config
        config = get_cached_config()
    except Exception:
        config = {}

    resolved_config = default_config.copy()
    resolved_config.update(get_plugin_bind_args(config if isinstance(config, dict) else {}, hwnd=hwnd))
    return resolved_config


def _summarize_dict_component_metrics(selected_boxes: list) -> Optional[Dict[str, float]]:
    """统计前景文字连通域的尺寸特征，用于诊断字库与当前画面的形态差异。"""
    if not selected_boxes:
        return None

    import statistics

    widths = [float(box[2]) for box in selected_boxes]
    heights = [float(box[3]) for box in selected_boxes]
    aspects = [width / max(height, 1.0) for width, height in zip(widths, heights)]

    return {
        "count": float(len(selected_boxes)),
        "avg_width": float(sum(widths) / len(widths)),
        "avg_height": float(sum(heights) / len(heights)),
        "avg_aspect": float(sum(aspects) / len(aspects)),
        "median_width": float(statistics.median(widths)),
        "median_height": float(statistics.median(heights)),
        "median_aspect": float(statistics.median(aspects)),
    }


def _analyze_dict_ocr_hints_from_image(
    image: Any,
) -> Tuple[Optional[str], Optional[Tuple[int, int, int, int]], Optional[Dict[str, float]]]:
    """从区域截图中提取前景字色范围、文字包围盒和形态统计。"""
    try:
        import cv2
        import numpy as np

        if image is None:
            return None, None, None

        frame = image
        if not hasattr(frame, 'shape'):
            return None, None, None

        if len(frame.shape) == 2:
            gray = frame
            bgr = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif frame.shape[2] == 4:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
            bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        else:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            bgr = frame

        height, width = gray.shape[:2]
        if width <= 0 or height <= 0:
            return None, None, None

        region_area = max(1, width * height)
        min_component_area = max(8, region_area // 3000)
        max_component_area = int(region_area * 0.35)
        edge_reject_area = int(region_area * 0.02)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)

        def _collect_candidate(mask: Any, foreground_mode: str) -> Optional[Tuple[float, Optional[str], Tuple[int, int, int, int], Optional[Dict[str, float]]]]:
            component_count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
            selected_mask = np.zeros_like(mask, dtype=np.uint8)
            selected_boxes = []

            for component_idx in range(1, component_count):
                x, y, w, h, area = stats[component_idx]
                if area < min_component_area or w < 2 or h < 4:
                    continue
                if w >= int(width * 0.95) or h >= int(height * 0.95):
                    continue

                touches_edge = x <= 1 or y <= 1 or (x + w) >= (width - 1) or (y + h) >= (height - 1)
                if touches_edge and area >= max_component_area:
                    continue
                if touches_edge and area >= edge_reject_area:
                    continue

                selected_mask[labels == component_idx] = 255
                selected_boxes.append((int(x), int(y), int(w), int(h), int(area)))

            if not selected_boxes:
                return None

            x1 = min(box[0] for box in selected_boxes)
            y1 = min(box[1] for box in selected_boxes)
            x2 = max(box[0] + box[2] for box in selected_boxes)
            y2 = max(box[1] + box[3] for box in selected_boxes)

            padding = 2
            x1 = max(0, x1 - padding)
            y1 = max(0, y1 - padding)
            x2 = min(width, x2 + padding)
            y2 = min(height, y2 + padding)

            component_metrics = _summarize_dict_component_metrics(selected_boxes)
            foreground_selector = selected_mask > 0
            foreground_pixels = bgr[foreground_selector]
            if foreground_pixels.size == 0:
                return None, None, (x1, y1, x2, y2), component_metrics

            foreground_gray = gray[foreground_selector]
            if foreground_mode == "light":
                core_gray_limit = np.percentile(foreground_gray, 35)
                core_selector = foreground_selector & (gray >= core_gray_limit)
            else:
                core_gray_limit = np.percentile(foreground_gray, 65)
                core_selector = foreground_selector & (gray <= core_gray_limit)

            core_pixels = bgr[core_selector]
            if core_pixels.size == 0:
                core_pixels = foreground_pixels

            lower_bgr = np.percentile(core_pixels, 5, axis=0)
            upper_bgr = np.percentile(core_pixels, 95, axis=0)
            lower_bgr = np.clip(lower_bgr - 8, 0, 255).astype(int)
            upper_bgr = np.clip(upper_bgr + 8, 0, 255).astype(int)

            start_color = f"{lower_bgr[2]:02X}{lower_bgr[1]:02X}{lower_bgr[0]:02X}"
            end_color = f"{upper_bgr[2]:02X}{upper_bgr[1]:02X}{upper_bgr[0]:02X}"

            background_selector = ~foreground_selector
            contrast_score = 0.0
            if np.any(background_selector):
                background_gray = gray[background_selector]
                contrast_score = abs(float(np.mean(foreground_gray)) - float(np.mean(background_gray)))

            component_area = sum(box[4] for box in selected_boxes)
            fill_ratio = component_area / max(region_area, 1)
            score = contrast_score + min(len(selected_boxes), 12) * 2.0 + min(fill_ratio * 120.0, 18.0)

            return score, f"{start_color}~{end_color}", (x1, y1, x2, y2), component_metrics

        _, dark_mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        _, light_mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        candidates = []
        dark_candidate = _collect_candidate(dark_mask, "dark")
        if dark_candidate:
            candidates.append(dark_candidate)
        light_candidate = _collect_candidate(light_mask, "light")
        if light_candidate:
            candidates.append(light_candidate)

        if not candidates:
            return None, None, None

        best_candidate = max(candidates, key=lambda item: item[0])
        return best_candidate[1], best_candidate[2], best_candidate[3]
    except Exception:
        return None, None, None


def _get_dict_ocr_visual_metrics_from_image(image: Any) -> Optional[Dict[str, float]]:
    """从区域截图中提取文字形态统计，用于估算识别归一化倍率。"""
    _derived_color, _local_bbox, component_metrics = _analyze_dict_ocr_hints_from_image(image)
    return component_metrics


def _capture_dict_ocr_image(
    target_hwnd: Optional[int],
    region_x1: int,
    region_y1: int,
    region_x2: int,
    region_y2: int,
) -> Tuple[Optional[Any], Tuple[int, int]]:
    """截取字库识别区域截图。"""
    if not target_hwnd or region_x2 <= region_x1 or region_y2 <= region_y1:
        return None, (region_x1, region_y1)

    try:
        from app_core.plugin_bridge import plugin_capture
        image = plugin_capture(target_hwnd, region_x1, region_y1, region_x2, region_y2)
        return image, (region_x1, region_y1)
    except Exception:
        return None, (region_x1, region_y1)


def _dict_db_has_required_schema(db_path: str) -> bool:
    """校验字库数据库是否已具备 OLA 所需的核心表结构。"""
    try:
        import os
        import sqlite3

        if not db_path or not os.path.exists(db_path):
            return False

        resolved_path = Path(db_path).resolve()
        db_uri = f"file:{resolved_path.as_posix()}?mode=ro"
        with sqlite3.connect(db_uri, uri=True) as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'ola_dict'
                LIMIT 1
                """
            ).fetchone()
        return bool(row)
    except Exception:
        return False


def _open_ola_dict_database(
    ola: Any,
    db_path: str,
    log_prefix: str,
    *,
    allow_create: bool,
) -> int:
    """统一打开并修复字库数据库，避免空壳 SQLite 文件导致 OLA 导入失败。"""
    import os

    schema_ready = _dict_db_has_required_schema(db_path)
    logger.info(f"{log_prefix} 尝试打开数据库: {db_path}, 文件存在: {os.path.exists(db_path)}")

    db_handle = ola.OpenDatabase(db_path, "")
    if not db_handle or db_handle <= 0:
        if not allow_create:
            return 0
        logger.warning(f"{log_prefix} OpenDatabase失败(ret=0)，尝试CreateDatabase")
        db_handle = ola.CreateDatabase(db_path, "")
        if not db_handle or db_handle <= 0:
            return 0
        logger.info(f"{log_prefix} 创建数据库成功，初始化数据库结构")
        try:
            ola.InitOlaDatabase(db_handle)
        except Exception as init_error:
            logger.error(f"{log_prefix} 初始化数据库结构异常: {init_error}")
            try:
                ola.CloseDatabase(db_handle)
            except Exception:
                pass
            return 0
        return db_handle

    logger.info(f"{log_prefix} 打开数据库成功")
    if schema_ready:
        return db_handle

    logger.warning(f"{log_prefix} 检测到数据库结构缺失，正在重新初始化: {db_path}")
    try:
        init_result = ola.InitOlaDatabase(db_handle)
    except Exception as init_error:
        logger.error(f"{log_prefix} 初始化数据库结构异常: {init_error}")
        try:
            ola.CloseDatabase(db_handle)
        except Exception:
            pass
        return 0

    if init_result not in (None, 1) and not _dict_db_has_required_schema(db_path):
        logger.error(f"{log_prefix} 初始化数据库结构失败(ret={init_result})")
        try:
            ola.CloseDatabase(db_handle)
        except Exception:
            pass
        return 0

    logger.info(f"{log_prefix} 数据库结构已修复")
    return db_handle


def _get_dict_template_metrics(dict_name: str, db_path: Optional[str] = None) -> Optional[Dict[str, float]]:
    """读取字库模板尺寸统计，用于诊断当前画面与字库是否同源。"""
    if not dict_name:
        return None

    try:
        import os
        import sqlite3

        metrics_db_path = db_path or _get_dict_db_path()
        if not os.path.exists(metrics_db_path):
            return None

        if not _dict_db_has_required_schema(metrics_db_path):
            return None

        resolved_path = Path(metrics_db_path).resolve()
        db_uri = f"file:{resolved_path.as_posix()}?mode=ro"
        with sqlite3.connect(db_uri, uri=True) as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*),
                    AVG(width),
                    AVG(height),
                    AVG(CAST(width AS REAL) / NULLIF(height, 0))
                FROM ola_dict
                WHERE "dict" = ?
                """,
                (dict_name,),
            ).fetchone()

        if not row or not row[0]:
            return None

        return {
            "entry_count": float(row[0]),
            "avg_width": float(row[1] or 0.0),
            "avg_height": float(row[2] or 0.0),
            "avg_aspect": float(row[3] or 0.0),
        }
    except Exception:
        return None


def _build_dict_ocr_scale_candidates(
    template_metrics: Optional[Dict[str, float]],
    visual_metrics: Optional[Dict[str, float]],
    image_height: int,
) -> Tuple[float, ...]:
    """为固定高度字库构建稳定的缩放候选列表。"""
    candidates = []

    def _append_scale(scale_value: float) -> None:
        if not math.isfinite(scale_value):
            return
        normalized = max(0.8, min(3.0, float(scale_value)))
        rounded = round(normalized, 2)
        if any(abs(existing - rounded) < 0.05 for existing in candidates):
            return
        candidates.append(rounded)

    ratio_candidates = []
    if template_metrics and visual_metrics:
        template_width = float(template_metrics.get("avg_width", 0.0) or 0.0)
        template_height = float(template_metrics.get("avg_height", 0.0) or 0.0)
        current_width = float(visual_metrics.get("median_width", 0.0) or 0.0)
        current_height = float(visual_metrics.get("median_height", 0.0) or 0.0)

        if template_width > 0 and current_width > 0:
            ratio_candidates.append(template_width / current_width)
        if template_height > 0 and current_height > 0:
            ratio_candidates.append(template_height / current_height)

        if len(ratio_candidates) >= 2:
            geometric_scale = math.sqrt(ratio_candidates[0] * ratio_candidates[1])
            _append_scale(geometric_scale)
            _append_scale(geometric_scale * 0.88)
            _append_scale(geometric_scale * 1.12)

        for ratio in ratio_candidates:
            _append_scale(ratio)

    if image_height > 0:
        _append_scale(240.0 / float(image_height))

    _append_scale(1.0)
    return tuple(candidates or (1.0,))


def _normalize_dict_ocr_image(image: Any) -> Optional[Any]:
    """统一转换为连续的 BGR 图像，便于加载到 OLA 内存图。"""
    try:
        import cv2
        import numpy as np

        if image is None or not hasattr(image, "shape"):
            return None

        frame = image
        if len(frame.shape) == 2:
            bgr = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif frame.shape[2] == 4:
            bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        else:
            bgr = frame

        if bgr.dtype != np.uint8:
            bgr = np.clip(bgr, 0, 255).astype(np.uint8)

        return np.ascontiguousarray(bgr)
    except Exception:
        return None


def _evaluate_dict_match_result(result_text: str, target_text: str, match_mode: str) -> Tuple[bool, str]:
    """统一判断识别结果是否满足目标匹配规则。"""
    if not target_text:
        return bool(result_text), ""

    if match_mode == '包含':
        return target_text in result_text, ""
    if match_mode == '完全匹配':
        return target_text == result_text, ""
    if match_mode == '正则匹配':
        import re
        try:
            return bool(re.search(target_text, result_text)), ""
        except re.error as e:
            return False, f"正则表达式错误: {e}"

    return False, ""


def _parse_dict_ocr_result(result_json: Any, scale_factor: float = 1.0) -> Tuple[str, list]:
    """解析 OLA 字库识别结果，并按缩放倍率还原坐标。"""
    result_text = ""
    ocr_regions = []
    safe_scale = float(scale_factor or 1.0)
    if safe_scale <= 0:
        safe_scale = 1.0

    if not result_json:
        return result_text, ocr_regions

    try:
        import json

        result_data = json.loads(result_json) if isinstance(result_json, str) else result_json
        if not isinstance(result_data, dict):
            return str(result_json).strip(), []

        result_text = str(result_data.get("Text", "") or "")
        regions = result_data.get("Regions", []) or []
        for region in regions:
            center = region.get("Center", {}) or {}
            center_x = float(center.get("x", 0) or 0)
            center_y = float(center.get("y", 0) or 0)
            if safe_scale != 1.0:
                center_x /= safe_scale
                center_y /= safe_scale

            vertices = []
            for vertex in region.get("Vertices", []) or []:
                x = float(vertex.get("x", 0) or 0)
                y = float(vertex.get("y", 0) or 0)
                if safe_scale != 1.0:
                    x /= safe_scale
                    y /= safe_scale
                vertices.append({"x": x, "y": y})

            ocr_regions.append({
                "text": region.get("Text", ""),
                "score": region.get("Score", 0),
                "center_x": center_x,
                "center_y": center_y,
                "vertices": vertices,
                "angle": region.get("Angle", 0),
            })
    except Exception:
        result_text = str(result_json).strip() if result_json else ""

    return result_text, ocr_regions


def _run_dict_ocr_on_image(
    ola: Any,
    image: Any,
    dict_name: str,
    effective_color: str,
    match_value: float,
    template_metrics: Optional[Dict[str, float]],
    visual_metrics: Optional[Dict[str, float]],
    target_text: str,
    match_mode: str,
) -> Tuple[Any, str, list, float]:
    """在区域截图上执行字库识别，并按候选倍率做归一化。"""
    prepared_image = _normalize_dict_ocr_image(image)
    if prepared_image is None:
        return {}, "", [], 1.0

    try:
        import cv2
    except Exception:
        return {}, "", [], 1.0

    image_height, image_width = prepared_image.shape[:2]
    scale_candidates = _build_dict_ocr_scale_candidates(template_metrics, visual_metrics, image_height)

    first_result_json = {}
    first_result_text = ""
    first_regions = []
    first_scale = 1.0
    best_non_empty = None

    for scale in scale_candidates:
        working_image = prepared_image
        if abs(scale - 1.0) >= 0.05:
            target_width = max(1, int(round(image_width * scale)))
            target_height = max(1, int(round(image_height * scale)))
            working_image = cv2.resize(
                prepared_image,
                (target_width, target_height),
                interpolation=cv2.INTER_LINEAR,
            )

        image_ptr = 0
        try:
            image_ptr = int(
                ola.LoadImageFromRGBData(
                    int(working_image.shape[1]),
                    int(working_image.shape[0]),
                    int(working_image.ctypes.data),
                    int(working_image.strides[0]),
                ) or 0
            )
            if image_ptr <= 0:
                continue

            current_result_json = ola.OcrFromDictPtrDetails(image_ptr, effective_color, dict_name, match_value)
            current_text, current_regions = _parse_dict_ocr_result(current_result_json, scale)

            if not first_result_json:
                first_result_json = current_result_json
                first_result_text = current_text
                first_regions = current_regions
                first_scale = scale

            if current_text and best_non_empty is None:
                best_non_empty = (current_result_json, current_text, current_regions, scale)

            matched, match_error = _evaluate_dict_match_result(current_text, target_text, match_mode)
            if current_text and (not target_text or matched or match_error):
                return current_result_json, current_text, current_regions, scale
        except Exception:
            continue
        finally:
            if image_ptr > 0:
                try:
                    release_image = getattr(ola, "FreeImagePtr", None)
                    if callable(release_image):
                        release_image(image_ptr)
                    else:
                        release_data = getattr(ola, "FreeImageData", None)
                        if callable(release_data):
                            release_data(image_ptr)
                        else:
                            release_all = getattr(ola, "FreeImageAll", None)
                            if callable(release_all):
                                release_all()
                except Exception:
                    pass

    if best_non_empty is not None:
        return best_non_empty

    return first_result_json, first_result_text, first_regions, first_scale


def _get_long_path(path: str) -> str:
    """将短路径转换为长路径（完整路径）"""
    import os
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.kernel32.GetLongPathNameW(path, buf, 260)
        return buf.value if buf.value else path
    except Exception:
        return path


def _get_ola_dir() -> str:
    """获取OLA目录路径（统一使用exe同级目录）"""
    import os
    import sys
    if getattr(sys, 'frozen', False):
        # 打包环境：统一使用exe同级目录
        exe_path = os.path.abspath(sys.executable)
        try:
            exe_path = os.path.realpath(exe_path)
        except Exception:
            pass
        exe_dir = os.path.dirname(exe_path)
        # 转换短路径为长路径
        exe_dir = _get_long_path(exe_dir)
        ola_dir = os.path.join(exe_dir, 'OLA')
        return ola_dir
    else:
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'OLA')


_OLA_DATA_MIGRATED = False


def _get_ola_data_dir() -> str:
    """获取字库数据目录（用户可写）。"""
    import os
    import shutil

    global _OLA_DATA_MIGRATED

    data_dir = os.path.join(get_user_data_dir("LCA"), "ola")
    os.makedirs(data_dir, exist_ok=True)

    if _OLA_DATA_MIGRATED:
        return data_dir

    legacy_dir = _get_ola_dir()
    try:
        if os.path.normcase(os.path.abspath(legacy_dir)) != os.path.normcase(os.path.abspath(data_dir)):
            migrate_items = (
                "ola_dict.db",
                "dict_list.json",
                "dict_colors.json",
                "dict_pics",
            )
            for name in migrate_items:
                src = os.path.join(legacy_dir, name)
                dst = os.path.join(data_dir, name)
                if os.path.exists(dst) or not os.path.exists(src):
                    continue
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
    except Exception as e:
        logger.warning(f"迁移旧字库数据失败: {e}")
    finally:
        _OLA_DATA_MIGRATED = True

    return data_dir


def _get_dict_db_path() -> str:
    """获取字库数据库路径"""
    import os
    return os.path.join(_get_ola_data_dir(), 'ola_dict.db')


def _get_dict_list_path() -> str:
    """获取字库列表配置文件路径"""
    import os
    return os.path.join(_get_ola_data_dir(), 'dict_list.json')


def _load_dict_list() -> list:
    """加载字库列表"""
    import json
    import os
    dict_list = []
    dict_config_path = _get_dict_list_path()
    try:
        if os.path.exists(dict_config_path):
            with open(dict_config_path, 'r', encoding='utf-8') as f:
                dict_list = json.load(f)
    except Exception as e:
        logger.error(f"加载字库列表失败: {e}")
    return dict_list


def _save_dict_list(dict_list: list) -> bool:
    """保存字库列表"""
    import json
    import os
    try:
        ola_dir = _get_ola_dir()
        if not os.path.exists(ola_dir):
            os.makedirs(ola_dir)
        dict_config_path = _get_dict_list_path()
        with open(dict_config_path, 'w', encoding='utf-8') as f:
            json.dump(dict_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"保存字库列表失败: {e}")
        return False


def _get_dict_color_path() -> str:
    """获取字库颜色配置文件路径"""
    import os
    return os.path.join(_get_ola_data_dir(), 'dict_colors.json')


def _load_dict_colors() -> dict:
    """加载字库颜色配置"""
    import json
    import os
    colors = {}
    color_config_path = _get_dict_color_path()
    try:
        if os.path.exists(color_config_path):
            with open(color_config_path, 'r', encoding='utf-8') as f:
                colors = json.load(f)
    except Exception as e:
        logger.error(f"加载字库颜色配置失败: {e}")
    return colors



def _get_dict_color(dict_name: str) -> str:
    """获取字库的颜色配置"""
    colors = _load_dict_colors()
    return colors.get(dict_name, "")


def _get_dict_tool_parent(kwargs) -> Any:
    main_window = kwargs.get('main_window')
    parameter_panel = kwargs.get('parameter_panel')
    return parameter_panel if parameter_panel else main_window


def _get_available_dict_names() -> list[str]:
    return [str(name).strip() for name in _load_dict_list() if str(name).strip()]


def open_text_preprocess_tool_dialog(params: Dict[str, Any], **kwargs) -> None:
    """打开文字预处理工具窗口。"""
    parent = _get_dict_tool_parent(kwargs)
    initial_path = str(kwargs.get('source_path') or params.get('source_path') or '').strip()

    from ui.dialogs.text_preprocess_tool_dialog import TextPreprocessToolDialog

    dialog = TextPreprocessToolDialog(parent=parent, initial_path=initial_path)
    dialog.exec()


def open_dict_tool_dialog(params: Dict[str, Any], **kwargs) -> None:
    """打开字库工具独立窗口。"""
    parent = _get_dict_tool_parent(kwargs)

    from ui.dialogs.dict_tool_dialog import DictToolDialog

    dialog_holder: dict[str, Any] = {}

    def _build_action_kwargs(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        action_kwargs = dict(kwargs)
        if extra:
            action_kwargs.update(extra)
        action_kwargs['parameter_panel'] = dialog_holder.get('dialog', parent)
        return action_kwargs

    def _delete_selected_dict(dict_name: str) -> None:
        delete_dict(params, **_build_action_kwargs({'dict_name': dict_name}))

    def _export_selected_dict(dict_name: str) -> None:
        export_dict(params, **_build_action_kwargs({'dict_name': dict_name}))

    dialog = DictToolDialog(
        parent=parent,
        list_dict_names=_get_available_dict_names,
        preview_payload_loader=get_dict_preview_payload,
        open_tool_callback=lambda: open_dm_dict_tool(params, **_build_action_kwargs()),
        import_dm_callback=lambda: import_dm_dict(params, **_build_action_kwargs()),
        import_bmp_callback=lambda: import_bmp_folder(params, **_build_action_kwargs()),
        delete_dict_callback=_delete_selected_dict,
        export_dict_callback=_export_selected_dict,
    )
    dialog_holder['dialog'] = dialog
    dialog.exec()


def open_dm_dict_tool(params: Dict[str, Any], **kwargs) -> None:
    """打开大漠综合工具。"""
    from PySide6.QtWidgets import QMessageBox
    import ctypes
    import os
    import subprocess

    parent = _get_dict_tool_parent(kwargs)

    if getattr(sys, 'frozen', False):
        exe_path = sys.executable
        try:
            buf = ctypes.create_unicode_buffer(1024)
            result = ctypes.windll.kernel32.GetLongPathNameW(exe_path, buf, 1024)
            if result > 0:
                exe_path = buf.value
        except Exception as e:
            logger.warning(f"转换长路径失败，使用原路径: {e}")
        app_root = os.path.dirname(exe_path)
    else:
        app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    tool_path = os.path.join(app_root, "tools", "大漠综合工具.exe")
    logger.info(
        f"[字库工具] 解析路径: exe_path={sys.executable if getattr(sys, 'frozen', False) else 'dev'}, "
        f"app_root={app_root}, tool_path={tool_path}"
    )

    if not os.path.exists(tool_path):
        QMessageBox.warning(parent, "工具不存在", f"未找到大漠综合工具:\n{tool_path}\n\n程序根目录: {app_root}")
        return

    try:
        subprocess.Popen([tool_path], cwd=os.path.dirname(tool_path))
        logger.info(f"[字库工具] 已启动大漠综合工具: {tool_path}")
    except Exception as e:
        logger.error(f"[字库工具] 启动大漠综合工具失败: {e}")
        QMessageBox.critical(parent, "启动失败", f"启动大漠综合工具失败: {e}")


def dict_tool_menu(params: Dict[str, Any], **kwargs) -> None:
    """字库工具菜单"""
    open_dict_tool_dialog(params, **kwargs)


def _get_pending_dict_dir() -> str:
    """获取待导入字库的目录路径"""
    import os
    return os.path.join(_get_ola_data_dir(), 'dict_pics')


def _get_dict_preview_dir(dict_name: str) -> str:
    import os
    return os.path.join(_get_pending_dict_dir(), str(dict_name or "").strip())


def get_dict_preview_payload(dict_name: str) -> Dict[str, Any]:
    """获取字库预览数据。"""
    import os

    normalized_name = str(dict_name or "").strip()
    preview_dir = _get_dict_preview_dir(normalized_name)
    valid_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.gif'}
    preview_items = []

    if normalized_name and os.path.isdir(preview_dir):
        for file_name in sorted(os.listdir(preview_dir), key=lambda item: str(item)):
            file_path = os.path.join(preview_dir, file_name)
            if not os.path.isfile(file_path):
                continue
            ext = os.path.splitext(file_name)[1].lower()
            if ext not in valid_extensions:
                continue
            char_text = os.path.splitext(file_name)[0].strip() or file_name
            preview_items.append({
                "text": char_text,
                "path": os.path.abspath(file_path),
            })

    metrics = _get_dict_template_metrics(normalized_name)
    entry_count = int(metrics.get("entry_count", 0.0) or 0) if metrics else 0

    return {
        "dict_name": normalized_name,
        "preview_dir": os.path.abspath(preview_dir),
        "items": preview_items,
        "preview_count": len(preview_items),
        "entry_count": max(len(preview_items), entry_count),
    }


def _sync_dict_preview_cache_from_dir(source_dir: str, dict_name: str) -> int:
    """将字符图片同步到字库预览缓存目录。"""
    import os
    import shutil

    normalized_name = str(dict_name or "").strip()
    if not normalized_name or not os.path.isdir(source_dir):
        return 0

    preview_dir = _get_dict_preview_dir(normalized_name)
    valid_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.gif'}

    os.makedirs(preview_dir, exist_ok=True)

    for existing_name in os.listdir(preview_dir):
        existing_path = os.path.join(preview_dir, existing_name)
        if not os.path.isfile(existing_path):
            continue
        existing_ext = os.path.splitext(existing_name)[1].lower()
        if existing_ext not in valid_extensions:
            continue
        try:
            os.remove(existing_path)
        except OSError:
            pass

    copied_count = 0
    for file_name in os.listdir(source_dir):
        source_path = os.path.join(source_dir, file_name)
        if not os.path.isfile(source_path):
            continue
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in valid_extensions:
            continue
        target_path = os.path.join(preview_dir, file_name)
        shutil.copy2(source_path, target_path)
        copied_count += 1

    return copied_count


def import_dm_dict(params: Dict[str, Any], **kwargs) -> None:
    """导入大漠字库文件"""
    from PySide6.QtWidgets import QMessageBox, QFileDialog, QInputDialog
    import os
    import glob
    import shutil

    main_window = kwargs.get('main_window')
    parameter_panel = kwargs.get('parameter_panel')

    parent = parameter_panel if parameter_panel else main_window

    # 选择大漠字库文件
    file_path, _ = QFileDialog.getOpenFileName(
        parent,
        "选择大漠字库文件",
        "",
        "大漠字库文件 (*.txt *.dict *.*);;所有文件 (*.*)"
    )

    if not file_path:
        return

    # 输入字库名称
    default_name = os.path.splitext(os.path.basename(file_path))[0]
    dict_name, ok = QInputDialog.getText(
        parent,
        "输入字库名称",
        "请输入导入后的字库名称:",
        text=default_name
    )

    if not ok or not dict_name.strip():
        return

    dict_name = dict_name.strip()
    pending_dir = _get_pending_dict_dir()
    output_dir = os.path.join(pending_dir, dict_name)
    full_output_dir = _get_long_path(os.path.abspath(output_dir))

    # 尝试获取OLA实例并导入
    ola = None
    temp_ola = False
    db = 0
    try:
        from plugins.adapters.ola.multi_instance_manager import get_ola_instance_manager
        manager = get_ola_instance_manager()
        window_instances = manager._window_instances if hasattr(manager, '_window_instances') else {}

        if window_instances:
            first_instance_data = list(window_instances.values())[0]
            ola = first_instance_data.get('ola') if isinstance(first_instance_data, dict) else None

        if not ola:
            # 尝试通过config.json获取绑定的窗口句柄并创建实例
            try:
                import json
                import os
                config_path = get_config_path()
                if os.path.exists(config_path):
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config_data = json.load(f)
                    bound_windows = get_bound_windows_for_mode(config_data)
                    for window_info in bound_windows:
                        if window_info.get('enabled', True):
                            hwnd = window_info.get('hwnd')
                            if hwnd and hwnd > 0:
                                ola = manager.get_instance_for_window(hwnd)
                                if ola:
                                    break
            except Exception:
                pass

    except ImportError:
        pass

    if not ola:
        # 创建独立OLA实例用于数据库操作（数据库功能不需要绑定窗口）
        logger.info(f"[导入字库] 未找到绑定窗口，创建独立实例进行数据库操作")
        try:
            from plugins.adapters.ola.adapter import _OLAPlugServer, _try_import_ola, OLA_AVAILABLE
            from plugins.adapters.ola.auth import authorize_ola_instance

            if not OLA_AVAILABLE:
                _try_import_ola()

            if _OLAPlugServer:
                temp_ola_instance = _OLAPlugServer()
                auth_result = authorize_ola_instance(temp_ola_instance)
                if not auth_result.success:
                    logger.error(f"[导入字库] OLA登录失败: {auth_result.message}")
                    try:
                        temp_ola_instance.DestroyCOLAPlugInterFace()
                    except Exception:
                        pass
                    temp_ola_instance = None
                if temp_ola_instance and temp_ola_instance.CreateCOLAPlugInterFace() != 0:
                    ola = temp_ola_instance
                    temp_ola = True

                    try:
                        ola_dir = _get_ola_dir()
                        temp_ola_instance.SetPath(ola_dir)
                    except Exception:
                        pass

                    logger.info(f"[导入字库] 创建独立OLA实例成功")
                elif temp_ola_instance:
                    try:
                        temp_ola_instance.DestroyCOLAPlugInterFace()
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"[导入字库] 创建独立OLA实例失败: {e}")

    if not ola:
        logger.error(f"[导入字库] 无法创建OLA实例")
        QMessageBox.warning(
            parent, "OLA不可用",
            f"无法导入字库 '{dict_name}'：OLA插件初始化失败\n\n"
            f"大漠字库文本必须通过插件原生接口导入，请先确认OLA可用后重试"
        )
        return

    # 有OLA实例，直接导入到数据库
    try:
        db_path = _get_dict_db_path()

        # 确保数据库目录存在
        db_dir = os.path.dirname(db_path)
        if not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir, exist_ok=True)
                logger.info(f"[导入字库] 创建数据库目录: {db_dir}")
            except PermissionError as e:
                logger.error(f"[导入字库] 无权限创建目录 {db_dir}: {e}")
                QMessageBox.warning(
                    parent, "权限错误",
                    f"无法创建目录（需要管理员权限）:\n{db_dir}\n\n"
                    f"请以管理员身份运行程序后重试"
                )
                return

        # 检查目录写权限
        if not os.access(db_dir, os.W_OK):
            logger.error(f"[导入字库] 目录无写权限: {db_dir}")
            QMessageBox.warning(
                parent, "权限错误",
                f"目录无写权限（需要管理员权限）:\n{db_dir}\n\n"
                f"请以管理员身份运行程序后重试"
            )
            return

        db = _open_ola_dict_database(ola, db_path, "[导入字库]", allow_create=True)
        if db == 0:
            logger.error(f"[导入字库] 打开或初始化数据库失败: {db_path}")
            QMessageBox.warning(
                parent, "OLA数据库功能不可用",
                f"无法打开或初始化字库数据库\n\n"
                f"可能原因：\n"
                f"1. OLA未注册（试用版限制数据库功能）\n"
                f"2. 需要以管理员身份运行程序\n"
                f"3. 当前数据库文件已损坏\n\n"
                f"解决方案：\n"
                f"- 注册OLA插件\n"
                f"- 或以管理员身份重新运行"
            )
            return

        logger.info(f"[导入字库] 使用原生 InitDictFromTxt 导入: {file_path}")
        result = ola.InitDictFromTxt(db, dict_name, file_path, 1)
        if result != 1:
            db_error = ""
            try:
                db_error = (ola.GetDatabaseError(db) or "").strip()
            except Exception:
                pass

            failure_reason = db_error or "原生字库导入接口返回失败"
            logger.warning(f"[导入字库] InitDictFromTxt 失败: {failure_reason}")
            QMessageBox.critical(
                parent, "导入失败",
                f"大漠字库导入失败:\n{failure_reason}"
            )
            return

        if not os.path.exists(pending_dir):
            os.makedirs(pending_dir, exist_ok=True)

        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        export_result = ola.ExportDict(db, dict_name, output_dir)
        export_error = ""
        if export_result != 1:
            try:
                export_error = (ola.GetDatabaseError(db) or "").strip()
            except Exception:
                pass

        exported_files = []
        if export_result == 1:
            valid_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.gif'}
            exported_files = [
                path for path in glob.glob(os.path.join(output_dir, "*"))
                if os.path.splitext(path)[1].lower() in valid_extensions
            ]

        char_count = len(exported_files)
        if char_count <= 0:
            metrics = _get_dict_template_metrics(dict_name, db_path)
            if metrics:
                char_count = int(metrics.get("entry_count", 0.0) or 0)

        if char_count <= 0:
            logger.warning(f"[导入字库] 原生导入成功，但未读取到任何字符: {dict_name}")
            QMessageBox.warning(
                parent, "导入失败",
                f"字库 '{dict_name}' 导入后未检测到任何字符"
            )
            return

        if char_count > 0:
            dict_list = _load_dict_list()
            if dict_name not in dict_list:
                dict_list.append(dict_name)
                _save_dict_list(dict_list)

            if parameter_panel and hasattr(parameter_panel, 'widgets'):
                if 'dict_name' in parameter_panel.widgets:
                    parameter_panel.widgets['dict_name'].setText(dict_name)

            QMessageBox.information(
                parent, "导入成功",
                f"字库 '{dict_name}' 导入成功\n"
                f"字符数: {char_count} 个\n\n"
                + (
                    f"字符图片保存在:\n{full_output_dir}"
                    if export_result == 1
                    else f"数据库已导入成功，但字符图片缓存同步失败:\n{full_output_dir}"
                )
                + (f"\n原因: {export_error}" if export_error else "")
            )
            logger.info(
                f"[导入字库] 从大漠字库导入 '{dict_name}' 成功，字符数={char_count}, "
                f"缓存同步={'是' if export_result == 1 else '否'}"
            )

    except Exception as e:
        logger.error(f"[导入字库] 导入异常: {e}")
        QMessageBox.warning(
            parent, "导入异常",
            f"导入时发生错误: {e}"
        )
    finally:
        if db:
            try:
                ola.CloseDatabase(db)
            except Exception:
                pass

        # 清理临时OLA实例
        if temp_ola and ola:
            try:
                ola.DestroyCOLAPlugInterFace()
                logger.info("[导入字库] 已清理临时OLA实例")
            except Exception as e:
                logger.warning(f"[导入字库] 清理临时OLA实例失败: {e}")


def import_bmp_folder(params: Dict[str, Any], **kwargs) -> None:
    """从BMP文件夹导入字库"""
    from PySide6.QtWidgets import QMessageBox, QFileDialog, QInputDialog
    import os

    main_window = kwargs.get('main_window')
    parameter_panel = kwargs.get('parameter_panel')

    parent = parameter_panel if parameter_panel else main_window

    # 选择图片文件夹
    folder_path = QFileDialog.getExistingDirectory(
        parent,
        "选择字库图片文件夹（文件名即字符内容）",
        "",
        QFileDialog.ShowDirsOnly
    )

    if not folder_path:
        return

    # 检查文件夹中的图片
    valid_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.gif'}
    image_files = []
    for f in os.listdir(folder_path):
        ext = os.path.splitext(f)[1].lower()
        if ext in valid_extensions:
            image_files.append(f)

    if not image_files:
        QMessageBox.warning(parent, "无图片", "文件夹中没有找到图片文件")
        return

    # 输入字库名称
    default_name = os.path.basename(folder_path)
    dict_name, ok = QInputDialog.getText(
        parent,
        "输入字库名称",
        f"找到 {len(image_files)} 个图片\n请输入字库名称:",
        text=default_name
    )

    if not ok or not dict_name.strip():
        return

    dict_name = dict_name.strip()

    try:
        from plugins.adapters.ola.multi_instance_manager import get_ola_instance_manager
        manager = get_ola_instance_manager()

        ola = None
        window_instances = manager._window_instances if hasattr(manager, '_window_instances') else {}

        if window_instances:
            first_instance_data = list(window_instances.values())[0]
            ola = first_instance_data.get('ola') if isinstance(first_instance_data, dict) else None

        if not ola:
            # 尝试通过config.json获取绑定的窗口句柄并创建实例
            try:
                import json
                import os
                config_path = get_config_path()
                if os.path.exists(config_path):
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config_data = json.load(f)
                    bound_windows = get_bound_windows_for_mode(config_data)
                    for window_info in bound_windows:
                        if window_info.get('enabled', True):
                            hwnd = window_info.get('hwnd')
                            if hwnd and hwnd > 0:
                                ola = manager.get_instance_for_window(hwnd)
                                if ola:
                                    break
            except Exception:
                pass

        temp_ola = False
        if not ola:
            # 没有已绑定的窗口，创建临时OLA实例用于数据库操作（字库识别不需要SetPath）
            try:
                from plugins.adapters.ola.adapter import _OLAPlugServer, _try_import_ola, OLA_AVAILABLE
                from plugins.adapters.ola.auth import authorize_ola_instance

                if not OLA_AVAILABLE:
                    _try_import_ola()

                if _OLAPlugServer:
                    temp_ola_instance = _OLAPlugServer()
                    auth_result = authorize_ola_instance(temp_ola_instance)
                    if not auth_result.success:
                        logger.error(f"[从文件夹导入] OLA登录失败: {auth_result.message}")
                        try:
                            temp_ola_instance.DestroyCOLAPlugInterFace()
                        except Exception:
                            pass
                        temp_ola_instance = None
                    if temp_ola_instance and temp_ola_instance.CreateCOLAPlugInterFace() != 0:
                        # 设置工作路径（数据库操作需要，但不要求成功）
                        try:
                            ola_dir = _get_ola_dir()
                            temp_ola_instance.SetPath(ola_dir)
                            logger.info(f"[从文件夹导入] SetPath: {ola_dir}")
                        except Exception:
                            pass
                        ola = temp_ola_instance
                        temp_ola = True
                        logger.info(f"[从文件夹导入] 创建临时OLA实例用于数据库操作")
                    elif temp_ola_instance:
                        try:
                            temp_ola_instance.DestroyCOLAPlugInterFace()
                        except Exception:
                            pass
            except Exception as e:
                logger.error(f"[从文件夹导入] 创建临时OLA实例失败: {e}")

        if not ola:
            QMessageBox.critical(parent, "导入失败", "无法获取OLA插件实例")
            return

        db_path = _get_dict_db_path()

        # 确保数据库目录存在
        db_dir = os.path.dirname(db_path)
        if not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir, exist_ok=True)
                logger.info(f"[从文件夹导入] 创建数据库目录: {db_dir}")
            except PermissionError as e:
                logger.error(f"[从文件夹导入] 无权限创建目录 {db_dir}: {e}")
                QMessageBox.critical(
                    parent, "权限错误",
                    f"无法创建目录（需要管理员权限）:\n{db_dir}\n\n"
                    f"请以管理员身份运行程序后重试"
                )
                return

        db = _open_ola_dict_database(ola, db_path, "[从文件夹导入]", allow_create=True)
        if db == 0:
            logger.error(f"[从文件夹导入] 打开或初始化数据库失败: {db_path}")
            QMessageBox.critical(parent, "导入失败", f"无法打开或初始化字库数据库:\n{db_path}")
            return

        result = ola.InitDictFromDir(db, dict_name, folder_path, 1)
        ola.CloseDatabase(db)

        if result == 1:
            dict_list = _load_dict_list()
            if dict_name not in dict_list:
                dict_list.append(dict_name)
                _save_dict_list(dict_list)

            synced_preview_count = 0
            try:
                synced_preview_count = _sync_dict_preview_cache_from_dir(folder_path, dict_name)
            except Exception as cache_error:
                logger.warning(f"[从文件夹导入] 同步预览缓存失败: {cache_error}")

            if parameter_panel and hasattr(parameter_panel, 'widgets'):
                if 'dict_name' in parameter_panel.widgets:
                    parameter_panel.widgets['dict_name'].setText(dict_name)

            QMessageBox.information(
                parent, "导入成功",
                f"字库 '{dict_name}' 创建成功\n"
                f"导入了 {len(image_files)} 个字符\n"
                f"预览缓存同步 {synced_preview_count} 个字符"
            )
            logger.info(f"[从文件夹导入] 从文件夹导入 '{dict_name}'，共 {len(image_files)} 个字符")
        else:
            QMessageBox.critical(parent, "导入失败", "字库导入失败")

    except Exception as e:
        logger.error(f"[从文件夹导入] 异常: {e}")
        QMessageBox.critical(parent, "导入失败", f"导入时发生错误: {e}")
    finally:
        # 清理临时OLA实例
        if temp_ola and ola:
            try:
                ola.DestroyCOLAPlugInterFace()
                logger.info("[从文件夹导入] 已清理临时OLA实例")
            except Exception as e:
                logger.warning(f"[从文件夹导入] 清理临时OLA实例失败: {e}")


def show_dict_list(params: Dict[str, Any], **kwargs) -> None:
    """查看字库列表。"""
    open_dict_tool_dialog(params, **kwargs)


def delete_dict(params: Dict[str, Any], **kwargs) -> None:
    """删除字库。"""
    from PySide6.QtWidgets import QMessageBox

    parent = _get_dict_tool_parent(kwargs)
    dict_list = _get_available_dict_names()

    if not dict_list:
        QMessageBox.information(parent, "字库管理", "当前没有已创建的字库")
        return

    dict_name = str(kwargs.get('dict_name') or params.get('dict_name') or '').strip()
    if not dict_name:
        QMessageBox.information(parent, "删除字库", "请先在字库工具窗口中选择要删除的字库")
        return
    if dict_name not in dict_list:
        QMessageBox.warning(parent, "删除字库", f"未找到字库 '{dict_name}'")
        return

    confirm = QMessageBox.question(
        parent, "确认删除",
        f"确定要删除字库 '{dict_name}' 吗?\n此操作不可恢复!",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No
    )

    if confirm != QMessageBox.Yes:
        return

    try:
        from plugins.adapters.ola.multi_instance_manager import get_ola_instance_manager
        manager = get_ola_instance_manager()

        ola = None
        window_instances = manager._window_instances if hasattr(manager, '_window_instances') else {}
        if window_instances:
            first_instance_data = list(window_instances.values())[0]
            ola = first_instance_data.get('ola') if isinstance(first_instance_data, dict) else None

        if not ola:
            raise RuntimeError("无法获取OLA插件实例，请先绑定窗口")

        db_path = _get_dict_db_path()
        db = _open_ola_dict_database(ola, db_path, "[字库管理]", allow_create=False)
        if db == 0:
            raise RuntimeError(f"无法打开字库数据库: {db_path}")

        try:
            result = ola.RemoveDict(db, dict_name)
            if result != 1:
                db_error = ""
                try:
                    db_error = (ola.GetDatabaseError(db) or "").strip()
                except Exception:
                    pass
                raise RuntimeError(db_error or f"删除字库失败: {dict_name}")
        finally:
            try:
                ola.CloseDatabase(db)
            except Exception:
                pass
            try:
                ola.SetConfigByKey('DbPath', '')
            except Exception:
                pass

        dict_list.remove(dict_name)
        _save_dict_list(dict_list)

        QMessageBox.information(parent, "删除成功", f"字库 '{dict_name}' 已删除")
        logger.info(f"[字库管理] 字库 '{dict_name}' 已删除")

    except Exception as e:
        logger.error(f"[字库管理] 删除字库异常: {e}")
        QMessageBox.critical(parent, "删除失败", f"删除字库时发生错误: {e}")


def export_dict(params: Dict[str, Any], **kwargs) -> None:
    """导出字库。"""
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    import os

    parent = _get_dict_tool_parent(kwargs)
    dict_list = _get_available_dict_names()

    if not dict_list:
        QMessageBox.information(parent, "字库管理", "当前没有已创建的字库")
        return

    dict_name = str(kwargs.get('dict_name') or params.get('dict_name') or '').strip()
    if not dict_name:
        QMessageBox.information(parent, "导出字库", "请先在字库工具窗口中选择要导出的字库")
        return
    if dict_name not in dict_list:
        QMessageBox.warning(parent, "导出字库", f"未找到字库 '{dict_name}'")
        return

    export_dir = QFileDialog.getExistingDirectory(
        parent, "选择导出目录", "", QFileDialog.ShowDirsOnly
    )

    if not export_dir:
        return

    try:
        from plugins.adapters.ola.multi_instance_manager import get_ola_instance_manager
        manager = get_ola_instance_manager()

        ola = None
        window_instances = manager._window_instances if hasattr(manager, '_window_instances') else {}
        if window_instances:
            first_instance_data = list(window_instances.values())[0]
            ola = first_instance_data.get('ola') if isinstance(first_instance_data, dict) else None

        if not ola:
            QMessageBox.critical(parent, "导出失败", "无法获取OLA插件实例，请先绑定窗口")
            return

        db_path = _get_dict_db_path()
        db = _open_ola_dict_database(ola, db_path, "[字库管理]", allow_create=False)
        if db == 0:
            QMessageBox.critical(parent, "导出失败", "无法打开字库数据库")
            return

        target_dir = os.path.join(export_dir, dict_name)
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)

        result = ola.ExportDict(db, dict_name, target_dir)
        ola.CloseDatabase(db)

        if result == 1:
            QMessageBox.information(
                parent, "导出成功",
                f"字库 '{dict_name}' 已导出到:\n{target_dir}"
            )
            logger.info(f"[字库管理] 字库 '{dict_name}' 已导出到 {target_dir}")
        else:
            QMessageBox.critical(parent, "导出失败", "字库导出失败")

    except Exception as e:
        logger.error(f"[字库管理] 导出字库异常: {e}")
        QMessageBox.critical(parent, "导出失败", f"导出字库时发生错误: {e}")


def manage_dict(params: Dict[str, Any], **kwargs) -> None:
    """管理字库 - 查看、删除字库"""
    open_dict_tool_dialog(params, **kwargs)


def execute_task(params: Dict[str, Any], counters: Dict[str, int], execution_mode: str,
                target_hwnd: Optional[int], window_region: Optional[Tuple[int, int, int, int]],
                card_id: Optional[int] = None, **kwargs) -> Tuple[bool, str, Optional[int]]:
    """
    执行字库识别任务

    Args:
        params: 任务参数
        counters: 计数器
        execution_mode: 执行模式
        target_hwnd: 目标窗口句柄
        window_region: 窗口区域
        card_id: 卡片ID
        **kwargs: 其他参数

    Returns:
        Tuple[bool, str, Optional[int]]: (成功状态, 动作, 下一个卡片ID)
    """
    logger.info(f"========== [字库识别] execute_task 被调用 卡片ID={card_id} ==========")

    # 获取停止检查器
    stop_checker = kwargs.get('stop_checker', None)

    # 获取参数
    region_mode, region_x, region_y, region_width, region_height = resolve_region_selection_params(
        params,
        default_mode='指定区域',
    )

    # 统一转换为OLA所需的左上/右下坐标
    region_x1 = region_x
    region_y1 = region_y
    region_x2 = region_x + region_width if region_width > 0 else 0
    region_y2 = region_y + region_height if region_height > 0 else 0

    # 如果是整个窗口模式，需要获取窗口客户区大小
    use_full_window = (region_mode == '整个窗口')

    # 字库设置
    dict_name = params.get('dict_name', '')
    color_json = params.get('color_json', '')
    match_value = params.get('match_value', 0.8)

    # 目标文字设置
    target_text = params.get('target_text', '')
    match_mode = params.get('match_mode', '包含')

    # 执行后操作参数
    on_success_action = params.get('on_success', '执行下一步')
    success_jump_id = params.get('success_jump_target_id')
    on_failure_action = params.get('on_failure', '执行下一步')
    failure_jump_id = params.get('failure_jump_target_id')

    def _build_failure_result(detail: str) -> Tuple[bool, str, Optional[int], str]:
        clean_detail = str(detail or "").strip() or "字库识别失败"

        if on_failure_action == '跳转到指定步骤' and failure_jump_id is not None:
            return False, '跳转到步骤', failure_jump_id, clean_detail
        if on_failure_action == '继续执行本步骤':
            return False, '继续执行本步骤', card_id, clean_detail
        if on_failure_action == '结束工作流':
            return False, '停止工作流', None, clean_detail
        return False, '执行下一步', None, clean_detail

    if not use_full_window:
        binding_mismatch_detail = get_recorded_region_binding_mismatch_detail(params, target_hwnd)
        if binding_mismatch_detail:
            return _build_failure_result(binding_mismatch_detail)

    logger.info(f"[字库识别] 参数: 区域=({region_x1},{region_y1},{region_x2},{region_y2}), "
                f"字库={dict_name}, 颜色={color_json}, 匹配值={match_value}, "
                f"目标文字={target_text}, 匹配模式={match_mode}")

    # 检查停止信号
    if stop_checker and stop_checker():
        logger.info("[字库识别] 检测到停止信号，终止任务")
        return False, "stop", None

    try:
        import os
        import sys

        # 获取OLA实例（通过多实例管理器）
        from plugins.adapters.ola.multi_instance_manager import get_ola_instance_manager
        manager = get_ola_instance_manager()
        ola_binding_config = _get_effective_ola_binding_config(manager, target_hwnd)
        ola = manager.get_instance_for_window(target_hwnd, ola_binding_config)

        if not ola:
            logger.error("[字库识别] 无法获取OLA插件实例")
            return _build_failure_result("无法获取OLA插件实例")

        # 如果是整个窗口模式，传 0,0,0,0 即可（OLA会自动使用整个客户区）
        if use_full_window:
            region_x1 = 0
            region_y1 = 0
            region_x2 = 0
            region_y2 = 0
            logger.info(f"[字库识别] 整个窗口模式，坐标设为 (0,0,0,0) - OLA将自动使用整个客户区")

        # 获取数据库路径（使用统一的路径函数）
        db_path = _get_dict_db_path()
        ola_dir = os.path.dirname(db_path)

        # 确保目录存在
        if not os.path.exists(ola_dir):
            os.makedirs(ola_dir)

        db_handle = _open_ola_dict_database(ola, db_path, "[字库识别]", allow_create=True)
        if not db_handle or db_handle <= 0:
            logger.error(f"[字库识别] 打开或初始化数据库失败: {db_path}")
            return _build_failure_result(f"打开字库数据库失败: {db_path}")
        logger.info(f"[字库识别] 成功打开数据库: {db_path}, handle={db_handle}")

        # 设置全局数据库路径（所有OLA对象共享）
        ola.SetConfigByKey('DbPath', db_path)
        logger.info(f"[字库识别] 已设置全局数据库路径: {db_path}")

        dict_template_metrics = _get_dict_template_metrics(dict_name, db_path)
        source_image = None
        source_origin_x = region_x1
        source_origin_y = region_y1
        derived_color = None
        derived_visual_metrics = None
        can_use_image_ocr = False

        try:
            can_use_image_ocr = (
                callable(getattr(ola, "LoadImageFromRGBData", None))
                and callable(getattr(ola, "OcrFromDictPtrDetails", None))
                and any(
                    callable(getattr(ola, method_name, None))
                    for method_name in ("FreeImagePtr", "FreeImageData", "FreeImageAll")
                )
            )
        except Exception:
            can_use_image_ocr = False

        if dict_name and (not use_full_window):
            source_image, (source_origin_x, source_origin_y) = _capture_dict_ocr_image(
                target_hwnd,
                region_x1,
                region_y1,
                region_x2,
                region_y2,
            )
            if source_image is not None:
                derived_color, _, derived_visual_metrics = _analyze_dict_ocr_hints_from_image(source_image)

        # 颜色过滤参数 - 用于过滤窗口截图中的文字颜色
        # colorJson 用于从窗口截图中提取指定颜色的文字，然后再用字库模板匹配
        if color_json:
            # 用户手动指定了颜色过滤参数
            effective_color = color_json
            logger.info(f"[字库识别] 使用用户指定的颜色过滤: {effective_color}")
        elif dict_name:
            # 尝试使用字库保存的颜色（原始文字颜色）
            saved_color = _get_dict_color(dict_name)
            # 000000 是二值化后的黑色，不是原始窗口文字颜色，视为无效
            # FFFFFF 是二值化后的白色背景，也不是文字颜色，视为无效
            if saved_color and saved_color not in ("000000", "FFFFFF", ""):
                # 如果保存的是有效的原始颜色，使用它（默认偏差48）
                effective_color = f"{saved_color}-303030"
                logger.info(f"[字库识别] 使用字库保存的原始文字颜色: {saved_color}")
            elif derived_color:
                effective_color = derived_color
                logger.info(f"[字库识别] 使用当前截图推导的文字颜色: {effective_color}")
            else:
                effective_color = ""
                logger.info(f"[字库识别] 字库未保存有效颜色，本次不使用颜色过滤")
        else:
            effective_color = ""
            logger.info(f"[字库识别] 未指定字库，不使用颜色过滤")

        logger.info(f"[字库识别] 最终颜色过滤参数: '{effective_color}'")

        result_json = {}
        result_text = ""
        ocr_regions = []

        if source_image is not None and dict_name and can_use_image_ocr:
            result_json, result_text, ocr_regions, _ = _run_dict_ocr_on_image(
                ola=ola,
                image=source_image,
                dict_name=dict_name,
                effective_color=effective_color,
                match_value=match_value,
                template_metrics=dict_template_metrics,
                visual_metrics=derived_visual_metrics,
                target_text=target_text,
                match_mode=match_mode,
            )
        else:
            # 调用OLA字库识别接口（使用Details版本获取坐标信息）
            result_json = ola.OcrFromDictDetails(
                region_x1, region_y1, region_x2, region_y2,
                effective_color, dict_name, match_value
            )
            result_text, ocr_regions = _parse_dict_ocr_result(result_json)

        logger.info(f"[字库识别] 识别结果JSON: {result_json}, type={type(result_json)}")

        for region in ocr_regions:
            logger.info(
                f"[字库识别] 识别到: '{region['text']}' 坐标=({region['center_x']}, {region['center_y']}) 评分={region['score']}"
            )

        # 保存识别结果到上下文（供后续任务使用，如文字点击）
        # 使用与常规OCR相同的格式，确保文字点击功能兼容
        logger.info(f"[字库识别] 开始保存上下文，ocr_regions数量: {len(ocr_regions)}, card_id: {card_id}")
        try:
            from task_workflow.workflow_context import get_workflow_context
            context = get_workflow_context()
            logger.info(f"[字库识别] 成功获取workflow_context")

            # 转换为标准OCR结果格式（与PaddleOCR格式兼容）
            standard_ocr_results = []
            for region in ocr_regions:
                # 将字库识别结果转换为标准格式
                # bbox格式: [x1,y1, x2,y1, x2,y2, x1,y2] (四角点，从左上角顺时针)
                center_x = region.get("center_x", 0)
                center_y = region.get("center_y", 0)
                vertices = region.get("vertices", [])

                # 如果有vertices，使用它构建bbox
                if vertices and len(vertices) >= 4:
                    bbox = []
                    for v in vertices[:4]:
                        bbox.extend([v.get('x', 0), v.get('y', 0)])
                else:
                    # 没有vertices，用中心点估算一个边界框（假设文字高度20，宽度为文字数*12）
                    text = region.get("text", "")
                    half_width = max(len(text) * 6, 10)
                    half_height = 10
                    bbox = [
                        center_x - half_width, center_y - half_height,  # 左上
                        center_x + half_width, center_y - half_height,  # 右上
                        center_x + half_width, center_y + half_height,  # 右下
                        center_x - half_width, center_y + half_height   # 左下
                    ]

                standard_result = {
                    "text": region.get("text", ""),
                    "confidence": region.get("score", 0.0),
                    "bbox": bbox
                }
                standard_ocr_results.append(standard_result)

            region_offset_x = source_origin_x if source_image is not None else region_x1
            region_offset_y = source_origin_y if source_image is not None else region_y1
            snapshot_fn = getattr(context, "set_ocr_result_snapshot", None)
            if callable(snapshot_fn):
                snapshot_fn(
                    card_id,
                    standard_ocr_results,
                    target_text=target_text,
                    match_mode=match_mode,
                    region_offset=(region_offset_x, region_offset_y),
                    window_hwnd=target_hwnd,
                )

            if standard_ocr_results:
                # 使用标准OCR结果存储接口
                context.set_ocr_results(card_id, standard_ocr_results)

                # 设置目标文字和匹配模式（供文字点击使用）
                context.set_card_data(card_id, 'ocr_target_text', target_text)
                context.set_card_data(card_id, 'ocr_match_mode', match_mode)
                context.set_card_data(card_id, 'ocr_region_offset', (region_offset_x, region_offset_y))
                context.set_card_data(card_id, 'ocr_window_hwnd', target_hwnd)
                context.set_card_data(card_id, 'is_dict_ocr', True)  # 标记为字库识别

                logger.info(f"[字库识别] 已保存识别结果到上下文，共 {len(standard_ocr_results)} 个区域")
        except Exception as e:
            logger.error(f"[字库识别] 保存结果到上下文失败: {e}")
            import traceback
            logger.error(traceback.format_exc())

        # 判断识别结果
        success = False
        failure_detail = ""
        if result_text:
            if not target_text:
                # 没有指定目标文字，只要识别到文字就算成功
                success = True
                logger.info(f"[字库识别] 识别到文字: {result_text}")
            else:
                success, failure_detail = _evaluate_dict_match_result(result_text, target_text, match_mode)

                if success:
                    logger.info(f"[字库识别] 文字匹配成功: '{target_text}' in '{result_text}'")
                else:
                    logger.info(f"[字库识别] 文字匹配失败: '{target_text}' not in '{result_text}'")
                    if not failure_detail:
                        failure_detail = f"目标文字不匹配，期望='{target_text}'，实际识别='{result_text}'"
        else:
            logger.info("[字库识别] 未识别到任何文字")
            if dict_name:
                failure_detail = f"字库 '{dict_name}' 未识别到任何文字"
            else:
                failure_detail = "未识别到任何文字"

        # 保存识别结果到变量
        save_to_variable = params.get('save_to_variable', '')
        if save_to_variable and result_text:
            try:
                from task_workflow.workflow_context import get_workflow_context
                context = get_workflow_context()
                context.set_global_var(save_to_variable, result_text, card_id=card_id)
                logger.info(f"[字库识别] 已保存识别结果到变量 '{save_to_variable}': {result_text}")
            except Exception as e:
                logger.error(f"[字库识别] 保存结果到变量失败: {e}")

        # 关闭数据库
        try:
            ola.CloseDatabase(db_handle)
            logger.info(f"[字库识别] 已关闭数据库")
        except Exception as e:
            logger.warning(f"[字库识别] 关闭数据库异常: {e}")

        # 根据结果返回（与OCR任务保持一致的返回格式）
        if success:
            logger.info(
                f"[字库识别] 识别成功: 结果='{result_text}', "
                f"目标='{target_text or '任意文字'}', 匹配模式={match_mode}, 区域数={len(ocr_regions)}"
            )
            if on_success_action == '跳转到指定步骤' and success_jump_id is not None:
                return True, '跳转到步骤', success_jump_id
            elif on_success_action == '继续执行本步骤':
                return True, '跳转到步骤', card_id
            elif on_success_action == '结束工作流':
                return True, '停止工作流', None
            else:
                return True, '执行下一步', None
        else:
            return _build_failure_result(failure_detail)

    except Exception as e:
        logger.error(f"[字库识别] 执行异常: {e}")
        import traceback
        traceback.print_exc()
        return _build_failure_result(str(e))


def get_params_definition() -> Dict[str, Dict[str, Any]]:
    """获取参数定义"""
    return {
        "---region_settings---": {"type": "separator", "label": "识别区域设置"},
        "region_mode": {
            "label": "区域模式",
            "type": "select",
            "options": ["指定区域", "整个窗口"],
            "default": "指定区域",
            "tooltip": "选择识别区域范围，整个窗口时坐标参数无效"
        },

        "---coordinate_mode---": {
            "type": "separator",
            "label": "指定区域模式",
            "condition": {"param": "region_mode", "value": "指定区域"}
        },
        "ocr_region_selector_tool": {
            "label": "框选识别区域",
            "type": "button",
            "button_text": "框选识别指定区域",
            "tooltip": "点击后在绑定窗口中框选识别区域",
            "condition": {"param": "region_mode", "value": "指定区域"},
            "widget_hint": "ocr_region_selector"
        },
        "region_coordinates": {
            "label": "指定的区域",
            "type": "text",
            "default": "未指定识别区域",
            "readonly": True,
            "tooltip": "显示当前选择的识别区域坐标（由框选工具自动设置）",
            "condition": {"param": "region_mode", "value": "指定区域"}
        },
        # 隐藏的坐标参数
        "region_x": {
            "type": "hidden",
            "default": 0,
            "condition": {"param": "region_mode", "value": "指定区域"}
        },
        "region_y": {
            "type": "hidden",
            "default": 0,
            "condition": {"param": "region_mode", "value": "指定区域"}
        },
        "region_width": {
            "type": "hidden",
            "default": 0,
            "condition": {"param": "region_mode", "value": "指定区域"}
        },
        "region_height": {
            "type": "hidden",
            "default": 0,
            "condition": {"param": "region_mode", "value": "指定区域"}
        },
        "region_hwnd": {
            "type": "hidden",
            "default": 0,
            "condition": {"param": "region_mode", "value": "指定区域"}
        },
        "region_window_title": {
            "type": "hidden",
            "default": "",
            "condition": {"param": "region_mode", "value": "指定区域"}
        },
        "region_window_class": {
            "type": "hidden",
            "default": "",
            "condition": {"param": "region_mode", "value": "指定区域"}
        },
        "region_client_width": {
            "type": "hidden",
            "default": 0,
            "condition": {"param": "region_mode", "value": "指定区域"}
        },
        "region_client_height": {
            "type": "hidden",
            "default": 0,
            "condition": {"param": "region_mode", "value": "指定区域"}
        },

        "---dict_settings---": {"type": "separator", "label": "字库设置"},
        "dict_tool_button": {
            "label": "字库工具",
            "type": "button",
            "button_text": "字库工具",
            "tooltip": "打开独立字库工具窗口",
            "action": "open_dict_tool_dialog",
        },
        "dict_name": {
            "label": "字库名称",
            "type": "str",
            "default": "",
            "tooltip": "字库名称，留空搜索所有字库。多个字库用|分割，如: dict1|dict2"
        },
        "color_json": {
            "label": "颜色过滤",
            "type": "str",
            "default": "",
            "tooltip": "窗口中实际文字的颜色，用于从截图中提取指定颜色文字\n格式: 颜色-偏差，如 FFFFFF-303030（白色文字，偏差48）\n留空时优先使用字库保存颜色，缺失时自动根据截图推导"
        },
        "match_value": {
            "label": "匹配阈值",
            "type": "float",
            "default": 0.8,
            "min": 0.1,
            "max": 1.0,
            "step": 0.05,
            "tooltip": "字库匹配阈值，越高越严格，建议0.8-0.9，范围0.1-1.0"
        },

        "---target_text---": {"type": "separator", "label": "目标文字设置"},
        "target_text": {
            "label": "目标文字",
            "type": "str",
            "default": "",
            "tooltip": "要匹配的目标文字，留空则识别到任意文字即为成功"
        },
        "match_mode": {
            "label": "匹配模式",
            "type": "select",
            "options": ["包含", "完全匹配", "正则匹配"],
            "default": "包含",
            "tooltip": "文字匹配模式：包含、完全匹配、正则匹配"
        },

        "---variable_settings---": {"type": "separator", "label": "变量输出"},
        "save_to_variable": {
            "label": "保存为变量",
            "type": "str",
            "default": "",
            "tooltip": "将识别结果保存到指定变量名，留空则不保存"
        },

        "---action_settings---": {"type": "separator", "label": "执行后操作"},
        "on_success": {
            "label": "识别成功时",
            "type": "select",
            "options": ["执行下一步", "跳转到指定步骤", "继续执行本步骤", "结束工作流"],
            "default": "执行下一步",
            "tooltip": "文字识别成功后的操作"
        },
        "success_jump_target_id": {
            "label": "成功跳转目标",
            "type": "int",
            "default": None,
            "required": False,
            "widget_hint": "card_selector",
            "tooltip": "识别成功时跳转到的目标卡片",
            "condition": {"param": "on_success", "value": "跳转到指定步骤"}
        },
        "on_failure": {
            "label": "识别失败时",
            "type": "select",
            "options": ["执行下一步", "跳转到指定步骤", "继续执行本步骤", "结束工作流"],
            "default": "执行下一步",
            "tooltip": "文字识别失败后的操作"
        },
        "failure_jump_target_id": {
            "label": "失败跳转目标",
            "type": "int",
            "default": None,
            "required": False,
            "widget_hint": "card_selector",
            "tooltip": "识别失败时跳转到的目标卡片",
            "condition": {"param": "on_failure", "value": "跳转到指定步骤"}
        },
    }
