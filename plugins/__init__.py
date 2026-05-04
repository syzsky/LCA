# -*- coding: utf-8 -*-
"""
插件系统 - Plugin System
提供统一的第三方插件适配层，支持大漠、OLA等插件

特点：
1. 无侵入式设计 - 不修改现有代码
2. 可选启用 - 默认使用原有逻辑
3. 多插件支持 - 自动降级和优先级管理
4. 向后兼容 - 100%兼容现有系统
"""

from .core.manager import PluginManager
from .core.interface import PluginCapability, IPluginAdapter

# 全局单例
_plugin_manager = None


def get_plugin_manager() -> PluginManager:
    """
    获取插件管理器单例

    Returns:
        PluginManager: 全局插件管理器实例
    """
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager()
        _plugin_manager.load_config()
    return _plugin_manager


def init_plugin_system(config_path: str = None):
    """
    初始化插件系统（可选调用）

    Args:
        config_path: 配置文件路径，默认为 plugins/config.json
    """
    manager = get_plugin_manager()
    if config_path:
        manager.load_config(config_path)
    return manager


__all__ = [
    'get_plugin_manager',
    'init_plugin_system',
    'PluginManager',
    'PluginCapability',
    'IPluginAdapter'
]

__version__ = '1.0.0'
