# -*- coding: utf-8 -*-
"""
Native Adapter - 纯 Python 原生插件适配器

替代 OLA 欧拉插件，使用开源 Python 库实现所有 RPA 功能：
- 图像识别: pyautogui + opencv-python (找图/找色/截图)
- 鼠标控制: pyautogui + pynput (移动/点击/拖拽/滚轮)
- 键盘控制: pyautogui + pynput (按键/输入文字/组合键)
- OCR识别: paddleocr (文字识别/查找文字)
- 窗口操作: pywin32 + pygetwindow (绑定/枚举/调整大小)

兼容 OLAAdapter 的全部接口，可直接替换。
"""

import logging
import time
import threading
from typing import List, Tuple, Optional, Any

from plugins.core.interface import (
    IPluginAdapter, IImagePlugin, IInputPlugin, IOCRPlugin,
    PluginCapability
)

logger = logging.getLogger(__name__)

# ========== 依赖检查 ==========

def _check_dependencies():
    """检查并报告依赖库的安装状态"""
    deps = {}
    try:
        import pyautogui
        deps['pyautogui'] = pyautogui.__version__
    except ImportError:
        deps['pyautogui'] = None

    try:
        import cv2
        deps['opencv-python'] = cv2.__version__
    except ImportError:
        deps['opencv-python'] = None

    try:
        import numpy
        deps['numpy'] = numpy.__version__
    except ImportError:
        deps['numpy'] = None

    try:
        import pynput
        deps['pynput'] = getattr(pynput, '__version__', 'installed')
    except ImportError:
        deps['pynput'] = None

    try:
        import win32gui
        deps['pywin32'] = 'installed'
    except ImportError:
        deps['pywin32'] = None

    try:
        import pygetwindow
        deps['pygetwindow'] = getattr(pygetwindow, '__version__', 'installed')
    except ImportError:
        deps['pygetwindow'] = None

    # OCR 是可选依赖（首次使用时自动下载模型）
    try:
        import paddleocr
        deps['paddleocr'] = paddleocr.__version__
    except ImportError:
        deps['paddleocr'] = None
        logger.info("paddleocr 未安装，OCR 功能将在首次使用时自动下载")

    return deps


def _get_missing_deps(deps: dict) -> List[str]:
    """获取未安装的必选依赖列表"""
    required = ['pyautogui', 'opencv-python', 'numpy', 'pynput', 'pywin32']
    missing = []
    for name in required:
        if deps.get(name) is None:
            missing.append(name)
    return missing


# ========== 鼠标轨迹模拟 ==========

def _human_like_move(x1: int, y1: int, x2: int, y2: int, duration: float = 0.3):
    """
    模拟人类鼠标移动轨迹（贝塞尔曲线）
    
    Args:
        x1, y1: 起点
        x2, y2: 终点
        duration: 移动持续时间（秒）
    """
    try:
        import pyautogui
        import random
        import math
        
        # 计算距离
        distance = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        
        # 短距离直接移动
        if distance < 50:
            pyautogui.moveTo(x2, y2, duration=min(duration, 0.1))
            return
        
        # 贝塞尔曲线控制点（随机偏移，模拟人类不精确性）
        steps = max(int(distance / 5), 20)
        control_x = (x1 + x2) / 2 + random.uniform(-distance * 0.2, distance * 0.2)
        control_y = (y1 + y2) / 2 + random.uniform(-distance * 0.2, distance * 0.2)
        
        # 二次贝塞尔曲线
        for i in range(steps + 1):
            t = i / steps
            # 贝塞尔公式: (1-t)^2 * P0 + 2t(1-t) * P1 + t^2 * P2
            bx = (1 - t) ** 2 * x1 + 2 * t * (1 - t) * control_x + t ** 2 * x2
            by = (1 - t) ** 2 * y1 + 2 * t * (1 - t) * control_y + t ** 2 * y2
            
            # 添加微小随机抖动
            jitter_x = random.uniform(-1, 1)
            jitter_y = random.uniform(-1, 1)
            
            pyautogui.moveTo(int(bx + jitter_x), int(by + jitter_y))
            time.sleep(duration / steps)
        
        # 确保最终到达目标
        pyautogui.moveTo(x2, y2, duration=0.05)
        
    except Exception as e:
        logger.warning(f"轨迹移动失败，使用直接移动: {e}")
        try:
            import pyautogui
            pyautogui.moveTo(x2, y2, duration=duration)
        except Exception:
            pass


# ========== 核心适配器 ==========

