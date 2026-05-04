"""
插件输入模拟器
使用插件系统（如OLA）进行输入模拟

【多实例模式】(v1.1.0)
为解决多窗口并发操作时的问题，现已支持多实例模式：
- 每个窗口使用独立的OLA实例
- 彻底解决虚拟键盘输入冲突（问题7）
- 彻底解决窗口绑定穿透问题（问题8）

多实例模式通过OLAAdapter自动启用，无需在此处额外处理。
"""

import logging
import threading
import time
from typing import Optional
from .base import BaseInputSimulator
from utils.window_binding_utils import resolve_plugin_ola_binding
from utils.input_timing import (
    DEFAULT_DOUBLE_CLICK_INTERVAL_SECONDS,
    DEFAULT_KEY_HOLD_SECONDS,
)
from utils.precise_sleep import precise_sleep as _shared_precise_sleep

logger = logging.getLogger(__name__)

_DEFAULT_DOUBLE_CLICK_INTERVAL_SECONDS = DEFAULT_DOUBLE_CLICK_INTERVAL_SECONDS


def _precise_sleep(duration: float) -> None:
    _shared_precise_sleep(duration)


# 全局绑定状态跟踪（跨实例共享）
# 注意：在多实例模式下，每个窗口有独立的OLA实例，此缓存仅用于快速查询
# 格式: {hwnd: {'instance': plugin_instance, 'config': {'mouse_mode': ..., 'keypad_mode': ..., ...}}}
_global_bound_windows = {}
_global_bound_windows_lock = threading.RLock()  # 线程锁保护全局绑定状态

def clear_global_bound_windows():
    """
    清除全局绑定窗口缓存

    当用户在UI中修改OLA绑定参数后，需要调用此函数清除缓存，
    确保下次操作使用新的配置参数重新绑定。
    """
    global _global_bound_windows
    with _global_bound_windows_lock:
        count = len(_global_bound_windows)
        _global_bound_windows.clear()
    logger.info(f"[插件绑定] 已清除全局绑定窗口缓存，共 {count} 个窗口")

