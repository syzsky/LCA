# -*- coding: utf-8 -*-
"""
全局插件桥接模块 - 将插件系统与现有任务无缝集成

功能：
1. 读取主配置文件(config.json)中的插件设置
2. 自动初始化插件系统
3. 提供统一的调用接口供任务使用
4. 不修改任何现有任务代码
"""

import json
import logging
import os
import copy
import threading
from typing import Optional, Tuple, List, Any, Dict, Callable

from app_core.client_identity import get_hardware_id
from app_core.plugin_activation_service import prepare_plugin_mode_activation
from utils.window_binding_utils import get_plugin_bind_args

logger = logging.getLogger(__name__)

# 全局插件管理器实例
_global_plugin_manager = None
_global_config = None
_plugin_manager_init_lock = threading.Lock()
_plugin_manager_state_lock = threading.RLock()
_plugin_manager_init_done_event = threading.Event()
_plugin_manager_init_state = "idle"  # idle / scheduled / running / ready / failed
_plugin_manager_init_done_event.set()

# 【性能优化】配置缓存相关 - 避免频繁读取配置文件导致多窗口并发卡顿
import time as _time_module
_config_cache = None  # 缓存的配置
_config_cache_time = 0  # 缓存时间戳
_CONFIG_CACHE_TTL = 5.0  # 缓存有效期（秒），5秒内使用缓存，之后重新读取
_config_cache_lock = None  # 延迟初始化的锁

# 后台授权检查相关
_authorization_check_thread = None
_authorization_status = None  # None: 未检查, True: 已授权, False: 未授权
_authorization_lock = threading.Lock()
_plugin_inflight_lock = threading.RLock()
_plugin_mode_last_error = ""