class NativeAdapter(IImagePlugin, IInputPlugin, IOCRPlugin):
    """
    纯 Python 原生插件适配器
    
    功能覆盖:
    - 图像识别（找图、找色、多点找色、截图、获取颜色）
    - 鼠标操作（移动、点击、按下、释放、拖拽、滚轮）
    - 键盘操作（按键、按下、释放、输入文字、组合键）
    - OCR识别（文字识别、查找文字位置）
    - 窗口操作（绑定、解绑、查找、枚举、信息、调整大小）
    
    与 OLAAdapter 完全兼容，配置中把 plugin_name 从 "ola" 改为 "native" 即可。
    """

    def __init__(self, use_human_like_mouse: bool = False):
        """
        初始化 Native 适配器
        
        Args:
            use_human_like_mouse: 是否使用类人鼠标移动轨迹（贝塞尔曲线）
        """
        self._initialized = False
        self._bound_hwnd = None
        self._use_human_like_mouse = use_human_like_mouse
        self._mouse_controller = None
        self._keyboard_controller = None
        self._ocr_engine = None
        self._lock = threading.RLock()

    # ---------- 基础信息 ----------

    def get_name(self) -> str:
        return "Native"

    def get_version(self) -> str:
        return "1.0.0"

    def get_capabilities(self) -> List[PluginCapability]:
        """返回支持的全部能力"""
        return [
            # 图像识别
            PluginCapability.IMAGE_FIND_PIC,
            PluginCapability.IMAGE_FIND_COLOR,
            PluginCapability.IMAGE_FIND_MULTI_COLOR,
            PluginCapability.IMAGE_CAPTURE,
            PluginCapability.IMAGE_GET_COLOR,

            # 鼠标操作
            PluginCapability.MOUSE_MOVE,
            PluginCapability.MOUSE_CLICK,
            PluginCapability.MOUSE_DOWN,
            PluginCapability.MOUSE_UP,
            PluginCapability.MOUSE_DRAG,
            PluginCapability.MOUSE_SCROLL,

            # 键盘操作
            PluginCapability.KEYBOARD_PRESS,
            PluginCapability.KEYBOARD_DOWN,
            PluginCapability.KEYBOARD_UP,
            PluginCapability.KEYBOARD_INPUT_TEXT,
            PluginCapability.KEYBOARD_COMBINATION,

            # OCR识别
            PluginCapability.OCR_TEXT,
            PluginCapability.OCR_FIND_TEXT,

            # 窗口操作
            PluginCapability.WINDOW_BIND,
            PluginCapability.WINDOW_UNBIND,
            PluginCapability.WINDOW_FIND,
            PluginCapability.WINDOW_ENUM,
            PluginCapability.WINDOW_INFO,
            PluginCapability.WINDOW_RESIZE,
        ]

    # ---------- 初始化/释放 ----------

    def initialize(self, config: dict) -> bool:
        """
        初始化原生插件
        
        Args:
            config: 配置字典
                - use_human_like_mouse: 是否使用类人鼠标轨迹 (默认False)
                - ocr_language: OCR语言 (默认"ch"中英文)
                - ocr_use_gpu: OCR是否使用GPU (默认False)
        """
        try:
            # 检查依赖
            deps = _check_dependencies()
            missing = _get_missing_deps(deps)
            if missing:
                logger.error(f"缺少必要依赖: {missing}")
                logger.info(f"请运行: pip install {' '.join(missing)}")
                return False

            # 记录依赖状态
            logger.info("=== Native 插件依赖状态 ===")
            for name, ver in deps.items():
                status = f"v{ver}" if ver else "未安装"
                logger.info(f"  {name}: {status}")

            # 读取配置
            self._use_human_like_mouse = config.get('use_human_like_mouse', False)
            ocr_language = config.get('ocr_language', 'ch')
            ocr_use_gpu = config.get('ocr_use_gpu', False)

            # 初始化鼠标控制器
            try:
                from pynput.mouse import Controller as MouseController
                self._mouse_controller = MouseController()
                logger.info("鼠标控制器初始化成功")
            except Exception as e:
                logger.warning(f"pynput 鼠标控制器初始化失败: {e}，将使用 pyautogui")

            # 初始化键盘控制器
            try:
                from pynput.keyboard import Controller as KeyboardController
                self._keyboard_controller = KeyboardController()
                logger.info("键盘控制器初始化成功")
            except Exception as e:
                logger.warning(f"pynput 键盘控制器初始化失败: {e}，将使用 pyautogui")

            # 初始化 OCR（延迟加载，首次使用时才初始化）
            self._ocr_config = {
                'language': ocr_language,
                'use_gpu': ocr_use_gpu,
            }
            logger.info(f"OCR配置: language={ocr_language}, use_gpu={ocr_use_gpu}")

            self._initialized = True
            logger.info(f"Native 插件初始化成功，版本: {self.get_version()}")
            return True

        except Exception as e:
            logger.error(f"Native 插件初始化失败: {e}", exc_info=True)
            return False

    def _ensure_ocr(self):
        """延迟初始化 OCR 引擎"""
        if self._ocr_engine is not None:
            return True

        try:
            from paddleocr import PaddleOCR
            logger.info("正在初始化 PaddleOCR 引擎（首次使用，可能需要几秒）...")
            self._ocr_engine = PaddleOCR(
                use_angle_cls=True,
                lang=self._ocr_config.get('language', 'ch'),
                use_gpu=self._ocr_config.get('use_gpu', False),
                show_log=False,
            )
            logger.info("PaddleOCR 引擎初始化成功")
            return True
        except Exception as e:
            logger.error(f"PaddleOCR 初始化失败: {e}")
            logger.info("请运行: pip install paddleocr paddlepaddle")
            return False

    def release(self) -> bool:
        """释放资源"""
        try:
            self._initialized = False
            self._mouse_controller = None
            self._keyboard_controller = None
            self._ocr_engine = None
            self._bound_hwnd = None
            logger.info("Native 插件资源已释放")
            return True
        except Exception as e:
            logger.error(f"Native 插件释放失败: {e}", exc_info=True)
            return False

    def health_check(self) -> bool:
        """健康检查"""
        return self._initialized

    def execute(self, capability: PluginCapability, method: str, *args, **kwargs) -> Any:
        """
        执行插件操作（通用接口）
        """
        if not self.health_check():
            raise RuntimeError("Native 插件未初始化或不可用")

        method_map = {
            # 图像
            'find_pic': self.find_pic,
            'find_pic_ex': self.find_pic_ex,
            'find_color': self.find_color,
            'find_multi_color': self.find_multi_color,
            'get_color': self.get_color,
            'capture': self.capture,
            # 鼠标
            'mouse_move': self.mouse_move,
            'mouse_click': self.mouse_click,
            'mouse_down': self.mouse_down,
            'mouse_up': self.mouse_up,
            'mouse_double_click': self.mouse_double_click,
            'mouse_drag': self.mouse_drag,
            'mouse_scroll': self.mouse_scroll,
            # 键盘
            'key_press': self.key_press,
            'key_down': self.key_down,
            'key_up': self.key_up,
            'key_input_text': self.key_input_text,
            # OCR
            'ocr': self.ocr,
            'find_text': self.find_text,
            # 窗口
            'bind_window': self.bind_window,
            'unbind_window': self.unbind_window,
            'find_window': self.find_window,
            'enum_window': self.enum_window,
            'get_window_title': self.get_window_title,
        }

        func = method_map.get(method)
        if func:
            return func(*args, **kwargs)
        else:
            raise NotImplementedError(f"Native 插件不支持方法: {method}")

    # ========== IImagePlugin 实现 ==========

    def bind_window(self, hwnd: int, display_mode: str = "normal",
                    mouse_mode: str = "normal", keypad_mode: str = "normal",
                    mode: int = 0, input_lock: bool = False,
                    activate_foreground: bool = False,
                    mouse_move_with_trajectory: bool = False,
                    pubstr: str = "") -> bool:
        """
        绑定窗口
        
        Native 适配器中，"绑定"意味着记录目标窗口句柄，
        后续操作（鼠标点击/OCR等）将针对该窗口的客户区坐标进行。
        """
        try:
            if not hwnd or hwnd == 0:
                logger.error("[Native] 窗口句柄无效")
                return False

            import win32gui
            if not win32gui.IsWindow(hwnd):
                logger.error(f"[Native] 窗口句柄 {hwnd} 对应的窗口不存在")
                return False

            self._bound_hwnd = hwnd
            self._use_human_like_mouse = mouse_move_with_trajectory

            # 前台模式：激活窗口
            if activate_foreground:
                try:
                    win32gui.SetForegroundWindow(hwnd)
                    time.sleep(0.1)
                    logger.info(f"[Native] 激活窗口到前台: {hwnd}")
                except Exception as e:
                    logger.warning(f"[Native] 激活窗口失败: {e}")

            title = win32gui.GetWindowText(hwnd)
            logger.info(f"[Native] 窗口绑定成功: hwnd={hwnd}, title='{title}'")
            return True

        except ImportError:
            logger.error("[Native] pywin32 未安装，无法绑定窗口")
            return False
        except Exception as e:
            logger.error(f"[Native] 绑定窗口异常: {e}", exc_info=True)
            return False

    def unbind_window(self) -> bool:
        """解绑窗口"""
        self._bound_hwnd = None
        logger.info("[Native] 窗口已解绑")
        return True

    def _screen_to_client(self, x: int, y: int, hwnd: int = None) -> Tuple[int, int]:
        """屏幕坐标转客户区坐标"""
        target_hwnd = hwnd or self._bound_hwnd
        if not target_hwnd:
            return x, y
        try:
            import ctypes
            from ctypes import wintypes
            point = wintypes.POINT(x, y)
            if ctypes.windll.user32.ScreenToClient(target_hwnd, ctypes.byref(point)):
                return point.x, point.y
        except Exception:
            pass
        return x, y

    def _client_to_screen(self, x: int, y: int, hwnd: int = None) -> Tuple[int, int]:
        """客户区坐标转屏幕坐标"""
        target_hwnd = hwnd or self._bound_hwnd
        if not target_hwnd:
            return x, y
        try:
            import ctypes
            from ctypes import wintypes
            point = wintypes.POINT(x, y)
            if ctypes.windll.user32.ClientToScreen(target_hwnd, ctypes.byref(point)):
                return point.x, point.y
        except Exception:
            pass
        return x, y

    def find_pic(self, x1: int, y1: int, x2: int, y2: int,
                 pic_name: str, similarity: float = 0.9) -> Optional[Tuple[int, int]]:
        """
        在指定区域查找图片
        
        Args:
            x1, y1: 搜索区域左上角（屏幕坐标）
            x2, y2: 搜索区域右下角（屏幕坐标）
            pic_name: 图片文件路径
            similarity: 相似度阈值 (0.0-1.0)
        
        Returns:
            找到返回 (x, y) 中心坐标，未找到返回 None
        """
        try:
            import cv2
            import numpy as np
            import pyautogui

            # 截取搜索区域
            region = (x1, y1, x2 - x1, y2 - y1)
            screenshot = pyautogui.screenshot(region=region)
            screenshot_cv = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

            # 读取模板图片
            template = cv2.imread(pic_name)
            if template is None:
                logger.error(f"[Native] 无法读取图片: {pic_name}")
                return None

            # 模板匹配
            result = cv2.matchTemplate(screenshot_cv, template, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

            if max_val >= similarity:
                # 计算中心坐标（屏幕坐标）
                center_x = x1 + max_loc[0] + template.shape[1] // 2
                center_y = y1 + max_loc[1] + template.shape[0] // 2
                logger.debug(f"[Native] 找图成功: {pic_name}, 相似度={max_val:.3f}, 位置=({center_x}, {center_y})")
                return (center_x, center_y)
            else:
                logger.debug(f"[Native] 找图未命中: {pic_name}, 最高相似度={max_val:.3f}")
                return None

        except Exception as e:
            logger.error(f"[Native] 找图异常: {e}", exc_info=True)
            return None

    def find_pic_ex(self, x1: int, y1: int, x2: int, y2: int,
                    pic_name: str, similarity: float = 0.9) -> List[Tuple[int, int]]:
        """
        查找区域内所有匹配的图片位置
        
        Returns:
            所有匹配位置的坐标列表
        """
        try:
            import cv2
            import numpy as np
            import pyautogui

            region = (x1, y1, x2 - x1, y2 - y1)
            screenshot = pyautogui.screenshot(region=region)
            screenshot_cv = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

            template = cv2.imread(pic_name)
            if template is None:
                return []

            result = cv2.matchTemplate(screenshot_cv, template, cv2.TM_CCOEFF_NORMED)
            locations = np.where(result >= similarity)

            matches = []
            w, h = template.shape[1], template.shape[0]
            for pt in zip(*locations[::-1]):
                center_x = x1 + pt[0] + w // 2
                center_y = y1 + pt[1] + h // 2
                matches.append((center_x, center_y))

            # 去重（合并相近的点）
            if matches:
                filtered = [matches[0]]
                for m in matches[1:]:
                    too_close = False
                    for f in filtered:
                        if abs(m[0] - f[0]) < w // 2 and abs(m[1] - f[1]) < h // 2:
                            too_close = True
                            break
                    if not too_close:
                        filtered.append(m)
                matches = filtered

            logger.debug(f"[Native] find_pic_ex: 找到 {len(matches)} 个匹配")
            return matches

        except Exception as e:
            logger.error(f"[Native] find_pic_ex 异常: {e}", exc_info=True)
            return []

    def find_color(self, x1: int, y1: int, x2: int, y2: int,
                   color: str, similarity: float = 1.0) -> Optional[Tuple[int, int]]:
        """
        在指定区域查找颜色
        
        Args:
            color: 颜色值，格式 "RRGGBB"（如 "FF0000" 红色）
            similarity: 颜色相似度 (0.0-1.0, 1.0=完全匹配)
        
        Returns:
            找到返回 (x, y)，未找到返回 None
        """
        try:
            import cv2
            import numpy as np
            import pyautogui

            # 解析颜色 (注意 OpenCV 是 BGR)
            color = color.lstrip('#')
            if len(color) != 6:
                logger.error(f"[Native] 颜色格式错误: {color}")
                return None
            r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
            target_bgr = np.array([b, g, r])

            # 截取区域
            region = (x1, y1, x2 - x1, y2 - y1)
            screenshot = pyautogui.screenshot(region=region)
            img = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

            # 计算颜色距离
            if similarity >= 1.0:
                # 精确匹配
                mask = np.all(img == target_bgr, axis=2)
            else:
                # 模糊匹配
                diff = np.sqrt(np.sum((img.astype(float) - target_bgr) ** 2, axis=2))
                max_diff = (1.0 - similarity) * 441.67  # 最大可能距离
                mask = diff <= max_diff

            coords = np.where(mask)
            if len(coords[0]) > 0:
                # 返回第一个匹配点
                px = x1 + int(coords[1][0])
                py = y1 + int(coords[0][0])
                logger.debug(f"[Native] 找色成功: {color} at ({px}, {py})")
                return (px, py)

            logger.debug(f"[Native] 找色未命中: {color}")
            return None

        except Exception as e:
            logger.error(f"[Native] 找色异常: {e}", exc_info=True)
            return None

    def get_color(self, x: int, y: int) -> str:
        """
        获取指定坐标的颜色值
        
        Returns:
            颜色值，格式 "RRGGBB"
        """
        try:
            import pyautogui
            # 截取 1x1 像素
            pixel = pyautogui.screenshot(region=(x, y, 1, 1)).getpixel((0, 0))
            return f"{pixel[0]:02X}{pixel[1]:02X}{pixel[2]:02X}"
        except Exception as e:
            logger.error(f"[Native] 获取颜色异常: {e}")
            return ""

    def capture(self, x1: int, y1: int, x2: int, y2: int) -> Any:
        """
        截取屏幕区域
        
        Returns:
            PIL.Image 对象
        """
        try:
            import pyautogui
            region = (x1, y1, x2 - x1, y2 - y1)
            return pyautogui.screenshot(region=region)
        except Exception as e:
            logger.error(f"[Native] 截图异常: {e}")
            return None

    # ========== IInputPlugin 实现（鼠标）==========

    def _move_mouse(self, x: int, y: int):
        """移动鼠标到指定坐标"""
        if self._use_human_like_mouse:
            try:
                import pyautogui
                current_x, current_y = pyautogui.position()
                _human_like_move(int(current_x), int(current_y), x, y, duration=0.3)
            except Exception:
                import pyautogui
                pyautogui.moveTo(x, y)
        else:
            if self._mouse_controller:
                self._mouse_controller.position = (x, y)
            else:
                import pyautogui
                pyautogui.moveTo(x, y)

    def mouse_move(self, x: int, y: int, hwnd: int = None) -> bool:
        """移动鼠标"""
        try:
            # 如果是客户区坐标，转换为屏幕坐标
            if hwnd and self._bound_hwnd:
                x, y = self._client_to_screen(x, y, hwnd)
            self._move_mouse(x, y)
            return True
        except Exception as e:
            logger.error(f"[Native] 鼠标移动异常: {e}")
            return False

    def mouse_click(self, x: int, y: int, button: str = "left",
                    is_screen_coord: bool = False, hwnd: int = None) -> bool:
        """
        鼠标点击
        
        Args:
            x, y: 坐标
            button: "left" / "right" / "middle"
            is_screen_coord: 是否为屏幕坐标（False 表示客户区坐标）
            hwnd: 目标窗口句柄
        """
        try:
            # 坐标转换
            target_x, target_y = x, y
            if not is_screen_coord and (hwnd or self._bound_hwnd):
                target_x, target_y = self._client_to_screen(x, y, hwnd or self._bound_hwnd)

            self._move_mouse(target_x, target_y)
            time.sleep(0.02)

            # 执行点击
            if self._mouse_controller:
                from pynput.mouse import Button
                btn_map = {"left": Button.left, "right": Button.right, "middle": Button.middle}
                btn = btn_map.get(button, Button.left)
                self._mouse_controller.click(btn)
            else:
                import pyautogui
                pyautogui.click(button=button)

            logger.debug(f"[Native] 鼠标点击: ({target_x}, {target_y}), button={button}")
            return True

        except Exception as e:
            logger.error(f"[Native] 鼠标点击异常: {e}", exc_info=True)
            return False

    def mouse_double_click(self, x: int, y: int, button: str = "left",
                           hwnd: int = None) -> bool:
        """鼠标双击"""
        try:
            target_x, target_y = x, y
            if hwnd or self._bound_hwnd:
                target_x, target_y = self._client_to_screen(x, y, hwnd or self._bound_hwnd)

            self._move_mouse(target_x, target_y)
            time.sleep(0.02)

            if self._mouse_controller:
                from pynput.mouse import Button
                btn = Button.left if button == "left" else Button.right
                self._mouse_controller.click(btn, 2)
            else:
                import pyautogui
                pyautogui.doubleClick(x=target_x, y=target_y, button=button)

            return True
        except Exception as e:
            logger.error(f"[Native] 鼠标双击异常: {e}", exc_info=True)
            return False

    def mouse_drag(self, x1: int, y1: int, x2: int, y2: int,
                   duration: float = 1.0, button: str = "left",
                   hwnd: int = None) -> bool:
        """
        鼠标拖拽
        """
        try:
            import math
            import pyautogui

            # 坐标转换
            if hwnd or self._bound_hwnd:
                x1, y1 = self._client_to_screen(x1, y1, hwnd or self._bound_hwnd)
                x2, y2 = self._client_to_screen(x2, y2, hwnd or self._bound_hwnd)

            # 移动到起点
            self._move_mouse(x1, y1)
            time.sleep(0.02)

            # 按下 + 平滑移动 + 释放
            btn = button  # pyautogui 使用字符串
            pyautogui.mouseDown(button=btn)

            distance = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
            steps = max(int(distance / 10), 10)
            step_dur = duration / steps

            for i in range(1, steps + 1):
                t = i / steps
                cx = int(x1 + (x2 - x1) * t)
                cy = int(y1 + (y2 - y1) * t)
                pyautogui.moveTo(cx, cy)
                time.sleep(step_dur)

            pyautogui.mouseUp(button=btn)
            logger.debug(f"[Native] 拖拽完成: ({x1},{y1})->({x2},{y2})")
            return True

        except Exception as e:
            logger.error(f"[Native] 鼠标拖拽异常: {e}", exc_info=True)
            try:
                import pyautogui
                pyautogui.mouseUp()
            except Exception:
                pass
            return False

    def mouse_down(self, x: int, y: int, button: str = "left",
                   is_screen_coord: bool = False, hwnd: int = None) -> bool:
        """鼠标按下（不释放）"""
        try:
            target_x, target_y = x, y
            if not is_screen_coord and (hwnd or self._bound_hwnd):
                target_x, target_y = self._client_to_screen(x, y, hwnd or self._bound_hwnd)

            self._move_mouse(target_x, target_y)
            time.sleep(0.02)

            if self._mouse_controller:
                from pynput.mouse import Button
                btn_map = {"left": Button.left, "right": Button.right, "middle": Button.middle}
                self._mouse_controller.press(btn_map.get(button, Button.left))
            else:
                import pyautogui
                pyautogui.mouseDown(button=button)

            return True
        except Exception as e:
            logger.error(f"[Native] 鼠标按下异常: {e}", exc_info=True)
            return False

    def mouse_up(self, x: int, y: int, button: str = "left",
                 is_screen_coord: bool = False, hwnd: int = None) -> bool:
        """鼠标释放"""
        try:
            target_x, target_y = x, y
            if not is_screen_coord and (hwnd or self._bound_hwnd):
                target_x, target_y = self._client_to_screen(x, y, hwnd or self._bound_hwnd)

            self._move_mouse(target_x, target_y)
            time.sleep(0.02)

            if self._mouse_controller:
                from pynput.mouse import Button
                btn_map = {"left": Button.left, "right": Button.right, "middle": Button.middle}
                self._mouse_controller.release(btn_map.get(button, Button.left))
            else:
                import pyautogui
                pyautogui.mouseUp(button=button)

            return True
        except Exception as e:
            logger.error(f"[Native] 鼠标释放异常: {e}", exc_info=True)
            return False

    def mouse_scroll(self, x: int, y: int, delta: int, hwnd: int = None) -> bool:
        """
        鼠标滚轮
        
        Args:
            delta: 正数向上，负数向下
        """
        try:
            target_x, target_y = x, y
            if hwnd or self._bound_hwnd:
                target_x, target_y = self._client_to_screen(x, y, hwnd or self._bound_hwnd)

            self._move_mouse(target_x, target_y)
            time.sleep(0.05)

            if self._mouse_controller:
                # pynput 的 scroll: (dx, dy), dy>0 向上
                scroll_amount = delta // 120
                if scroll_amount == 0:
                    scroll_amount = 1 if delta > 0 else -1
                self._mouse_controller.scroll(0, scroll_amount)
            else:
                import pyautogui
                pyautogui.scroll(delta // 120, x=target_x, y=target_y)

            logger.debug(f"[Native] 滚轮: delta={delta}")
            return True
        except Exception as e:
            logger.error(f"[Native] 鼠标滚轮异常: {e}", exc_info=True)
            return False

    # ========== IInputPlugin 实现（键盘）==========

    def _get_key(self, vk_code):
        """将虚拟键码转换为 pynput/pyautogui 可识别的键"""
        # 特殊键映射
        VK_MAP = {
            0x08: 'backspace', 0x09: 'tab', 0x0D: 'enter', 0x10: 'shift',
            0x11: 'ctrl', 0x12: 'alt', 0x1B: 'esc', 0x20: 'space',
            0x21: 'pageup', 0x22: 'pagedown', 0x23: 'end', 0x24: 'home',
            0x25: 'left', 0x26: 'up', 0x27: 'right', 0x28: 'down',
            0x2D: 'insert', 0x2E: 'delete',
            0x70: 'f1', 0x71: 'f2', 0x72: 'f3', 0x73: 'f4',
            0x74: 'f5', 0x75: 'f6', 0x76: 'f7', 0x77: 'f8',
            0x78: 'f9', 0x79: 'f10', 0x7A: 'f11', 0x7B: 'f12',
        }
        if vk_code in VK_MAP:
            return VK_MAP[vk_code]
        # 数字键 0-9
        if 0x30 <= vk_code <= 0x39:
            return chr(vk_code)
        # 字母键 A-Z
        if 0x41 <= vk_code <= 0x5A:
            return chr(vk_code).lower()
        return None

    def key_press(self, vk_code: int, hwnd: int = None) -> bool:
        """
        按下并释放按键
        
        Args:
            vk_code: Windows 虚拟键码
        """
        try:
            key_name = self._get_key(vk_code)
            
            if self._keyboard_controller and key_name:
                from pynput.keyboard import Key
                special_keys = {
                    'backspace': Key.backspace, 'tab': Key.tab, 'enter': Key.enter,
                    'shift': Key.shift, 'ctrl': Key.ctrl, 'alt': Key.alt,
                    'esc': Key.esc, 'space': Key.space,
                    'pageup': Key.page_up, 'pagedown': Key.page_down,
                    'end': Key.end, 'home': Key.home,
                    'left': Key.left, 'up': Key.up, 'right': Key.right, 'down': Key.down,
                    'insert': Key.insert, 'delete': Key.delete,
                    'f1': Key.f1, 'f2': Key.f2, 'f3': Key.f3, 'f4': Key.f4,
                    'f5': Key.f5, 'f6': Key.f6, 'f7': Key.f7, 'f8': Key.f8,
                    'f9': Key.f9, 'f10': Key.f10, 'f11': Key.f11, 'f12': Key.f12,
                }
                if key_name in special_keys:
                    self._keyboard_controller.press(special_keys[key_name])
                    self._keyboard_controller.release(special_keys[key_name])
                else:
                    self._keyboard_controller.press(key_name)
                    self._keyboard_controller.release(key_name)
            else:
                import pyautogui
                if key_name:
                    pyautogui.press(key_name)
                else:
                    # 使用 vk_code 直接发送
                    pyautogui.keyDown(vk_code)
                    pyautogui.keyUp(vk_code)

            logger.debug(f"[Native] 按键: VK={vk_code:#x}")
            return True
        except Exception as e:
            logger.error(f"[Native] 按键异常: {e}", exc_info=True)
            return False

    def key_down(self, vk_code: int, hwnd: int = None) -> bool:
        """按键按下（不释放）"""
        try:
            key_name = self._get_key(vk_code)

            if self._keyboard_controller and key_name:
                from pynput.keyboard import Key
                special_keys = {
                    'backspace': Key.backspace, 'tab': Key.tab, 'enter': Key.enter,
                    'shift': Key.shift, 'ctrl': Key.ctrl, 'alt': Key.alt,
                    'esc': Key.esc, 'space': Key.space,
                    'left': Key.left, 'up': Key.up, 'right': Key.right, 'down': Key.down,
                    'f1': Key.f1, 'f2': Key.f2, 'f3': Key.f3, 'f4': Key.f4,
                    'f5': Key.f5, 'f6': Key.f6, 'f7': Key.f7, 'f8': Key.f8,
                    'f9': Key.f9, 'f10': Key.f10, 'f11': Key.f11, 'f12': Key.f12,
                }
                if key_name in special_keys:
                    self._keyboard_controller.press(special_keys[key_name])
                else:
                    self._keyboard_controller.press(key_name)
            else:
                import pyautogui
                pyautogui.keyDown(vk_code)

            return True
        except Exception as e:
            logger.error(f"[Native] 按键按下异常: {e}", exc_info=True)
            return False

    def key_up(self, vk_code: int, hwnd: int = None) -> bool:
        """按键释放"""
        try:
            key_name = self._get_key(vk_code)

            if self._keyboard_controller and key_name:
                from pynput.keyboard import Key
                special_keys = {
                    'backspace': Key.backspace, 'tab': Key.tab, 'enter': Key.enter,
                    'shift': Key.shift, 'ctrl': Key.ctrl, 'alt': Key.alt,
                    'esc': Key.esc, 'space': Key.space,
                    'left': Key.left, 'up': Key.up, 'right': Key.right, 'down': Key.down,
                    'f1': Key.f1, 'f2': Key.f2, 'f3': Key.f3, 'f4': Key.f4,
                    'f5': Key.f5, 'f6': Key.f6, 'f7': Key.f7, 'f8': Key.f8,
                    'f9': Key.f9, 'f10': Key.f10, 'f11': Key.f11, 'f12': Key.f12,
                }
                if key_name in special_keys:
                    self._keyboard_controller.release(special_keys[key_name])
                else:
                    self._keyboard_controller.release(key_name)
            else:
                import pyautogui
                pyautogui.keyUp(vk_code)

            return True
        except Exception as e:
            logger.error(f"[Native] 按键释放异常: {e}", exc_info=True)
            return False

    def key_input_text(self, text: str, hwnd: int = None) -> bool:
        """
        输入文字
        
        Args:
            text: 要输入的文字
        """
        try:
            if self._keyboard_controller:
                self._keyboard_controller.type(text)
            else:
                import pyautogui
                pyautogui.typewrite(text, interval=0.01)

            logger.debug(f"[Native] 输入文字: '{text[:20]}{'...' if len(text) > 20 else ''}'")
            return True
        except Exception as e:
            logger.error(f"[Native] 输入文字异常: {e}", exc_info=True)
            return False

    # ========== IOCRPlugin 实现 ==========

    def ocr(self, x1: int, y1: int, x2: int, y2: int, hwnd: int = None) -> str:
        """
        OCR识别区域文字
        
        Returns:
            JSON格式字符串，包含文字和坐标信息
            格式: {"Text": "...", "Regions": [{"Text": "...", "Center": {"x":..., "y":...}, "Score": 0.95}]}
        """
        import json

        if not self._ensure_ocr():
            logger.error("[Native] OCR 引擎不可用")
            return ""

        try:
            # 截图
            screenshot = self.capture(x1, y1, x2, y2)
            if screenshot is None:
                return ""

            # PIL Image -> numpy array
            import numpy as np
            img_array = np.array(screenshot)

            # OCR 识别
            results = self._ocr_engine.ocr(img_array, cls=True)

            if not results or not results[0]:
                logger.debug("[Native] OCR 未识别到文字")
                return ""

            # 组装结果
            all_text = []
            regions = []
            for line in results[0]:
                if line:
                    box = line[0]  # 坐标框
                    text = line[1][0]  # 文字
                    score = line[1][1]  # 置信度

                    all_text.append(text)

                    # 计算中心点（加上区域偏移）
                    center_x = int(sum(p[0] for p in box) / len(box)) + x1
                    center_y = int(sum(p[1] for p in box) / len(box)) + y1

                    regions.append({
                        "Text": text,
                        "Center": {"x": center_x, "y": center_y},
                        "Vertices": [[int(p[0]) + x1, int(p[1]) + y1] for p in box],
                        "Score": round(score, 3),
                    })

            result_json = {
                "Text": "".join(all_text),
                "Regions": regions,
            }

            logger.debug(f"[Native] OCR 成功: 识别到 {len(regions)} 个文字区域")
            return json.dumps(result_json, ensure_ascii=False)

        except Exception as e:
            logger.error(f"[Native] OCR 异常: {e}", exc_info=True)
            return ""

    def find_text(self, x1: int, y1: int, x2: int, y2: int,
                  text: str, hwnd: int = None) -> Optional[Tuple[int, int]]:
        """
        查找文字位置
        
        Returns:
            找到返回 (x, y) 中心坐标，未找到返回 None
        """
        ocr_result = self.ocr(x1, y1, x2, y2, hwnd)
        if not ocr_result:
            return None

        try:
            import json
            data = json.loads(ocr_result)

            # 先精确匹配
            for region in data.get("Regions", []):
                if region["Text"] == text:
                    return (region["Center"]["x"], region["Center"]["y"])

            # 再模糊匹配（包含关系）
            for region in data.get("Regions", []):
                if text in region["Text"] or region["Text"] in text:
                    return (region["Center"]["x"], region["Center"]["y"])

            return None

        except Exception as e:
            logger.error(f"[Native] 查找文字异常: {e}")
            return None

    # ========== 窗口操作 ==========

    def find_window(self, title: str = None, class_name: str = None) -> Optional[int]:
        """
        查找窗口句柄
        
        Args:
            title: 窗口标题（模糊匹配）
            class_name: 窗口类名
        
        Returns:
            窗口句柄 (hwnd)，未找到返回 None
        """
        try:
            import win32gui

            if title and not class_name:
                # 使用 pygetwindow 模糊匹配
                try:
                    import pygetwindow as gw
                    windows = gw.getWindowsWithTitle(title)
                    if windows:
                        hwnd = windows[0]._hWnd
                        logger.debug(f"[Native] 找到窗口: '{title}' -> hwnd={hwnd}")
                        return hwnd
                except Exception:
                    pass

                # 回退到 win32gui 枚举
                result = []

                def enum_callback(hwnd, _):
                    if win32gui.IsWindowVisible(hwnd):
                        text = win32gui.GetWindowText(hwnd)
                        if title.lower() in text.lower():
                            result.append(hwnd)

                win32gui.EnumWindows(enum_callback, None)
                if result:
                    return result[0]

            elif class_name:
                hwnd = win32gui.FindWindow(class_name, title)
                if hwnd:
                    return hwnd

            return None

        except Exception as e:
            logger.error(f"[Native] 查找窗口异常: {e}")
            return None

    def enum_window(self) -> List[dict]:
        """
        枚举所有可见窗口
        
        Returns:
            窗口信息列表 [{"hwnd": ..., "title": ..., "class": ..., "rect": ...}]
        """
        try:
            import win32gui

            result = []

            def enum_callback(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if title:  # 只返回有标题的窗口
                        class_name = win32gui.GetClassName(hwnd)
                        rect = win32gui.GetWindowRect(hwnd)
                        result.append({
                            "hwnd": hwnd,
                            "title": title,
                            "class": class_name,
                            "rect": rect,
                        })

            win32gui.EnumWindows(enum_callback, None)
            return result

        except Exception as e:
            logger.error(f"[Native] 枚举窗口异常: {e}")
            return []

    def get_window_title(self, hwnd: int) -> str:
        """获取窗口标题"""
        try:
            import win32gui
            return win32gui.GetWindowText(hwnd)
        except Exception:
            return ""

    def set_client_size(self, hwnd: int, width: int, height: int) -> bool:
        """
        设置窗口客户区大小
        """
        try:
            import win32gui
            import win32con

            # 获取当前窗口矩形
            rect = win32gui.GetWindowRect(hwnd)
            client_rect = win32gui.GetClientRect(hwnd)

            # 计算非客户区（边框+标题栏）大小
            border_x = (rect[2] - rect[0]) - (client_rect[2] - client_rect[0])
            border_y = (rect[3] - rect[1]) - (client_rect[3] - client_rect[1])

            # 计算新的窗口大小
            new_width = width + border_x
            new_height = height + border_y

            win32gui.SetWindowPos(
                hwnd, 0,
                0, 0, new_width, new_height,
                win32con.SWP_NOMOVE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
            )
            logger.info(f"[Native] 设置客户区大小: {width}x{height}")
            return True

        except Exception as e:
            logger.error(f"[Native] 设置客户区大小异常: {e}")
            return False

    def set_window_size(self, hwnd: int, width: int, height: int) -> bool:
        """设置窗口整体大小"""
        try:
            import win32gui
            import win32con

            win32gui.SetWindowPos(
                hwnd, 0,
                0, 0, width, height,
                win32con.SWP_NOMOVE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
            )
            logger.info(f"[Native] 设置窗口大小: {width}x{height}")
            return True

        except Exception as e:
            logger.error(f"[Native] 设置窗口大小异常: {e}")
            return False

    def get_client_size(self, hwnd: int) -> Optional[Tuple[int, int]]:
        """获取窗口客户区大小"""
        try:
            import win32gui
            client_rect = win32gui.GetClientRect(hwnd)
            return (client_rect[2] - client_rect[0], client_rect[3] - client_rect[1])
        except Exception:
            return None
