# -*- coding: utf-8 -*-
"""
OLA 多实例管理器

解决问题：
1. 多工作流同时运行时虚拟键盘输入冲突（问题7）
2. 多开后窗口绑定穿透问题（问题8）

原理：
- OLA每个实例只能同时绑定一个窗口
- 通过为每个窗口创建独立的OLA实例，实现真正的并行操作
- 每个窗口的操作在其专属OLA实例上执行，互不干扰
"""

import logging
import threading
import time
from typing import Dict, Optional, Any

from plugins.adapters.ola.auth import authorize_ola_instance
from plugins.adapters.ola.runtime_config import (
    get_ola_registration_info,
    get_ola_sdk_dir,
)

logger = logging.getLogger(__name__)


class OLAMultiInstanceManager:
    """
    OLA多实例管理器

    为每个窗口维护独立的OLA实例，解决多窗口并发操作时的绑定冲突问题。

    使用方式：
    1. 获取管理器单例: manager = get_ola_instance_manager()
    2. 获取窗口专属实例: ola = manager.get_instance_for_window(hwnd)
    3. 使用ola执行操作（已自动绑定到对应窗口）
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        """单例模式"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        # 窗口到OLA实例的映射: {hwnd: {'ola': OLAPlugServer, 'config': {...}, 'lock': RLock}}
        self._window_instances: Dict[int, Dict[str, Any]] = {}

        # 全局锁，保护实例创建/删除
        self._global_lock = threading.RLock()
        self._last_failure_detail = ""

        # OLA类引用（延迟导入）
        self._OLAPlugServer = None
        self._USE_COM = False
        self._OLA_AVAILABLE = False

        self._initialized = True
        logger.info("[OLA多实例管理器] 初始化完成")
        user_code, _, _ = get_ola_registration_info()
        masked_user_code = f"{user_code[:8]}..." if user_code else "(空)"
        logger.info(f"[OLA多实例管理器] 注册信息: user_code={masked_user_code}")

    def _ensure_ola_imported(self) -> bool:
        """确保OLA SDK已导入"""
        if self._OLA_AVAILABLE:
            return True

        try:
            # 尝试导入OLA
            from .adapter import _try_import_ola, _OLAPlugServer, _USE_COM, OLA_AVAILABLE

            if not OLA_AVAILABLE:
                _try_import_ola()

            # 重新获取导入结果
            from .adapter import _OLAPlugServer, _USE_COM, OLA_AVAILABLE

            self._OLAPlugServer = _OLAPlugServer
            self._USE_COM = _USE_COM
            self._OLA_AVAILABLE = OLA_AVAILABLE

            if self._OLA_AVAILABLE:
                logger.info(f"[OLA多实例管理器] OLA SDK导入成功 ({'COM' if self._USE_COM else 'DLL'}模式)")
            else:
                logger.error("[OLA多实例管理器] OLA SDK导入失败")

            return self._OLA_AVAILABLE

        except Exception as e:
            logger.error(f"[OLA多实例管理器] 导入OLA SDK异常: {e}")
            return False

    def _is_window_handle_valid(self, hwnd: int) -> bool:
        """检查窗口句柄是否仍然有效"""
        if not hwnd or hwnd <= 0:
            return False
        try:
            import win32gui
            return bool(win32gui.IsWindow(hwnd))
        except Exception:
            # 无法校验时不阻塞流程，交由绑定接口返回值判定
            return True

    @staticmethod
    def _normalize_bind_config(config: Optional[dict] = None) -> Dict[str, Any]:
        """统一补齐绑定配置默认值，保证比较和绑定链路一致。"""
        source = config or {}
        return {
            'display_mode': source.get('display_mode', 'normal'),
            'mouse_mode': source.get('mouse_mode', 'normal'),
            'keypad_mode': source.get('keypad_mode', 'normal'),
            'mode': source.get('mode', 0),
            'input_lock': source.get('input_lock', False),
            'mouse_move_with_trajectory': source.get('mouse_move_with_trajectory', False),
            'pubstr': source.get('pubstr', ''),
        }

    @staticmethod
    def _diff_bind_configs(cached_config: Optional[dict], new_config: Optional[dict]) -> list:
        """返回绑定配置差异，保证重绑判断只走一套规则。"""
        cached = OLAMultiInstanceManager._normalize_bind_config(cached_config)
        current = OLAMultiInstanceManager._normalize_bind_config(new_config)
        key_params = [
            'display_mode',
            'mouse_mode',
            'keypad_mode',
            'mode',
            'input_lock',
            'mouse_move_with_trajectory',
            'pubstr',
        ]
        diff_params = []
        for key in key_params:
            cached_val = cached.get(key)
            new_val = current.get(key)
            if cached_val != new_val:
                diff_params.append(f"{key}: {cached_val} -> {new_val}")
        return diff_params

    @staticmethod
    def _destroy_ola_instance(ola: Optional[Any]):
        """统一释放OLA实例，确保临时探测和失败路径都能闭环清理。"""
        if ola is None:
            return
        try:
            ola.UnBindWindow()
        except Exception:
            pass
        try:
            ola.DestroyCOLAPlugInterFace()
        except Exception:
            pass

    @staticmethod
    def _get_ola_last_error_details(ola: Optional[Any]) -> tuple[int, str]:
        """读取 OLA 最近一次错误，失败时返回空结果，不影响主流程。"""
        if ola is None:
            return 0, ""

        error_code = 0
        error_text = ""

        try:
            getter = getattr(ola, "GetLastError", None)
            if callable(getter):
                error_code = int(getter() or 0)
        except Exception:
            error_code = 0

        try:
            getter = getattr(ola, "GetLastErrorString", None)
            if callable(getter):
                error_text = str(getter() or "").strip()
        except Exception:
            error_text = ""

        return error_code, error_text

    def _set_last_failure_detail(self, detail: str) -> None:
        with self._global_lock:
            self._last_failure_detail = str(detail or "").strip()

    def _clear_last_failure_detail(self) -> None:
        self._set_last_failure_detail("")

    def get_last_failure_detail(self) -> str:
        with self._global_lock:
            return self._last_failure_detail

    def _bind_window_with_retry(
        self,
        ola: Any,
        hwnd: int,
        display_mode: str,
        mouse_mode: str,
        keypad_mode: str,
        mode: int,
        pubstr: str = "",
        max_retries: int = 3,
        retry_delay: float = 0.12,
        success_delay: float = 0.05,
    ) -> int:
        """带重试的绑定，降低偶发 ret=0 概率"""
        last_ret = 0

        for attempt in range(max_retries):
            if not self._is_window_handle_valid(hwnd):
                logger.error(f"[OLA多实例管理器] 窗口句柄无效，停止绑定: hwnd={hwnd}")
                return 0

            if attempt > 0:
                try:
                    ola.SetWindowState(hwnd, 1)
                except Exception:
                    pass
                time.sleep(retry_delay)

            if pubstr:
                last_ret = ola.BindWindowEx(hwnd, display_mode, mouse_mode, keypad_mode, pubstr, mode)
            else:
                last_ret = ola.BindWindow(hwnd, display_mode, mouse_mode, keypad_mode, mode)

            if last_ret == 1:
                if attempt > 0:
                    logger.info(f"[OLA多实例管理器] 窗口 {hwnd} 第 {attempt + 1} 次绑定成功")
                if success_delay > 0:
                    time.sleep(success_delay)
                return 1

            logger.warning(
                f"[OLA多实例管理器] 窗口 {hwnd} 绑定失败 (尝试 {attempt + 1}/{max_retries}): ret={last_ret}"
            )
            error_code, error_text = self._get_ola_last_error_details(ola)
            if error_code or error_text:
                logger.warning(
                    f"[OLA多实例管理器] 窗口 {hwnd} 绑定失败详情: code={error_code}, message={error_text or '(空)'}"
                )

        return last_ret

    def get_instance_for_window(self, hwnd: int, config: dict = None) -> Optional[Any]:
        """
        获取指定窗口的专属OLA实例

        如果该窗口已有实例且配置相同，直接返回；
        如果配置不同或没有实例，创建新实例并绑定。

        Args:
            hwnd: 窗口句柄
            config: 绑定配置，包含 display_mode, mouse_mode, keypad_mode, mode, input_lock 等
                   如果为None，使用默认配置

        Returns:
            OLA实例（OLAPlugServer），失败返回None
        """
        if not hwnd or hwnd <= 0:
            logger.error(f"[OLA多实例管理器] 无效的窗口句柄: {hwnd}")
            return None

        # 【线程安全修复】快速路径也需要加锁，避免多线程竞争
        # 使用RLock，允许同一线程多次获取锁
        with self._global_lock:
            # 快速路径：如果config为None且窗口已绑定，直接返回缓存实例
            if config is None and hwnd in self._window_instances:
                if not self._is_window_handle_valid(hwnd):
                    self.release_instance(hwnd)
                    return None
                return self._window_instances[hwnd]['ola']

        if not self._ensure_ola_imported():
            logger.error("[OLA多实例管理器] OLA SDK不可用")
            return None

        with self._global_lock:
            # 如果config为None，优先返回已缓存的实例（保持原配置不变）
            if config is None:
                if hwnd in self._window_instances:
                    logger.debug(f"[OLA多实例管理器] 窗口 {hwnd} 复用现有OLA实例（未传入新配置）")
                    return self._window_instances[hwnd]['ola']
                else:
                    # 没有缓存实例时才使用默认配置创建
                    config = self._normalize_bind_config()

            # 复制配置，避免调用方对象被就地修改
            config = self._normalize_bind_config(config)

            # 检查是否已有该窗口的实例
            if hwnd in self._window_instances:
                instance_data = self._window_instances[hwnd]
                cached_config = self._normalize_bind_config(instance_data.get('config', {}))
                diff_params = self._diff_bind_configs(cached_config, config)
                config_same = len(diff_params) == 0

                if config_same:
                    logger.debug(f"[OLA多实例管理器] 窗口 {hwnd} 复用现有OLA实例")
                    return instance_data['ola']
                else:
                    # 配置变化，需要重新绑定
                    logger.info(f"[OLA多实例管理器] 窗口 {hwnd} 配置变化，重新绑定")
                    logger.info(f"[OLA多实例管理器] 变化的参数: {diff_params}")
                    if self._rebind_instance(hwnd, config):
                        return self._window_instances[hwnd]['ola']
                    # 重绑定失败时销毁旧实例并重建，避免返回未绑定实例
                    self.release_instance(hwnd)
                    return self._create_instance_for_window(hwnd, config)
            else:
                # 创建新实例
                return self._create_instance_for_window(hwnd, config)

    def probe_window_binding(self, hwnd: int, config: dict = None) -> bool:
        """探测窗口能否完成绑定，并在成功时预热缓存实例供后续执行复用。"""
        self._clear_last_failure_detail()
        if not hwnd or hwnd <= 0:
            self._set_last_failure_detail("无效的窗口句柄")
            logger.error(f"[OLA多实例管理器] 无效的窗口句柄: {hwnd}")
            return False

        if not self._ensure_ola_imported():
            self._set_last_failure_detail("OLA SDK 不可用")
            logger.error("[OLA多实例管理器] OLA SDK不可用")
            return False

        config = self._normalize_bind_config(config)
        # 预检直接走正式实例获取链路：
        # 1. 复用已有实例时立即返回
        # 2. 首次预检时创建并缓存实例，后续执行直接复用，避免每次启动都重复耗时绑定
        ola = self.get_instance_for_window(hwnd, config)
        if ola is None:
            return False

        self._clear_last_failure_detail()
        return True

    def _create_instance_for_window(
        self,
        hwnd: int,
        config: dict,
        persist_instance: bool = True,
        max_bind_retries: int = 3,
        retry_delay: float = 0.12,
        success_delay: float = 0.05,
    ) -> Optional[Any]:
        """
        为窗口创建新的OLA实例

        Args:
            hwnd: 窗口句柄
            config: 绑定配置

        Returns:
            新创建的OLA实例
        """
        try:
            import json
            config = self._normalize_bind_config(config)
            instance_label = "新OLA实例" if persist_instance else "临时OLA实例"
            logger.info(f"[OLA多实例管理器] 为窗口 {hwnd} 创建{instance_label}")

            # 创建新OLA实例
            ola = self._OLAPlugServer()

            auth_result = authorize_ola_instance(ola)
            if not auth_result.success:
                logger.error(f"[OLA多实例管理器] 窗口 {hwnd} OLA 登录失败: {auth_result.message}")
                self._set_last_failure_detail(auth_result.message)
                self._destroy_ola_instance(ola)
                return None

            logger.debug(f"[OLA多实例管理器] 窗口 {hwnd} OLA登录成功")

            # 创建对象
            if ola.CreateCOLAPlugInterFace() == 0:
                self._set_last_failure_detail("OLA 实例创建失败")
                logger.error(f"[OLA多实例管理器] 窗口 {hwnd} 创建OLA实例失败")
                self._destroy_ola_instance(ola)
                return None

            # 设置工作路径
            try:
                ola_dll_dir = get_ola_sdk_dir()
                ola.SetPath(ola_dll_dir)
            except Exception as e:
                logger.warning(f"[OLA多实例管理器] 设置工作路径失败: {e}")

            # 【新增】配置OCR参数
            try:
                ocr_config = {
                    "OcrDetDbThresh": 0.1,
                    "OcrDetDbBoxThresh": 0.3,
                    "OcrUseAngleCls": True,
                    "OcrDetDbUnclipRatio": 3.0,
                    "OcrRecBatchNum": 6,
                    "OcrMinArea": 3,
                }

                result = ola.SetOcrConfig(json.dumps(ocr_config))
                if result == 1:
                    logger.debug(f"[OLA多实例管理器] 窗口 {hwnd} OCR配置成功")
            except Exception as e:
                logger.debug(f"[OLA多实例管理器] 窗口 {hwnd} OCR配置异常: {e}")

            # 【新增】设置公共属性（InputLock和SimModeType配置）
            try:
                input_lock = config.get('input_lock', False)
                mouse_move_with_trajectory = config.get('mouse_move_with_trajectory', False)

                ola_common_config = {
                    "InputLock": input_lock,
                    "SimModeType": 0,
                    "EnableRealMouse": mouse_move_with_trajectory,
                }

                # 启用轨迹时添加默认轨迹参数
                if mouse_move_with_trajectory:
                    ola_common_config.update({
                        "RealMouseMode": 1,
                        "RealMouseBaseTimePer100Pixels": 200,
                        "RealMouseNoise": 5.0,
                        "RealMouseDeviation": 25,
                        "RealMouseMinSteps": 150,
                    })

                result = ola.SetConfig(json.dumps(ola_common_config))
                if result == 1:
                    trajectory_status = "启用" if mouse_move_with_trajectory else "禁用"
                    logger.debug(f"[OLA多实例管理器] 窗口 {hwnd} 配置成功: InputLock={input_lock}, 鼠标轨迹={trajectory_status}")
            except Exception as e:
                logger.debug(f"[OLA多实例管理器] 窗口 {hwnd} 公共属性配置异常: {e}")

            # 绑定窗口
            display_mode = config.get('display_mode', 'normal')
            mouse_mode = config.get('mouse_mode', 'normal')
            keypad_mode = config.get('keypad_mode', 'normal')
            mode = config.get('mode', 0)
            pubstr = config.get('pubstr', '')

            if pubstr:
                logger.info(f"[OLA多实例管理器] 窗口 {hwnd} 使用 BindWindowEx: pubstr={pubstr}")
            ret = self._bind_window_with_retry(
                ola,
                hwnd,
                display_mode,
                mouse_mode,
                keypad_mode,
                mode,
                pubstr,
                max_retries=max_bind_retries,
                retry_delay=retry_delay,
                success_delay=success_delay,
            )

            if ret != 1:
                logger.error(f"[OLA多实例管理器] 窗口 {hwnd} 绑定失败: ret={ret}")
                error_code, error_text = self._get_ola_last_error_details(ola)
                failure_detail = error_text or (f"错误码 {error_code}" if error_code else "插件绑定失败")
                self._set_last_failure_detail(failure_detail)
                if error_code or error_text:
                    logger.error(
                        f"[OLA多实例管理器] 窗口 {hwnd} 最终绑定失败详情: code={error_code}, message={error_text or '(空)'}"
                    )
                self._destroy_ola_instance(ola)
                return None

            self._clear_last_failure_detail()
            if not persist_instance:
                return ola

            # 保存实例信息
            self._window_instances[hwnd] = {
                'ola': ola,
                'config': config.copy(),
                'lock': threading.RLock()
            }

            logger.info(f"[OLA多实例管理器] 窗口 {hwnd} OLA实例创建并绑定成功 "
                       f"(display={display_mode}, mouse={mouse_mode}, keypad={keypad_mode})")

            return ola

        except Exception as e:
            logger.error(f"[OLA多实例管理器] 创建窗口 {hwnd} OLA实例异常: {e}", exc_info=True)
            return None

    def _rebind_instance(self, hwnd: int, new_config: dict) -> bool:
        """
        重新绑定已存在的实例

        Args:
            hwnd: 窗口句柄
            new_config: 新配置
        """
        if hwnd not in self._window_instances:
            return False

        try:
            import json
            new_config = self._normalize_bind_config(new_config)
            instance_data = self._window_instances[hwnd]
            ola = instance_data['ola']

            # 先解绑
            try:
                ola.UnBindWindow()
            except:
                pass

            # 【关键修复】重新设置公共属性（InputLock和鼠标轨迹配置）
            try:
                input_lock = new_config.get('input_lock', False)
                mouse_move_with_trajectory = new_config.get('mouse_move_with_trajectory', False)

                ola_common_config = {
                    "InputLock": input_lock,
                    "EnableRealMouse": mouse_move_with_trajectory,
                }
                if mouse_move_with_trajectory:
                    ola_common_config.update({
                        "RealMouseMode": 1,
                        "RealMouseBaseTimePer100Pixels": 150,
                        "RealMouseNoise": 5.0,
                        "RealMouseDeviation": 25,
                        "RealMouseMinSteps": 100,
                    })

                result = ola.SetConfig(json.dumps(ola_common_config))
                if result == 1:
                    trajectory_status = "启用" if mouse_move_with_trajectory else "禁用"
                    logger.debug(f"[OLA多实例管理器] 窗口 {hwnd} 公共属性配置成功: InputLock={input_lock}, 鼠标轨迹={trajectory_status}")
            except Exception as e:
                logger.warning(f"[OLA多实例管理器] 窗口 {hwnd} 公共属性配置异常: {e}")

            # 重新绑定
            display_mode = new_config.get('display_mode', 'normal')
            mouse_mode = new_config.get('mouse_mode', 'normal')
            keypad_mode = new_config.get('keypad_mode', 'normal')
            mode = new_config.get('mode', 0)
            pubstr = new_config.get('pubstr', '')

            if pubstr:
                logger.info(f"[OLA多实例管理器] 窗口 {hwnd} 重新绑定使用 BindWindowEx: pubstr={pubstr}")
            ret = self._bind_window_with_retry(
                ola, hwnd, display_mode, mouse_mode, keypad_mode, mode, pubstr
            )

            if ret == 1:
                instance_data['config'] = new_config.copy()
                logger.info(f"[OLA多实例管理器] 窗口 {hwnd} 重新绑定成功 "
                           f"(display={display_mode}, mouse={mouse_mode}, keypad={keypad_mode})")
                return True
            else:
                logger.error(f"[OLA多实例管理器] 窗口 {hwnd} 重新绑定失败: ret={ret}")
                return False

        except Exception as e:
            logger.error(f"[OLA多实例管理器] 重新绑定窗口 {hwnd} 异常: {e}")
            return False

    def get_lock_for_window(self, hwnd: int) -> Optional[threading.RLock]:
        """
        获取窗口专属的操作锁

        用于需要原子操作的场景（如拖拽）

        Args:
            hwnd: 窗口句柄

        Returns:
            该窗口的RLock，不存在返回None
        """
        with self._global_lock:
            if hwnd in self._window_instances:
                return self._window_instances[hwnd].get('lock')
        return None

    def release_instance(self, hwnd: int):
        """
        释放指定窗口的OLA实例

        Args:
            hwnd: 窗口句柄
        """
        with self._global_lock:
            if hwnd in self._window_instances:
                try:
                    instance_data = self._window_instances[hwnd]
                    ola = instance_data['ola']

                    # 解绑并释放
                    self._destroy_ola_instance(ola)

                    del self._window_instances[hwnd]
                    logger.info(f"[OLA多实例管理器] 窗口 {hwnd} OLA实例已释放")

                except Exception as e:
                    logger.error(f"[OLA多实例管理器] 释放窗口 {hwnd} OLA实例异常: {e}")

    def release_all(self):
        """释放所有OLA实例"""
        with self._global_lock:
            hwnds = list(self._window_instances.keys())
            for hwnd in hwnds:
                self.release_instance(hwnd)
            logger.info(f"[OLA多实例管理器] 已释放所有OLA实例，共 {len(hwnds)} 个")

    def get_bound_windows(self) -> list:
        """
        获取所有已绑定的窗口句柄列表

        Returns:
            已绑定窗口句柄列表
        """
        with self._global_lock:
            return list(self._window_instances.keys())

    def is_window_bound(self, hwnd: int) -> bool:
        """
        检查窗口是否已绑定

        【性能优化】直接检查字典，无需获取全局锁（dict的in操作是线程安全的读操作）

        Args:
            hwnd: 窗口句柄

        Returns:
            True表示已绑定
        """
        # 【性能优化】dict的in操作是原子的，无需加锁
        return hwnd in self._window_instances

    def get_window_config(self, hwnd: int) -> Optional[dict]:
        """
        获取窗口的绑定配置

        【性能优化】快速路径无需获取全局锁

        Args:
            hwnd: 窗口句柄

        Returns:
            配置字典，包含 display_mode, mouse_mode, keypad_mode, mode,
            input_lock, mouse_move_with_trajectory, pubstr 等
            如果窗口未绑定返回None
        """
        # 【性能优化】先检查是否存在，再获取
        if hwnd in self._window_instances:
            return self._window_instances[hwnd].get('config', {}).copy()
        return None

    def get_instance_count(self) -> int:
        """获取当前OLA实例数量"""
        with self._global_lock:
            return len(self._window_instances)