class _PluginInFlightRequest:
    """插件请求并发复用容器：同键仅执行一次，其他线程等待结果。"""

    __slots__ = ("event", "value", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.value: Any = None
        self.error: Optional[Exception] = None


_plugin_inflight_requests: Dict[Tuple[str, Tuple[Any, ...]], _PluginInFlightRequest] = {}


def _resolve_plugin_pic_path(pic_name: str) -> str:
    """将插件找图路径统一转换为绝对路径，避免并发键不一致。"""
    raw = str(pic_name or "").strip()
    if not raw:
        return raw
    if os.path.isabs(raw):
        return raw

    import sys

    if getattr(sys, "frozen", False):
        exe_path = os.path.abspath(sys.executable)
        try:
            exe_path = os.path.realpath(exe_path)
        except Exception:
            pass
        base_dir = os.path.dirname(exe_path)
    else:
        try:
            from utils.app_paths import get_app_root
            base_dir = get_app_root()
        except Exception:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.normpath(os.path.join(base_dir, raw))


def _clone_plugin_inflight_value(value: Any) -> Any:
    """等待线程返回副本，避免可变对象被并发修改。"""
    if value is None:
        return None
    try:
        module_name = str(type(value).__module__ or "")
        if module_name.startswith("numpy") and hasattr(value, "copy"):
            return value.copy()
    except Exception:
        pass
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _resolve_plugin_bind_args(config: Optional[Dict[str, Any]], hwnd: int) -> Dict[str, Any]:
    return get_plugin_bind_args(config, hwnd=hwnd)


def _bind_plugin_window(plugin: Any, hwnd: int, bind_args: Dict[str, Any]) -> bool:
    return plugin.bind_window(
        hwnd,
        bind_args["display_mode"],
        bind_args["mouse_mode"],
        bind_args["keypad_mode"],
        bind_args["bind_mode"],
        input_lock=bind_args["input_lock"],
        mouse_move_with_trajectory=bind_args["mouse_move_with_trajectory"],
        pubstr=bind_args["pubstr"],
    )


def _run_plugin_inflight(
    scope: str,
    key: Tuple[Any, ...],
    worker: Callable[[], Any],
    wait_timeout: float = 6.0,
) -> Any:
    inflight_key = (str(scope), tuple(key))
    owner = False

    with _plugin_inflight_lock:
        request = _plugin_inflight_requests.get(inflight_key)
        if request is None:
            request = _PluginInFlightRequest()
            _plugin_inflight_requests[inflight_key] = request
            owner = True

    if owner:
        try:
            request.value = worker()
        except Exception as exc:
            request.error = exc
            request.value = None
        finally:
            request.event.set()
            with _plugin_inflight_lock:
                _plugin_inflight_requests.pop(inflight_key, None)
        if request.error is not None:
            raise request.error
        return request.value

    if not request.event.wait(timeout=max(0.5, float(wait_timeout))):
        logger.warning(f"[{scope}] 并发复用等待超时，返回空结果")
        return None

    if request.error is not None:
        raise request.error
    return _clone_plugin_inflight_value(request.value)


def _set_plugin_mode_last_error(message: str) -> None:
    global _plugin_mode_last_error
    _plugin_mode_last_error = str(message or "").strip()


def get_plugin_mode_last_error() -> str:
    return str(_plugin_mode_last_error or "").strip()


def _build_plugin_runtime_error_message(mode: str, raw_message: str, machine_code: str = "") -> str:
    base_message = str(raw_message or "").strip()
    if mode == "ola" and machine_code:
        if "未激活" in base_message or "请先激活" in base_message or "未找到授权信息" in base_message:
            return f"插件模式未激活，请先完成插件授权。\n当前机器码：{machine_code}"
        if machine_code not in base_message:
            return f"{base_message}\n当前机器码：{machine_code}" if base_message else f"当前机器码：{machine_code}"
    return base_message or "插件模式初始化失败"


def check_plugin_mode_runtime(mode: str, runtime_config_override: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    normalized_mode = str(mode or "").strip().lower()
    if not normalized_mode or normalized_mode == "disabled":
        _set_plugin_mode_last_error("")
        return True, ""

    try:
        from plugins.core.manager import PluginManager

        plugin_manager = PluginManager()
        plugin_manager.load_config()
        plugin_config = plugin_manager.get_plugin_config(normalized_mode)

        if normalized_mode == "ola" and isinstance(runtime_config_override, dict):
            from plugins.adapters.ola.runtime_config import normalize_ola_auth_settings

            plugin_config = dict(plugin_config)
            plugin_config.update(normalize_ola_auth_settings(runtime_config_override))
    except Exception as exc:
        message = f"插件模式初始化失败：{exc}"
        _set_plugin_mode_last_error(message)
        return False, message

    if normalized_mode != "ola":
        _set_plugin_mode_last_error("")
        return True, ""

    try:
        from plugins.adapters.ola.auth import probe_ola_authorization

        auth_result = probe_ola_authorization(plugin_config)
        if auth_result.success:
            _set_plugin_mode_last_error("")
            return True, ""

        message = _build_plugin_runtime_error_message(
            normalized_mode,
            auth_result.message,
            auth_result.machine_code,
        )
        _set_plugin_mode_last_error(message)
        return False, message
    except Exception as exc:
        message = f"插件模式初始化失败：{exc}"
        _set_plugin_mode_last_error(message)
        return False, message


def _background_authorization_check():
    """后台线程中执行授权检查"""
    global _authorization_status
    try:
        allowed, status_type = _check_plugin_authorization()
        with _authorization_lock:
            _authorization_status = allowed

        # 根据状态类型输出准确的日志
        if allowed:
            if status_type == "verified":
                logger.debug("后台授权检查完成：已授权（授权文件验证通过）")
            elif status_type == "validation_disabled":
                logger.debug("后台授权检查完成：服务器验证已关闭，无需授权")
            else:
                logger.debug(f"后台授权检查完成：允许使用（状态: {status_type}）")
        else:
            logger.warning("后台授权检查完成：未授权")
    except Exception as e:
        logger.error(f"后台授权检查异常: {e}", exc_info=True)
        with _authorization_lock:
            _authorization_status = False


def start_authorization_check():
    """启动后台授权检查（非阻塞）"""
    global _authorization_check_thread, _authorization_status

    try:
        config = get_cached_config()
        plugin_settings = config.get("plugin_settings", {}) if isinstance(config, dict) else {}
        if not bool(plugin_settings.get("enabled", False)):
            logger.debug("插件模式未启用，跳过后台授权检查")
            return
    except Exception as exc:
        logger.debug(f"读取插件模式配置失败，继续执行后台授权检查: {exc}")

    with _authorization_lock:
        # 如果已经有结果或正在检查中，不重复启动
        if _authorization_status is not None:
            return

        # 如果线程正在运行，不重复启动
        if _authorization_check_thread and _authorization_check_thread.is_alive():
            return

    # 启动后台检查线程
    _authorization_check_thread = threading.Thread(
        target=_background_authorization_check,
        daemon=True,
        name="AuthorizationCheckThread"
    )
    _authorization_check_thread.start()
    logger.debug("已启动后台授权检查线程")


def get_authorization_status() -> Optional[bool]:
    """
    获取当前授权状态（非阻塞）

    Returns:
        Optional[bool]: None表示正在检查中, True表示已授权, False表示未授权
    """
    with _authorization_lock:
        return _authorization_status


def _resolve_main_config_path() -> str:
    try:
        from utils.app_paths import get_config_path
        config_path = get_config_path()
        if config_path:
            return config_path
    except Exception:
        pass

    try:
        from utils.app_paths import get_app_root
        app_root = get_app_root()
    except Exception:
        app_root = os.path.dirname(os.path.dirname(__file__))
    default_config = os.path.join(app_root, "config", "default_config.json")
    if os.path.exists(default_config):
        return default_config
    return os.path.join(app_root, "config.json")


def _read_main_config_file(config_path: str) -> dict:
    """读取主配置文件（容错），失败时返回空字典。"""
    if not os.path.exists(config_path):
        return {}

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            raw = f.read()

        if not raw or not raw.strip():
            logger.error(f"主配置文件为空: {config_path}")
            return {}

        config_data = json.loads(raw)
        if not isinstance(config_data, dict):
            logger.error(f"主配置文件格式错误（根节点必须是对象）: {config_path}")
            return {}

        return config_data
    except json.JSONDecodeError as e:
        logger.error(f"加载主配置文件失败: {e} (路径: {config_path})")
        return {}
    except OSError as e:
        logger.error(f"读取主配置文件失败: {e} (路径: {config_path})")
        return {}


def _write_main_config_file(config_path: str, config_data: dict) -> bool:
    """原子写入主配置文件，避免写入中断导致配置损坏。"""
    try:
        config_dir = os.path.dirname(config_path)
        if config_dir:
            os.makedirs(config_dir, exist_ok=True)

        tmp_path = f"{config_path}.tmp.{os.getpid()}.{int(_time_module.time() * 1000)}"
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4, ensure_ascii=False)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp_path, config_path)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
        return True
    except OSError as e:
        logger.error(f"写入主配置文件失败: {e} (路径: {config_path})")
        return False


def load_main_config() -> dict:
    """加载主配置文件"""
    global _global_config

    if _global_config is not None:
        return _global_config

    config_path = _resolve_main_config_path()

    _global_config = _read_main_config_file(config_path)
    return _global_config


