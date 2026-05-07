# -*- coding: utf-8 -*-
"""
Native Plugin Adapter - 纯 Python 原生插件适配器
无需外部 DLL，使用 pyautogui/pynput/paddleocr/pywin32 实现所有功能
兼容 LCA 插件接口 (IImagePlugin, IInputPlugin, IOCRPlugin)
"""

from .adapter import NativeAdapter

__all__ = ['NativeAdapter']
