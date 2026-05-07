# -*- coding: utf-8 -*-
"""
插件管理器 - 负责插件的加载、管理和调度
"""

import json
import logging
import os
import shutil
from typing import Dict, List, Optional, Any
from .interface import IPluginAdapter, PluginCapability

# 条件导入 OLA 相关模块（如果 OLA SDK 存在）
try:
    from plugins.adapters.ola.runtime_config import normalize_ola_auth_settings
    _OLA_CONFIG_AVAILABLE = True
except ImportError:
    _OLA_CONFIG_AVAILABLE = False

from utils.app_paths import get_config_path, get_user_data_dir

logger = logging.getLogger(__name__)


def _get_user_plugin_config_path() -> str:
    return os.path.join(get_user_data_dir("LCA"), "plugins", "config.json")


def _get_legacy_plugin_config_path() -> str:
    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(current_dir, "config.json")


def _migrate_plugin_config(target_path: str) -> None:
    if not target_path or os.path.exists(target_path):
        return
    legacy_path = _get_legacy_plugin_config_path()
    if not os.path.exists(legacy_path):
        return
    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        shutil.copy2(legacy_path, target_path)
    except Exception as e:
        logger.warning(f"迁移插件配置失败: {e}")


class PluginManager:
    """
    插件管理器

    特点：
    1. 单例模式 - 全局唯一实例
    2. 优先级管理 - 根据配置自动选择插件
    3. 降级策略 - 失败自动切换备用插件
    4. 热加载 - 支持运行时加载/卸载插件
    5. 多插件支持 - 支持 native/ola 等多种插件
    """

    def __init__(self):
        self._plugins: Dict[str, IPluginAdapter] = {}  # {plugin_name: adapter_instance}
        self._priorities: Dict[PluginCapability, List[str]] = {}  # {capability: [plugin_names]}
        self._config: Dict = {}
        self._enabled = False  # 插件系统是否启用

    def load_config(self, config_path: str = None):
        """
        加载插件配置

        Args:
            config_path: 配置文件路径，默认为 plugins/config.json
        """
        if config_path is None:
            config_path = _get_user_plugin_config_path()
            _migrate_plugin_config(config_path)

        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    self._config = json.load(f)

                    # 检测旧版本配置格式，自动覆盖
                    ola_plugin = self._config.get('plugins', {}).get('ola', {})
                    is_old_format = (
                        'builtin' in self._config.get('plugins', {}) or
                        'fallback_chain' in self._config or
                        ('dll_path' in ola_plugin and 'config' not in ola_plugin)
                    )

                    if is_old_format:
                        logger.warning(f"检测到旧版本配置格式，自动覆盖为新格式: {config_path}")
                        self._create_default_config(config_path)
                        with open(config_path, 'r', encoding='utf-8') as f:
                            self._config = json.load(f)

                    self._enabled = self._config.get('plugin_system_enabled', False)
                    logger.info(f"插件配置加载成功: {config_path}")
                    logger.info(f"插件系统状态: {'启用' if self._enabled else '禁用（使用原有逻辑）'}")
                    logger.info(f"插件配置详情: {json.dumps(self._config, ensure_ascii=False, indent=2)}")
            else:
                logger.warning(f"插件配置文件不存在: {config_path}，使用默认配置")
                self._create_default_config(config_path)
        except Exception as e:
            logger.error(f"加载插件配置失败: {e}", exc_info=True)

    def _load_main_plugin_settings(self) -> Dict[str, Any]:
        try:
            main_config_path = get_config_path()
            if not main_config_path or not os.path.exists(main_config_path):
                return {}
            with open(main_config_path, 'r', encoding='utf-8') as f:
                main_config = json.load(f)
            plugin_settings = main_config.get('plugin_settings', {})
            return plugin_settings if isinstance(plugin_settings, dict) else {}
        except Exception as e:
            logger.warning(f"读取主配置中的插件设置失败: {e}")
            return {}

    def _merge_main_runtime_overrides(self, plugin_name: str, inner_config: Dict[str, Any]) -> Dict[str, Any]:
        merged_config = dict(inner_config) if isinstance(inner_config, dict) else {}
        if plugin_name != "ola":
            return merged_config

        if not _OLA_CONFIG_AVAILABLE:
            return merged_config

        try:
            plugin_settings = self._load_main_plugin_settings()
            if 'ola_auth' not in plugin_settings:
                return merged_config

            ola_auth = normalize_ola_auth_settings(plugin_settings.get('ola_auth'))
            for key, value in ola_auth.items():
                merged_config[key] = value
        except Exception as e:
            logger.warning(f"合并 OLA 运行时配置失败: {e}")

        return merged_config

    def _create_default_config(self, config_path: str):
        """创建默认配置文件（优先使用 Native 插件）"""
        default_config = {
            "plugin_system_enabled": True,
            "description": "插件系统全局配置 - 默认使用 Native 纯Python原生插件",
            "plugins": {
                "native": {
                    "enabled": True,
                    "priority": 5,
                    "description": "纯Python原生插件 - 无需外部DLL",
                    "config": {
                        "use_human_like_mouse": False,
                        "ocr_language": "ch",
                        "ocr_use_gpu": False
                    }
                },
                "ola": {
                    "enabled": False,
                    "priority": 10,
                    "description": "OLA欧拉插件（需要DLL）",
                    "config": {
                        "dll_path": "OLA/OLAPlug_x64.dll",
                        "auto_bind_mode": {
                            "display": "normal",
                            "mouse": "normal",
                            "keypad": "normal"
                        }
                    }
                }
            }
        }

        try:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=2, ensure_ascii=False)
            self._config = default_config
            logger.info(f"已创建默认插件配置: {config_path}")
        except Exception as e:
            logger.error(f"创建默认配置失败: {e}", exc_info=True)

    def register_plugin(self, plugin_name: str, adapter: IPluginAdapter, priority: int = 100):
        """
        注册插件

        Args:
            plugin_name: 插件名称
            adapter: 插件适配器实例
            priority: 优先级 (0-1000,数字越小优先级越高)
        """
        try:
            self._plugins[plugin_name] = adapter

            # 获取插件能力列表
            capabilities = adapter.get_capabilities()
            logger.debug(f"插件 {plugin_name} 的能力列表: {[str(cap) for cap in capabilities]}")

            # 注册插件能力到优先级列表
            for capability in capabilities:
                if capability not in self._priorities:
                    self._priorities[capability] = []

                # 按优先级插入
                self._priorities[capability].append(plugin_name)
                self._priorities[capability].sort(
                    key=lambda name: self._config.get('plugins', {}).get(name, {}).get('priority', 100)
                )
                logger.debug(f"注册能力: {capability} -> {plugin_name}")

            logger.info(f"插件注册成功: {plugin_name} (优先级: {priority}), 能力数量: {len(capabilities)}")
            return True
        except Exception as e:
            logger.error(f"插件注册失败: {plugin_name}, {e}", exc_info=True)
            return False

    def load_plugin(self, plugin_name: str) -> bool:
        """
        加载插件

        Args:
            plugin_name: 插件名称 ("native" 或 "ola")

        Returns:
            bool: 加载是否成功
        """
        try:
            # 如果配置为空，尝试重新加载配置
            if not self._config or not self._config.get('plugins'):
                logger.warning(f"配置为空，尝试重新加载配置文件")
                self.load_config()

            plugin_config = self._config.get('plugins', {}).get(plugin_name, {})

            logger.debug(f"尝试加载插件 {plugin_name}, 配置: {json.dumps(plugin_config, ensure_ascii=False)}")

            if not plugin_config.get('enabled', False):
                logger.warning(f"插件 {plugin_name} 未启用（enabled={plugin_config.get('enabled', '未设置')}）")
                logger.warning(f"完整配置: plugins={list(self._config.get('plugins', {}).keys())}")
                return False

            # 动态加载插件适配器
            if plugin_name == "native":
                try:
                    from plugins.adapters.native.adapter import NativeAdapter
                    adapter = NativeAdapter()
                except ImportError as e:
                    logger.error(f"Native 插件导入失败: {e}")
                    logger.info("请确认 plugins/adapters/native/ 目录存在且包含 adapter.py")
                    return False
            elif plugin_name == "ola":
                try:
                    from plugins.adapters.ola.adapter import OLAAdapter
                    adapter = OLAAdapter()
                except ImportError as e:
                    logger.error(f"OLA 插件导入失败: {e}")
                    logger.info("请确认 OLA SDK 已正确安装")
                    return False
            else:
                logger.error(f"未知的插件类型: {plugin_name}")
                return False

            # 初始化插件 - 传入 config 字段
            plugin_inner_config = self._merge_main_runtime_overrides(
                plugin_name,
                plugin_config.get('config', {}),
            )
            if adapter.initialize(plugin_inner_config):
                priority = plugin_config.get('priority', 100)
                self.register_plugin(plugin_name, adapter, priority)
                return True
            else:
                logger.error(f"插件初始化失败: {plugin_name}")
                return False

        except Exception as e:
            logger.error(f"加载插件失败: {plugin_name}, {e}", exc_info=True)
            return False

    def unload_plugin(self, plugin_name: str) -> bool:
        """卸载插件"""
        try:
            if plugin_name in self._plugins:
                adapter = self._plugins[plugin_name]
                adapter.release()
                del self._plugins[plugin_name]

                # 从优先级列表中移除
                for capability in self._priorities:
                    if plugin_name in self._priorities[capability]:
                        self._priorities[capability].remove(plugin_name)

                logger.info(f"插件卸载成功: {plugin_name}")
                return True
            return False
        except Exception as e:
            logger.error(f"插件卸载失败: {plugin_name}, {e}", exc_info=True)
            return False

    def get_plugin(self, plugin_name: str) -> Optional[IPluginAdapter]:
        """获取指定插件"""
        return self._plugins.get(plugin_name)

    def get_plugin_config(self, plugin_name: str) -> Dict[str, Any]:
        """获取指定插件的内部配置。"""
        plugin_config = self._config.get('plugins', {}).get(plugin_name, {})
        inner_config = plugin_config.get('config', {})
        if isinstance(inner_config, dict):
            return self._merge_main_runtime_overrides(plugin_name, inner_config)
        return {}

    def get_preferred_plugin(self, capability: PluginCapability) -> Optional[IPluginAdapter]:
        """
        根据能力获取首选插件（按优先级）

        Args:
            capability: 插件能力

        Returns:
            Optional[IPluginAdapter]: 首选插件，未找到返回None
        """
        if not self._enabled:
            # 插件系统未启用
            return None

        plugin_names = self._priorities.get(capability, [])
        logger.debug(f"查找能力 {capability} 的插件: 候选列表={plugin_names}")

        for name in plugin_names:
            plugin = self._plugins.get(name)
            if plugin:
                health = plugin.health_check()
                logger.debug(f"  检查插件 {name}: 存在={plugin is not None}, 健康={health}")
                if health:
                    logger.debug(f"  ✓ 选中插件 {name} 支持能力 {capability}")
                    return plugin
            else:
                logger.debug(f"  插件 {name} 不存在")

        logger.warning(f"未找到可用的插件支持能力: {capability}, 可用能力列表: {list(self._priorities.keys())}")
        return None

    def execute(self, capability: PluginCapability, method: str, *args, **kwargs) -> Any:
        """
        执行操作（不支持降级，插件模式和原生模式完全独立）

        Args:
            capability: 插件能力
            method: 方法名
            *args, **kwargs: 方法参数

        Returns:
            Any: 执行结果
        """
        if not self._enabled:
            raise RuntimeError("插件系统未启用，请使用原生模式")

        # 获取首选插件
        plugin = self.get_preferred_plugin(capability)
        if not plugin:
            raise RuntimeError(f"未找到支持能力 {capability} 的插件")

        # 直接执行，不降级
        return plugin.execute(capability, method, *args, **kwargs)

    def list_plugins(self) -> List[dict]:
        """列出所有插件信息"""
        result = []
        for name, adapter in self._plugins.items():
            result.append({
                'name': name,
                'version': adapter.get_version(),
                'capabilities': [cap.value for cap in adapter.get_capabilities()],
                'healthy': adapter.health_check()
            })
        return result

    def is_enabled(self) -> bool:
        """插件系统是否启用"""
        return self._enabled

    def enable(self):
        """启用插件系统"""
        self._enabled = True
        logger.info("插件系统已启用")

    def disable(self):
        """禁用插件系统（恢复原有逻辑）"""
        self._enabled = False
        logger.info("插件系统已禁用，使用原有逻辑")