# 全局管理器实例
_manager_instance: Optional[OLAMultiInstanceManager] = None
_manager_lock = threading.Lock()


def get_ola_instance_manager() -> OLAMultiInstanceManager:
    """
    获取OLA多实例管理器单例

    Returns:
        OLAMultiInstanceManager实例
    """
    global _manager_instance

    if _manager_instance is None:
        with _manager_lock:
            if _manager_instance is None:
                _manager_instance = OLAMultiInstanceManager()

    return _manager_instance








# ==================== OCR队列管理器 ====================
# 解决多窗口并发OCR时的资源竞争问题

import queue
import time as _time

class OCRQueueManager:
    """
    OCR请求队列管理器

    解决问题：多窗口并发OCR时，OLA内部资源（OCR引擎、GPU等）竞争导致卡顿

    原理：
    - 使用信号量控制并发OCR数量
    - 超过并发限制的请求排队等待
    - 避免大量OCR请求同时执行造成资源竞争
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        # OCR并发限制（同时执行的OCR数量）
        # 设为2：允许一定并发但避免过多竞争
        self._max_concurrent = 2
        self._semaphore = threading.Semaphore(self._max_concurrent)

        # 统计信息
        self._total_requests = 0
        self._queued_requests = 0
        self._stats_lock = threading.Lock()

        self._initialized = True
        logger.info(f"[OCR队列] 初始化完成，最大并发数: {self._max_concurrent}")

    def execute_ocr(self, ocr_func, *args, **kwargs):
        """
        执行OCR操作（带队列控制）

        Args:
            ocr_func: OCR函数
            *args, **kwargs: OCR函数参数

        Returns:
            OCR结果
        """
        with self._stats_lock:
            self._total_requests += 1
            request_id = self._total_requests

        # 尝试获取信号量
        acquired = self._semaphore.acquire(blocking=False)

        if not acquired:
            # 需要排队
            with self._stats_lock:
                self._queued_requests += 1
            logger.debug(f"[OCR队列] 请求#{request_id} 排队等待")

            # 阻塞等待
            self._semaphore.acquire(blocking=True)

            with self._stats_lock:
                self._queued_requests -= 1
            logger.debug(f"[OCR队列] 请求#{request_id} 开始执行")

        try:
            # 执行OCR
            return ocr_func(*args, **kwargs)
        finally:
            # 释放信号量
            self._semaphore.release()

    def set_max_concurrent(self, value: int):
        """设置最大并发数"""
        if value < 1:
            value = 1
        if value > 10:
            value = 10

        old_value = self._max_concurrent
        self._max_concurrent = value
        self._semaphore = threading.Semaphore(value)
        logger.info(f"[OCR队列] 最大并发数: {old_value} -> {value}")

    def get_stats(self) -> dict:
        """获取统计信息"""
        with self._stats_lock:
            return {
                'max_concurrent': self._max_concurrent,
                'total_requests': self._total_requests,
                'queued_requests': self._queued_requests
            }


# 全局OCR队列管理器
_ocr_queue_manager: Optional[OCRQueueManager] = None
_ocr_queue_lock = threading.Lock()


def get_ocr_queue_manager() -> OCRQueueManager:
    """获取OCR队列管理器单例"""
    global _ocr_queue_manager

    if _ocr_queue_manager is None:
        with _ocr_queue_lock:
            if _ocr_queue_manager is None:
                _ocr_queue_manager = OCRQueueManager()

    return _ocr_queue_manager


def execute_ocr_with_queue(ocr_func, *args, **kwargs):
    """
    便捷函数：通过队列执行OCR

    Args:
        ocr_func: OCR函数
        *args, **kwargs: OCR函数参数

    Returns:
        OCR结果
    """
    manager = get_ocr_queue_manager()
    return manager.execute_ocr(ocr_func, *args, **kwargs)
