# -*- coding: utf-8 -*-
"""插件核心模块"""

from .interface import IPluginAdapter, PluginCapability
from .manager import PluginManager

__all__ = ['IPluginAdapter', 'PluginCapability', 'PluginManager']
