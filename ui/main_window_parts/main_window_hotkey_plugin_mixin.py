import logging

from PySide6.QtCore import QThread, QTimer

logger = logging.getLogger(__name__)


class MainWindowHotkeyPluginMixin:

    def _schedule_plugin_hotkey_retry(self, delay_ms: int = 200):

        try:

            if getattr(self, '_plugin_hotkey_retry_pending', False):

                return

            self._plugin_hotkey_retry_pending = True

            def _retry():

                self._plugin_hotkey_retry_pending = False

                try:

                    self._update_hotkeys()

                except Exception as e:

                    logger.error(f"[插件热键] 延迟重试失败: {e}")

            QTimer.singleShot(max(0, int(delay_ms)), self, _retry)

        except Exception as e:

            self._plugin_hotkey_retry_pending = False

            logger.error(f"[插件热键] 安排延迟重试失败: {e}")

    def _update_hotkeys_plugin_mode(self):

        """插件模式的热键更新 - 使用OLA插件API"""

        try:

            from app_core.plugin_bridge import get_plugin_manager, get_plugin_manager_initialization_state

            from plugins.core.interface import PluginCapability

            # 清理之前的插件热键

            if hasattr(self, '_plugin_hotkey_listener') and self._plugin_hotkey_listener:

                self._plugin_hotkey_listener.stop_listening()

                self._plugin_hotkey_listener = None

            # 获取OLA插件实例

            init_state = get_plugin_manager_initialization_state()

            pm = get_plugin_manager(wait=False)

            if not pm:

                if init_state in {'scheduled', 'running'}:

                    logger.info("[插件热键] 插件仍在初始化，延后注册热键")

                    self._schedule_plugin_hotkey_retry()

                    return

            if not pm or not pm.is_enabled():

                logger.warning("[插件热键] 插件管理器不可用，回退到原有模式")

                self._update_hotkeys_original_mode()

                return

            plugin = pm.get_preferred_plugin(PluginCapability.IMAGE_FIND_PIC)

            if not plugin or not hasattr(plugin, 'ola') or not plugin.ola:

                logger.warning("[插件热键] 无法获取OLA实例，回退到原有模式")

                self._update_hotkeys_original_mode()

                return

            ola = plugin.ola

            # 启动热键钩子

            result = ola.StartHotkeyHook()

            if result != 1:

                logger.warning(f"[插件热键] 启动热键钩子失败（返回值: {result}），回退到原有模式")

                self._update_hotkeys_original_mode()

                return

            logger.info("[插件热键] 热键钩子启动成功")

            # 获取热键配置 - 智能判断是控件还是字符串

            start_key = self._get_hotkey_value('start')

            stop_key = self._get_hotkey_value('stop')

            record_key = self.config.get('record_hotkey', 'F11').upper()

            replay_key = self.config.get('replay_hotkey', 'F12').upper()

            pause_key = self.config.get('pause_workflow_hotkey', 'F11').upper()

            action_names = {

                'start': '启动任务',

                'stop': '停止任务',

                'pause': '暂停/恢复',

                'record': '录制',

                'replay': '回放',

            }

            action_keys = {

                'start': start_key,

                'stop': stop_key,

                'pause': pause_key,

                'record': record_key,

                'replay': replay_key,

            }

            action_keys, conflict_messages = self._resolve_hotkey_conflicts(action_keys, action_names)

            start_key = action_keys.get('start')

            stop_key = action_keys.get('stop')

            pause_key = action_keys.get('pause')

            record_key = action_keys.get('record')

            replay_key = action_keys.get('replay')

            # 保存OLA实例和已注册热键列表，用于后续清理

            self._ola_hotkey_instance = ola

            self._ola_registered_hotkeys = []  # (type, key, modifier) - type: 'hotkey' or 'mouse'

            self._ola_hotkey_callbacks = {}  # 保持回调引用

            failed_hotkeys = []  # 记录注册失败的快捷键

            if conflict_messages:

                from PySide6.QtWidgets import QMessageBox

                QMessageBox.warning(

                    self,

                    "快捷键冲突",

                    "以下快捷键被多个功能同时使用，已保留优先项，其他冲突项已忽略：\n\n"

                    f"{chr(10).join(conflict_messages)}\n\n请修改为不同快捷键。"

                )

            # 鼠标按钮映射 - 尝试使用 4 和 5（可能是OLA的实际约定）

            # 0=左键, 1=右键, 2=中键, 3=?, 4=XBUTTON1, 5=XBUTTON2

            mouse_button_map = {'XBUTTON1': 4, 'XBUTTON2': 5}

            # 收集需要注册的鼠标侧键及其对应功能

            mouse_button_actions = {}  # {button_id: action_name}

            if start_key and start_key.upper() in mouse_button_map:

                button_id = mouse_button_map[start_key.upper()]

                mouse_button_actions[button_id] = 'start'

            if stop_key and stop_key.upper() in mouse_button_map:

                button_id = mouse_button_map[stop_key.upper()]

                mouse_button_actions[button_id] = 'stop'

            if pause_key and pause_key.upper() in mouse_button_map:

                button_id = mouse_button_map[pause_key.upper()]

                mouse_button_actions[button_id] = 'pause'

            # 如果有鼠标侧键需要注册，使用统一的回调处理

            # OLA DLL 每个button可以注册一个回调，关键是ctypes回调对象必须保持引用

            if mouse_button_actions:

                def on_mouse_button_callback(button, x, y, flag):

                    """统一的鼠标按钮回调，根据button参数分发到不同功能"""

                    action = mouse_button_actions.get(button)

                    logger.info(f"[插件热键] 鼠标按钮回调: button={button}, action={action}")

                    if action == 'start':

                        self._on_start_task_hotkey()

                    elif action == 'stop':

                        self._on_stop_task_hotkey()

                    elif action == 'pause':

                        self._on_pause_workflow_hotkey()

                self._ola_hotkey_callbacks['mouse_unified'] = on_mouse_button_callback

                # 关键：必须在注册前将回调转换为ctypes函数指针并保存，防止被垃圾回收

                from ctypes import WINFUNCTYPE, c_int32

                MouseCallbackType = WINFUNCTYPE(None, c_int32, c_int32, c_int32, c_int32)

                ctypes_callback = MouseCallbackType(on_mouse_button_callback)

                self._ola_hotkey_callbacks['mouse_ctypes'] = ctypes_callback

                # 分别注册每个需要的按钮，共用同一个ctypes回调

                for button_id, action in mouse_button_actions.items():

                    key_name = 'XBUTTON1' if button_id == 4 else 'XBUTTON2'

                    # 直接调用底层DLL，绕过OLAPlugDLLHelper的回调包装（它每次都创建新的ctypes对象）

                    from OLA.OLAPlugDLLHelper import OLAPlugDLLHelper

                    result = OLAPlugDLLHelper._dll.RegisterMouseButton(

                        ola.OLAObject, button_id, 0, ctypes_callback

                    )

                    if result == 1:

                        self._ola_registered_hotkeys.append(('mouse', button_id, 0))

                        logger.info(f"[插件热键] {action}任务快捷键已设置: {key_name} (button_id={button_id})")

                    else:

                        action_name = action_names.get(action, action)

                        failed_hotkeys.append(f"{action_name}({key_name})")

                        logger.warning(f"[插件热键] {action}任务快捷键注册失败: {key_name} (返回值: {result})")

            # 注册键盘热键 - 启动任务

            if start_key and start_key.upper() not in mouse_button_map:

                start_vk = self._get_vk_code_for_plugin(start_key)

                if start_vk:

                    def on_start_callback(keycode, modifiers):

                        self._on_start_task_hotkey()

                    self._ola_hotkey_callbacks['start'] = on_start_callback

                    result = ola.RegisterHotkey(start_vk, 0, on_start_callback)

                    if result == 1:

                        self._ola_registered_hotkeys.append(('hotkey', start_vk, 0))

                        logger.info(f"[插件热键] 启动任务快捷键已设置: {start_key}")

                    else:

                        failed_hotkeys.append(f"启动任务({start_key})")

                        logger.warning(f"[插件热键] 启动任务快捷键注册失败: {start_key}")

                else:

                    failed_hotkeys.append(f"启动任务({start_key})")

                    logger.warning(f"[插件热键] 启动任务快捷键不支持: {start_key}")

            # 注册键盘热键 - 停止任务

            if stop_key and stop_key.upper() not in mouse_button_map:

                stop_vk = self._get_vk_code_for_plugin(stop_key)

                if stop_vk:

                    def on_stop_callback(keycode, modifiers):

                        self._on_stop_task_hotkey()

                    self._ola_hotkey_callbacks['stop'] = on_stop_callback

                    result = ola.RegisterHotkey(stop_vk, 0, on_stop_callback)

                    if result == 1:

                        self._ola_registered_hotkeys.append(('hotkey', stop_vk, 0))

                        logger.info(f"[插件热键] 停止任务快捷键已设置: {stop_key}")

                    else:

                        failed_hotkeys.append(f"停止任务({stop_key})")

                        logger.warning(f"[插件热键] 停止任务快捷键注册失败: {stop_key}")

                else:

                    failed_hotkeys.append(f"停止任务({stop_key})")

                    logger.warning(f"[插件热键] 停止任务快捷键不支持: {stop_key}")

            # 注册录制热键

            if record_key:

                record_vk = self._get_vk_code_for_plugin(record_key)

                if record_vk:

                    def on_record_callback(keycode, modifiers):

                        self._on_record_hotkey()

                    self._ola_hotkey_callbacks['record'] = on_record_callback

                    result = ola.RegisterHotkey(record_vk, 0, on_record_callback)

                    if result == 1:

                        self._ola_registered_hotkeys.append(('hotkey', record_vk, 0))

                        logger.info(f"[插件热键] 录制快捷键已设置: {record_key}")

                    else:

                        failed_hotkeys.append(f"录制({record_key})")

                else:

                    failed_hotkeys.append(f"录制({record_key})")

                    logger.warning(f"[插件热键] 录制快捷键不支持: {record_key}")

            # 注册回放热键

            if replay_key:

                replay_vk = self._get_vk_code_for_plugin(replay_key)

                if replay_vk:

                    def on_replay_callback(keycode, modifiers):

                        self._on_replay_hotkey()

                    self._ola_hotkey_callbacks['replay'] = on_replay_callback

                    result = ola.RegisterHotkey(replay_vk, 0, on_replay_callback)

                    if result == 1:

                        self._ola_registered_hotkeys.append(('hotkey', replay_vk, 0))

                        logger.info(f"[插件热键] 回放快捷键已设置: {replay_key}")

                    else:

                        failed_hotkeys.append(f"回放({replay_key})")

                else:

                    failed_hotkeys.append(f"回放({replay_key})")

                    logger.warning(f"[插件热键] 回放快捷键不支持: {replay_key}")

            # 注册暂停工作流热键

            pause_vk = self._get_vk_code_for_plugin(pause_key) if pause_key else None

            if pause_vk:

                def on_pause_callback(keycode, modifiers):

                    self._on_pause_workflow_hotkey()

                self._ola_hotkey_callbacks['pause'] = on_pause_callback

                result = ola.RegisterHotkey(pause_vk, 0, on_pause_callback)

                if result == 1:

                    self._ola_registered_hotkeys.append(('hotkey', pause_vk, 0))

                    logger.info(f"[插件热键] 暂停工作流快捷键已设置: {pause_key}")

                else:

                    failed_hotkeys.append(f"暂停工作流({pause_key})")

            elif pause_key:

                failed_hotkeys.append(f"暂停工作流({pause_key})")

                logger.warning(f"[插件热键] 暂停工作流快捷键不支持: {pause_key}")

            logger.info(

                f"✓ [插件模式] 快捷键系统已更新 - 启动: {start_key or '-'}, 停止: {stop_key or '-'}, "

                f"暂停: {pause_key or '-'}, 录制: {record_key or '-'}, 回放: {replay_key or '-'}"

            )

            # 如果有快捷键注册失败，提示用户

            if failed_hotkeys:

                from PySide6.QtWidgets import QMessageBox

                QMessageBox.warning(

                    self,

                    "快捷键注册失败",

                    f"以下快捷键可能被其他程序占用，注册失败：\n\n{', '.join(failed_hotkeys)}\n\n请尝试更换其他快捷键。"

                )

        except Exception as e:

            logger.error(f"[插件热键] 设置失败: {e}，回退到原有模式")

            import traceback

            logger.debug(traceback.format_exc())

            self._update_hotkeys_original_mode()

    def _cleanup_plugin_hotkeys(self):

        """清理插件模式的热键"""

        try:

            if hasattr(self, '_ola_hotkey_instance') and self._ola_hotkey_instance:

                # 取消注册所有热键和鼠标按钮

                if hasattr(self, '_ola_registered_hotkeys'):

                    for item in self._ola_registered_hotkeys:

                        try:

                            if len(item) == 3:

                                reg_type, key, modifier = item

                                if reg_type == 'mouse':

                                    self._ola_hotkey_instance.UnregisterMouseButton(key, modifier)

                                else:  # 'hotkey'

                                    self._ola_hotkey_instance.UnregisterHotkey(key, modifier)

                            else:

                                # 兼容旧格式 (keycode, modifiers)

                                keycode, modifiers = item

                                self._ola_hotkey_instance.UnregisterHotkey(keycode, modifiers)

                        except Exception as e:

                            logger.debug(f"取消注册插件热键失败: {e}")

                    self._ola_registered_hotkeys.clear()

                # 停止热键钩子

                try:

                    self._ola_hotkey_instance.StopHotkeyHook()

                    logger.info("[插件热键] 热键钩子已停止")

                except Exception as e:

                    logger.debug(f"停止插件热键钩子失败: {e}")

            # 清理回调引用

            if hasattr(self, '_ola_hotkey_callbacks'):

                self._ola_hotkey_callbacks.clear()

        except Exception as e:

            logger.warning(f"清理插件热键失败: {e}")

    def _queue_hotkey_callback(self, callback):

        """将热键回调投递到主线程执行，避免频繁创建后台线程。"""

        if callback is None:

            return

        try:

            if QThread.currentThread() == self.thread():

                callback()

            else:

                QTimer.singleShot(0, self, callback)

        except Exception as e:

            logger.error(f"投递热键回调失败: {e}")

    def _get_vk_code_for_plugin(self, hotkey: str) -> int:

        """将热键字符串转换为虚拟键码（用于插件模式）"""

        hotkey_map = {

            # F1-F12

            'F1': 0x70, 'F2': 0x71, 'F3': 0x72, 'F4': 0x73,

            'F5': 0x74, 'F6': 0x75, 'F7': 0x76, 'F8': 0x77,

            'F9': 0x78, 'F10': 0x79, 'F11': 0x7A, 'F12': 0x7B,

            # 导航键

            'HOME': 0x24, 'END': 0x23,

            'INSERT': 0x2D, 'DELETE': 0x2E,

            'PAGEUP': 0x21, 'PAGEDOWN': 0x22,

            # 特殊键

            'PRINTSCREEN': 0x2C, 'SCROLLLOCK': 0x91, 'PAUSE': 0x13,

            'NUMLOCK': 0x90,

            # 小键盘数字

            'NUM0': 0x60, 'NUM1': 0x61, 'NUM2': 0x62, 'NUM3': 0x63,

            'NUM4': 0x64, 'NUM5': 0x65, 'NUM6': 0x66, 'NUM7': 0x67,

            'NUM8': 0x68, 'NUM9': 0x69,

            # 小键盘运算符

            'NUMMULTIPLY': 0x6A, 'NUMADD': 0x6B, 'NUMSUBTRACT': 0x6D,

            'NUMDIVIDE': 0x6F, 'NUMDECIMAL': 0x6E,

            # 鼠标侧键

            'XBUTTON1': 0x05, 'XBUTTON2': 0x06

        }

        return hotkey_map.get(hotkey.upper(), None)
