# -*- coding: utf-8 -*-
"""
OLA（欧拉）插件适配器
将OLA插件接口适配为统一的插件接口
"""

import sys
import os
import logging
import time
import threading
from typing import List, Tuple, Optional, Any

from plugins.core.interface import (
    IPluginAdapter, IImagePlugin, IInputPlugin, IOCRPlugin,
    PluginCapability
)
from plugins.adapters.ola.runtime_config import (
    configure_ola_runtime,
    get_ola_sdk_dir,
)
from plugins.adapters.ola.auth import authorize_ola_instance
from utils.window_binding_utils import get_plugin_bind_args

logger = logging.getLogger(__name__)

# OLA SDK 导入标志
# 优先使用 COM 方式加载，如果失败则回退到 DLL 直接加载
OLA_AVAILABLE = False
_OLAPlugServer = None
_USE_COM = False  # 标记是否使用 COM 方式

def _try_import_ola():
    """尝试导入 OLA SDK，优先使用 COM 方式"""
    global OLA_AVAILABLE, _OLAPlugServer, _USE_COM

    if OLA_AVAILABLE:
        return True

    # 方案1: 优先尝试 COM 方式（推荐）
    try:
        from OLA.OLAPlugCOMLoader import OLAPlugServerCOM
        _OLAPlugServer = OLAPlugServerCOM
        OLA_AVAILABLE = True
        _USE_COM = True
        logger.info("OLA SDK 导入成功（COM 方式）")
        return True
    except ImportError as e:
        logger.debug(f"COM 方式导入 OLA 失败: {e}")
    except Exception as e:
        logger.error(f"COM 方式导入 OLA 异常: {e}", exc_info=True)

    # 方案2: 直接导入（适用于 Nuitka 打包环境，OLA 已被编译到主程序中）
    try:
        # 先导入 OLAPlugDLLHelper，然后将其注册到 sys.modules 的顶层
        # 这样可以解决 OLAPlugServer.py 中的 "from OLAPlugDLLHelper import" 问题
        from OLA import OLAPlugDLLHelper as DLLHelper

        # 【Nuitka兼容】诊断DLL加载状态（可能在编译环境中失败，使用try-except保护）
        try:
            dll_path = os.path.join(DLLHelper._dll_dir, DLLHelper.DLL)
            dll_exists = os.path.exists(dll_path)
            logger.info(f"OLA DLL 路径: {dll_path}")
            logger.info(f"OLA DLL 文件存在: {dll_exists}")
            logger.info(f"OLA DLL 对象: {DLLHelper._dll}")
        except AttributeError:
            # Nuitka编译后类变量可能不可访问，跳过诊断
            logger.debug("OLA DLL诊断跳过（Nuitka编译环境）")

        logger.info(f"sys.frozen: {getattr(sys, 'frozen', False)}")
        logger.info(f"sys.executable: {sys.executable}")

        sys.modules['OLAPlugDLLHelper'] = DLLHelper

        from OLA.OLAPlugServer import OLAPlugServer as OLAPlugServerClass
        _OLAPlugServer = OLAPlugServerClass
        OLA_AVAILABLE = True
        _USE_COM = False
        logger.info("OLA SDK 导入成功（DLL 直接导入）")
        return True
    except ImportError as e:
        logger.debug(f"直接导入 OLA 失败: {e}")
    except Exception as e:
        logger.error(f"OLA SDK 导入异常: {e}", exc_info=True)

    # 方案3: 添加路径后导入（适用于开发环境）
    try:
        # 确定 OLA SDK 路径
        if getattr(sys, 'frozen', False):
            # Nuitka打包环境: OLA目录在可执行文件所在目录
            exe_path = os.path.abspath(sys.executable)
            try:
                exe_path = os.path.realpath(exe_path)
            except Exception:
                pass
            ola_sdk_path = os.path.join(os.path.dirname(exe_path), 'OLA')
        else:
            # 开发环境: OLA目录在项目根目录
            # adapter.py -> ola/ -> adapters/ -> plugins/ -> LCA/ -> OLA/
            current_file = os.path.abspath(__file__)
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_file))))
            ola_sdk_path = os.path.join(project_root, 'OLA')

        # 添加到 sys.path
        if ola_sdk_path not in sys.path:
            sys.path.insert(0, ola_sdk_path)

        # 先导入 OLAPlugDLLHelper 到顶层
        from OLA import OLAPlugDLLHelper as DLLHelper
        sys.modules['OLAPlugDLLHelper'] = DLLHelper

        # 再次尝试导入
        from OLA.OLAPlugServer import OLAPlugServer as OLAPlugServerClass
        _OLAPlugServer = OLAPlugServerClass
        OLA_AVAILABLE = True
        _USE_COM = False
        logger.info(f"OLA SDK 导入成功（路径导入）: {ola_sdk_path}")
        return True
    except ImportError as e:
        logger.error(f"OLA SDK 导入失败: {e}")
        OLA_AVAILABLE = False
        return False