def reload_main_config() -> dict:
    """强制重新加载主配置文件（忽略缓存）"""
    global _global_config, _config_cache, _config_cache_time

    config_path = _resolve_main_config_path()

    _global_config = _read_main_config_file(config_path)
    # 更新缓存
    _config_cache = _global_config
    _config_cache_time = _time_module.time()
    logger.debug(f"已重新加载主配置文件")
    return _global_config


def get_cached_config() -> dict:
    """
    获取配置（带TTL缓存）

    【性能优化】避免频繁读取配置文件导致多窗口并发卡顿
    - 5秒内使用缓存
    - 超过5秒重新读取

    Returns:
        dict: 配置字典
    """
    global _config_cache, _config_cache_time, _config_cache_lock

    # 延迟初始化锁
    if _config_cache_lock is None:
        _config_cache_lock = threading.Lock()

    current_time = _time_module.time()

    # 快速路径：缓存有效时直接返回
    if _config_cache is not None and (current_time - _config_cache_time) < _CONFIG_CACHE_TTL:
        return _config_cache

    # 需要刷新缓存
    with _config_cache_lock:
        # 双重检查
        if _config_cache is not None and (_time_module.time() - _config_cache_time) < _CONFIG_CACHE_TTL:
            return _config_cache

        # 重新加载
        return reload_main_config()


def _set_plugin_manager_init_state(state: str, _error: Any = None) -> None:
    global _plugin_manager_init_state

    normalized_state = str(state or "idle").strip().lower()
    if normalized_state not in {"idle", "scheduled", "running", "ready", "failed"}:
        normalized_state = "idle"

    with _plugin_manager_state_lock:
        _plugin_manager_init_state = normalized_state
        if normalized_state == "running":
            _plugin_manager_init_done_event.clear()
        else:
            _plugin_manager_init_done_event.set()


def get_plugin_manager_initialization_state() -> str:
    with _plugin_manager_state_lock:
        return str(_plugin_manager_init_state or "idle")


def is_plugin_manager_initializing() -> bool:
    return get_plugin_manager_initialization_state() == "running"


def mark_plugin_manager_init_scheduled() -> None:
    current_state = get_plugin_manager_initialization_state()
    if current_state in {"ready", "running"}:
        return
    _set_plugin_manager_init_state("scheduled")


def reset_plugin_manager_runtime_state(reset_config: bool = False) -> None:
    global _global_plugin_manager, _global_config

    with _plugin_manager_state_lock:
        _global_plugin_manager = None
        if reset_config:
            _global_config = None
        _set_plugin_manager_init_state("idle")


def _initialize_plugin_manager_impl():
    global _global_plugin_manager

    # 【关键修复】优先使用 plugins 模块的全局单例，避免创建多个实例
    try:
        from plugins import get_plugin_manager as get_plugins_manager
        pm = get_plugins_manager()
        if pm is not None:
            # 如果plugins模块已经创建了单例，使用它
            _global_plugin_manager = pm

            # 读取主配置，确定是否启用插件系统（主配置优先于plugins/config.json）
            main_config = load_main_config()
            plugin_settings = main_config.get('plugin_settings', {})

            # 检查主配置是否启用插件系统
            if plugin_settings.get('enabled', False):
                # 根据主配置启用插件系统（覆盖plugins/config.json的设置）
                if not pm.is_enabled():
                    pm.enable()
                    logger.info("根据主配置启用插件系统")

                # 确保插件已加载
                plugins_list = pm.list_plugins()
                if len(plugins_list) == 0:
                    # 没有加载任何插件，执行加载
                    preferred_plugin = plugin_settings.get('preferred_plugin', 'ola')
                    if preferred_plugin:
                        runtime_ready, runtime_message = check_plugin_mode_runtime(preferred_plugin)
                        if runtime_ready:
                            pm.load_plugin(preferred_plugin)
                            logger.info(f"延迟加载首选插件: {preferred_plugin}")
                        else:
                            logger.error(f"插件运行时校验失败，禁用插件系统: {runtime_message}")
                            pm.disable()
            else:
                # 主配置禁用插件系统
                if pm.is_enabled():
                    pm.disable()
                    logger.info("根据主配置禁用插件系统")

            return pm
    except Exception as e:
        # 捕获所有异常，使用后备逻辑
        logger.warning(f"使用plugins模块单例失败: {e}，将创建新实例")

    try:
        # 读取主配置
        main_config = load_main_config()
        plugin_settings = main_config.get('plugin_settings', {})

        # 导入插件系统
        from plugins import init_plugin_system

        # 初始化插件系统
        pm = init_plugin_system()

        # 根据主配置启用/禁用插件系统
        if plugin_settings.get('enabled', False):
            # 【后台授权检查】启动后台授权检查，不阻塞初始化
            start_authorization_check()

            # 暂时以授权状态初始化，后台线程会更新状态
            auth_status = get_authorization_status()

            if auth_status is False:
                # 如果后台检查已经完成且未授权
                logger.critical("插件模式未授权，强制禁用插件系统")
                logger.warning("如需使用插件模式，请通过全局设置完成授权验证")
                pm.disable()
                logger.info("插件系统已禁用（未授权）")
            else:
                # auth_status 为 None (正在检查) 或 True (已授权)，允许启用
                pm.enable()
                logger.info("插件系统已启用（由主配置控制）")

                # 加载插件
                preferred_plugin = plugin_settings.get('preferred_plugin', 'ola')
                if preferred_plugin:
                    runtime_ready, runtime_message = check_plugin_mode_runtime(preferred_plugin)
                    if runtime_ready:
                        pm.load_plugin(preferred_plugin)
                        logger.info(f"首选插件: {preferred_plugin}")
                    else:
                        logger.error(f"插件运行时校验失败，禁用插件系统: {runtime_message}")
                        pm.disable()
        else:
            pm.disable()
            logger.info("插件系统已禁用，使用原有逻辑（由主配置控制）")

        _global_plugin_manager = pm
        return pm

    except Exception as e:
        logger.error(f"初始化插件管理器失败: {e}", exc_info=True)
        return None


