# -*- coding: utf-8 -*-
"""
插件接口定义 - 统一的插件能力抽象
"""

from abc import ABC, abstractmethod
from typing import List, Tuple, Optional, Any
from enum import Enum


class PluginCapability(Enum):
    """插件能力枚举"""
    # 图像识别
    IMAGE_FIND_PIC = "image_find_pic"              # 找图
    IMAGE_FIND_COLOR = "image_find_color"          # 找色
    IMAGE_FIND_MULTI_COLOR = "image_find_multi_color"  # 多点找色
    IMAGE_CAPTURE = "image_capture"                # 截图
    IMAGE_GET_COLOR = "image_get_color"            # 获取颜色

    # 鼠标操作
    MOUSE_MOVE = "mouse_move"                      # 移动鼠标
    MOUSE_CLICK = "mouse_click"                    # 鼠标点击
    MOUSE_DOWN = "mouse_down"                      # 鼠标按下
    MOUSE_UP = "mouse_up"                          # 鼠标释放
    MOUSE_DRAG = "mouse_drag"                      # 鼠标拖拽
    MOUSE_SCROLL = "mouse_scroll"                  # 鼠标滚轮

    # 键盘操作
    KEYBOARD_PRESS = "keyboard_press"              # 按键
    KEYBOARD_DOWN = "keyboard_down"                # 按键按下
    KEYBOARD_UP = "keyboard_up"                    # 按键释放
    KEYBOARD_INPUT_TEXT = "keyboard_input_text"    # 输入文字
    KEYBOARD_COMBINATION = "keyboard_combination"  # 组合键

    # OCR识别
    OCR_TEXT = "ocr_text"                          # 文字识别
    OCR_FIND_TEXT = "ocr_find_text"                # 查找文字

    # 窗口操作
    WINDOW_BIND = "window_bind"                    # 绑定窗口
    WINDOW_UNBIND = "window_unbind"                # 解绑窗口
    WINDOW_FIND = "window_find"                    # 查找窗口
    WINDOW_ENUM = "window_enum"                    # 枚举窗口
    WINDOW_INFO = "window_info"                    # 窗口信息
    WINDOW_RESIZE = "window_resize"                # 调整窗口大小


# 防止Nuitka编译时优化掉未直接使用的枚举值
_ALL_CAPABILITIES = [
    PluginCapability.IMAGE_FIND_PIC,
    PluginCapability.IMAGE_FIND_COLOR,
    PluginCapability.IMAGE_FIND_MULTI_COLOR,
    PluginCapability.IMAGE_CAPTURE,
    PluginCapability.IMAGE_GET_COLOR,
    PluginCapability.MOUSE_MOVE,
    PluginCapability.MOUSE_CLICK,
    PluginCapability.MOUSE_DOWN,
    PluginCapability.MOUSE_UP,
    PluginCapability.MOUSE_DRAG,
    PluginCapability.MOUSE_SCROLL,
    PluginCapability.KEYBOARD_PRESS,
    PluginCapability.KEYBOARD_DOWN,
    PluginCapability.KEYBOARD_UP,
    PluginCapability.KEYBOARD_INPUT_TEXT,
    PluginCapability.KEYBOARD_COMBINATION,
    PluginCapability.OCR_TEXT,
    PluginCapability.OCR_FIND_TEXT,
    PluginCapability.WINDOW_BIND,
    PluginCapability.WINDOW_UNBIND,
    PluginCapability.WINDOW_FIND,
    PluginCapability.WINDOW_ENUM,
    PluginCapability.WINDOW_INFO,
    PluginCapability.WINDOW_RESIZE,
]


class IPluginAdapter(ABC):
    """插件适配器基础接口"""

    @abstractmethod
    def get_name(self) -> str:
        """获取插件名称"""
        pass

    @abstractmethod
    def get_version(self) -> str:
        """获取插件版本"""
        pass

    @abstractmethod
    def get_capabilities(self) -> List[PluginCapability]:
        """获取插件支持的能力列表"""
        pass

    @abstractmethod
    def initialize(self, config: dict) -> bool:
        """
        初始化插件

        Args:
            config: 插件配置字典

        Returns:
            bool: 初始化是否成功
        """
        pass

    @abstractmethod
    def release(self) -> bool:
        """释放插件资源"""
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """健康检查"""
        pass

    @abstractmethod
    def execute(self, capability: PluginCapability, method: str, *args, **kwargs) -> Any:
        """
        执行插件操作（通用接口）

        Args:
            capability: 插件能力
            method: 方法名
            *args, **kwargs: 方法参数

        Returns:
            Any: 执行结果
        """
        pass