class OLAAdapter(IImagePlugin, IInputPlugin, IOCRPlugin):
    """
    OLA插件适配器

    功能：
    - 图像识别（找图、找色、截图）
    - 鼠标操作（移动、点击、拖拽、滚轮）
    - 键盘操作（按键、输入文字）
    - OCR识别
    - 窗口操作

    【多实例支持】(v1.1.0)
    OLA每个实例只能同时绑定一个窗口。为解决多窗口并发操作问题：
    - 当 use_multi_instance=True 时，使用多实例管理器为每个窗口创建独立OLA实例
    - 当 use_multi_instance=False 时，使用传统单实例模式（需线程锁保护）

    多实例模式解决的问题：
    1. 多工作流同时运行时虚拟键盘输入冲突（问题7）
    2. 多开后窗口绑定穿透问题（问题8）
    """

    # 类级别的线程锁，保护OLA操作的原子性（单实例模式使用）
    _ola_lock = threading.RLock()

    # 类级别：是否启用多实例模式（默认启用）
    _use_multi_instance_default = True

    def __init__(self, use_multi_instance: bool = None):
        """
        初始化OLA适配器

        Args:
            use_multi_instance: 是否使用多实例模式
                - True: 每个窗口使用独立OLA实例（推荐，解决并发问题）
                - False: 使用单一OLA实例（传统模式）
                - None: 使用类级别默认值
        """
        self.ola = None
        self._initialized = False
        self._bound_hwnd = None
        self._mouse_move_with_trajectory = False  # 默认直接移动(快速)
        # 【关键修复】保存绑定时的模式参数，用于后续鼠标操作判断
        self._bound_mouse_mode = 'normal'  # 鼠标绑定模式 (normal/windows/windows3等)
        self._bound_display_mode = 'normal'  # 显示绑定模式
        self._bound_keypad_mode = 'normal'  # 键盘绑定模式
        self._bound_mode = 0
        self._bound_pubstr = ''

        # 多实例模式设置
        if use_multi_instance is None:
            self._use_multi_instance = OLAAdapter._use_multi_instance_default
        else:
            self._use_multi_instance = use_multi_instance

        # 多实例管理器（延迟初始化）
        self._multi_instance_manager = None

    def _reset_bound_context(self) -> None:
        """清理适配器记录的当前绑定上下文，不触碰多实例缓存。"""
        self._bound_hwnd = None
        self._bound_display_mode = 'normal'
        self._bound_mouse_mode = 'normal'
        self._bound_keypad_mode = 'normal'
        self._bound_mode = 0
        self._bound_pubstr = ''
        self._mouse_move_with_trajectory = False
        if self._use_multi_instance:
            self.ola = None

    def _get_ola_for_operation(self, hwnd: int = None) -> Any:
        """
        获取应该用于操作的OLA实例（多实例模式线程安全）

        在多实例模式下，根据hwnd获取窗口专属的OLA实例；
        如果未指定hwnd，使用当前绑定的窗口。

        Args:
            hwnd: 目标窗口句柄，如果为None则使用 self._bound_hwnd

        Returns:
            OLA实例，如果获取失败返回 self.ola 作为后备
        """
        # 确定目标窗口
        target_hwnd = hwnd if hwnd else self._bound_hwnd

        # 多实例模式：从管理器获取窗口专属实例
        if self._use_multi_instance and self._multi_instance_manager and target_hwnd:
            window_ola = self._multi_instance_manager.get_instance_for_window(target_hwnd)
            if window_ola:
                return window_ola
            else:
                logger.warning(f"[OLA] 未找到窗口 {target_hwnd} 的OLA实例，回退到默认实例")

        # 单实例模式或获取失败：使用默认实例
        return self.ola

    def get_name(self) -> str:
        return "OLA"

    def get_version(self) -> str:
        return "1.1.0"  # 多实例支持版本

    def get_capabilities(self) -> List[PluginCapability]:
        """OLA插件支持的能力"""
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
            PluginCapability.MOUSE_DOWN,      # 鼠标按下
            PluginCapability.MOUSE_UP,        # 鼠标释放
            PluginCapability.MOUSE_DRAG,
            PluginCapability.MOUSE_SCROLL,

            # 键盘操作
            PluginCapability.KEYBOARD_PRESS,
            PluginCapability.KEYBOARD_DOWN,   # 按键按下
            PluginCapability.KEYBOARD_UP,     # 按键释放
            PluginCapability.KEYBOARD_INPUT_TEXT,
            PluginCapability.KEYBOARD_COMBINATION,

            # OCR识别
            PluginCapability.OCR_TEXT,
            PluginCapability.OCR_FIND_TEXT,

            # 窗口操作
            PluginCapability.WINDOW_BIND,
            PluginCapability.WINDOW_UNBIND,
            PluginCapability.WINDOW_FIND,
            PluginCapability.WINDOW_ENUM,      # 枚举窗口
            PluginCapability.WINDOW_INFO,      # 获取窗口信息
            PluginCapability.WINDOW_RESIZE,    # 调整窗口大小
        ]

    def initialize(self, config: dict) -> bool:
        """
        初始化OLA插件

        Args:
            config: 配置字典，包含：
                - dll_path: DLL文件路径
                - user_code: 用户注册码（可选）
                - soft_code: 软件注册码（可选）
                - use_multi_instance: 是否使用多实例模式（可选，默认True）
        """
        try:
            configure_ola_runtime(config)

            # 检查是否通过配置指定多实例模式
            if 'use_multi_instance' in config:
                self._use_multi_instance = config['use_multi_instance']

            # 尝试导入 OLA SDK
            if not _try_import_ola():
                logger.error("OLA SDK 不可用，请确认 OLA 模块已正确打包或存在于项目目录")
                return False

            # 【多实例模式】初始化多实例管理器
            if self._use_multi_instance:
                try:
                    from .multi_instance_manager import get_ola_instance_manager
                    self._multi_instance_manager = get_ola_instance_manager()
                    logger.info("[OLA] 多实例模式已启用 - 每个窗口将使用独立OLA实例")
                except Exception as e:
                    logger.warning(f"[OLA] 多实例管理器初始化失败，回退到单实例模式: {e}")
                    self._use_multi_instance = False

            # 【关键修复】在Nuitka打包环境中，设置SetPath以确保DLL能找到资源
            # 获取OLA DLL所在目录
            ola_dll_dir = get_ola_sdk_dir()
            logger.info(f"OLA DLL目录: {ola_dll_dir}")

            # 创建OLA实例（单实例模式使用，多实例模式下作为默认实例）
            self.ola = _OLAPlugServer()
            load_mode = "COM" if _USE_COM else "DLL"
            logger.info(f"OLA插件实例创建完成（{load_mode}方式）")

            auth_result = authorize_ola_instance(self.ola)
            if not auth_result.success:
                logger.error(f"OLA 插件登录失败: {auth_result.message}")
                try:
                    self.ola.DestroyCOLAPlugInterFace()
                except Exception:
                    pass
                self.ola = None
                return False

            logger.info("OLA 插件登录成功")

            # 创建对象
            if self.ola.CreateCOLAPlugInterFace() == 0:
                logger.error("OLA插件创建实例失败")
                try:
                    self.ola.DestroyCOLAPlugInterFace()
                except Exception:
                    pass
                self.ola = None
                return False

            # 【尝试修复】设置OLA的工作路径
            try:
                self.ola.SetPath(ola_dll_dir)
                logger.info(f"OLA SetPath成功: {ola_dll_dir}")
            except Exception as e:
                logger.warning(f"OLA SetPath失败: {e}，这可能不影响基本功能")

            # 【OCR优化】配置OCR检测参数
            try:
                import json
                # 默认OCR配置（宽松参数，提高小字体和低对比度文字的识别率）
                ocr_config = {
                    "OcrDetDbThresh": 0.1,
                    "OcrDetDbBoxThresh": 0.3,
                    "OcrUseAngleCls": True,
                    "OcrDetDbUnclipRatio": 3.0,
                    "OcrRecBatchNum": 6,
                    "OcrMinArea": 3,
                }

                config_str = json.dumps(ocr_config)
                result = self.ola.SetOcrConfig(config_str)
                if result == 1:
                    logger.debug(f"OLA OCR配置成功")
                else:
                    logger.debug(f"OLA OCR配置返回: {result}")
            except Exception as e:
                logger.debug(f"OLA OCR配置异常: {e}")

            self._initialized = True
            mode_str = "多实例" if self._use_multi_instance else "单实例"
            logger.info(f"OLA插件初始化成功，版本: {self.get_version()}，模式: {mode_str}")
            return True

        except Exception as e:
            logger.error(f"OLA插件初始化失败: {e}", exc_info=True)
            return False

    def release(self) -> bool:
        """释放OLA插件资源"""
        try:
            # 【多实例模式】释放所有窗口的OLA实例
            if self._use_multi_instance and self._multi_instance_manager:
                try:
                    self._multi_instance_manager.release_all()
                    logger.info("[OLA] 多实例模式：已释放所有窗口的OLA实例")
                except Exception as e:
                    logger.warning(f"[OLA] 释放多实例管理器失败: {e}")

            # 释放默认OLA实例
            if self.ola:
                if self._bound_hwnd:
                    self.unbind_window()
                self.ola.DestroyCOLAPlugInterFace()
                self.ola = None

            self._initialized = False
            logger.info("OLA插件资源已释放")
            return True
        except Exception as e:
            logger.error(f"OLA插件释放失败: {e}", exc_info=True)
            return False

    def health_check(self) -> bool:
        """健康检查"""
        return self._initialized and self.ola is not None

    def execute(self, capability: PluginCapability, method: str, *args, **kwargs) -> Any:
        """
        执行插件操作（通用接口）

        Args:
            capability: 插件能力
            method: 方法名
            *args, **kwargs: 方法参数
        """
        if not self.health_check():
            raise RuntimeError("OLA插件未初始化或不可用")

        # 处理鼠标移动方式配置
        if 'mouse_move_with_trajectory' in kwargs:
            self._mouse_move_with_trajectory = kwargs.pop('mouse_move_with_trajectory')
            logger.debug(f"OLA鼠标移动方式设置为: {'轨迹移动' if self._mouse_move_with_trajectory else '直接移动'}")
        elif 'hwnd' in kwargs and self._use_multi_instance and self._multi_instance_manager:
            # 多实例模式下从窗口配置获取
            hwnd = kwargs.get('hwnd')
            window_config = self._multi_instance_manager.get_window_config(hwnd)
            if window_config:
                self._mouse_move_with_trajectory = window_config.get('mouse_move_with_trajectory', False)
                logger.debug(f"OLA从窗口配置获取鼠标移动方式: {'轨迹移动' if self._mouse_move_with_trajectory else '直接移动'}")

        # 根据能力和方法名调用对应的实现
        method_map = {
            'find_pic': self.find_pic,
            'find_pic_ex': self.find_pic_ex,
            'find_color': self.find_color,
            'find_multi_color': self.find_multi_color,
            'get_color': self.get_color,
            'capture': self.capture,
            'mouse_move': self.mouse_move,
            'mouse_click': self.mouse_click,
            'mouse_down': self.mouse_down,
            'mouse_up': self.mouse_up,
            'mouse_drag': self.mouse_drag,
            'mouse_scroll': self.mouse_scroll,
            'key_press': self.key_press,
            'key_down': self.key_down,
            'key_up': self.key_up,
            'key_input_text': self.key_input_text,
            'ocr': self.ocr,
            'bind_window': self.bind_window,
            'unbind_window': self.unbind_window,
            'find_window': self.find_window,
            'enum_window': self.enum_window,           # 枚举窗口
            'get_window_title': self.get_window_title, # 获取窗口标题
        }

        func = method_map.get(method)
        if func:
            return func(*args, **kwargs)
        else:
            raise NotImplementedError(f"OLA插件不支持方法: {method}")

    # ========== IImagePlugin 接口实现 ==========

    def bind_window(self, hwnd: int, display_mode: str = "normal",
                    mouse_mode: str = "normal", keypad_mode: str = "normal",
                    mode: int = 0, input_lock: bool = False,
                    activate_foreground: bool = False,
                    mouse_move_with_trajectory: bool = False,
                    pubstr: str = "") -> bool:
        """
        绑定窗口

        Args:
            hwnd: 窗口句柄
            display_mode: 显示模式 (normal/gdi/gdi2/gdi3/gdi4/gdi5/dxgi/vnc/dx等)
            mouse_mode: 鼠标模式 (normal/windows/windows3/vnc/dx.mouse.*等)
            keypad_mode: 键盘模式 (normal/windows/vnc/dx.keypad.*等)
            mode: 绑定模式 (0=推荐, 1=远程线程注入, 2=驱动注入模式1, 3=驱动注入模式2, 4=驱动注入模式3)
            input_lock: 后台绑定时是否锁定前台鼠标键盘 (默认False，不锁定)
            activate_foreground: 绑定前是否激活窗口到前台（前台模式推荐True）
            mouse_move_with_trajectory: 是否使用鼠标轨迹移动 (默认False，直接移动)
            pubstr: 绑定模式pub参数 (例如: "ola.bypass.guard", 绑定失败时可尝试使用)

        【多实例模式】
        当启用多实例模式时，会为每个窗口创建独立的OLA实例，
        避免多窗口并发操作时的绑定冲突问题。
        """
        # 【窗口有效性检查】在绑定前验证窗口句柄
        if not self._validate_window_handle(hwnd):
            logger.error(f"[OLA] 窗口句柄无效，无法绑定: {hwnd}")
            return False

        # 【多实例模式】使用专属OLA实例
        if self._use_multi_instance and self._multi_instance_manager:
            return self._bind_window_multi_instance(
                hwnd, display_mode, mouse_mode, keypad_mode, mode,
                input_lock, activate_foreground, mouse_move_with_trajectory, pubstr
            )

        # 【单实例模式】使用原有逻辑
        return self._bind_window_single_instance(
            hwnd, display_mode, mouse_mode, keypad_mode, mode,
            input_lock, activate_foreground, mouse_move_with_trajectory, pubstr
        )

    def _validate_window_handle(self, hwnd: int) -> bool:
        """
        验证窗口句柄是否有效

        Args:
            hwnd: 窗口句柄

        Returns:
            True 如果窗口有效，否则 False
        """
        if not hwnd or hwnd == 0:
            logger.warning("[OLA] 窗口句柄为空或为0")
            return False

        try:
            import win32gui
            if not win32gui.IsWindow(hwnd):
                logger.warning(f"[OLA] 窗口句柄 {hwnd} 对应的窗口不存在")
                return False

            # 获取窗口信息用于日志
            try:
                title = win32gui.GetWindowText(hwnd)
                class_name = win32gui.GetClassName(hwnd)
                logger.debug(f"[OLA] 窗口验证通过: hwnd={hwnd}, title='{title}', class='{class_name}'")
            except:
                pass

            return True
        except ImportError:
            # win32gui不可用时，假设有效
            logger.warning("[OLA] win32gui不可用，跳过窗口验证")
            return True
        except Exception as e:
            logger.error(f"[OLA] 验证窗口句柄异常: {e}")
            return False

    def _bind_window_multi_instance(self, hwnd: int, display_mode: str,
                                     mouse_mode: str, keypad_mode: str,
                                     mode: int, input_lock: bool,
                                     activate_foreground: bool,
                                     mouse_move_with_trajectory: bool,
                                     pubstr: str = "") -> bool:
        """
        多实例模式绑定窗口

        每个窗口使用独立的OLA实例，彻底解决多窗口并发操作时的冲突问题。

        【重要】在多实例模式下，每个线程/窗口操作时需要先获取窗口专属OLA实例，
        然后使用该实例执行操作。不能依赖 self.ola，因为它可能被其他线程覆盖。
        """
        try:
            # 构建绑定配置
            config = {
                'display_mode': display_mode,
                'mouse_mode': mouse_mode,
                'keypad_mode': keypad_mode,
                'mode': mode,
                'input_lock': input_lock,
                'mouse_move_with_trajectory': mouse_move_with_trajectory,
                'pubstr': str(pubstr or '').strip()
            }

            # 【修复】先获取窗口专属OLA实例，再用它激活窗口
            window_ola = self._multi_instance_manager.get_instance_for_window(hwnd, config)

            if not window_ola:
                logger.error(f"[OLA多实例] 窗口 {hwnd} 获取OLA实例失败")
                return False

            # 前台模式：使用窗口专属OLA实例激活窗口
            if activate_foreground:
                try:
                    window_ola.SetWindowState(hwnd, 1)
                    logger.info(f"[OLA多实例] 激活窗口到前台: {hwnd}")
                    time.sleep(0.1)
                except Exception as e:
                    logger.warning(f"[OLA多实例] 激活窗口异常: {e}")

            # 保存状态到适配器（兼容性）
            # 【注意】这些状态仅用于向后兼容，实际操作应通过多实例管理器获取专属实例
            self._bound_hwnd = hwnd
            self._bound_display_mode = display_mode
            self._bound_mouse_mode = mouse_mode
            self._bound_keypad_mode = keypad_mode
            self._bound_mode = mode
            self._bound_pubstr = str(pubstr or '').strip()
            self._mouse_move_with_trajectory = mouse_move_with_trajectory

            # 【线程安全说明】
            # 在多实例模式下，self.ola 仅作为"当前最后绑定的窗口"的快捷引用
            # 多线程并发时，应使用 _multi_instance_manager.get_instance_for_window(hwnd) 获取正确实例
            # 此处赋值主要是为了兼容现有的单线程调用代码
            self.ola = window_ola

            logger.info(f"[OLA多实例] 窗口 {hwnd} 绑定成功 "
                       f"(display={display_mode}, mouse={mouse_mode}, keypad={keypad_mode})")
            return True

        except Exception as e:
            logger.error(f"[OLA多实例] 绑定窗口 {hwnd} 异常: {e}", exc_info=True)
            return False

    def _bind_window_single_instance(self, hwnd: int, display_mode: str,
                                      mouse_mode: str, keypad_mode: str,
                                      mode: int, input_lock: bool,
                                      activate_foreground: bool,
                                      mouse_move_with_trajectory: bool,
                                      pubstr: str = "") -> bool:
        """
        单实例模式绑定窗口（原有逻辑）

        使用线程锁保护，适用于单窗口场景或需要严格顺序执行的场景。
        """
        # 【多线程安全】使用线程锁保护绑定操作
        with self._ola_lock:
            try:
                # [新增] 前台模式：绑定前激活窗口
                if activate_foreground:
                    try:
                        # 使用OLA的SetWindowState激活窗口
                        # state=1表示激活窗口到前台
                        result = self.ola.SetWindowState(hwnd, 1)
                        if result == 1:
                            logger.info(f"OLA激活窗口到前台成功: {hwnd}")
                            time.sleep(0.1)  # 等待窗口激活
                        else:
                            logger.warning(f"OLA激活窗口到前台失败: {hwnd}, 返回值: {result}")
                    except Exception as e:
                        logger.warning(f"OLA激活窗口异常: {e}")

                # [新增] 在绑定前设置公共属性（包括InputLock和鼠标轨迹配置）
                try:
                    import json
                    from utils.app_paths import get_config_path
                    # 从统一主配置读取参数（开发/打包一致）
                    config_path = get_config_path()
                    config = {}
                    if os.path.exists(config_path):
                        with open(config_path, 'r', encoding='utf-8') as f:
                            config = json.load(f)
                    bind_args = get_plugin_bind_args(config, hwnd=hwnd)
                    trajectory_config = bind_args.get('trajectory_config', {})

                    # SimModeType 只在前台模式下生效，后台模式使用默认值0
                    # 前台模式：display_mode=normal 且 mouse_mode=normal
                    is_foreground_mode = (display_mode == 'normal' and mouse_mode == 'normal')
                    sim_mode_type = bind_args.get('sim_mode_type', 0) if is_foreground_mode else 0

                    ola_config = {
                        "InputLock": input_lock,
                        "SimModeType": sim_mode_type,
                        "EnableRealMouse": mouse_move_with_trajectory,
                    }

                    # 启用轨迹时添加默认轨迹参数
                    if mouse_move_with_trajectory:
                        ola_config.update({
                            "RealMouseMode": 1,
                            "RealMouseBaseTimePer100Pixels": 200,
                            "RealMouseNoise": 5.0,
                            "RealMouseDeviation": 25,
                            "RealMouseMinSteps": 150,
                        })

                    # 保存轨迹配置状态到实例变量
                    self._mouse_move_with_trajectory = mouse_move_with_trajectory

                    config_str = json.dumps(ola_config)
                    result = self.ola.SetConfig(config_str)
                    if result == 1:
                        trajectory_status = "启用" if mouse_move_with_trajectory else "禁用"
                        mode_desc = "前台模式" if is_foreground_mode else "后台模式(SimModeType强制为0)"
                        logger.info(f"OLA公共属性配置成功: InputLock={input_lock}, 鼠标轨迹={trajectory_status}, SimModeType={sim_mode_type} ({mode_desc})")
                    else:
                        logger.warning(f"OLA公共属性配置失败 (返回值: {result})")
                except Exception as e:
                    logger.warning(f"OLA公共属性配置异常: {e}")

                # 【多线程优化】相同窗口重复绑定时，跳过解绑-重绑操作
                # 在多工作流并行执行时，频繁解绑-重绑会导致其他线程的操作失败
                # 只有绑定模式参数不同时，才需要重新绑定
                if self._bound_hwnd == hwnd:
                    # 检查绑定参数是否相同（包括轨迹配置）
                    if (self._bound_display_mode == display_mode and
                        self._bound_mouse_mode == mouse_mode and
                        self._bound_keypad_mode == keypad_mode and
                        self._bound_mode == mode and
                        self._bound_pubstr == str(pubstr or '').strip() and
                        self._mouse_move_with_trajectory == mouse_move_with_trajectory):
                        logger.debug(f"[OLA] 窗口 {hwnd} 已绑定且参数相同，跳过重复绑定（多线程安全）")
                        return True
                    else:
                        # 参数不同，需要重新配置
                        logger.info(f"[OLA刷新] 窗口 {hwnd} 绑定参数变化，更新配置")

                        # 如果只是轨迹参数变化，只需要更新SetConfig，无需解绑重绑
                        if (self._bound_display_mode == display_mode and
                            self._bound_mouse_mode == mouse_mode and
                            self._bound_keypad_mode == keypad_mode and
                            self._bound_mode == mode and
                            self._bound_pubstr == str(pubstr or '').strip() and
                            self._mouse_move_with_trajectory != mouse_move_with_trajectory):
                            # 只更新SetConfig
                            self._mouse_move_with_trajectory = mouse_move_with_trajectory
                            ola_config_update = {
                                "EnableRealMouse": mouse_move_with_trajectory,
                            }
                            if mouse_move_with_trajectory:
                                ola_config_update.update({
                                    "RealMouseMode": 1,
                                    "RealMouseBaseTimePer100Pixels": 200,
                                    "RealMouseNoise": 5.0,
                                    "RealMouseDeviation": 25,
                                    "RealMouseMinSteps": 150,
                                })
                            config_str_update = json.dumps(ola_config_update)
                            result = self.ola.SetConfig(config_str_update)
                            if result == 1:
                                trajectory_status = "启用" if mouse_move_with_trajectory else "禁用"
                                logger.info(f"OLA轨迹配置更新成功: EnableRealMouse={trajectory_status}")
                                return True
                            else:
                                logger.warning(f"OLA轨迹配置更新失败 (返回值: {result})")

                        # 其他参数变化，需要重新绑定
                        logger.info(f"[OLA刷新] 窗口 {hwnd} 绑定参数变化，重新绑定: "
                                   f"{self._bound_display_mode}/{self._bound_mouse_mode}/{self._bound_keypad_mode}/mode={self._bound_mode}/pubstr={self._bound_pubstr or '(无)'} -> "
                                   f"{display_mode}/{mouse_mode}/{keypad_mode}/mode={mode}/pubstr={str(pubstr or '').strip() or '(无)'}")
                        ret_unbind = self.ola.UnBindWindow()
                        logger.debug(f"[OLA刷新] 解绑结果: {ret_unbind}")
                        self._bound_hwnd = None
                        time.sleep(0.1)

                # 如果绑定了其他窗口，先解绑
                if self._bound_hwnd and self._bound_hwnd != hwnd:
                    logger.info(f"OLA先解绑旧窗口: {self._bound_hwnd}")
                    self.ola.UnBindWindow()
                    self._bound_hwnd = None

                # 绑定新窗口（带重试机制）
                max_retries = 3
                retry_delay = 0.1  # 100ms

                for attempt in range(max_retries):
                    if attempt > 0:
                        logger.warning(f"OLA绑定窗口重试 {attempt}/{max_retries-1}: {hwnd}")
                        time.sleep(retry_delay)

                    logger.info(
                        f"OLA正在尝试绑定窗口: {hwnd}, 参数: display={display_mode}, "
                        f"mouse={mouse_mode}, keypad={keypad_mode}, mode={mode}, pubstr={pubstr if pubstr else '(无)'}"
                    )
                    sanitized_pubstr = str(pubstr or '').strip()
                    if sanitized_pubstr:
                        ret = self.ola.BindWindowEx(
                            hwnd, display_mode, mouse_mode, keypad_mode, sanitized_pubstr, mode
                        )
                    else:
                        ret = self.ola.BindWindow(hwnd, display_mode, mouse_mode, keypad_mode, mode)

                    if ret == 1:
                        self._bound_hwnd = hwnd
                        # 【关键修复】保存绑定时的模式参数，用于后续鼠标操作判断
                        self._bound_display_mode = display_mode
                        self._bound_mouse_mode = mouse_mode
                        self._bound_keypad_mode = keypad_mode
                        self._bound_mode = mode
                        self._bound_pubstr = sanitized_pubstr
                        logger.info(f"OLA绑定窗口成功: {hwnd}, 模式: {display_mode}/{mouse_mode}/{keypad_mode}, mode={mode}")
                        if attempt > 0:
                            logger.info(f"OLA绑定在第 {attempt + 1} 次尝试后成功")

                        # 【关键】绑定后等待，让OLA捕获新截图
                        time.sleep(0.05)  # 给OLA 50ms时间捕获新的窗口截图
                        logger.debug(f"[OLA刷新] 绑定后等待截图捕获完成")

                        return True
                    else:
                        logger.warning(f"OLA绑定窗口失败 (尝试 {attempt + 1}/{max_retries}): {hwnd}, 返回值: {ret}")

                # 所有重试都失败
                logger.error(f"OLA绑定窗口失败: {hwnd}, 参数: {display_mode}/{mouse_mode}/{keypad_mode}, mode={mode}, 已重试 {max_retries} 次")
                return False
            except Exception as e:
                logger.error(f"OLA绑定窗口异常: {e}", exc_info=True)
                return False

    def unbind_window(self, hwnd: int = None) -> bool:
        """解绑窗口

        Args:
            hwnd: 要解绑的窗口句柄。多实例模式下必须指定，单实例模式下可选。
                  多窗口并发修复：必须显式指定hwnd，避免使用共享的self._bound_hwnd导致误释放其他窗口的实例。

        Returns:
            bool: 解绑是否成功
        """
        # 【多线程安全】使用线程锁保护解绑操作
        with self._ola_lock:
            try:
                # 【多窗口并发修复】优先使用传入的hwnd，避免使用可能被其他线程覆盖的self._bound_hwnd
                target_hwnd = hwnd if hwnd else self._bound_hwnd

                if target_hwnd:
                    # 【多实例模式】通过管理器释放窗口专属实例
                    if self._use_multi_instance and self._multi_instance_manager:
                        # 多实例模式的实例生命周期由管理器统一维护。
                        # 这里的解绑只结束当前任务的绑定上下文，避免每个步骤都执行一次昂贵的
                        # UnBindWindow + DestroyCOLAPlugInterFace，导致步骤完成后额外卡顿。
                        if not self._multi_instance_manager._is_window_handle_valid(target_hwnd):
                            self._multi_instance_manager.release_instance(target_hwnd)
                        if target_hwnd == self._bound_hwnd:
                            self._reset_bound_context()
                        logger.info(f"[OLA多实例] 解绑窗口成功: {target_hwnd}")
                    else:
                        # 单实例模式：直接解绑
                        ret = self.ola.UnBindWindow()
                        if ret == 1:
                            logger.info(f"OLA解绑窗口成功: {target_hwnd}")
                        else:
                            logger.warning(f"OLA解绑窗口返回: {ret}")

                    # 【多窗口并发修复】只有当解绑的是当前绑定的窗口时，才重置状态
                    # 避免在多窗口并发时错误地重置其他窗口的状态
                    if target_hwnd == self._bound_hwnd:
                        self._reset_bound_context()
                    return True
                return False
            except Exception as e:
                logger.error(f"OLA解绑窗口异常: {e}", exc_info=True)
                return False

    def find_window(self, class_name: str = "", title: str = "") -> int:
        """
        查找窗口

        Args:
            class_name: 窗口类名
            title: 窗口标题（支持模糊匹配）

        Returns:
            int: 窗口句柄，0表示未找到
        """
        try:
            hwnd = self.ola.FindWindow(class_name, title)
            if hwnd and hwnd != 0:
                logger.info(f"OLA找到窗口: class='{class_name}', title='{title}' -> HWND {hwnd}")
            else:
                logger.debug(f"OLA未找到窗口: class='{class_name}', title='{title}'")
            return hwnd if hwnd else 0
        except Exception as e:
            logger.error(f"OLA查找窗口异常: {e}", exc_info=True)
            return 0

    def enum_window(self, filter_type: int = 0, filter_content: str = "") -> str:
        """
        枚举窗口（无需绑定即可使用）

        Args:
            filter_type: 过滤类型
                0: 不过滤，枚举所有窗口
                1: 按标题过滤
                2: 按类名过滤
            filter_content: 过滤内容（标题或类名）

        Returns:
            str: 窗口句柄列表，格式 "hwnd1,hwnd2,hwnd3"，空字符串表示未找到
        """
        try:
            # 【关键修复】窗口枚举功能不需要完整初始化，只需要 OLA SDK 可用
            # 如果 self.ola 不存在，尝试创建临时实例
            ola_instance = self.ola
            temp_instance = False

            if not ola_instance:
                logger.info("OLA 实例不存在，尝试创建临时实例用于窗口枚举")
                if not _try_import_ola():
                    logger.error("OLA SDK 不可用，无法枚举窗口")
                    return ""

                try:
                    ola_instance = _OLAPlugServer()
                    if ola_instance.CreateCOLAPlugInterFace() == 0:
                        logger.error("创建临时 OLA 实例失败")
                        return ""
                    temp_instance = True
                    logger.info("临时 OLA 实例创建成功")
                except Exception as e:
                    logger.error(f"创建临时 OLA 实例异常: {e}")
                    return ""

            # OLA 的 EnumWindow 参数说明：
            # filter: 位标志
            #   1  = 匹配标题
            #   2  = 匹配类名
            #   4  = 子窗口
            #   8  = 顶级窗口
            #   16 = 可见窗口

            if filter_type == 0:
                # 枚举所有可见的顶级窗口
                title = ""
                class_name = ""
                # 尝试1: 仅顶级窗口（不加可见标志）
                filter_flags = 8
                logger.info(f"OLA EnumWindow 尝试1: parent=0, title='', class='', filter={filter_flags}")
                result = ola_instance.EnumWindow(0, title, class_name, filter_flags)
                logger.info(f"OLA EnumWindow 尝试1结果: type={type(result)}, value={repr(result)[:100] if result else None}")

                # 如果失败，尝试2: 加上可见标志
                if not result or not result.strip():
                    filter_flags = 8 + 16
                    logger.info(f"OLA EnumWindow 尝试2: filter={filter_flags}")
                    result = ola_instance.EnumWindow(0, title, class_name, filter_flags)
                    logger.info(f"OLA EnumWindow 尝试2结果: type={type(result)}, value={repr(result)[:100] if result else None}")

                # 如果还失败，尝试3: 不加任何过滤（返回所有窗口）
                if not result or not result.strip():
                    filter_flags = 0
                    logger.info(f"OLA EnumWindow 尝试3: filter={filter_flags}")
                    result = ola_instance.EnumWindow(0, title, class_name, filter_flags)
                    logger.info(f"OLA EnumWindow 尝试3结果: type={type(result)}, value={repr(result)[:100] if result else None}")

            elif filter_type == 1:
                # 按标题过滤
                title = filter_content
                class_name = ""
                filter_flags = 1 + 8 + 16  # 标题 + 顶级 + 可见
                result = ola_instance.EnumWindow(0, title, class_name, filter_flags)

            elif filter_type == 2:
                # 按类名过滤
                title = ""
                class_name = filter_content
                filter_flags = 2 + 8 + 16  # 类名 + 顶级 + 可见
                result = ola_instance.EnumWindow(0, title, class_name, filter_flags)
            else:
                logger.warning(f"OLA枚举窗口: 未知的filter_type {filter_type}")
                result = ""

            # 清理临时实例
            if temp_instance:
                try:
                    ola_instance.DestroyCOLAPlugInterFace()
                    logger.debug("已释放临时 OLA 实例")
                except:
                    pass

            if result and result.strip():
                logger.debug(f"OLA枚举窗口成功: filter_type={filter_type}, content='{filter_content}' -> 找到{len(result.split(','))}个窗口")
                return result
            else:
                logger.warning(f"OLA枚举窗口失败: filter_type={filter_type}, content='{filter_content}' -> 返回空")
                return ""
        except Exception as e:
            logger.error(f"OLA枚举窗口异常: {e}", exc_info=True)
            return ""

    def get_window_title(self, hwnd: int) -> str:
        """
        获取窗口标题（无需绑定即可使用）

        Args:
            hwnd: 窗口句柄

        Returns:
            str: 窗口标题，失败返回空字符串
        """
        try:
            # 【关键修复】窗口信息功能不需要完整初始化
            ola_instance = self.ola
            temp_instance = False

            if not ola_instance:
                if not _try_import_ola():
                    return ""

                try:
                    ola_instance = _OLAPlugServer()
                    if ola_instance.CreateCOLAPlugInterFace() == 0:
                        return ""
                    temp_instance = True
                except:
                    return ""

            title = ola_instance.GetWindowTitle(hwnd)

            # 清理临时实例
            if temp_instance:
                try:
                    ola_instance.DestroyCOLAPlugInterFace()
                except:
                    pass

            return title if title else ""
        except Exception as e:
            logger.debug(f"OLA获取窗口标题异常: hwnd={hwnd}, {e}")
            return ""

    def find_pic(self, x1: int, y1: int, x2: int, y2: int,
                 pic_name: str, similarity: float = 0.9, hwnd: int = None) -> Optional[Tuple[int, int]]:
        """
        查找图片

        Args:
            x1, y1: 左上角坐标
            x2, y2: 右下角坐标
            pic_name: 图片路径
            similarity: 相似度 (0.0-1.0)
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）

        Returns:
            Optional[Tuple[int, int]]: 找到返回(x, y)，未找到返回None
        """
        result_dict = self.find_pic_with_confidence(x1, y1, x2, y2, pic_name, similarity, hwnd=hwnd)
        if result_dict and result_dict.get('found'):
            return (result_dict['x'], result_dict['y'])
        return None

    def find_pic_with_confidence(self, x1: int, y1: int, x2: int, y2: int,
                                  pic_name: str, similarity: float = 0.9, hwnd: int = None) -> Optional[dict]:
        """
        查找图片（返回完整结果包括相似度）

        Args:
            x1, y1: 左上角坐标
            x2, y2: 右下角坐标
            pic_name: 图片路径
            similarity: 相似度阈值 (0.0-1.0)
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）

        Returns:
            Optional[dict]: 返回字典包含:
                - found: bool, 是否达到阈值
                - x, y: int, 匹配位置
                - confidence: float, 实际相似度
                - threshold: float, 要求的阈值
        """
        try:
            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd
            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)

            # OLA找图方法：MatchWindowsFromPath
            # 参数：x1, y1, x2, y2, 图片路径, 相似度, 类型(1=灰度,2=彩色), 角度, 缩放
            import time
            before_match = time.time()
            logger.debug(f"OLA找图调用参数: 区域=({x1},{y1})-({x2},{y2}), 路径={pic_name}, 相似度={similarity}")

            result = ola.MatchWindowsFromPath(
                x1, y1, x2, y2,
                pic_name,
                similarity,
                2,  # 类型：2=彩色匹配
                0.0,  # 角度：0=不旋转
                1.0  # 缩放：1.0=不缩放
            )

            match_duration = time.time() - before_match
            # 记录完整返回值
            logger.debug(f"OLA找图返回值: {result}, 耗时: {match_duration:.4f}s")

            # result格式：{"MatchVal": 0.85, "MatchState": true, "MatchPoint": {"x": 100, "y": 200}}
            if result:
                match_point = result.get('MatchPoint', {})
                x, y = match_point.get('x'), match_point.get('y')
                match_val = result.get('MatchVal', 0.0)
                match_state = result.get('MatchState', False)

                if x is not None and y is not None:
                    result_dict = {
                        'found': match_state,
                        'x': x,
                        'y': y,
                        'confidence': match_val,
                        'threshold': similarity
                    }

                    if match_state:
                        logger.info(f"OLA找图成功: {pic_name} -> ({x}, {y}), 相似度: {match_val:.4f} >= {similarity:.4f}")
                    else:
                        logger.info(f"OLA找图未达标: {pic_name} -> ({x}, {y}), 相似度: {match_val:.4f} < {similarity:.4f}")

                    return result_dict

            logger.debug(f"OLA找图失败: {pic_name}, 无有效返回值")
            return None
        except Exception as e:
            logger.error(f"OLA找图异常: {e}", exc_info=True)
            return None

    def find_pic_ex(self, x1: int, y1: int, x2: int, y2: int,
                    pic_name: str, similarity: float = 0.9, hwnd: int = None) -> List[Tuple[int, int]]:
        """
        查找多个图片

        Args:
            x1, y1: 左上角坐标
            x2, y2: 右下角坐标
            pic_name: 图片路径
            similarity: 相似度 (0.0-1.0)
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）

        Returns:
            List[Tuple[int, int]]: 所有找到的坐标列表
        """
        try:
            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd
            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)
            # OLA找多图方法：MatchWindowsFromPathAll
            result = ola.MatchWindowsFromPathAll(
                x1, y1, x2, y2,
                pic_name,
                similarity,
                2,  # 类型：2=彩色匹配
                0.0,  # 角度：0=不旋转
                1.0  # 缩放：1.0=不缩放
            )

            # result格式：[{"MatchVal": 0.85, "MatchState": true, "MatchPoint": {"x": 100, "y": 200}}, ...]
            coords_list = []
            if result and isinstance(result, list):
                for item in result:
                    if item.get('MatchState'):
                        match_point = item.get('MatchPoint', {})
                        x, y = match_point.get('x'), match_point.get('y')
                        if x is not None and y is not None:
                            coords_list.append((x, y))

            logger.debug(f"OLA找多图成功: {pic_name} -> {len(coords_list)}个")
            return coords_list
        except Exception as e:
            logger.error(f"OLA找多图异常: {e}", exc_info=True)
            return []

    def find_color(self, x1: int, y1: int, x2: int, y2: int,
                   color: str, similarity: float = 1.0, hwnd: int = None) -> Optional[Tuple[int, int]]:
        """
        查找颜色

        Args:
            color: 颜色值,格式如 "FF0000" 表示红色 (RRGGBB格式十六进制)
            similarity: 相似度 (0.0-1.0), 用于计算容差颜色
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）
        """
        try:
            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd

            # OLA的FindColor参数:
            # FindColor(x1, y1, x2, y2, color1, color2, _dir, x=None, y=None)
            # - color1: 颜色范围起始值(十六进制RRGGBB) - 最小RGB值
            # - color2: 颜色范围结束值(十六进制RRGGBB) - 最大RGB值
            # - _dir: 查找方向 (0=从左到右从上到下)
            # 返回: (ret, x, y) - ret=1表示找到

            # 解析RRGGBB颜色
            r = int(color[0:2], 16)
            g = int(color[2:4], 16)
            b = int(color[4:6], 16)

            # 容差计算：将相似度转换回容差
            # 调用方使用: similarity = 1.0 - (tolerance / 255.0)
            # 所以: tolerance = (1.0 - similarity) * 255
            tolerance = int((1.0 - similarity) * 255)
            tolerance = max(0, min(tolerance, 128))  # 限制容差范围

            # 计算颜色范围 (确保不超出0-255)
            r_start = max(0, r - tolerance)
            r_end = min(255, r + tolerance)
            g_start = max(0, g - tolerance)
            g_end = min(255, g + tolerance)
            b_start = max(0, b - tolerance)
            b_end = min(255, b + tolerance)

            # 构造color1(起始)和color2(结束) - RRGGBB格式
            color1 = f"{r_start:02X}{g_start:02X}{b_start:02X}"
            color2 = f"{r_end:02X}{g_end:02X}{b_end:02X}"

            logger.debug(f"OLA FindColor: 区域({x1},{y1})-({x2},{y2}), 目标颜色={color}, 容差=±{tolerance}, 范围={color1}~{color2}, 相似度={similarity}, hwnd={target_hwnd}")

            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)
            # 调用OLA的FindColor
            result = ola.FindColor(x1, y1, x2, y2, color1, color2, 0)
            logger.debug(f"OLA FindColor原始返回: {result}, 类型: {type(result)}")

            # 处理返回值 - OLA Helper返回格式是 (ret, x, y)
            # 但实际测试发现返回的可能是 (x, y, ret) 或 ret=1表示成功
            if isinstance(result, tuple) and len(result) >= 3:
                # 检查哪个值是ret（0或1）
                val0, val1, val2 = result[0], result[1], result[2]

                # 如果第三个值是0或1，说明格式是 (x, y, ret)
                if val2 in (0, 1) and val0 not in (0, 1):
                    ret, found_x, found_y = val2, val0, val1
                    logger.debug(f"OLA FindColor返回格式: (x, y, ret) = ({found_x}, {found_y}, {ret})")
                else:
                    # 标准格式 (ret, x, y)
                    ret, found_x, found_y = val0, val1, val2
                    logger.debug(f"OLA FindColor返回格式: (ret, x, y) = ({ret}, {found_x}, {found_y})")
            else:
                logger.warning(f"OLA FindColor返回格式异常: {result}")
                ret = result if isinstance(result, int) else 0
                found_x, found_y = 0, 0

            if ret == 1:
                logger.debug(f"OLA FindColor找到颜色: ({found_x}, {found_y})")
                return (found_x, found_y)
            else:
                logger.debug(f"OLA FindColor未找到颜色 {color}(范围{color1}~{color2}), ret={ret}")
                return None

        except Exception as e:
            logger.error(f"OLA找色异常: {e}", exc_info=True)
            return None

    def find_multi_color(self, x1: int, y1: int, x2: int, y2: int,
                         first_color: str, offset_colors: str,
                         similarity: float = 1.0, direction: int = 0, hwnd: int = None) -> Optional[Tuple[int, int]]:
        """
        多点找色

        Args:
            x1, y1, x2, y2: 搜索区域坐标
            first_color: 第一个颜色(RRGGBB格式十六进制), 如"FF0000"表示红色
            offset_colors: 偏移点颜色列表, 格式"偏移x,偏移y,颜色|偏移x,偏移y,颜色|..."
                          例如: "10,20,00FF00|30,40,0000FF"
            similarity: 相似度 (0.0-1.0)
            direction: 查找方向 (0-8)
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）

        Returns:
            找到返回 (x, y), 未找到返回 None
        """
        try:
            import json

            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd

            # 辅助函数: 根据相似度计算颜色范围(StartColor和EndColor)
            def calculate_color_range(color_hex: str, similarity: float) -> tuple:
                """
                根据相似度计算颜色范围

                Args:
                    color_hex: RRGGBB格式十六进制颜色, 如"FF0000"表示红色
                    similarity: 相似度 (0.0-1.0)

                Returns:
                    (start_color_hex, end_color_hex) - RRGGBB格式(OLA标准格式)
                """
                # 解析RRGGBB颜色 (OLA使用RRGGBB格式，不是BGR)
                r = int(color_hex[0:2], 16)
                g = int(color_hex[2:4], 16)
                b = int(color_hex[4:6], 16)

                # 容差计算：将相似度转换回容差
                # 调用方使用: similarity = 1.0 - (tolerance / 255.0)
                # 所以: tolerance = (1.0 - similarity) * 255
                tolerance = int((1.0 - similarity) * 255)

                # 限制容差范围
                tolerance = max(0, min(tolerance, 128))

                # 计算范围 (确保不超出0-255)
                r_start = max(0, r - tolerance)
                r_end = min(255, r + tolerance)
                g_start = max(0, g - tolerance)
                g_end = min(255, g + tolerance)
                b_start = max(0, b - tolerance)
                b_end = min(255, b + tolerance)

                # 构造StartColor和EndColor (RRGGBB格式，OLA标准，带#前缀)
                start_color = f"#{r_start:02X}{g_start:02X}{b_start:02X}"
                end_color = f"#{r_end:02X}{g_end:02X}{b_end:02X}"

                return (start_color, end_color, tolerance)

            # 计算第一个颜色的范围
            first_start, first_end, tolerance = calculate_color_range(first_color, similarity)

            # 构造colorList JSON (第一个颜色)
            color_list = [
                {
                    "StartColor": first_start,
                    "EndColor": first_end,
                    "Type": 0
                }
            ]
            color_list_json = json.dumps(color_list, ensure_ascii=False)

            # 构造pointColorList JSON (偏移点颜色)
            point_color_list = []

            if offset_colors:
                # 解析偏移点颜色: "偏移x,偏移y,颜色|偏移x,偏移y,颜色|..."
                offset_parts = offset_colors.split('|')
                for part in offset_parts:
                    part = part.strip()
                    if not part:
                        continue

                    # 解析单个偏移点: "偏移x,偏移y,颜色"
                    tokens = part.split(',')
                    if len(tokens) != 3:
                        logger.warning(f"[OLA多点找色] 偏移点格式错误: {part}")
                        continue

                    try:
                        offset_x = int(tokens[0].strip())
                        offset_y = int(tokens[1].strip())
                        offset_color = tokens[2].strip()

                        # 计算偏移点颜色的范围
                        offset_start, offset_end, _ = calculate_color_range(offset_color, similarity)

                        point_color_list.append({
                            "Point": {"X": offset_x, "Y": offset_y},
                            "Colors": [
                                {
                                    "StartColor": offset_start,
                                    "EndColor": offset_end,
                                    "Type": 0
                                }
                            ]
                        })
                    except ValueError as e:
                        logger.warning(f"[OLA多点找色] 解析偏移点失败: {part}, 错误: {e}")
                        continue

            point_color_list_json = json.dumps(point_color_list, ensure_ascii=False)

            logger.debug(f"OLA FindMultiColor: 区域({x1},{y1})-({x2},{y2}), "
                        f"第一色={first_color}(范围:{first_start}~{first_end}), "
                        f"容差=±{tolerance}, 偏移点数={len(point_color_list)}, "
                        f"相似度={similarity}, 方向={direction}, hwnd={target_hwnd}")
            logger.debug(f"  colorList={color_list_json}")
            logger.debug(f"  pointColorList={point_color_list_json}")

            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)
            # 调用OLA的FindMultiColor
            result = ola.FindMultiColor(
                x1, y1, x2, y2,
                color_list_json,
                point_color_list_json,
                similarity,
                direction
            )
            logger.debug(f"OLA FindMultiColor原始返回: {result}, 类型: {type(result)}")

            # 处理返回值 - 与FindColor相同的格式问题
            if isinstance(result, tuple) and len(result) >= 3:
                val0, val1, val2 = result[0], result[1], result[2]

                # 如果第三个值是0或1，说明格式是 (x, y, ret)
                if val2 in (0, 1) and val0 not in (0, 1):
                    ret, found_x, found_y = val2, val0, val1
                    logger.debug(f"OLA FindMultiColor返回格式: (x, y, ret) = ({found_x}, {found_y}, {ret})")
                else:
                    ret, found_x, found_y = val0, val1, val2
                    logger.debug(f"OLA FindMultiColor返回格式: (ret, x, y) = ({ret}, {found_x}, {found_y})")
            else:
                logger.warning(f"OLA FindMultiColor返回格式异常: {result}")
                ret = result if isinstance(result, int) else 0
                found_x, found_y = 0, 0

            if ret == 1:
                logger.debug(f"OLA FindMultiColor找到: ({found_x}, {found_y})")
                return (found_x, found_y)
            else:
                logger.debug(f"OLA FindMultiColor未找到, ret={ret}")
                return None

        except Exception as e:
            logger.error(f"OLA多点找色异常: {e}", exc_info=True)
            return None

    def get_color(self, x: int, y: int, hwnd: int = None) -> str:
        """获取指定坐标的颜色值

        Args:
            x: X坐标
            y: Y坐标
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）

        Returns:
            str: RRGGBB格式的十六进制字符串,如"FF0000"表示红色
        """
        try:
            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd
            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)
            # 调用OLA的GetColor
            color_hex = ola.GetColor(x, y)

            if color_hex and color_hex.strip():
                # OLA返回的是RRGGBB格式的小写字符串
                color_hex = color_hex.strip().upper()
                logger.debug(f"OLA GetColor({x}, {y}) = {color_hex}")
                return color_hex
            else:
                logger.warning(f"OLA GetColor({x}, {y}) 返回空")
                return ""

        except Exception as e:
            logger.error(f"OLA获取颜色异常: {e}", exc_info=True)
            return ""

    def capture(self, x1: int, y1: int, x2: int, y2: int, hwnd: int = None) -> Any:
        """
        截取屏幕区域

        Args:
            x1, y1: 左上角坐标
            x2, y2: 右下角坐标
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）

        Returns:
            str: 临时图片文件路径，失败返回None
        """
        try:
            import os
            import tempfile

            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd
            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)

            # 创建临时文件路径（使用绝对路径）
            temp_dir = tempfile.gettempdir()
            # 确保目录存在
            if not os.path.exists(temp_dir):
                os.makedirs(temp_dir, exist_ok=True)

            temp_file = os.path.abspath(os.path.join(temp_dir, f"ola_capture_{os.getpid()}_{id(self)}.png"))

            logger.info(f"OLA截图: 区域=({x1},{y1})-({x2},{y2}), 文件={temp_file}")

            # OLA截图方法：Capture(x1, y1, x2, y2, file_path)
            result = ola.Capture(x1, y1, x2, y2, temp_file)

            logger.info(f"OLA Capture返回值: {result}")

            if result == 1:
                # 检查文件是否真的创建了
                if os.path.exists(temp_file):
                    file_size = os.path.getsize(temp_file)
                    logger.info(f"OLA截图成功: {temp_file}, 大小: {file_size} bytes")
                    return temp_file  # 返回文件路径，plugin_bridge会转换为numpy数组
                else:
                    logger.error(f"OLA截图返回成功但文件不存在: {temp_file}")
                    return None
            else:
                logger.error(f"OLA截图失败: result={result}")
                return None
        except Exception as e:
            logger.error(f"OLA截图异常: {e}", exc_info=True)
            return None

    # ========== IInputPlugin 接口实现 ==========

    def _move_mouse(self, x: int, y: int, ola_instance: Any = None, target_hwnd: int = None) -> bool:
        """
        内部方法: 根据配置移动鼠标

        Args:
            x: X坐标
            y: Y坐标
            ola_instance: 要使用的OLA实例，如果为None则自动获取
            target_hwnd: 目标窗口句柄（多实例模式下用于获取正确的窗口配置）

        Returns:
            bool: 移动是否成功
        """
        try:
            # 【多实例线程安全】确定要使用的hwnd
            hwnd_for_config = target_hwnd if target_hwnd else self._bound_hwnd

            # 【多实例线程安全】使用指定的OLA实例或自动获取
            ola = ola_instance if ola_instance else self._get_ola_for_operation(hwnd_for_config)

            # 【多实例线程安全】从多实例管理器获取当前窗口的配置
            # 不使用self._mouse_move_with_trajectory，因为它在多线程下可能被覆盖
            mouse_move_with_trajectory = self._mouse_move_with_trajectory  # 默认值
            bound_mouse_mode = self._bound_mouse_mode  # 默认值

            if self._use_multi_instance and self._multi_instance_manager and hwnd_for_config:
                window_config = self._multi_instance_manager.get_window_config(hwnd_for_config)
                if window_config:
                    mouse_move_with_trajectory = window_config.get('mouse_move_with_trajectory', False)
                    bound_mouse_mode = window_config.get('mouse_mode', 'normal')

            # 【用户需求】无论前台还是后台，都根据 mouse_move_with_trajectory 参数决定是否带轨迹
            # - mouse_move_with_trajectory=true: 使用 MoveTo（带轨迹）
            # - mouse_move_with_trajectory=false: 使用 MoveToWithoutSimulator（无轨迹）

            if mouse_move_with_trajectory:
                # 启用轨迹：使用 MoveTo
                logger.debug(f"OLA轨迹移动到 ({x}, {y}), mouse_mode={bound_mouse_mode}")
                ret = ola.MoveTo(x, y)
            else:
                # 禁用轨迹：使用 MoveToWithoutSimulator
                logger.debug(f"OLA直接移动到 ({x}, {y}), mouse_mode={bound_mouse_mode}")
                ret = ola.MoveToWithoutSimulator(x, y)

            return ret == 1
        except Exception as e:
            logger.error(f"OLA移动异常: {e}", exc_info=True)
            return False

    def mouse_move(self, x: int, y: int, is_screen_coord: bool = False, hwnd: int = None) -> bool:
        """移动鼠标

        Args:
            x: x坐标
            y: y坐标
            is_screen_coord: 是否为屏幕坐标（前台模式时为True）
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）
        """
        # 【多实例线程安全】确定目标窗口句柄
        target_hwnd = hwnd if hwnd else self._bound_hwnd

        # 如果是屏幕坐标且绑定了窗口，需要转换为客户区坐标
        if is_screen_coord and target_hwnd:
            try:
                import ctypes
                from ctypes import wintypes

                # 屏幕坐标转换为客户区坐标
                point = wintypes.POINT(x, y)
                if ctypes.windll.user32.ScreenToClient(target_hwnd, ctypes.byref(point)):
                    x, y = point.x, point.y
                    logger.debug(f"OLA前台模式坐标转换: 屏幕({x}, {y}) -> 客户区({point.x}, {point.y})")
                else:
                    logger.warning(f"OLA前台模式坐标转换失败，使用原始坐标")
            except Exception as e:
                logger.warning(f"OLA前台模式坐标转换异常: {e}")

        # 【多实例线程安全】获取目标窗口专属的OLA实例
        ola = self._get_ola_for_operation(target_hwnd)
        return self._move_mouse(x, y, ola, target_hwnd)

    def mouse_click(self, x: int, y: int, button: str = "left", is_screen_coord: bool = False, hwnd: int = None) -> bool:
        """鼠标点击

        【关键修复】OLA 会根据 BindWindow 时设置的 mouse_mode 参数自动决定如何执行鼠标操作：
        - mouse_mode='normal': 前台模式，移动物理鼠标
        - mouse_mode='windows': 后台模式，通过Windows消息发送，不移动物理鼠标

        因此这里不需要根据 is_screen_coord 来判断，直接调用 OLA 方法即可。

        Args:
            x: x坐标（客户区坐标）
            y: y坐标（客户区坐标）
            button: 按钮类型
            is_screen_coord: 是否为屏幕坐标（仅用于坐标转换，不影响执行方式）
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）
        """
        try:
            if x is None or y is None:
                logger.error("OLA点击失败: 缺少坐标")
                return False
            try:
                x = int(x)
                y = int(y)
            except Exception:
                logger.error(f"OLA点击失败: 坐标无效 ({x}, {y})")
                return False

            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd

            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)

            # 【多实例线程安全】从多实例管理器获取目标窗口的配置
            bound_mouse_mode = self._bound_mouse_mode  # 默认值

            if self._use_multi_instance and self._multi_instance_manager and target_hwnd:
                window_config = self._multi_instance_manager.get_window_config(target_hwnd)
                if window_config:
                    bound_mouse_mode = window_config.get('mouse_mode', 'normal')

            # 坐标转换：如果是屏幕坐标，转换为客户区坐标
            client_x, client_y = x, y
            if is_screen_coord and target_hwnd:
                try:
                    import ctypes
                    from ctypes import wintypes

                    point = wintypes.POINT(x, y)
                    if ctypes.windll.user32.ScreenToClient(target_hwnd, ctypes.byref(point)):
                        client_x, client_y = point.x, point.y
                        logger.debug(f"OLA点击坐标转换: 屏幕({x}, {y}) -> 客户区({client_x}, {client_y})")
                    else:
                        logger.warning(f"OLA点击坐标转换失败，使用原始坐标")
                except Exception as e:
                    logger.warning(f"OLA点击坐标转换异常: {e}")

            # 【关键修复】根据mouse_mode选择移动方法
            # 前台模式(normal)必须使用MoveTo才能移动物理鼠标
            # 后台模式可以使用MoveToWithoutSimulator
            logger.debug(f"OLA点击: 定位到({client_x}, {client_y}), mouse_mode={bound_mouse_mode}")
            if bound_mouse_mode == 'normal':
                # 前台模式：使用MoveTo移动物理鼠标
                move_ret = ola.MoveTo(client_x, client_y)
            else:
                # 后台模式：使用MoveToWithoutSimulator
                move_ret = ola.MoveToWithoutSimulator(client_x, client_y)
            if move_ret != 1:
                logger.error(f"OLA定位失败: ({client_x}, {client_y}), 返回值={move_ret}")
                return False

            # 执行点击
            if button == "left":
                ret = ola.LeftClick()
            elif button == "right":
                ret = ola.RightClick()
            elif button == "middle":
                ret = ola.MiddleClick()
            else:
                logger.warning(f"未知的鼠标按钮: {button}")
                return False

            if ret == 1:
                logger.debug(f"OLA点击成功: ({client_x}, {client_y}), button={button}, mouse_mode={bound_mouse_mode}")
                return True
            else:
                logger.error(f"OLA点击失败: ({client_x}, {client_y}), button={button}, 返回值={ret}")
                return False

        except Exception as e:
            logger.error(f"OLA鼠标点击异常: {e}", exc_info=True)
            return False

    def mouse_double_click(self, x: int, y: int, button: str = "left", hwnd: int = None) -> bool:
        """鼠标双击

        【关键修复】根据mouse_mode选择合适的移动方法

        Args:
            x: x坐标
            y: y坐标
            button: 按钮类型
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）
        """
        try:
            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd

            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)

            # 【多实例线程安全】从多实例管理器获取目标窗口的配置
            bound_mouse_mode = self._bound_mouse_mode  # 默认值

            if self._use_multi_instance and self._multi_instance_manager and target_hwnd:
                window_config = self._multi_instance_manager.get_window_config(target_hwnd)
                if window_config:
                    bound_mouse_mode = window_config.get('mouse_mode', 'normal')

            # 双击：前台模式使用MoveTo，后台模式使用MoveToWithoutSimulator
            logger.debug(f"OLA双击: 定位到({x}, {y}), mouse_mode={bound_mouse_mode}")
            if bound_mouse_mode == 'normal':
                ret = ola.MoveTo(x, y)
            else:
                ret = ola.MoveToWithoutSimulator(x, y)
            if ret != 1:
                logger.error(f"OLA双击定位失败: ({x}, {y})")
                return False

            # 只支持左键双击
            if button == "left":
                ret = ola.LeftDoubleClick()
                if ret == 1:
                    logger.debug(f"OLA双击成功: ({x}, {y})")
                    return True
                else:
                    logger.error(f"OLA双击失败: ({x}, {y}), 返回值={ret}")
                    return False
            else:
                logger.warning(f"OLA只支持左键双击，不支持: {button}")
                return False
        except Exception as e:
            logger.error(f"OLA鼠标双击异常: {e}", exc_info=True)
            return False

    def mouse_drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: float = 1.0,
        button: str = "left",
        hwnd: int = None,
    ) -> bool:
        """鼠标拖拽 - 使用OLA原生方式

        Args:
            x1, y1: 起点坐标
            x2, y2: 终点坐标
            duration: 拖拽持续时间（秒）
            button: 鼠标按钮
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）
        """
        release_fn = None
        try:
            import time

            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd

            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)

            # 【多实例线程安全】获取当前窗口的移动配置
            mouse_move_with_trajectory = self._mouse_move_with_trajectory  # 默认值
            if self._use_multi_instance and self._multi_instance_manager and target_hwnd:
                window_config = self._multi_instance_manager.get_window_config(target_hwnd)
                if window_config:
                    mouse_move_with_trajectory = window_config.get('mouse_move_with_trajectory', False)

            move_mode = '轨迹移动' if mouse_move_with_trajectory else '直接移动'
            button = {
                "左键": "left",
                "右键": "right",
                "中键": "middle",
                "left": "left",
                "right": "right",
                "middle": "middle",
            }.get(str(button or "left").strip().lower(), "")
            if button == "left":
                down_fn = ola.LeftDown
                release_fn = ola.LeftUp
            elif button == "right":
                down_fn = ola.RightDown
                release_fn = ola.RightUp
            elif button == "middle":
                down_fn = ola.MiddleDown
                release_fn = ola.MiddleUp
            else:
                logger.error(f"[OLA拖拽] 不支持的鼠标按钮: {button}")
                return False

            logger.info(
                f"[OLA拖拽] 开始: ({x1}, {y1}) -> ({x2}, {y2}), 时间={duration:.2f}s, "
                f"按钮={button}, hwnd={target_hwnd}, 移动方式={move_mode}"
            )
            start_time = time.time()

            # 1. 移动到起点
            ret = ola.MoveTo(x1, y1)
            if ret != 1:
                logger.error(f"[OLA拖拽] 移动到起点失败: ({x1}, {y1})")
                return False
            time.sleep(0.02)

            # 2. 按下指定按钮
            ret = down_fn()
            if ret != 1:
                logger.error(f"[OLA拖拽] 按下{button}键失败: {ret}")
                return False
            time.sleep(0.02)

            # 3. 平滑移动到终点（消息模式下需要分段移动）
            import math
            distance = math.sqrt((x2 - x1)**2 + (y2 - y1)**2)
            steps = max(int(distance / 10), 10)
            step_duration = (duration - 0.1) / steps

            for i in range(1, steps + 1):
                t = i / steps
                curr_x = int(x1 + (x2 - x1) * t)
                curr_y = int(y1 + (y2 - y1) * t)
                ret = ola.MoveTo(curr_x, curr_y)
                if ret != 1:
                    logger.warning(f"[OLA拖拽] 移动到中间点失败: ({curr_x}, {curr_y})")
                if step_duration > 0:
                    time.sleep(step_duration)

            # 4. 释放指定按钮
            ret = release_fn()
            if ret != 1:
                logger.error(f"[OLA拖拽] 释放{button}键失败: {ret}")
                return False

            actual_duration = time.time() - start_time
            logger.info(f"[OLA拖拽] 完成: ({x1}, {y1}) -> ({x2}, {y2}), 设定={duration:.2f}s, 实际={actual_duration:.2f}s")
            return True

        except Exception as e:
            logger.error(f"OLA鼠标拖拽异常: {e}", exc_info=True)
            try:
                if callable(release_fn):
                    release_fn()
            except:
                pass
            return False

    def mouse_down(self, x: int, y: int, button: str = "left", is_screen_coord: bool = False, hwnd: int = None) -> bool:
        """鼠标按下

        【关键修复】统一使用 MoveToWithoutSimulator，让 OLA 根据绑定参数自动处理

        Args:
            x: x坐标
            y: y坐标
            button: 按钮类型
            is_screen_coord: 是否为屏幕坐标（仅用于坐标转换）
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）
        """
        try:
            if x is None or y is None:
                logger.error("OLA鼠标按下失败: 缺少坐标")
                return False
            try:
                x = int(x)
                y = int(y)
            except Exception:
                logger.error(f"OLA鼠标按下失败: 坐标无效 ({x}, {y})")
                return False

            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd

            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)

            # 【多实例线程安全】从多实例管理器获取目标窗口的配置
            bound_mouse_mode = self._bound_mouse_mode  # 默认值
            if self._use_multi_instance and self._multi_instance_manager and target_hwnd:
                window_config = self._multi_instance_manager.get_window_config(target_hwnd)
                if window_config:
                    bound_mouse_mode = window_config.get('mouse_mode', 'normal')

            logger.info(f"[OLA按下] 准备按下{button}键: ({x}, {y}), mouse_mode={bound_mouse_mode}, hwnd={target_hwnd}")

            # 坐标转换
            client_x, client_y = x, y
            if is_screen_coord and target_hwnd:
                try:
                    import ctypes
                    from ctypes import wintypes

                    point = wintypes.POINT(x, y)
                    if ctypes.windll.user32.ScreenToClient(target_hwnd, ctypes.byref(point)):
                        client_x, client_y = point.x, point.y
                        logger.debug(f"OLA按下坐标转换: 屏幕({x}, {y}) -> 客户区({client_x}, {client_y})")
                except Exception as e:
                    logger.warning(f"OLA按下坐标转换异常: {e}")

            # 按下：统一使用MoveToWithoutSimulator，让OLA根据绑定参数自动处理前后台
            ret = ola.MoveToWithoutSimulator(client_x, client_y)
            if ret != 1:
                logger.error(f"OLA按下定位失败: ({client_x}, {client_y})")
                return False

            logger.info(f"[OLA按下] 已定位,准备按下...")

            # 根据按钮类型按下
            if button == "left":
                ret = ola.LeftDown()
            elif button == "right":
                ret = ola.RightDown()
            elif button == "middle":
                ret = ola.MiddleDown()
            else:
                logger.warning(f"未知的鼠标按钮: {button}")
                return False

            logger.info(f"[OLA按下] 按下{button}键结果: {ret} (1=成功, 0=失败)")
            return ret == 1
        except Exception as e:
            logger.error(f"OLA鼠标按下异常: {e}", exc_info=True)
            return False

    def mouse_up(self, x: int, y: int, button: str = "left", is_screen_coord: bool = False, hwnd: int = None) -> bool:
        """鼠标释放

        【关键修复】统一使用 MoveToWithoutSimulator，让 OLA 根据绑定参数自动处理

        Args:
            x: x坐标
            y: y坐标
            button: 按钮类型
            is_screen_coord: 是否为屏幕坐标（仅用于坐标转换）
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）
        """
        try:
            if x is None or y is None:
                logger.error("OLA鼠标释放失败: 缺少坐标")
                return False
            try:
                x = int(x)
                y = int(y)
            except Exception:
                logger.error(f"OLA鼠标释放失败: 坐标无效 ({x}, {y})")
                return False

            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd

            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)

            # 从多实例管理器获取配置
            bound_mouse_mode = self._bound_mouse_mode
            if self._use_multi_instance and self._multi_instance_manager and target_hwnd:
                window_config = self._multi_instance_manager.get_window_config(target_hwnd)
                if window_config:
                    bound_mouse_mode = window_config.get('mouse_mode', 'normal')

            logger.info(f"[OLA释放] 准备释放{button}键: ({x}, {y}), mouse_mode={bound_mouse_mode}, hwnd={target_hwnd}")

            # 坐标转换
            client_x, client_y = x, y
            if is_screen_coord and target_hwnd:
                try:
                    import ctypes
                    from ctypes import wintypes

                    point = wintypes.POINT(x, y)
                    if ctypes.windll.user32.ScreenToClient(target_hwnd, ctypes.byref(point)):
                        client_x, client_y = point.x, point.y
                        logger.debug(f"OLA释放坐标转换: 屏幕({x}, {y}) -> 客户区({client_x}, {client_y})")
                except Exception as e:
                    logger.warning(f"OLA释放坐标转换异常: {e}")

            # 释放：统一使用MoveToWithoutSimulator，让OLA根据绑定参数自动处理前后台
            ret = ola.MoveToWithoutSimulator(client_x, client_y)
            if ret != 1:
                logger.error(f"OLA释放定位失败: ({client_x}, {client_y})")
                return False

            logger.info(f"[OLA释放] 已定位,准备释放...")

            # 根据按钮类型释放
            if button == "left":
                ret = ola.LeftUp()
            elif button == "right":
                ret = ola.RightUp()
            elif button == "middle":
                ret = ola.MiddleUp()
            else:
                logger.warning(f"未知的鼠标按钮: {button}")
                return False

            logger.info(f"[OLA释放] 释放{button}键结果: {ret} (1=成功, 0=失败)")
            return ret == 1
        except Exception as e:
            logger.error(f"OLA鼠标释放异常: {e}", exc_info=True)
            return False

    def mouse_scroll(self, x: int, y: int, delta: int, hwnd: int = None) -> bool:
        """鼠标滚轮

        Args:
            x: X坐标
            y: Y坐标
            delta: 滚动量，遵循全项目统一约定
                   正数: 向上滚动（WheelUp）
                   负数: 向下滚动（WheelDown）
                   绝对值通常是 120 的倍数
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）

        Returns:
            bool: 是否成功
        """
        try:
            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd

            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)

            logger.info(f"[OLA滚轮] 在位置({x}, {y})滚动，delta={delta}, hwnd={target_hwnd}")

            # 1. 移动到目标位置（传入OLA实例和目标窗口句柄）
            if not self._move_mouse(x, y, ola, target_hwnd):
                logger.error(f"[OLA滚轮] 移动到目标位置失败: ({x}, {y})")
                return False

            time.sleep(0.05)  # 移动后短暂延迟

            # 2. 执行滚轮操作
            # 与前台/后台链路保持一致：正数上滚，负数下滚。
            # 计算滚动次数（delta 通常是 120 的倍数）
            scroll_count = abs(delta) // 120
            if scroll_count == 0:
                scroll_count = 1  # 至少滚动一次

            success_count = 0

            for i in range(scroll_count):
                if delta > 0:
                    # 正数 = 向上滚动
                    ret = ola.WheelUp()
                    logger.debug(f"[OLA滚轮] 第{i+1}次向上滚动，结果: {ret}")
                else:
                    # 负数 = 向下滚动
                    ret = ola.WheelDown()
                    logger.debug(f"[OLA滚轮] 第{i+1}次向下滚动，结果: {ret}")

                if ret == 1:
                    success_count += 1
                else:
                    logger.warning(f"[OLA滚轮] 第{i+1}次滚动失败: {ret}")

            logger.info(f"[OLA滚轮] 完成滚动: 成功{success_count}/{scroll_count}次")
            return success_count > 0

        except Exception as e:
            logger.error(f"OLA鼠标滚轮异常: {e}", exc_info=True)
            return False

    def key_press(self, vk_code: int, hwnd: int = None) -> bool:
        """按下按键

        Args:
            vk_code: 虚拟键码
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）
        """
        try:
            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd
            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)
            ret = ola.KeyPress(vk_code)
            return ret == 1
        except Exception as e:
            logger.error(f"OLA按键异常: {e}", exc_info=True)
            return False

    def key_down(self, vk_code: int, hwnd: int = None) -> bool:
        """按键按下

        Args:
            vk_code: 虚拟键码
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）
        """
        try:
            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd

            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)

            # 【多实例线程安全】从多实例管理器获取目标窗口的配置
            bound_keypad_mode = self._bound_keypad_mode
            if self._use_multi_instance and self._multi_instance_manager and target_hwnd:
                window_config = self._multi_instance_manager.get_window_config(target_hwnd)
                if window_config:
                    bound_keypad_mode = window_config.get('keypad_mode', 'normal')

            if not ola:
                logger.error(f"[OLA按键按下] 未绑定窗口或OLA实例不可用")
                return False

            # 确保vk_code是整数
            if isinstance(vk_code, str):
                vk_code = int(vk_code)

            logger.debug(f"[OLA按键按下] VK={vk_code}, keypad_mode={bound_keypad_mode}, hwnd={target_hwnd}")
            ret = ola.KeyDown(vk_code)
            success = ret == 1

            if not success:
                logger.warning(f"[OLA按键按下] 失败: VK={vk_code}, ret={ret}")

            return success
        except Exception as e:
            logger.error(f"OLA按键按下异常: {e}", exc_info=True)
            return False

    def key_up(self, vk_code: int, hwnd: int = None) -> bool:
        """按键释放

        Args:
            vk_code: 虚拟键码
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）
        """
        try:
            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd

            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)

            # 【多实例线程安全】从多实例管理器获取目标窗口的配置
            bound_keypad_mode = self._bound_keypad_mode
            if self._use_multi_instance and self._multi_instance_manager and target_hwnd:
                window_config = self._multi_instance_manager.get_window_config(target_hwnd)
                if window_config:
                    bound_keypad_mode = window_config.get('keypad_mode', 'normal')

            if not ola:
                logger.error(f"[OLA按键释放] 未绑定窗口或OLA实例不可用")
                return False

            # 确保vk_code是整数
            if isinstance(vk_code, str):
                vk_code = int(vk_code)

            logger.debug(f"[OLA按键释放] VK={vk_code}, keypad_mode={bound_keypad_mode}, hwnd={target_hwnd}")
            ret = ola.KeyUp(vk_code)
            success = ret == 1

            if not success:
                logger.warning(f"[OLA按键释放] 失败: VK={vk_code}, ret={ret}")

            return success
        except Exception as e:
            logger.error(f"OLA按键释放异常: {e}", exc_info=True)
            return False

    def key_input_text(self, text: str, hwnd: int = None) -> bool:
        """输入文字

        OLA的SendString可能需要窗口句柄参数

        Args:
            text: 要输入的文字
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）
        """
        try:
            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd
            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)
            # OLA的SendString方法签名: SendString(hwnd, text)
            if target_hwnd:
                ret = ola.SendString(target_hwnd, text)
                logger.info(f"OLA SendString with hwnd={target_hwnd}, text='{text}', result={ret}")
                return ret == 1
            else:
                logger.error("OLA输入文字失败: 未绑定窗口")
                return False
        except Exception as e:
            logger.error(f"OLA输入文字异常: {e}", exc_info=True)
            return False

    # ========== IOCRPlugin 接口实现 ==========

    def ocr(self, x1: int, y1: int, x2: int, y2: int, hwnd: int = None) -> str:
        """OCR识别区域文字（带坐标信息）

        【优化】减少调试日志，移除测试截图，优化执行顺序

        返回格式：
        - 如果识别成功，返回JSON格式字符串，包含文字和坐标信息
        - 如果识别失败，返回空字符串

        JSON格式示例：
        {
            "Text": "完整文字",
            "Regions": [
                {
                    "Text": "文字1",
                    "Center": {"x": 100, "y": 200},
                    "Vertices": [...],
                    "Score": 0.95
                }
            ]
        }

        Args:
            x1, y1: 左上角坐标
            x2, y2: 右下角坐标
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）
        """
        # 【性能优化】使用OCR队列控制并发，避免多窗口竞争导致卡顿
        try:
            from .multi_instance_manager import execute_ocr_with_queue
            return execute_ocr_with_queue(self._do_ocr, x1, y1, x2, y2, hwnd)
        except ImportError:
            # 队列管理器不可用，直接执行
            return self._do_ocr(x1, y1, x2, y2, hwnd)

    def _do_ocr(self, x1: int, y1: int, x2: int, y2: int, hwnd: int = None) -> str:
        """实际执行OCR的内部方法"""
        try:
            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd
            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)

            logger.debug(f"[OLA OCR] 区域: ({x1},{y1})->({x2},{y2}), hwnd={target_hwnd}")

            import json

            # 【优化】优先使用带详细信息的OcrDetails方法，这是最常用的
            try:
                details_result = ola.OcrDetails(x1, y1, x2, y2)
                if details_result:
                    json_data = None
                    if isinstance(details_result, dict):
                        json_data = details_result
                    elif isinstance(details_result, str) and details_result.strip():
                        try:
                            json_data = json.loads(details_result)
                        except json.JSONDecodeError:
                            pass

                    if json_data and (json_data.get("Text") or json_data.get("Regions")):
                        logger.debug(f"[OLA OCR] OcrDetails成功: Text长度={len(json_data.get('Text', ''))}")
                        return json.dumps(json_data, ensure_ascii=False)
            except Exception as e:
                logger.debug(f"[OLA OCR] OcrDetails失败: {e}")

            # 【优化】第二选择：使用简单的Ocr方法
            try:
                text = ola.Ocr(x1, y1, x2, y2)
                if text and text.strip():
                    result_json = {
                        "Text": text.strip(),
                        "Regions": []
                    }
                    logger.debug(f"[OLA OCR] Ocr成功: Text长度={len(text.strip())}")
                    return json.dumps(result_json, ensure_ascii=False)
            except Exception as e:
                logger.debug(f"[OLA OCR] Ocr失败: {e}")

            logger.debug(f"[OLA OCR] 所有方法均未识别到文字")
            return ""
        except Exception as e:
            logger.error(f"OLA OCR异常: {e}", exc_info=True)
            return ""

    def find_text(self, x1: int, y1: int, x2: int, y2: int,
                  text: str, hwnd: int = None) -> Optional[Tuple[int, int]]:
        """查找文字位置

        Args:
            x1, y1: 左上角坐标
            x2, y2: 右下角坐标
            text: 要查找的文字
            hwnd: 目标窗口句柄（多实例模式下用于获取正确的OLA实例，为None时使用self._bound_hwnd）
        """
        try:
            # 【多实例线程安全】确定目标窗口句柄
            target_hwnd = hwnd if hwnd else self._bound_hwnd
            # 【多实例线程安全】获取目标窗口专属的OLA实例
            ola = self._get_ola_for_operation(target_hwnd)
            # FindStr 参数: x1, y1, x2, y2, str, colorJson, dict, matchVal, outX, outY
            # 使用默认参数: 空颜色、默认字典、0.9匹配度
            ret, out_x, out_y = ola.FindStr(x1, y1, x2, y2, text, "{}", "", 0.9)

            if ret == 1 and out_x != -1 and out_y != -1:
                return (out_x, out_y)
            return None
        except Exception as e:
            logger.error(f"OLA查找文字异常: {e}", exc_info=True)
            return None

    # ==================== 窗口分辨率调整 ====================

    def set_client_size(self, hwnd: int, width: int, height: int) -> bool:
        """
        设置窗口客户区大小（不包含标题栏和边框）

        【插件模式隔离】此方法仅在插件模式下使用，不会降级到原有逻辑

        Args:
            hwnd: 窗口句柄
            width: 目标客户区宽度
            height: 目标客户区高度

        Returns:
            bool: 是否成功
        """
        try:
            # 【多实例线程安全】获取指定窗口的OLA实例
            ola = self._get_ola_for_operation(hwnd)
            if not ola:
                logger.error("[OLA SetClientSize] OLA实例未初始化")
                return False

            if not hwnd or hwnd <= 0:
                logger.error(f"[OLA SetClientSize] 无效的窗口句柄: {hwnd}")
                return False

            if width <= 0 or height <= 0:
                logger.error(f"[OLA SetClientSize] 无效的尺寸: {width}x{height}")
                return False

            logger.info(f"[OLA SetClientSize] 设置窗口(hwnd={hwnd})客户区大小为: {width}x{height}")

            # 调用OLA的SetClientSize API
            ret = ola.SetClientSize(hwnd, width, height)

            if ret == 1:
                logger.info(f"[OLA SetClientSize] 设置成功: {width}x{height}")
                return True
            else:
                logger.warning(f"[OLA SetClientSize] 设置失败，返回值: {ret}")
                return False

        except Exception as e:
            logger.error(f"[OLA SetClientSize] 异常: {e}", exc_info=True)
            return False

    def set_window_size(self, hwnd: int, width: int, height: int) -> bool:
        """
        设置窗口整体大小（包含标题栏和边框）

        【插件模式隔离】此方法仅在插件模式下使用，不会降级到原有逻辑

        Args:
            hwnd: 窗口句柄
            width: 目标窗口宽度
            height: 目标窗口高度

        Returns:
            bool: 是否成功
        """
        try:
            # 【多实例线程安全】获取指定窗口的OLA实例
            ola = self._get_ola_for_operation(hwnd)
            if not ola:
                logger.error("[OLA SetWindowSize] OLA实例未初始化")
                return False

            if not hwnd or hwnd <= 0:
                logger.error(f"[OLA SetWindowSize] 无效的窗口句柄: {hwnd}")
                return False

            if width <= 0 or height <= 0:
                logger.error(f"[OLA SetWindowSize] 无效的尺寸: {width}x{height}")
                return False

            logger.info(f"[OLA SetWindowSize] 设置窗口(hwnd={hwnd})大小为: {width}x{height}")

            # 调用OLA的SetWindowSize API
            ret = ola.SetWindowSize(hwnd, width, height)

            if ret == 1:
                logger.info(f"[OLA SetWindowSize] 设置成功: {width}x{height}")
                return True
            else:
                logger.warning(f"[OLA SetWindowSize] 设置失败，返回值: {ret}")
                return False

        except Exception as e:
            logger.error(f"[OLA SetWindowSize] 异常: {e}", exc_info=True)
            return False

    def get_client_size(self, hwnd: int) -> Optional[Tuple[int, int]]:
        """
        获取窗口客户区大小

        Args:
            hwnd: 窗口句柄

        Returns:
            Optional[Tuple[int, int]]: (宽度, 高度) 或 None
        """
        try:
            # 【多实例线程安全】获取指定窗口的OLA实例
            ola = self._get_ola_for_operation(hwnd)
            if not ola:
                logger.error("[OLA GetClientSize] OLA实例未初始化")
                return None

            if not hwnd or hwnd <= 0:
                logger.error(f"[OLA GetClientSize] 无效的窗口句柄: {hwnd}")
                return None

            # 调用OLA的GetClientSize API
            ret, width, height = ola.GetClientSize(hwnd)

            if ret == 1 and width > 0 and height > 0:
                logger.debug(f"[OLA GetClientSize] hwnd={hwnd}, 大小: {width}x{height}")
                return (width, height)
            else:
                logger.warning(f"[OLA GetClientSize] 获取失败，ret={ret}")
                return None

        except Exception as e:
            logger.error(f"[OLA GetClientSize] 异常: {e}", exc_info=True)
            return None
