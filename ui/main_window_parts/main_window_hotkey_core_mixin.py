import logging

logger = logging.getLogger(__name__)


class MainWindowHotkeyCoreMixin:

    def _update_hotkeys(self):

        """更新全局快捷键 - 统一的快捷键管理系统（支持插件模式）"""

        try:

            # 检查是否启用插件模式

            from app_core.plugin_bridge import is_plugin_enabled

            use_plugin_hotkeys = is_plugin_enabled()

            if use_plugin_hotkeys:

                # 插件模式：使用OLA的热键API

                self._update_hotkeys_plugin_mode()

            else:

                # 原有模式：使用keyboard/mouse库

                self._update_hotkeys_original_mode()

        except Exception as e:

            logger.error(f"更新快捷键失败: {e}")

            import traceback

            logger.debug(f"快捷键更新错误详情: {traceback.format_exc()}")

    def _get_hotkey_value(self, hotkey_type: str) -> str:

        """

        智能获取热键值 - 兼容控件和字符串两种情况

        Args:

            hotkey_type: 'start', 'stop', 或 'pause'

        Returns:

            热键字符串值（大写）

        """

        if hotkey_type == 'start':

            attr = self.start_task_hotkey

            default = 'XBUTTON1'

            config_key = 'start_task_hotkey'

        elif hotkey_type == 'stop':

            attr = self.stop_task_hotkey

            default = 'XBUTTON2'

            config_key = 'stop_task_hotkey'

        elif hotkey_type == 'pause':

            attr = self.pause_workflow_hotkey

            default = 'F11'

            config_key = 'pause_workflow_hotkey'

        else:

            return 'F9'

        # 如果是控件对象（有 currentData 方法）

        if hasattr(attr, 'currentData'):

            value = attr.currentData()

            if value:

                return value.upper()

            else:

                return default

        # 如果是字符串

        elif isinstance(attr, str):

            return attr.upper()

        # 回退到配置

        else:

            return self.config.get(config_key, default).upper()

    def _resolve_hotkey_conflicts(self, action_keys: dict, action_names: dict):

        """Resolve duplicate hotkey assignments and return filtered mapping + messages."""

        preferred_order = ['start', 'stop', 'pause', 'record', 'replay']

        resolved = {}

        seen = {}

        conflicts = []

        for action in preferred_order:

            key = action_keys.get(action)

            if not key:

                continue

            normalized = str(key).upper()

            if normalized in seen:

                conflicts.append(

                    f"{normalized}: {action_names.get(seen[normalized], seen[normalized])} / {action_names.get(action, action)}"

                )

                continue

            seen[normalized] = action

            resolved[action] = key

        for action, key in action_keys.items():

            if action in preferred_order or not key:

                continue

            normalized = str(key).upper()

            if normalized in seen:

                conflicts.append(

                    f"{normalized}: {action_names.get(seen[normalized], seen[normalized])} / {action_names.get(action, action)}"

                )

                continue

            seen[normalized] = action

            resolved[action] = key

        return resolved, conflicts