def get_plugin_manager(wait: bool = True):
    """
    获取全局插件管理器（根据主配置自动初始化）

    注意：授权检查在后台执行，不阻塞UI

    Returns:
        PluginManager: 插件管理器实例
    """
    global _global_plugin_manager

    while True:
        with _plugin_manager_state_lock:
            init_state = _plugin_manager_init_state
            if init_state == "ready" and _global_plugin_manager is not None:
                return _global_plugin_manager
            if init_state == "running":
                if not wait:
                    return None
                wait_event = _plugin_manager_init_done_event
            else:
                wait_event = None

        if wait_event is not None:
            wait_event.wait()
            continue

        if not wait:
            return None

        with _plugin_manager_init_lock:
            with _plugin_manager_state_lock:
                init_state = _plugin_manager_init_state
                if init_state == "ready" and _global_plugin_manager is not None:
                    return _global_plugin_manager
                if init_state == "running":
                    continue
                _set_plugin_manager_init_state("running")

            pm = _initialize_plugin_manager_impl()
            if pm is None:
                _set_plugin_manager_init_state("failed", "插件管理器初始化失败")
                return None

            _global_plugin_manager = pm
            _set_plugin_manager_init_state("ready")
            return pm


def is_plugin_enabled() -> bool:
    """检查插件系统是否启用"""
    config = get_cached_config()
    return config.get('plugin_settings', {}).get('enabled', False)




# ==================== 便捷调用接口 ====================
# 这些接口可以直接在任务中使用，自动使用插件（如果启用）

def plugin_find_pic(hwnd: int, x1: int, y1: int, x2: int, y2: int,
                   pic_name: str, similarity: float = 0.9) -> Optional[Tuple[int, int]]:
    """
    找图（自动使用插件或原有逻辑）

    Args:
        hwnd: 窗口句柄
        x1, y1, x2, y2: 搜索区域
        pic_name: 图片路径
        similarity: 相似度

    Returns:
        Optional[Tuple[int, int]]: 找到返回坐标，否则返回None
    """
    pm = get_plugin_manager()

    if not (pm and pm.is_enabled()):
        # 插件未启用，返回None
        return None

    try:
        from plugins.core.interface import PluginCapability

        plugin = pm.get_preferred_plugin(PluginCapability.IMAGE_FIND_PIC)
        if not plugin:
            return None

        try:
            hwnd_i = int(hwnd)
            x1_i, y1_i, x2_i, y2_i = int(x1), int(y1), int(x2), int(y2)
        except Exception:
            return None

        similarity_value = _normalize_similarity(similarity, default=0.9)
        resolved_pic_name = _resolve_plugin_pic_path(pic_name)
        if not resolved_pic_name:
            return None

        inflight_key = (
            hwnd_i,
            x1_i,
            y1_i,
            x2_i,
            y2_i,
            str(resolved_pic_name).lower(),
            int(round(similarity_value * 1000.0)),
        )

        def _do_find_pic() -> Optional[Tuple[int, int]]:
            # 【关键修复】使用带缓存的配置读取，避免频繁I/O导致卡顿
            config = get_cached_config()
            bind_args = _resolve_plugin_bind_args(config, hwnd_i)

            # 绑定窗口（传递绑定参数）
            if not _bind_plugin_window(plugin, hwnd_i, bind_args):
                logger.error(f"插件找图失败：无法绑定窗口 {hwnd_i}")
                return None

            # 执行找图
            # 【多窗口线程安全】已通过bind_window绑定，无需传递hwnd
            return plugin.find_pic(x1_i, y1_i, x2_i, y2_i, resolved_pic_name, similarity_value)

        return _run_plugin_inflight(
            scope="plugin_find_pic",
            key=inflight_key,
            worker=_do_find_pic,
            wait_timeout=6.0,
        )
    except Exception as e:
        logger.error(f"插件找图失败: {e}", exc_info=True)
        return None


