"""
输入模拟模块
提供统一的键盘鼠标模拟接口，支持普通窗口和模拟器窗口
"""

from .base import BaseInputSimulator, InputSimulatorType, ElementNotFoundError
from .standard_window import StandardWindowInputSimulator
from .factory import (
    InputSimulatorFactory,
    GlobalInputSimulatorManager,
    global_input_simulator_manager,
    SimulatorBackend,
    BackendNotAvailableError
)

# 插件模拟器（可选，需要插件系统支持）
try:
    from .plugin_simulator import PluginInputSimulator
except ImportError:
    PluginInputSimulator = None

__all__ = [
    'BaseInputSimulator',
    'InputSimulatorType',
    'ElementNotFoundError',
    'StandardWindowInputSimulator',
    'InputSimulatorFactory',
    'GlobalInputSimulatorManager',
    'global_input_simulator_manager',
    'SimulatorBackend',
    'BackendNotAvailableError',
    'PluginInputSimulator'
]