class PluginInputSimulator(BaseInputSimulator):
    """基于插件系统的输入模拟器"""
    supports_atomic_click_hold = False

    def __init__(self, hwnd: int, execution_mode: str = "background"):
        """
        初始化插件输入模拟器

        Args:
            hwnd: 窗口句柄
            execution_mode: 执行模式（插件系统会自动处理）
        """
        super().__init__(hwnd)
        self.execution_mode = execution_mode
        self.plugin_manager = None
        self._bound = False

        # 注意：不再缓存前台模式状态，改为每次访问时实时读取配置

        # 初始化插件管理器
        try:
            from app_core.plugin_bridge import get_plugin_manager
            self.plugin_manager = get_plugin_manager()
            logger.info(f"插件输入模拟器初始化成功 (hwnd={hwnd})")
        except Exception as e:
            logger.error(f"插件输入模拟器初始化失败: {e}")

    @property
    def _is_foreground_mode(self) -> bool:
        """
        检测鼠标是否为前台模式（使用缓存配置，避免频繁I/O）

        只有mouse_mode为"normal"时，鼠标操作才需要屏幕坐标转换
        display_mode和keypad_mode为normal只需要激活窗口，不影响坐标转换

        Returns:
            bool: True表示鼠标前台模式（需要坐标转换），False表示鼠标后台模式
        """
        try:
            # 【性能优化】使用带缓存的配置读取，避免频繁I/O导致多窗口并发卡顿
            from app_core.plugin_bridge import get_cached_config
            config = get_cached_config()
            ola_binding = resolve_plugin_ola_binding(config, hwnd=self.hwnd)
            mouse_mode = str(ola_binding.get('mouse_mode', 'normal') or 'normal').strip().lower()

            # 只有mouse_mode为normal，鼠标操作才是前台模式
            is_mouse_foreground = (mouse_mode == 'normal')

            logger.debug(f"插件鼠标模式检测: mouse_mode={mouse_mode}, 鼠标前台模式={is_mouse_foreground}")
            return is_mouse_foreground
        except Exception as e:
            logger.warning(f"检测插件鼠标模式失败，默认为后台模式: {e}")
            return False

    def _get_coordinate_conversion_hwnd(self) -> int:
        """
        获取用于坐标转换的窗口句柄

        对于模拟器渲染窗口，需要使用其父窗口（模拟器主窗口）进行坐标转换
        对于普通窗口，直接使用绑定的窗口句柄

        Returns:
            int: 用于坐标转换的窗口句柄
        """
        try:
            import win32gui

            if not self.hwnd or not win32gui.IsWindow(self.hwnd):
                return self.hwnd

            # 获取窗口类名
            class_name = win32gui.GetClassName(self.hwnd)
            window_title = win32gui.GetWindowText(self.hwnd)


            return self.hwnd

        except Exception as e:
            logger.warning(f"[坐标转换] 获取坐标转换窗口失败: {e}，使用原窗口")
            return self.hwnd


    def _activate_window_for_foreground(self) -> bool:
        """
        激活窗口用于前台操作

        对于模拟器渲染窗口，会激活其父窗口（模拟器主窗口）

        Returns:
            bool: 是否成功激活
        """
        try:
            import win32gui
            import win32con
            import time

            if not self.hwnd or not win32gui.IsWindow(self.hwnd):
                logger.warning("[插件激活窗口] 无效的窗口句柄")
                return False

            # 检测是否为模拟器渲染窗口，如果是则激活父窗口
            activate_hwnd = self.hwnd
            class_name = win32gui.GetClassName(self.hwnd)

            # 检测是否为模拟器渲染窗口
            is_render_window = "Render" in class_name or class_name in ["TheRender", "RenderWindow"]

            if is_render_window:
                # 查找模拟器主窗口
                parent_hwnd = self._find_emulator_parent_window()
                if parent_hwnd:
                    activate_hwnd = parent_hwnd
                    logger.debug(f"[插件激活窗口] 检测到渲染窗口，激活父窗口: {activate_hwnd}")

            # 检查窗口是否已经是前台窗口
            foreground_hwnd = win32gui.GetForegroundWindow()
            if foreground_hwnd == activate_hwnd:
                logger.debug(f"[插件激活窗口] 窗口已是前台窗口: {activate_hwnd}")
                return True

            # 激活窗口
            try:
                # 先尝试使用 SetForegroundWindow
                win32gui.SetForegroundWindow(activate_hwnd)
                _precise_sleep(0.05)

                # 验证是否成功
                if win32gui.GetForegroundWindow() == activate_hwnd:
                    logger.info(f"[插件激活窗口] 成功激活窗口: {activate_hwnd}")
                    return True

                # 如果失败，尝试其他方法
                win32gui.PostMessage(activate_hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
                _precise_sleep(0.05)

                win32gui.ShowWindow(activate_hwnd, win32con.SW_SHOW)
                win32gui.SetForegroundWindow(activate_hwnd)
                _precise_sleep(0.05)

                logger.info(f"[插件激活窗口] 尝试激活窗口完成: {activate_hwnd}")
                return True

            except Exception as e:
                logger.warning(f"[插件激活窗口] 激活窗口失败: {e}")
                return False

        except Exception as e:
            logger.error(f"[插件激活窗口] 异常: {e}")
            return False

    def _bind_window(self) -> bool:
        """
        绑定窗口到插件

        Returns:
            bool: 绑定是否成功
        """
        global _global_bound_windows

        if not self.plugin_manager:
            return False

        try:
            from plugins.core.interface import PluginCapability

            # 先读取当前配置
            # 【性能优化】使用带缓存的配置读取，避免频繁I/O导致多窗口并发卡顿
            try:
                from app_core.plugin_bridge import get_cached_config
                config = get_cached_config()
                ola_binding = resolve_plugin_ola_binding(config, hwnd=self.hwnd)
                display_mode = str(ola_binding.get('display_mode', 'normal') or 'normal').strip().lower()
                mouse_mode = str(ola_binding.get('mouse_mode', 'normal') or 'normal').strip().lower()
                keypad_mode = str(ola_binding.get('keypad_mode', 'normal') or 'normal').strip().lower()
                bind_mode = ola_binding.get('mode', 0)
                # 读取并传递 input_lock 参数
                input_lock = ola_binding.get('input_lock', False)
                # 读取并传递 mouse_move_with_trajectory 参数
                mouse_move_with_trajectory = ola_binding.get('mouse_move_with_trajectory', False)
                sim_mode_type = ola_binding.get('sim_mode_type', 0)
                # 读取pubstr参数
                pubstr = ola_binding.get('pubstr', '')

                current_config = {
                    'display_mode': display_mode,
                    'mouse_mode': mouse_mode,
                    'keypad_mode': keypad_mode,
                    'bind_mode': bind_mode,
                    'input_lock': input_lock,
                    'mouse_move_with_trajectory': mouse_move_with_trajectory,
                    'sim_mode_type': sim_mode_type,
                    'pubstr': pubstr
                }

                logger.debug(f"[插件绑定参数] 从缓存配置读取: display_mode={display_mode}, mouse_mode={mouse_mode}, keypad_mode={keypad_mode}, mode={bind_mode}, input_lock={input_lock}")
            except Exception as e:
                logger.warning(f"读取OLA绑定配置失败，使用默认值: {e}")
                display_mode = 'normal'
                mouse_mode = 'normal'
                keypad_mode = 'normal'
                bind_mode = 0
                input_lock = False
                mouse_move_with_trajectory = False
                sim_mode_type = 0
                pubstr = ''
                current_config = {
                    'display_mode': display_mode,
                    'mouse_mode': mouse_mode,
                    'keypad_mode': keypad_mode,
                    'bind_mode': bind_mode,
                    'input_lock': input_lock,
                    'mouse_move_with_trajectory': mouse_move_with_trajectory,
                    'sim_mode_type': sim_mode_type,
                    'pubstr': pubstr
                }

            # 【线程安全】使用锁保护全局绑定状态的检查和修改
            with _global_bound_windows_lock:
                # 检查全局绑定状态和配置是否变化
                if self.hwnd in _global_bound_windows:
                    cached_data = _global_bound_windows[self.hwnd]
                    cached_config = cached_data.get('config', {})

                    # 检查配置是否相同
                    config_changed = (cached_config != current_config)

                    if config_changed:
                        logger.info(f"[插件绑定] 检测到配置变化，需要重新绑定。旧配置: {cached_config}, 新配置: {current_config}")
                        # 先从缓存中移除
                        del _global_bound_windows[self.hwnd]
                        self._bound = False
                    else:
                        self._bound = True
                        logger.debug(f"窗口 {self.hwnd} 已在全局绑定列表中且配置未变化，跳过绑定")
                        return True

            # 获取首选插件
            plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.WINDOW_BIND)
            if plugin:
                # 绑定窗口，传递绑定参数
                # 判断是否需要激活窗口：display/mouse/keypad任何一个为normal都需要激活
                # 但要注意：只有mouse_mode为normal时，鼠标坐标才需要转换
                need_activate = (display_mode == 'normal' or mouse_mode == 'normal' or keypad_mode == 'normal')

                # 传递所有绑定参数到OLA适配器（包括鼠标轨迹配置）
                result = plugin.bind_window(
                    self.hwnd, display_mode, mouse_mode, keypad_mode, bind_mode,
                    input_lock=input_lock,
                    activate_foreground=need_activate,
                    mouse_move_with_trajectory=mouse_move_with_trajectory,
                    pubstr=pubstr
                )

                if result:
                    self._bound = True
                    # 【线程安全】使用锁保护写入操作
                    with _global_bound_windows_lock:
                        # 记录到全局绑定列表，同时保存配置
                        _global_bound_windows[self.hwnd] = {
                            'instance': self,
                            'config': current_config
                        }
                    logger.info(f"[插件绑定成功] hwnd={self.hwnd}, display={display_mode}, mouse={mouse_mode}, keypad={keypad_mode}, mode={bind_mode}, input_lock={input_lock}, 激活窗口={need_activate}")
                    return True
                else:
                    logger.error(f"插件绑定窗口失败 (hwnd={self.hwnd})")
                    return False
            else:
                logger.error("未找到可用的窗口绑定插件")
                return False
        except Exception as e:
            logger.error(f"插件绑定窗口异常: {e}", exc_info=True)
            return False

    def _unbind_window(self):
        """解绑窗口

        多窗口并发修复：显式传入hwnd参数，避免在多窗口并发时误释放其他窗口的实例。
        """
        global _global_bound_windows

        if not self._bound or not self.plugin_manager:
            return

        try:
            from plugins.core.interface import PluginCapability

            plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.WINDOW_UNBIND)
            if plugin:
                # 【多窗口并发修复】显式传入hwnd，避免使用共享状态导致误释放
                plugin.unbind_window(self.hwnd)
                self._bound = False
                # 【线程安全】使用锁保护全局绑定列表
                with _global_bound_windows_lock:
                    if self.hwnd in _global_bound_windows:
                        del _global_bound_windows[self.hwnd]
                logger.debug(f"插件已解绑窗口 (hwnd={self.hwnd})")
        except Exception as e:
            logger.warning(f"插件解绑窗口失败: {e}")

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1, interval: float = 0.1) -> bool:
        """
        点击操作

        Args:
            x: x坐标（前台模式为屏幕坐标，后台模式为客户区坐标）
            y: y坐标（前台模式为屏幕坐标，后台模式为客户区坐标）
            button: 按钮类型 ("left", "right", "middle")
            clicks: 点击次数
            interval: 点击间隔（秒）
        """
        if not self.plugin_manager:
            logger.error("插件管理器未初始化")
            return False

        if x is None or y is None:
            logger.error("插件点击失败: 缺少坐标")
            return False

        try:
            target_x = int(x)
            target_y = int(y)
        except Exception:
            logger.error(f"插件点击失败: 坐标无效 ({x}, {y})")
            return False

        try:
            safe_clicks = max(1, int(clicks))
        except Exception:
            safe_clicks = 1
        try:
            safe_interval = max(0.0, float(interval))
        except Exception:
            safe_interval = 0.0

        try:
            from plugins.core.interface import PluginCapability

            # 绑定窗口（前台模式会自动激活窗口）
            if not self._bind_window():
                logger.error(f"无法绑定窗口 (hwnd={self.hwnd})，点击操作失败")
                return False

            # 获取首选插件
            plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.MOUSE_CLICK)
            if plugin:
                # 执行多次点击
                all_success = True
                for i in range(safe_clicks):
                    # 【多窗口线程安全】传递hwnd参数，确保操作发送到正确的窗口
                    if self._is_foreground_mode and hasattr(plugin.mouse_click, '__code__'):
                        # 前台模式：传递is_screen_coord=True和hwnd
                        result = plugin.mouse_click(target_x, target_y, button, is_screen_coord=True, hwnd=self.hwnd)
                    else:
                        # 后台模式：传递hwnd
                        result = plugin.mouse_click(target_x, target_y, button, hwnd=self.hwnd)

                    if not result:
                        all_success = False
                        logger.warning(f"插件点击第{i+1}次失败: ({target_x}, {target_y}), button={button}")
                    if i < safe_clicks - 1:  # 不在最后一次点击后等待
                        _precise_sleep(safe_interval)

                logger.debug(f"插件点击: ({target_x}, {target_y}), button={button}, clicks={safe_clicks}, 鼠标前台模式={self._is_foreground_mode}, result={all_success}")
                return all_success
            else:
                logger.warning("未找到可用的鼠标点击插件")
                return False

        except Exception as e:
            logger.error(f"插件点击失败: {e}")
            return False

    def double_click(self, x: int, y: int, button: str = "left") -> bool:
        """
        双击操作

        Args:
            x: x坐标
            y: y坐标
            button: 按钮类型
        """
        # OLA插件通常没有单独的双击接口，执行两次单击（clicks=2）
        return self.click(x, y, button, clicks=2, interval=_DEFAULT_DOUBLE_CLICK_INTERVAL_SECONDS)

    def mouse_down(self, x: int, y: int, button: str = "left", is_screen_coord: bool = None):
        """
        鼠标按下

        Args:
            x: x坐标
            y: y坐标
            button: 按钮类型
            is_screen_coord: 是否为屏幕坐标，None表示自动判断（前台模式默认True，后台模式默认False）
        """
        if not self.plugin_manager:
            return False

        if x is None or y is None:
            logger.error("插件鼠标按下失败: 缺少坐标")
            return False

        try:
            target_x = int(x)
            target_y = int(y)
        except Exception:
            logger.error(f"插件鼠标按下失败: 坐标无效 ({x}, {y})")
            return False

        try:
            from plugins.core.interface import PluginCapability

            # 绑定窗口（前台模式会自动激活窗口）
            if not self._bind_window():
                logger.error(f"无法绑定窗口 (hwnd={self.hwnd})，鼠标按下操作失败")
                return False

            plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.MOUSE_DOWN)
            if plugin:
                # 【多窗口线程安全】传递hwnd参数
                # 如果未指定is_screen_coord，前台模式默认True，后台模式默认False
                if is_screen_coord is None:
                    is_screen_coord = self._is_foreground_mode
                return plugin.mouse_down(target_x, target_y, button, is_screen_coord=is_screen_coord, hwnd=self.hwnd)
            else:
                logger.warning("未找到可用的鼠标按下插件")
                return False

        except Exception as e:
            logger.error(f"插件鼠标按下失败: {e}")
            return False

    def mouse_up(self, x: int, y: int, button: str = "left", is_screen_coord: bool = None):
        """
        鼠标释放

        Args:
            x: x坐标
            y: y坐标
            button: 按钮类型
            is_screen_coord: 是否为屏幕坐标，None表示自动判断（前台模式默认True，后台模式默认False）
        """
        if not self.plugin_manager:
            return False

        if x is None or y is None:
            logger.error("插件鼠标释放失败: 缺少坐标")
            return False

        try:
            target_x = int(x)
            target_y = int(y)
        except Exception:
            logger.error(f"插件鼠标释放失败: 坐标无效 ({x}, {y})")
            return False

        try:
            from plugins.core.interface import PluginCapability

            # 绑定窗口（前台模式会自动激活窗口）
            if not self._bind_window():
                logger.error(f"无法绑定窗口 (hwnd={self.hwnd})，鼠标释放操作失败")
                return False

            plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.MOUSE_UP)
            if plugin:
                # 【多窗口线程安全】传递hwnd参数
                # 如果未指定is_screen_coord，前台模式默认True，后台模式默认False
                if is_screen_coord is None:
                    is_screen_coord = self._is_foreground_mode
                return plugin.mouse_up(target_x, target_y, button, is_screen_coord=is_screen_coord, hwnd=self.hwnd)
            else:
                logger.warning("未找到可用的鼠标释放插件")
                return False

        except Exception as e:
            logger.error(f"插件鼠标释放失败: {e}")
            return False

    def mouse_move(self, x: int, y: int):
        """
        鼠标移动

        Args:
            x: x坐标
            y: y坐标
        """
        if not self.plugin_manager:
            return False

        try:
            from plugins.core.interface import PluginCapability

            # 绑定窗口，检查绑定结果
            if not self._bind_window():
                logger.error(f"无法绑定窗口 (hwnd={self.hwnd})，鼠标移动操作失败")
                return False

            plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.MOUSE_MOVE)
            if plugin:
                # 【多窗口线程安全】传递hwnd参数
                return plugin.mouse_move(x, y, hwnd=self.hwnd)
            else:
                logger.warning("未找到可用的鼠标移动插件")
                return False

        except Exception as e:
            logger.error(f"插件鼠标移动失败: {e}")
            return False

    def send_text(self, text: str, stop_checker=None) -> bool:
        """
        发送文本

        Args:
            text: 要发送的文本
        """
        if not self.plugin_manager:
            return False
        if stop_checker and stop_checker():
            raise InterruptedError("stop requested")

        try:
            from plugins.core.interface import PluginCapability

            # 绑定窗口，检查绑定结果
            if not self._bind_window():
                logger.error(f"无法绑定窗口 (hwnd={self.hwnd})，发送文本操作失败")
                return False

            plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.KEYBOARD_INPUT_TEXT)
            if plugin:
                # 【多窗口线程安全】已通过_bind_window绑定，无需传递hwnd
                result = plugin.key_input_text(text)
                logger.debug(f"插件发送文本: {text}, result={result}")
                return result
            else:
                logger.warning("未找到可用的文本输入插件")
                return False

        except InterruptedError:
            raise
        except Exception as e:
            logger.error(f"插件发送文本失败: {e}")
            return False

    def send_keys(self, keys: str):
        """
        发送按键序列

        Args:
            keys: 按键序列
        """
        if not self.plugin_manager:
            return False

        try:
            from plugins.core.interface import PluginCapability

            # 绑定窗口，检查绑定结果
            if not self._bind_window():
                logger.error(f"无法绑定窗口 (hwnd={self.hwnd})，发送按键操作失败")
                return False

            plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.KEYBOARD_PRESS)
            if plugin:
                # 【多窗口线程安全】传递hwnd参数
                return plugin.key_press(keys, hwnd=self.hwnd)
            else:
                logger.warning("未找到可用的按键输入插件")
                return False

        except Exception as e:
            logger.error(f"插件发送按键失败: {e}")
            return False

    def key_down(self, key: str):
        """
        按键按下

        Args:
            key: 按键
        """
        if not self.plugin_manager:
            return False

        try:
            from plugins.core.interface import PluginCapability

            self._bind_window()

            plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.KEYBOARD_DOWN)
            if plugin:
                # 【多窗口线程安全】传递hwnd参数
                return plugin.key_down(key, hwnd=self.hwnd)
            else:
                logger.warning("未找到可用的按键按下插件")
                return False

        except Exception as e:
            logger.error(f"插件按键按下失败: {e}")
            return False

    def key_up(self, key: str):
        """
        按键释放

        Args:
            key: 按键
        """
        if not self.plugin_manager:
            return False

        try:
            from plugins.core.interface import PluginCapability

            self._bind_window()

            plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.KEYBOARD_UP)
            if plugin:
                # 【多窗口线程安全】传递hwnd参数
                return plugin.key_up(key, hwnd=self.hwnd)
            else:
                logger.warning("未找到可用的按键释放插件")
                return False

        except Exception as e:
            logger.error(f"插件按键释放失败: {e}")
            return False

    def move_mouse(self, x: int, y: int) -> bool:
        """
        移动鼠标到指定位置

        Args:
            x: 目标x坐标（客户区坐标）
            y: 目标y坐标（客户区坐标）

        Returns:
            bool: 是否成功
        """
        if not self.plugin_manager:
            return False

        try:
            from plugins.core.interface import PluginCapability

            # 绑定窗口
            if not self._bind_window():
                logger.error("移动鼠标前绑定窗口失败")
                return False

            # 获取鼠标移动插件
            plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.MOUSE_MOVE)

            if plugin:
                # 使用插件移动鼠标
                # 前台模式传递is_screen_coord=True，后台模式传递False
                is_screen_coord = self._is_foreground_mode
                result = plugin.mouse_move(x, y, is_screen_coord=is_screen_coord, hwnd=self.hwnd)

                if result:
                    logger.debug(f"插件移动鼠标成功: ({x}, {y}), 前台模式={is_screen_coord}")
                    return True
                else:
                    logger.warning("插件移动鼠标失败")
                    return False
            else:
                logger.warning("未找到可用的鼠标移动插件")
                return False

        except Exception as e:
            logger.error(f"插件移动鼠标失败: {e}", exc_info=True)
            return False

    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int,
             duration: float = 1.0, button: str = 'left') -> bool:
        """
        鼠标拖拽

        Args:
            start_x, start_y: 起点客户区坐标
            end_x, end_y: 终点客户区坐标
            duration: 拖拽持续时间
            button: 鼠标按钮
        """
        if not self.plugin_manager:
            return False

        try:
            import time
            from plugins.core.interface import PluginCapability

            # 记录开始时间
            start_time = time.time()

            # 绑定窗口，检查绑定结果
            if not self._bind_window():
                logger.error(f"无法绑定窗口 (hwnd={self.hwnd})，拖拽操作失败")
                return False

            # 【修复】前台模式需要先激活窗口，但不需要坐标转换
            # OLA总是期望客户区坐标，会根据绑定时的mouse_mode自动处理
            if self._is_foreground_mode:
                self._activate_window_for_foreground()
                logger.info(f"[插件拖拽] 前台模式，已激活窗口，使用客户区坐标: ({start_x},{start_y})->({end_x},{end_y})")
            else:
                logger.debug(f"[插件拖拽] 后台模式使用客户区坐标: ({start_x},{start_y})->({end_x},{end_y})")

            # 获取OLA插件，直接调用其拖拽方法
            # OLA总是使用客户区坐标，不需要转换
            plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.MOUSE_DRAG)
            if plugin and hasattr(plugin, 'mouse_drag'):
                logger.info(f"[插件拖拽] 使用插件的mouse_drag方法，持续时间={duration:.2f}s")
                # 【多窗口线程安全】传递hwnd参数
                result = plugin.mouse_drag(
                    start_x,
                    start_y,
                    end_x,
                    end_y,
                    duration,
                    button=button,
                    hwnd=self.hwnd,
                )

                # 计算实际耗时
                actual_duration = time.time() - start_time
                logger.debug(f"拖拽完成: 设置时长={duration:.2f}s, 实际时长={actual_duration:.2f}s")
                return result
            else:
                # 降级方案：使用分步操作
                logger.warning(f"插件不支持mouse_drag，使用分步操作（按下->移动->释放）")

                # 拖拽实现：按下 -> 移动 -> 释放（使用客户区坐标）
                result = self.mouse_down(start_x, start_y, button)
                if not result:
                    return False

                # 按下后短暂延迟，确保系统识别到按下状态（前台模式关键）
                _precise_sleep(0.05)

                # 移动到终点（保持按下状态）
                result = self.mouse_move(end_x, end_y)

                # 计算已经过去的时间
                elapsed = time.time() - start_time

                # 如果还有剩余时间，继续等待以达到指定duration
                remaining = max(0, duration - elapsed)
                if remaining > 0:
                    _precise_sleep(remaining)

                # 释放鼠标
                result = result and self.mouse_up(end_x, end_y, button)

                # 记录实际执行时间
                actual_duration = time.time() - start_time
                logger.debug(f"拖拽完成: 设置时长={duration:.2f}s, 实际时长={actual_duration:.2f}s")

                return result
        except Exception as e:
            logger.error(f"插件拖拽失败: {e}")
            return False

    def drag_path(self, path_points: list, duration: float = 2.0, button: str = 'left', timestamps: list = None) -> bool:
        """
        多点路径拖拽（平滑移动）

        Args:
            path_points: 路径点列表 [(x1, y1), (x2, y2), ...]
            duration: 总拖拽时长（秒），如果提供timestamps则此参数被忽略
            button: 按钮类型
            timestamps: 时间戳列表（秒），如果提供则按时间戳执行精确回放

        Returns:
            bool: 是否成功
        """
        if not self.plugin_manager:
            return False

        try:
            import time

            if len(path_points) < 2:
                logger.error("路径点数量不足，至少需要2个点")
                return False

            # 绑定窗口
            if not self._bind_window():
                logger.error(f"无法绑定窗口 (hwnd={self.hwnd})，多点拖拽操作失败")
                return False

            # 【修复】前台模式需要先激活窗口
            if self._is_foreground_mode:
                self._activate_window_for_foreground()
                logger.info(f"[插件多点拖拽] 前台模式，已激活窗口")
            else:
                logger.debug(f"[插件多点拖拽] 后台模式")

            # 检查是否使用时间戳精确回放
            use_timestamps = timestamps and len(timestamps) == len(path_points)

            if use_timestamps:
                total_duration = timestamps[-1]
                logger.info(f"[插件模式] 开始多点路径拖拽(精确回放): {len(path_points)}个点, 总时长: {total_duration:.3f}秒")
            else:
                total_duration = duration
                logger.info(f"[插件模式] 开始多点路径拖拽(平滑插值): {len(path_points)}个点, 总时长: {duration}秒")

            # 获取OLA插件对象，直接使用MoveTo + LeftDown/LeftUp
            from plugins.core.interface import PluginCapability
            plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.MOUSE_DOWN)

            if not plugin or not hasattr(plugin, '_get_ola_for_operation'):
                logger.error("无法获取OLA插件对象，使用标准方式")
                return self._drag_path_standard(path_points, duration, button, timestamps)

            # 【多窗口线程安全】使用 _get_ola_for_operation 获取当前窗口专属的OLA实例
            ola = plugin._get_ola_for_operation(self.hwnd)

            # 记录开始时间（在操作前记录）
            start_time = time.time()

            # 移动到起点
            start_x, start_y = int(path_points[0][0]), int(path_points[0][1])
            move_ret = ola.MoveTo(start_x, start_y)
            if move_ret != 1:
                logger.error(f"[插件模式] 移动到起点失败: ({start_x}, {start_y})")
                return False

            _precise_sleep(0.02)  # 短暂延迟

            # 按下鼠标（根据按钮类型）
            if button == "left":
                down_ret = ola.LeftDown()
            elif button == "right":
                down_ret = ola.RightDown()
            elif button == "middle":
                down_ret = ola.MiddleDown()
            else:
                logger.error(f"未知的鼠标按钮: {button}")
                return False

            if down_ret != 1:
                logger.error(f"[插件模式] 在起点按下鼠标失败: ({start_x}, {start_y})")
                return False

            _precise_sleep(0.02)  # 按下后短暂延迟，确保系统识别

            try:
                if use_timestamps:
                    # 使用时间戳精确回放
                    logger.debug(f"[插件模式] 时间戳精确回放模式，共{len(path_points)}个点")

                    for i in range(1, len(path_points)):
                        x, y = int(path_points[i][0]), int(path_points[i][1])
                        target_timestamp = timestamps[i]

                        # 等待到目标时间点
                        current_elapsed = time.time() - start_time
                        wait_time = target_timestamp - current_elapsed

                        if wait_time > 0:
                            _precise_sleep(wait_time)

                        # 移动到下一个点
                        move_ret = ola.MoveTo(x, y)
                        if move_ret != 1:
                            logger.warning(f"[插件模式] 移动到点{i}失败: ({x}, {y})")

                else:
                    # 平滑插值逻辑（使用二次贝塞尔插值）
                    logger.debug(f"[插件模式] 使用平滑插值")

                    # 计算所有点之间的距离
                    total_distance = 0
                    distances = [0]
                    for i in range(1, len(path_points)):
                        dx = path_points[i][0] - path_points[i-1][0]
                        dy = path_points[i][1] - path_points[i-1][1]
                        dist = (dx*dx + dy*dy) ** 0.5
                        total_distance += dist
                        distances.append(total_distance)

                    # 根据距离分配时间
                    target_interval = 0.016  # 约60fps的间隔
                    num_steps = max(int(total_duration / target_interval), len(path_points) - 1)

                    for step in range(1, num_steps + 1):
                        # 计算当前时间比例
                        t = step / num_steps
                        target_distance = t * total_distance

                        # 找到对应的路径段
                        segment_idx = 0
                        for i in range(1, len(distances)):
                            if distances[i] >= target_distance:
                                segment_idx = i - 1
                                break
                        else:
                            segment_idx = len(path_points) - 2

                        # 在段内插值
                        if segment_idx < len(path_points) - 1:
                            segment_start_dist = distances[segment_idx]
                            segment_end_dist = distances[segment_idx + 1]
                            segment_length = segment_end_dist - segment_start_dist

                            if segment_length > 0:
                                local_t = (target_distance - segment_start_dist) / segment_length
                            else:
                                local_t = 0

                            x1, y1 = path_points[segment_idx]
                            x2, y2 = path_points[segment_idx + 1]
                            x = int(x1 + (x2 - x1) * local_t)
                            y = int(y1 + (y2 - y1) * local_t)
                        else:
                            x, y = int(path_points[-1][0]), int(path_points[-1][1])

                        # 移动
                        move_ret = ola.MoveTo(x, y)
                        if move_ret != 1:
                            logger.warning(f"[插件模式] 插值移动失败: ({x}, {y})")

                        # 等待到下一帧
                        if step < num_steps:
                            _precise_sleep(target_interval)

                # 短暂延迟确保最后一个移动完成
                _precise_sleep(0.02)

            finally:
                # 在最后一个点释放鼠标（确保释放）
                end_x, end_y = int(path_points[-1][0]), int(path_points[-1][1])

                # 释放鼠标（根据按钮类型）
                if button == "left":
                    up_ret = ola.LeftUp()
                elif button == "right":
                    up_ret = ola.RightUp()
                elif button == "middle":
                    up_ret = ola.MiddleUp()
                else:
                    up_ret = 0

                if up_ret != 1:
                    logger.warning(f"[插件模式] 在终点释放鼠标失败: ({end_x}, {end_y})")

            # 记录实际执行时间
            actual_duration = time.time() - start_time
            logger.info(f"[插件模式] 多点拖拽完成: {len(path_points)}个点, 目标时长={total_duration:.3f}s, 实际时长={actual_duration:.3f}s")

            return True

        except Exception as e:
            logger.error(f"插件多点路径拖拽失败: {e}", exc_info=True)
            # 确保释放鼠标
            try:
                from plugins.core.interface import PluginCapability
                plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.MOUSE_DOWN)
                if plugin and hasattr(plugin, '_get_ola_for_operation'):
                    # 【多窗口线程安全】使用正确窗口的OLA实例释放鼠标
                    ola = plugin._get_ola_for_operation(self.hwnd)
                    if button == "right":
                        ola.RightUp()
                    elif button == "middle":
                        ola.MiddleUp()
                    else:
                        ola.LeftUp()
            except:
                pass
            return False

    def _drag_path_standard(self, path_points: list, duration: float, button: str, timestamps: list) -> bool:
        """
        标准方式的多点拖拽（当无法直接使用OLA对象时的回退方案）
        """
        try:
            import time
            start_time = time.time()
            use_timestamps = timestamps and len(timestamps) == len(path_points)

            # 在第一个点按下鼠标
            start_x, start_y = path_points[0]
            result = self.mouse_down(start_x, start_y, button)
            if not result:
                logger.error(f"[插件模式-标准] 在起点按下鼠标失败: ({start_x}, {start_y})")
                return False

            if use_timestamps:
                # 使用时间戳精确回放
                for i in range(1, len(path_points)):
                    x, y = path_points[i]
                    target_timestamp = timestamps[i]

                    # 等待到目标时间点
                    current_elapsed = time.time() - start_time
                    wait_time = target_timestamp - current_elapsed

                    if wait_time > 0:
                        _precise_sleep(wait_time)

                    # 移动到下一个点
                    result = self.mouse_move(x, y)
                    if not result:
                        logger.warning(f"[插件模式-标准] 移动到点{i}失败: ({x}, {y})")
            else:
                # 平滑插值逻辑（简化版）
                for i in range(1, len(path_points)):
                    x, y = path_points[i]
                    result = self.mouse_move(x, y)
                    if not result:
                        logger.warning(f"[插件模式-标准] 移动到点{i}失败: ({x}, {y})")

                    if i < len(path_points) - 1:
                        _precise_sleep(duration / len(path_points))

            # 在最后一个点释放鼠标
            end_x, end_y = path_points[-1]
            result = self.mouse_up(end_x, end_y, button)
            if not result:
                logger.error(f"[插件模式-标准] 在终点释放鼠标失败: ({end_x}, {end_y})")
                return False

            return True

        except Exception as e:
            logger.error(f"[插件模式-标准] 多点拖拽失败: {e}", exc_info=True)
            return False

    def scroll(self, x: int, y: int, delta: int) -> bool:
        """
        鼠标滚轮
        """
        if not self.plugin_manager:
            return False

        try:
            from plugins.core.interface import PluginCapability

            # 绑定窗口，检查绑定结果
            if not self._bind_window():
                logger.error(f"无法绑定窗口 (hwnd={self.hwnd})，滚轮操作失败")
                return False

            plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.MOUSE_SCROLL)
            if plugin:
                # 【多窗口线程安全】传递hwnd参数
                return plugin.mouse_scroll(x, y, delta, hwnd=self.hwnd)
            else:
                logger.warning("未找到可用的鼠标滚轮插件")
                return False

        except Exception as e:
            logger.error(f"插件滚轮失败: {e}")
            return False

    def send_key(self, vk_code: int, scan_code: int = 0, extended: bool = False) -> bool:
        """
        发送按键（完整按下释放）
        """
        return self.send_key_down(vk_code, scan_code, extended) and \
               self.send_key_up(vk_code, scan_code, extended)

    def send_key_down(self, vk_code: int, scan_code: int = 0, extended: bool = False) -> bool:
        """
        发送按键按下
        """
        if not self.plugin_manager:
            return False

        try:
            from plugins.core.interface import PluginCapability

            # 绑定窗口，检查绑定结果
            if not self._bind_window():
                logger.error(f"无法绑定窗口 (hwnd={self.hwnd})，按键按下操作失败")
                return False

            plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.KEYBOARD_DOWN)
            if plugin:
                key_str = str(vk_code)
                # 【多窗口线程安全】传递hwnd参数
                return plugin.key_down(key_str, hwnd=self.hwnd)
            else:
                logger.warning("未找到可用的按键按下插件")
                return False

        except Exception as e:
            logger.error(f"插件按键按下失败: {e}")
            return False

    def send_key_up(self, vk_code: int, scan_code: int = 0, extended: bool = False) -> bool:
        """
        发送按键释放
        """
        if not self.plugin_manager:
            return False

        try:
            from plugins.core.interface import PluginCapability

            # 绑定窗口，检查绑定结果
            if not self._bind_window():
                logger.error(f"无法绑定窗口 (hwnd={self.hwnd})，按键释放操作失败")
                return False

            plugin = self.plugin_manager.get_preferred_plugin(PluginCapability.KEYBOARD_UP)
            if plugin:
                key_str = str(vk_code)
                # 【多窗口线程安全】传递hwnd参数
                return plugin.key_up(key_str, hwnd=self.hwnd)
            else:
                logger.warning("未找到可用的按键释放插件")
                return False

        except Exception as e:
            logger.error(f"插件按键释放失败: {e}")
            return False

    def send_key_combination(self, keys: list, hold_duration: float = DEFAULT_KEY_HOLD_SECONDS) -> bool:
        """
        发送组合键
        """
        try:
            import time
            if not self.press_key_combination(keys):
                return False
            _precise_sleep(hold_duration)
            return self.release_key_combination(keys)
        except Exception as e:
            logger.error(f"插件组合键失败: {e}")
            return False

    def press_key_combination(self, keys: list) -> bool:
        """
        按下组合键（不释放）
        """
        if not self.plugin_manager or not keys:
            return False

        try:
            for key in keys:
                if not self.send_key_down(key):
                    for pressed_key in keys[:keys.index(key)]:
                        self.send_key_up(pressed_key)
                    return False
            return True
        except Exception as e:
            logger.error(f"插件组合键按下失败: {e}")
            return False

    def release_key_combination(self, keys: list) -> bool:
        """
        释放组合键（逆序）
        """
        if not self.plugin_manager or not keys:
            return False

        try:
            for key in reversed(keys):
                self.send_key_up(key)
            return True
        except Exception as e:
            logger.error(f"插件组合键释放失败: {e}")
            return False

    def __del__(self):
        """析构函数，确保解绑窗口"""
        try:
            self._unbind_window()
        except:
            pass

    # ========== 元素操作方法（插件模式不支持，明确抛异常禁止降级） ==========

    def click_element(
        self,
        name: str = None,
        automation_id: str = None,
        class_name: str = None,
        control_type: str = None,
        found_index: int = 0,
        search_depth: int = 10,
        timeout: float = 5.0,
        use_invoke: bool = True
    ) -> bool:
        """
        点击UI元素 - 插件模式不支持

        OLA插件不支持UIAutomation元素操作。
        如需元素点击功能，请使用原生模式 (backend="native")。

        Raises:
            NotImplementedError: 插件模式不支持此功能
        """
        raise NotImplementedError(
            "插件模式(OLA)不支持UIAutomation元素点击功能。"
            "如需元素点击，请使用原生模式: backend='native'"
        )

    def find_element(
        self,
        name: str = None,
        automation_id: str = None,
        class_name: str = None,
        control_type: str = None,
        search_depth: int = 10,
        timeout: float = 5.0
    ):
        """
        查找UI元素 - 插件模式不支持

        Raises:
            NotImplementedError: 插件模式不支持此功能
        """
        raise NotImplementedError(
            "插件模式(OLA)不支持UIAutomation元素查找功能。"
            "如需元素查找，请使用原生模式: backend='native'"
        )

    def find_all_elements(
        self,
        name: str = None,
        automation_id: str = None,
        class_name: str = None,
        control_type: str = None,
        search_depth: int = 10,
        timeout: float = 5.0
    ):
        """
        查找所有匹配的UI元素 - 插件模式不支持

        Raises:
            NotImplementedError: 插件模式不支持此功能
        """
        raise NotImplementedError(
            "插件模式(OLA)不支持UIAutomation元素查找功能。"
            "如需元素查找，请使用原生模式: backend='native'"
        )

    def get_element_text(
        self,
        name: str = None,
        automation_id: str = None,
        class_name: str = None,
        control_type: str = None,
        search_depth: int = 10,
        timeout: float = 5.0
    ):
        """
        获取UI元素的文本 - 插件模式不支持

        Raises:
            NotImplementedError: 插件模式不支持此功能
        """
        raise NotImplementedError(
            "插件模式(OLA)不支持UIAutomation元素文本获取功能。"
            "如需此功能，请使用原生模式: backend='native'"
        )