def plugin_find_pic_with_confidence(hwnd: int, x1: int, y1: int, x2: int, y2: int,
                                     pic_name: str, similarity: float = 0.9) -> Optional[dict]:
    """
    找图并返回完整信息（包括相似度）

    Args:
        hwnd: 窗口句柄
        x1, y1, x2, y2: 搜索区域
        pic_name: 图片路径
        similarity: 相似度阈值

    Returns:
        Optional[dict]: 返回字典包含:
            - found: bool, 是否达到阈值
            - x, y: int, 匹配位置
            - confidence: float, 实际相似度
            - threshold: float, 要求的阈值
    """
    pm = get_plugin_manager()

    if not (pm and pm.is_enabled()):
        return None

    try:
        from plugins.core.interface import PluginCapability

        plugin = pm.get_preferred_plugin(PluginCapability.IMAGE_FIND_PIC)
        if not plugin:
            return None

        try:
            hwnd_i = int(hwnd)
            x1_i, y1_i, x2_i, y2_i = int(x1), int(y1), int(x2), int(y2)
        except Exception:
            return None

        similarity_value = _normalize_similarity(similarity, default=0.9)
        resolved_pic_name = _resolve_plugin_pic_path(pic_name)
        if not resolved_pic_name:
            return None

        inflight_key = (
            hwnd_i,
            x1_i,
            y1_i,
            x2_i,
            y2_i,
            str(resolved_pic_name).lower(),
            int(round(similarity_value * 1000.0)),
        )

        def _do_find_pic_with_confidence() -> Optional[dict]:
            # 【关键修复】使用带缓存的配置读取，避免频繁I/O导致卡顿
            config = get_cached_config()
            bind_args = _resolve_plugin_bind_args(config, hwnd_i)

            # 绑定窗口
            if not _bind_plugin_window(plugin, hwnd_i, bind_args):
                logger.error(f"插件找图失败：无法绑定窗口 {hwnd_i}")
                return None

            # 执行找图（获取完整结果）
            if hasattr(plugin, 'find_pic_with_confidence'):
                return plugin.find_pic_with_confidence(x1_i, y1_i, x2_i, y2_i, resolved_pic_name, similarity_value)

            # 插件不支持返回相似度，回退到普通find_pic
            coords = plugin.find_pic(x1_i, y1_i, x2_i, y2_i, resolved_pic_name, similarity_value)
            if coords:
                return {
                    'found': True,
                    'x': coords[0],
                    'y': coords[1],
                    'confidence': similarity_value + 0.01,  # 估计值
                    'threshold': similarity_value
                }
            return None

        return _run_plugin_inflight(
            scope="plugin_find_pic_with_confidence",
            key=inflight_key,
            worker=_do_find_pic_with_confidence,
            wait_timeout=6.0,
        )
    except Exception as e:
        logger.error(f"插件找图失败: {e}", exc_info=True)
        return None


def plugin_mouse_click(hwnd: int, x: int, y: int, button: str = "left") -> bool:
    """
    鼠标点击（自动使用插件或原有逻辑）

    Args:
        hwnd: 窗口句柄
        x, y: 点击坐标
        button: 按钮类型

    Returns:
        bool: 是否成功
    """
    pm = get_plugin_manager()

    if pm and pm.is_enabled():
        try:
            from plugins.core.interface import PluginCapability

            plugin = pm.get_preferred_plugin(PluginCapability.MOUSE_CLICK)
            if plugin:
                # 【关键修复】使用带缓存的配置读取，避免频繁I/O导致卡顿
                config = get_cached_config()
                bind_args = _resolve_plugin_bind_args(config, int(hwnd))
                logger.debug(f"[plugin_mouse_click] 绑定参数: display={bind_args['display_mode']}, mouse={bind_args['mouse_mode']}, keypad={bind_args['keypad_mode']}, mode={bind_args['bind_mode']}, input_lock={bind_args['input_lock']}")

                # 绑定窗口（传递绑定参数）
                if not _bind_plugin_window(plugin, hwnd, bind_args):
                    logger.error(f"插件鼠标点击失败：无法绑定窗口 {hwnd}")
                    return False

                # 执行点击
                # 【多窗口线程安全】传递hwnd参数，确保操作发送到正确的窗口
                result = plugin.execute(
                    PluginCapability.MOUSE_CLICK,
                    'mouse_click',
                    x, y, button,
                    hwnd=hwnd
                )

                # 【修复】保持窗口绑定状态，不要每次都解绑
                # plugin.unbind_window()
                return result
        except Exception as e:
            logger.error(f"插件鼠标点击失败: {e}", exc_info=True)
            return False

    # 插件未启用，返回False
    return False


def plugin_key_input_text(hwnd: int, text: str) -> bool:
    """
    输入文字（自动使用插件或原有逻辑）

    Args:
        hwnd: 窗口句柄
        text: 要输入的文字

    Returns:
        bool: 是否成功
    """
    pm = get_plugin_manager()

    if pm and pm.is_enabled():
        try:
            from plugins.core.interface import PluginCapability

            plugin = pm.get_preferred_plugin(PluginCapability.KEYBOARD_INPUT_TEXT)
            if plugin:
                # 【关键修复】使用带缓存的配置读取，避免频繁I/O导致卡顿
                config = get_cached_config()
                bind_args = _resolve_plugin_bind_args(config, int(hwnd))
                logger.debug(f"[plugin_key_input_text] 绑定参数: display={bind_args['display_mode']}, mouse={bind_args['mouse_mode']}, keypad={bind_args['keypad_mode']}, mode={bind_args['bind_mode']}, input_lock={bind_args['input_lock']}")

                # 绑定窗口（传递绑定参数）
                if not _bind_plugin_window(plugin, hwnd, bind_args):
                    logger.error(f"插件输入文字失败：无法绑定窗口 {hwnd}")
                    return False

                # 【多窗口线程安全】已通过bind_window绑定，无需传递hwnd
                result = plugin.key_input_text(text)
                # 【修复】保持窗口绑定状态，不要每次都解绑
                # plugin.unbind_window()
                return result
        except Exception as e:
            logger.error(f"插件输入文字失败: {e}", exc_info=True)
            return False

    # 插件未启用，返回False
    return False