class IImagePlugin(IPluginAdapter):
    """图像识别插件接口"""

    @abstractmethod
    def bind_window(self, hwnd: int, display_mode: str = "normal",
                    mouse_mode: str = "normal", keypad_mode: str = "normal",
                    mode: int = 0) -> bool:
        """
        绑定窗口

        Args:
            hwnd: 窗口句柄
            display_mode: 显示模式 (normal/gdi/gdi2/gdi3/gdi4/gdi5/dxgi/vnc/dx等)
            mouse_mode: 鼠标模式 (normal/windows/windows3/vnc/dx.mouse.*等)
            keypad_mode: 键盘模式 (normal/windows/vnc/dx.keypad.*等)
            mode: 绑定模式 (0=推荐, 1=远程线程注入, 2=驱动注入模式1, 3=驱动注入模式2, 4=驱动注入模式3)

        Returns:
            bool: 绑定是否成功
        """
        pass

    @abstractmethod
    def unbind_window(self) -> bool:
        """解绑窗口"""
        pass

    @abstractmethod
    def find_pic(self, x1: int, y1: int, x2: int, y2: int,
                 pic_name: str, similarity: float = 0.9) -> Optional[Tuple[int, int]]:
        """
        查找图片

        Args:
            x1, y1: 左上角坐标
            x2, y2: 右下角坐标
            pic_name: 图片路径或名称
            similarity: 相似度 (0.0-1.0)

        Returns:
            Optional[Tuple[int, int]]: 找到返回(x, y)坐标，未找到返回None
        """
        pass

    @abstractmethod
    def find_pic_ex(self, x1: int, y1: int, x2: int, y2: int,
                    pic_name: str, similarity: float = 0.9) -> List[Tuple[int, int]]:
        """
        查找多个图片

        Returns:
            List[Tuple[int, int]]: 所有找到的坐标列表
        """
        pass

    @abstractmethod
    def find_color(self, x1: int, y1: int, x2: int, y2: int,
                   color: str, similarity: float = 1.0) -> Optional[Tuple[int, int]]:
        """
        查找颜色

        Args:
            color: 颜色值，格式如 "FFFFFF" 或 "FF0000"
            similarity: 相似度

        Returns:
            Optional[Tuple[int, int]]: 找到返回(x, y)，未找到返回None
        """
        pass

    @abstractmethod
    def get_color(self, x: int, y: int) -> str:
        """
        获取指定坐标的颜色值

        Returns:
            str: 颜色值，格式如 "FFFFFF"
        """
        pass

    @abstractmethod
    def capture(self, x1: int, y1: int, x2: int, y2: int) -> Any:
        """
        截取屏幕区域

        Returns:
            Any: 图像对象（具体类型由插件决定）
        """
        pass


class IInputPlugin(IPluginAdapter):
    """输入插件接口（鼠标+键盘）"""

    @abstractmethod
    def bind_window(self, hwnd: int) -> bool:
        """绑定窗口"""
        pass

    @abstractmethod
    def mouse_move(self, x: int, y: int) -> bool:
        """移动鼠标"""
        pass

    @abstractmethod
    def mouse_click(self, x: int, y: int, button: str = "left") -> bool:
        """
        鼠标点击

        Args:
            button: "left" / "right" / "middle"
        """
        pass

    @abstractmethod
    def mouse_double_click(self, x: int, y: int, button: str = "left") -> bool:
        """鼠标双击"""
        pass

    @abstractmethod
    def mouse_drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: float = 1.0,
        button: str = "left",
        hwnd: int = None,
    ) -> bool:
        """鼠标拖拽"""
        pass

    @abstractmethod
    def mouse_scroll(self, x: int, y: int, delta: int) -> bool:
        """
        鼠标滚轮

        Args:
            delta: 滚动量（正数向上，负数向下）
        """
        pass

    @abstractmethod
    def key_press(self, vk_code: int) -> bool:
        """按下按键"""
        pass

    @abstractmethod
    def key_down(self, vk_code: int) -> bool:
        """按键按下（不释放）"""
        pass

    @abstractmethod
    def key_up(self, vk_code: int) -> bool:
        """按键释放"""
        pass

    @abstractmethod
    def key_input_text(self, text: str) -> bool:
        """输入文字"""
        pass


class IOCRPlugin(IPluginAdapter):
    """OCR识别插件接口"""

    @abstractmethod
    def ocr(self, x1: int, y1: int, x2: int, y2: int) -> str:
        """
        识别区域中的文字

        Returns:
            str: 识别的文字内容
        """
        pass

    @abstractmethod
    def find_text(self, x1: int, y1: int, x2: int, y2: int,
                  text: str) -> Optional[Tuple[int, int]]:
        """
        查找文字位置

        Returns:
            Optional[Tuple[int, int]]: 找到返回坐标，未找到返回None
        """
        pass