def plugin_find_color(hwnd: int, x1: int, y1: int, x2: int, y2: int,
                     color: str, similarity: float = 1.0) -> Optional[Tuple[int, int]]:
    """
    找色（自动使用插件或原有逻辑）

    Args:
        hwnd: 窗口句柄
        x1, y1, x2, y2: 搜索区域
        color: 颜色值（如"FFFFFF"）
        similarity: 相似度

    Returns:
        Optional[Tuple[int, int]]: 找到返回坐标，否则返回None
    """
    pm = get_plugin_manager()

    if pm and pm.is_enabled():
        try:
            from plugins.core.interface import PluginCapability

            plugin = pm.get_preferred_plugin(PluginCapability.IMAGE_FIND_COLOR)
            if plugin:
                # 【关键修复】使用带缓存的配置读取，避免频繁I/O导致卡顿
                config = get_cached_config()
                bind_args = _resolve_plugin_bind_args(config, int(hwnd))
                logger.debug(f"[plugin_find_color] 绑定参数: display={bind_args['display_mode']}, mouse={bind_args['mouse_mode']}, keypad={bind_args['keypad_mode']}, mode={bind_args['bind_mode']}, input_lock={bind_args['input_lock']}")

                if not _bind_plugin_window(plugin, hwnd, bind_args):
                    logger.error(f"插件找色失败：无法绑定窗口 {hwnd}")
                    return None

                # 【多窗口线程安全】已通过bind_window绑定，无需传递hwnd
                result = plugin.find_color(x1, y1, x2, y2, color, similarity)
                # 【修复】保持窗口绑定状态
                # plugin.unbind_window()
                return result
        except Exception as e:
            logger.error(f"插件找色失败: {e}", exc_info=True)
            return None

    # 插件未启用，返回None
    return None


def plugin_ocr(hwnd: int, x1: int, y1: int, x2: int, y2: int) -> Optional[str]:
    """
    OCR识别（自动使用插件或原有逻辑）

    【优化】减少冗余日志，提高执行速度
    【性能优化】多窗口模式下避免频繁读取配置文件

    Args:
        hwnd: 窗口句柄
        x1, y1, x2, y2: 识别区域

    Returns:
        Optional[str]: 识别的文字内容，失败返回None
    """
    logger.debug(f"[plugin_ocr] OCR识别 - hwnd={hwnd}, 区域=({x1},{y1})-({x2},{y2})")
    pm = get_plugin_manager()

    if not (pm and pm.is_enabled()):
        logger.debug(f"[plugin_ocr] 插件管理器未启用")
        # 插件未启用，返回None
        return None

    try:
        from plugins.core.interface import PluginCapability

        plugin = pm.get_preferred_plugin(PluginCapability.OCR_TEXT)
        if not plugin:
            logger.warning(f"[plugin_ocr] 未找到OCR插件")
            return None

        try:
            hwnd_i = int(hwnd)
            x1_i, y1_i, x2_i, y2_i = int(x1), int(y1), int(x2), int(y2)
        except Exception:
            return None

        inflight_key = (hwnd_i, x1_i, y1_i, x2_i, y2_i)

        def _do_plugin_ocr() -> Optional[str]:
            # 【性能优化】多实例模式下，如果窗口已绑定则直接使用缓存的OLA实例
            # 避免每次OCR都读取配置文件，解决多窗口并发时的卡顿问题
            try:
                from plugins.adapters.ola.multi_instance_manager import get_ola_instance_manager
                manager = get_ola_instance_manager()
                if manager.is_window_bound(hwnd_i):
                    # 窗口已绑定，直接执行OCR，无需重新读取配置
                    result_cached = plugin.ocr(x1_i, y1_i, x2_i, y2_i)
                    logger.debug(f"[plugin_ocr] OCR完成(缓存): 结果长度={len(result_cached) if result_cached else 0}")
                    return result_cached
            except ImportError:
                pass
            except Exception as bound_exc:
                logger.debug(f"[plugin_ocr] 检查缓存绑定失败: {bound_exc}")

            # 窗口未绑定或非多实例模式，需要读取配置并绑定
            # 使用带缓存的配置读取，避免频繁I/O
            config = get_cached_config()
            bind_args = _resolve_plugin_bind_args(config, hwnd_i)

            # 绑定窗口（传递绑定参数）
            # 如果已经绑定了相同窗口，OLA适配器会自动跳过重复绑定
            bind_success = _bind_plugin_window(plugin, hwnd_i, bind_args)

            if not bind_success:
                logger.error(f"[plugin_ocr] 绑定窗口失败，无法进行OCR识别")
                return None

            # 【多窗口线程安全】已通过bind_window绑定，无需传递hwnd
            result = plugin.ocr(x1_i, y1_i, x2_i, y2_i)
            logger.debug(f"[plugin_ocr] OCR完成: 结果长度={len(result) if result else 0}")
            return result

        return _run_plugin_inflight(
            scope="plugin_ocr",
            key=inflight_key,
            worker=_do_plugin_ocr,
            wait_timeout=8.0,
        )
    except Exception as e:
        logger.error(f"插件OCR识别失败: {e}", exc_info=True)
        return None


def plugin_capture(hwnd: int, x1: int, y1: int, x2: int, y2: int) -> Optional[Any]:
    """
    截取窗口区域（自动使用插件或原有逻辑）

    Args:
        hwnd: 窗口句柄
        x1, y1, x2, y2: 截图区域

    Returns:
        Optional[Any]: 截图数据（numpy数组或PIL图像），失败返回None
    """
    pm = get_plugin_manager()

    if not (pm and pm.is_enabled()):
        # 插件未启用，返回None
        return None

    try:
        from plugins.core.interface import PluginCapability

        plugin = pm.get_preferred_plugin(PluginCapability.IMAGE_CAPTURE)
        if not plugin:
            return None

        try:
            hwnd_i = int(hwnd)
            x1_i, y1_i, x2_i, y2_i = int(x1), int(y1), int(x2), int(y2)
        except Exception:
            return None

        inflight_key = (hwnd_i, x1_i, y1_i, x2_i, y2_i)

        def _do_plugin_capture() -> Optional[Any]:
            # 【关键修复】使用带缓存的配置读取，避免频繁I/O导致卡顿
            config = get_cached_config()
            bind_args = _resolve_plugin_bind_args(config, hwnd_i)

            logger.debug(f"[plugin_capture] 绑定参数: display={bind_args['display_mode']}, mouse={bind_args['mouse_mode']}, keypad={bind_args['keypad_mode']}, mode={bind_args['bind_mode']}, input_lock={bind_args['input_lock']}")

            # 绑定窗口（传递绑定参数）
            if not _bind_plugin_window(plugin, hwnd_i, bind_args):
                logger.error(f"插件截图失败：无法绑定窗口 {hwnd_i}")
                return None

            # 【多窗口线程安全】传递hwnd参数，确保操作发送到正确的窗口
            result = plugin.capture(x1_i, y1_i, x2_i, y2_i, hwnd=hwnd_i)

            # 将OLA返回的图像转换为numpy数组
            if result is not None:
                try:
                    import cv2

                    # OLA返回的是文件路径，使用OpenCV读取（自动为BGR格式）
                    if isinstance(result, str):
                        img_array = cv2.imread(result)
                        if img_array is None:
                            logger.error(f"无法读取插件截图文件: {result}")
                            return None
                        logger.debug(f"插件截图转换成功: shape={img_array.shape}, dtype={img_array.dtype}")
                        return img_array
                    # 已经是numpy数组，直接返回
                    return result
                except Exception as convert_exc:
                    logger.error(f"插件截图数据转换失败: {convert_exc}", exc_info=True)
                    return None
            return result

        return _run_plugin_inflight(
            scope="plugin_capture",
            key=inflight_key,
            worker=_do_plugin_capture,
            wait_timeout=6.0,
        )
    except Exception as e:
        logger.error(f"插件截图失败: {e}", exc_info=True)
        return None


def get_plugin_info() -> dict:
    """
    获取当前插件信息（供UI显示）

    Returns:
        dict: 插件信息
    """
    pm = get_plugin_manager()

    if not pm:
        return {
            'enabled': False,
            'mode': 'native',
            'plugins': []
        }

    config = load_main_config()
    plugin_settings = config.get('plugin_settings', {})

    return {
        'enabled': pm.is_enabled(),
        'mode': plugin_settings.get('preferred_plugin', 'ola'),
        'plugins': pm.list_plugins() if pm else []
    }


def _normalize_similarity(value: Any, default: float = 0.9) -> float:
    try:
        normalized = float(value)
    except Exception:
        normalized = float(default)
    if normalized < 0.0:
        return 0.0
    if normalized > 1.0:
        return 1.0
    return normalized


def _check_plugin_authorization() -> Tuple[bool, str]:
    try:
        activation_result = prepare_plugin_mode_activation(str(get_hardware_id() or "").strip())
        if activation_result.success:
            if activation_result.validation_enabled:
                return True, "verified"
            return True, "validation_disabled"
        logger.warning(f"插件授权检查失败: {activation_result.message}")
        return False, "unauthorized"
    except Exception as exc:
        logger.error(f"插件授权检查失败: {exc}", exc_info=True)
        return False, "unauthorized"


def set_plugin_mode(mode: str, runtime_config_override: Optional[Dict[str, Any]] = None) -> bool:
    """
    设置插件模式（供UI调用）

    Args:
        mode: 插件模式 ("ola" / "disabled")

    Returns:
        bool: 是否设置成功
    """
    try:
        _set_plugin_mode_last_error("")

        # 【关键安全检查】如果要启用插件模式，必须先验证授权
        if mode != 'disabled':
            allowed, status_type = _check_plugin_authorization()
            if not allowed:
                logger.critical("设置插件模式失败：未授权")
                logger.warning("启用插件模式需要有效的授权码，请先完成授权验证")
                _set_plugin_mode_last_error("插件模式授权未通过，请先完成授权。")
                return False
            else:
                # 记录允许的原因
                if status_type == "verified":
                    logger.info("授权验证通过，允许启用插件模式")
                elif status_type == "validation_disabled":
                    logger.info("服务器验证已关闭，允许启用插件模式（无需授权）")

            runtime_ready, runtime_message = check_plugin_mode_runtime(
                mode,
                runtime_config_override=runtime_config_override,
            )
            if not runtime_ready:
                logger.critical(f"设置插件模式失败：{runtime_message}")
                return False

        config_path = _resolve_main_config_path()

        # 读取配置（容错）
        config = _read_main_config_file(config_path)
        if not isinstance(config, dict):
            config = {}

        # 更新配置
        if 'plugin_settings' not in config:
            config['plugin_settings'] = {}

        if mode == 'disabled':
            config['plugin_settings']['enabled'] = False
        else:
            config['plugin_settings']['enabled'] = True
            config['plugin_settings']['preferred_plugin'] = mode

        # 保存配置（原子写入）
        if not _write_main_config_file(config_path, config):
            return False

        # 重新初始化插件管理器（同时重置两个单例和初始化状态）
        reset_plugin_manager_runtime_state(reset_config=True)

        # 【关键修复】同时重置 plugins 模块的单例
        try:
            import plugins
            plugins._plugin_manager = None
            logger.info("已重置 plugins 模块的插件管理器单例")
        except Exception as e:
            logger.warning(f"重置 plugins 模块单例失败: {e}")

        # 【关键修复】清除输入模拟器缓存，确保下次使用新的配置
        try:
            from utils.input_simulation import global_input_simulator_manager
            global_input_simulator_manager.clear_cache()
            logger.info("已清除输入模拟器缓存")
        except Exception as e:
            logger.warning(f"清除输入模拟器缓存失败: {e}")

        # 【关键修复】清除插件绑定窗口缓存
        try:
            from utils.input_simulation.plugin_simulator import clear_global_bound_windows
            clear_global_bound_windows()
        except Exception as e:
            logger.warning(f"清除插件绑定窗口缓存失败: {e}")

        logger.info(f"插件模式已设置为: {mode}")
        return True

    except Exception as e:
        logger.error(f"设置插件模式失败: {e}", exc_info=True)
        return False


# ==================== 分辨率调整 ====================

def plugin_set_client_size(hwnd: int, width: int, height: int) -> bool:
    """
    使用插件设置窗口客户区大小

    【插件模式隔离】此函数仅在插件模式下工作，不会降级到原有逻辑

    Args:
        hwnd: 窗口句柄
        width: 目标客户区宽度
        height: 目标客户区高度

    Returns:
        bool: 是否成功
    """
    pm = get_plugin_manager()

    if pm and pm.is_enabled():
        try:
            from plugins.core.interface import PluginCapability

            # 获取支持WINDOW_RESIZE的插件
            plugin = pm.get_preferred_plugin(PluginCapability.WINDOW_RESIZE)
            if plugin and hasattr(plugin, 'set_client_size'):
                result = plugin.set_client_size(hwnd, width, height)
                logger.info(f"[plugin_set_client_size] hwnd={hwnd}, {width}x{height}, 结果: {result}")
                return result
            else:
                logger.warning(f"[plugin_set_client_size] 未找到支持WINDOW_RESIZE的插件")
                return False
        except Exception as e:
            logger.error(f"[plugin_set_client_size] 异常: {e}", exc_info=True)
            return False

    # 插件未启用，不执行任何操作（不降级到原有逻辑）
    logger.warning(f"[plugin_set_client_size] 插件系统未启用，不执行分辨率调整")
    return False


def plugin_get_client_size(hwnd: int) -> tuple:
    """
    使用插件获取窗口客户区大小

    【插件模式隔离】此函数仅在插件模式下工作，不会降级到原有逻辑

    Args:
        hwnd: 窗口句柄

    Returns:
        tuple: (宽度, 高度) 或 (0, 0) 如果失败
    """
    pm = get_plugin_manager()

    if pm and pm.is_enabled():
        try:
            from plugins.core.interface import PluginCapability

            plugin = pm.get_preferred_plugin(PluginCapability.WINDOW_RESIZE)
            if plugin and hasattr(plugin, 'get_client_size'):
                result = plugin.get_client_size(hwnd)
                if result:
                    return result
                return (0, 0)
            else:
                logger.warning(f"[plugin_get_client_size] 未找到支持WINDOW_RESIZE的插件")
                return (0, 0)
        except Exception as e:
            logger.error(f"[plugin_get_client_size] 异常: {e}", exc_info=True)
            return (0, 0)

    # 插件未启用
    logger.warning(f"[plugin_get_client_size] 插件系统未启用")
    return (0, 0)


# ==================== 初始化 ====================

def initialize_plugin_system():
    """
    初始化插件系统（在主程序启动时调用）

    这个函数应该在 main.py 开头调用一次
    """
    logger.info("正在初始化全局插件系统...")

    pm = get_plugin_manager()

    if pm:
        config = load_main_config()
        plugin_settings = config.get('plugin_settings', {})

        if plugin_settings.get('enabled', False):
            logger.info(f"插件系统已启用")
            logger.info(f"首选插件: {plugin_settings.get('preferred_plugin', 'ola')}")

            # 显示加载的插件
            plugins = pm.list_plugins()
            logger.info(f"已加载 {len(plugins)} 个插件:")
            for plugin in plugins:
                logger.info(f"  - {plugin['name']} v{plugin['version']} ({'健康' if plugin['healthy'] else '异常'})")

            # 清除输入模拟器缓存，强制使用插件系统
            try:
                from utils.input_simulation import global_input_simulator_manager
                global_input_simulator_manager.clear_cache()
                logger.info("已清除输入模拟器缓存，下次创建将使用插件系统")
            except Exception as e:
                logger.warning(f"清除输入模拟器缓存失败: {e}")
        else:
            logger.info("插件系统已禁用，使用原有逻辑")

        return pm
    else:
        logger.warning("插件系统初始化失败")
        return None


# ==================== 多实例OLA管理 ====================

def get_ola_instance_count() -> int:
    """
    获取当前活跃的OLA实例数量

    Returns:
        int: OLA实例数量
    """
    try:
        from plugins.adapters.ola.multi_instance_manager import get_ola_instance_manager
        manager = get_ola_instance_manager()
        return manager.get_instance_count()
    except ImportError:
        return 0
    except Exception as e:
        logger.warning(f"[插件桥接] 获取OLA实例数量失败: {e}")
        return 0




if __name__ == '__main__':
    # 测试
    logging.basicConfig(level=logging.INFO)

    pm = initialize_plugin_system()

    if pm:
        info = get_plugin_info()
        logger.info(f"插件启用: {info['enabled']}")
        logger.info(f"当前模式: {info['mode']}")
        logger.info(f"已加载插件: {len(info['plugins'])} 个")
        logger.info(f"OLA实例数量: {get_ola_instance_count()}")
