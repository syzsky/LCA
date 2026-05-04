"""
OLA 插件 COM 加载器

使用 DLL 的 InitCom 函数初始化 COM 注册，然后使用 comtypes 创建 COM 对象
参考文档：创建OLA-COM对象.html
"""

import os
import sys
import json
import logging
import importlib.util
import re
from ctypes import WinDLL, c_int, WINFUNCTYPE, c_int32, c_int64, cast, c_void_p
from pathlib import Path
from typing import Optional, Union, Tuple, List, Callable

import comtypes.client

logger = logging.getLogger(__name__)

# 定义回调函数类型（与 OLAPlugDLLHelper 一致）
HotkeyCallback = WINFUNCTYPE(c_int32, c_int32, c_int32)
MouseCallback = WINFUNCTYPE(None, c_int32, c_int32, c_int32, c_int32)
MouseWheelCallback = WINFUNCTYPE(None, c_int32, c_int32, c_int32, c_int32)
MouseMoveCallback = WINFUNCTYPE(None, c_int32, c_int32)
MouseDragCallback = WINFUNCTYPE(None, c_int32, c_int32)


class OLAPlugCOMLoader:
    """OLA 插件 COM 加载器

    工作原理：
    1. 加载 DLL
    2. 调用 DLL 的 InitCom 函数初始化 COM 注册
    3. 使用 comtypes.client.CreateObject 创建 COM 对象
    """

    DLL_NAME = "OLAPlug_x64.dll"
    PROGID = "OlaPlug.OlaSoft"  # COM ProgID
    REQUIRED_COMTYPES_GEN_MODULES = (
        "_00020430_0000_0000_C000_000000000046_0_2_0",
        "OLAPlugLib",
    )

    def __init__(self):
        self._com_object = None
        self._dll = None
        self._dll_path = self._get_dll_path()
        self._com_initialized = False

    def _get_dll_path(self) -> str:
        """获取 DLL 路径"""
        if getattr(sys, 'frozen', False):
            # Nuitka 打包环境
            exe_path = os.path.abspath(sys.executable)
            # 转换短路径为完整路径
            try:
                exe_path = os.path.realpath(exe_path)
            except Exception:
                pass
            dll_dir = os.path.join(os.path.dirname(exe_path), 'OLA')
        else:
            # 开发环境
            dll_dir = os.path.dirname(os.path.abspath(__file__))

        dll_path = os.path.join(dll_dir, self.DLL_NAME)

        if not os.path.exists(dll_path):
            raise FileNotFoundError(f"找不到 OLA DLL 文件: {dll_path}")

        logger.info(f"OLA DLL 路径: {dll_path}")
        return dll_path

    def _init_com(self) -> bool:
        """调用 DLL 的 InitCom 函数初始化 COM 注册

        Returns:
            初始化是否成功
        """
        if self._com_initialized:
            return True

        try:
            # 确保 DLL 目录在 PATH 中
            dll_dir = os.path.dirname(self._dll_path)
            if dll_dir not in os.environ.get('PATH', ''):
                os.environ['PATH'] = dll_dir + os.pathsep + os.environ.get('PATH', '')

            # 加载 DLL
            if self._dll is None:
                self._dll = WinDLL(self._dll_path)
                logger.info(f"DLL 加载成功: {self._dll_path}")

            # 调用 InitCom 初始化 COM 注册
            self._dll.InitCom.argtypes = []
            self._dll.InitCom.restype = c_int
            result = self._dll.InitCom()

            logger.info(f"InitCom 返回值: {result}")
            self._com_initialized = True
            return True

        except Exception as e:
            logger.error(f"InitCom 初始化失败: {e}")
            return False

    def _resolve_packaged_comtypes_gen_dir(self) -> str:
        """解析打包运行目录中的 comtypes.gen 目录。"""
        candidate_roots = []

        exe_path = os.path.abspath(sys.executable)
        try:
            exe_path = os.path.realpath(exe_path)
        except Exception:
            pass
        candidate_roots.append(os.path.dirname(exe_path))

        dll_root = os.path.dirname(os.path.dirname(os.path.abspath(self._dll_path)))
        candidate_roots.append(dll_root)

        visited = set()
        for root in candidate_roots:
            if not root:
                continue
            normalized = os.path.normcase(os.path.normpath(root))
            if normalized in visited:
                continue
            visited.add(normalized)

            gen_dir = os.path.join(root, "comtypes", "gen")
            if os.path.isdir(gen_dir):
                return gen_dir

        return ""

    def _preload_required_comtypes_modules(self) -> None:
        """
        打包环境下强制预加载 OLA 依赖的 comtypes.gen 模块。
        避免运行时进入 comtypes 动态生成链路导致启动不稳定。
        """
        import comtypes
        import comtypes.gen

        gen_dir = self._resolve_packaged_comtypes_gen_dir()
        if not gen_dir:
            return

        package_paths = list(getattr(comtypes.gen, "__path__", []))
        if gen_dir not in package_paths:
            comtypes.gen.__path__ = package_paths + [gen_dir]

        try:
            comtypes.client.gen_dir = gen_dir
        except Exception:
            pass

        for module_name in self._resolve_required_comtypes_gen_modules(gen_dir):
            full_name = f"comtypes.gen.{module_name}"
            if full_name in sys.modules:
                module_obj = sys.modules.get(full_name)
                if module_obj is not None:
                    setattr(comtypes.gen, module_name, module_obj)
                continue

            module_file = os.path.join(gen_dir, f"{module_name}.py")
            if not os.path.isfile(module_file):
                raise FileNotFoundError(f"缺少预生成 comtypes 文件: {module_file}")

            spec = importlib.util.spec_from_file_location(full_name, module_file)
            if spec is None or spec.loader is None:
                raise ImportError(f"无法创建模块加载器: {full_name}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[full_name] = module
            try:
                spec.loader.exec_module(module)
                setattr(comtypes.gen, module_name, module)
            except Exception:
                sys.modules.pop(full_name, None)
                raise

    def _extract_wrapper_module_names(self, module_file: str) -> List[str]:
        if not os.path.isfile(module_file):
            return []
        try:
            content = Path(module_file).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []

        module_names: List[str] = []
        for match in re.findall(r"comtypes\.gen\.([A-Za-z0-9_]+)", content, flags=re.IGNORECASE):
            normalized = str(match or "").strip()
            if not normalized or normalized == "OLAPlugLib":
                continue
            if normalized not in module_names:
                module_names.append(normalized)
        return module_names

    def _resolve_required_comtypes_gen_modules(self, gen_dir: str) -> List[str]:
        module_names: List[str] = []
        for base_name in self.REQUIRED_COMTYPES_GEN_MODULES:
            if base_name == "OLAPlugLib":
                wrapper_names = self._extract_wrapper_module_names(
                    os.path.join(gen_dir, "OLAPlugLib.py")
                )
                for wrapper_name in wrapper_names:
                    if wrapper_name not in module_names:
                        module_names.append(wrapper_name)
            if base_name not in module_names:
                module_names.append(base_name)
        return module_names

    def load_com_object(self):
        """加载 COM 对象

        步骤：
        1. 调用 InitCom 初始化 COM 注册
        2. 使用 CreateObject 创建 COM 对象
        """
        # 先初始化 COM
        if not self._init_com():
            raise RuntimeError("COM 初始化失败")

        self._preload_required_comtypes_modules()

        try:
            logger.info(f"尝试创建 COM 对象: {self.PROGID}")

            # 创建 COM 对象
            self._com_object = comtypes.client.CreateObject(self.PROGID)

            logger.info("COM 对象创建成功")
            return self._com_object

        except Exception as e:
            logger.error(f"创建 COM 对象失败: {e}")
            logger.info("尝试使用替代的 ProgID...")

            # 尝试其他可能的 ProgID（根据文档）
            alternative_progids = [
                "ola.olasoft",
                "ola",
                "olaplug",
                "ola.ola"
            ]

            for progid in alternative_progids:
                try:
                    logger.info(f"尝试 ProgID: {progid}")
                    self._com_object = comtypes.client.CreateObject(progid)
                    logger.info(f"使用 {progid} 创建成功")
                    return self._com_object
                except Exception as e2:
                    logger.debug(f"ProgID {progid} 失败: {e2}")

            raise RuntimeError("所有 COM 创建方法都失败")

    def get_com_object(self):
        """获取 COM 对象"""
        if self._com_object is None:
            self.load_com_object()
        return self._com_object

    def release(self):
        """释放 COM 对象"""
        if self._com_object:
            try:
                # 释放 COM 对象
                self._com_object = None
                logger.info("COM 对象已释放")
            except Exception as e:
                logger.error(f"释放 COM 对象失败: {e}")


# 全局实例
_loader_instance: Optional[OLAPlugCOMLoader] = None


def get_ola_com_loader() -> OLAPlugCOMLoader:
    """获取全局 OLA COM 加载器实例"""
    global _loader_instance
    if _loader_instance is None:
        _loader_instance = OLAPlugCOMLoader()
    return _loader_instance


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.DEBUG)

    loader = get_ola_com_loader()
    try:
        com_obj = loader.load_com_object()
        print(f"COM 对象创建成功: {com_obj}")
    except Exception as e:
        print(f"测试失败: {e}")
    finally:
        loader.release()


class OLAPlugServerCOM:
    """
    OLA 插件 COM 封装类

    与 OLAPlugServer 主流程接口兼容，但使用 COM 方式加载 DLL
    已实现方法可直接替代；未显式封装的方法将自动透传到底层 COM 对象

    回调支持说明：
    - COM 接口的回调参数类型是 c_longlong（函数地址），不是直接的函数指针
    - 本类会自动将 Python 回调函数转换为 ctypes 函数指针，并传递其地址给 COM
    - 回调函数引用会被保存以防止垃圾回收
    """

    # 回调函数存储（防止被垃圾回收）
    _callbacks = {}

    def __init__(self):
        self._loader = get_ola_com_loader()
        self._com = None
        self.OLAObject = None  # 兼容 OLAPlugServer
        try:
            from plugins.adapters.ola.runtime_config import get_ola_registration_info
            self.UserCode, self.SoftCode, self.FeatureList = get_ola_registration_info()
        except Exception:
            self.UserCode = ""
            self.SoftCode = ""
            self.FeatureList = ""

    def __getattr__(self, name: str):
        """透传未封装的方法到 COM 对象，提升与 DLL 版接口兼容性。"""
        if name.startswith("_"):
            raise AttributeError(name)

        try:
            target = getattr(self._get_or_create_com_object(), name)
        except Exception as e:
            raise AttributeError(f"COM 对象未初始化，无法访问方法: {name}, {e}") from e
        if not callable(target):
            return target

        def _proxy(*args, **kwargs):
            try:
                return target(*args, **kwargs)
            except Exception as e:
                logger.error(f"{name} 失败: {e}")
                raise

        return _proxy

    def CreateCOLAPlugInterFace(self) -> int:
        """创建 OLA COM 对象"""
        try:
            if self._com is None:
                self._com = self._loader.load_com_object()
                logger.info("OLA COM 对象创建成功")
            self.OLAObject = 1  # 兼容性：设置为非零值
            self.SetConfigByKey("DefaultEncoding", "1")
            return 1
        except Exception as e:
            logger.error(f"OLA COM 对象创建失败: {e}")
            return 0

    def DestroyCOLAPlugInterFace(self) -> int:
        """释放 COM 对象"""
        try:
            self._loader.release()
            self._com = None
            self.OLAObject = None
            return 1
        except Exception as e:
            logger.error(f"释放 COM 对象失败: {e}")
            return 0

    # ========== 基础方法 ==========

    def Ver(self) -> str:
        """获取版本号"""
        try:
            return self._get_or_create_com_object().Ver()
        except Exception as e:
            logger.error(f"Ver 失败: {e}")
            return ""

    def SetPath(self, path: str) -> int:
        """设置工作路径"""
        try:
            return self._get_or_create_com_object().SetPath(path)
        except Exception as e:
            logger.error(f"SetPath 失败: {e}", exc_info=True)
            return 0

    def GetPath(self) -> str:
        """获取工作路径"""
        try:
            return self._get_or_create_com_object().GetPath()
        except Exception as e:
            logger.error(f"GetPath 失败: {e}")
            return ""

    def GetMachineCode(self) -> str:
        """获取机器码"""
        try:
            return self._get_or_create_com_object().GetMachineCode()
        except Exception as e:
            logger.error(f"GetMachineCode 失败: {e}")
            return ""

    def GetBasePath(self) -> str:
        """获取基础路径"""
        try:
            return self._get_or_create_com_object().GetBasePath()
        except Exception as e:
            logger.error(f"GetBasePath 失败: {e}")
            return ""

    def _get_or_create_com_object(self):
        if self._com is None:
            self._com = self._loader.load_com_object()
        return self._com

    def Reg(self, userCode: str, softCode: str, featureList: str) -> int:
        """注册"""
        try:
            return self._get_or_create_com_object().Reg(userCode, softCode, featureList)
        except Exception as e:
            logger.error(f"Reg 失败: {e}")
            return 0

    def Login(self, userCode: str, softCode: str, featureList: str, softVersion: str, dealerCode: str) -> str:
        """登录。"""
        try:
            return str(
                self._get_or_create_com_object().Login(
                    userCode,
                    softCode,
                    featureList,
                    softVersion,
                    dealerCode,
                ) or ""
            )
        except Exception as e:
            logger.error(f"Login 失败: {e}")
            return ""

    def Activate(self, userCode: str, softCode: str, softVersion: str, dealerCode: str, licenseKey: str) -> str:
        """激活。"""
        try:
            return str(
                self._get_or_create_com_object().Activate(
                    userCode,
                    softCode,
                    softVersion,
                    dealerCode,
                    licenseKey,
                ) or ""
            )
        except Exception as e:
            logger.error(f"Activate 失败: {e}")
            return ""

    # ========== 配置方法 ==========

    def SetConfig(self, configStr: Union[str, dict]) -> int:
        """设置配置"""
        try:
            if not isinstance(configStr, str):
                configStr = json.dumps(configStr)
            return self._get_or_create_com_object().SetConfig(configStr)
        except Exception as e:
            logger.error(f"SetConfig 失败: {e}")
            return 0

    def SetConfigByKey(self, key: str, value: str) -> int:
        """按键设置配置"""
        try:
            logger.info(f"[SetConfigByKey] key='{key}', value='{value}'")
            result = self._get_or_create_com_object().SetConfigByKey(key, value)
            logger.info(f"[SetConfigByKey] 返回: {result}")
            return result
        except Exception as e:
            logger.error(f"SetConfigByKey 失败: {e}")
            return 0

    def SetOcrConfig(self, configStr: Union[str, dict]) -> int:
        """设置 OCR 配置"""
        try:
            if not isinstance(configStr, str):
                configStr = json.dumps(configStr)
            return self._get_or_create_com_object().SetOcrConfig(configStr)
        except Exception as e:
            logger.error(f"SetOcrConfig 失败: {e}")
            return 0

    def GetLastError(self) -> int:
        """获取最后一次错误ID。"""
        try:
            return int(self._get_or_create_com_object().GetLastError())
        except Exception as e:
            logger.error(f"GetLastError 失败: {e}")
            return 0

    def GetLastErrorString(self) -> str:
        """获取最后一次错误字符串。"""
        try:
            return str(self._get_or_create_com_object().GetLastErrorString() or "")
        except Exception as e:
            logger.error(f"GetLastErrorString 失败: {e}")
            return ""

    # ========== 窗口操作 ==========

    def BindWindow(self, hwnd: int, display: str, mouse: str, keypad: str, mode: int) -> int:
        """绑定窗口"""
        try:
            return self._com.BindWindow(hwnd, display, mouse, keypad, mode)
        except Exception as e:
            logger.error(f"BindWindow 失败: {e}")
            return 0

    def BindWindowEx(self, hwnd: int, display: str, mouse: str, keypad: str, pubstr: str, mode: int) -> int:
        """绑定窗口（扩展）"""
        try:
            return self._com.BindWindowEx(hwnd, display, mouse, keypad, pubstr, mode)
        except Exception as e:
            logger.error(f"BindWindowEx 失败: {e}")
            return 0

    def UnBindWindow(self) -> int:
        """解绑窗口"""
        try:
            return self._com.UnBindWindow()
        except Exception as e:
            logger.error(f"UnBindWindow 失败: {e}")
            return 0

    def GetBindWindow(self) -> int:
        """获取绑定的窗口"""
        try:
            return self._com.GetBindWindow()
        except Exception as e:
            logger.error(f"GetBindWindow 失败: {e}")
            return 0

    def FindWindow(self, class_name: str, title: str) -> int:
        """查找窗口"""
        try:
            return self._com.FindWindow(class_name, title)
        except Exception as e:
            logger.error(f"FindWindow 失败: {e}")
            return 0

    def EnumWindow(self, parent: int, title: str, class_name: str, filter_flags: int) -> str:
        """枚举窗口"""
        try:
            return self._com.EnumWindow(parent, title, class_name, filter_flags)
        except Exception as e:
            logger.error(f"EnumWindow 失败: {e}")
            return ""

    def GetWindowTitle(self, hwnd: int) -> str:
        """获取窗口标题"""
        try:
            return self._com.GetWindowTitle(hwnd)
        except Exception as e:
            logger.error(f"GetWindowTitle 失败: {e}")
            return ""

    def SetWindowState(self, hwnd: int, state: int) -> int:
        """设置窗口状态"""
        try:
            return self._com.SetWindowState(hwnd, state)
        except Exception as e:
            logger.error(f"SetWindowState 失败: {e}")
            return 0

    def SetWindowSize(self, hwnd: int, width: int, height: int) -> int:
        """Set whole window size."""
        try:
            return self._com.SetWindowSize(hwnd, width, height)
        except Exception as e:
            logger.error(f"设置窗口大小失败：{e}")
            return 0

    def SetClientSize(self, hwnd: int, width: int, height: int) -> int:
        """Set client area size."""
        try:
            return self._com.SetClientSize(hwnd, width, height)
        except Exception as e:
            logger.error(f"设置客户区大小失败：{e}")
            return 0

    def GetClientSize(self, hwnd: int, width: int = None, height: int = None):
        """获取窗口客户区大小"""
        try:
            result = self._com.GetClientSize(hwnd)
            if isinstance(result, (list, tuple)):
                if len(result) >= 3:
                    # COM返回格式: (width, height, result_code)
                    w, h, ret = result[0], result[1], result[2]
                    return ret, w, h
                else:
                    logger.warning(f"GetClientSize 返回数据不足: {result}")
                    return 0, 0, 0
            else:
                logger.warning(f"GetClientSize 返回格式异常: {result}, type={type(result)}")
                return 0, 0, 0
        except Exception as e:
            logger.error(f"GetClientSize 失败: {e}")
            return 0, 0, 0

    # ========== 鼠标操作 ==========

    def MoveTo(self, x: int, y: int) -> int:
        """移动鼠标（带轨迹）"""
        try:
            return self._com.MoveTo(x, y)
        except Exception as e:
            logger.error(f"MoveTo 失败: {e}")
            return 0

    def MoveToWithoutSimulator(self, x: int, y: int) -> int:
        """移动鼠标（直接移动）"""
        try:
            return self._com.MoveToWithoutSimulator(x, y)
        except Exception as e:
            logger.error(f"MoveToWithoutSimulator 失败: {e}")
            return 0

    def LeftClick(self) -> int:
        """左键点击"""
        try:
            return self._com.LeftClick()
        except Exception as e:
            logger.error(f"LeftClick 失败: {e}")
            return 0

    def RightClick(self) -> int:
        """右键点击"""
        try:
            return self._com.RightClick()
        except Exception as e:
            logger.error(f"RightClick 失败: {e}")
            return 0

    def MiddleClick(self) -> int:
        """中键点击"""
        try:
            return self._com.MiddleClick()
        except Exception as e:
            logger.error(f"MiddleClick 失败: {e}")
            return 0

    def LeftDoubleClick(self) -> int:
        """左键双击"""
        try:
            return self._com.LeftDoubleClick()
        except Exception as e:
            logger.error(f"LeftDoubleClick 失败: {e}")
            return 0

    def LeftDown(self) -> int:
        """左键按下"""
        try:
            return self._com.LeftDown()
        except Exception as e:
            logger.error(f"LeftDown 失败: {e}")
            return 0

    def LeftUp(self) -> int:
        """左键释放"""
        try:
            return self._com.LeftUp()
        except Exception as e:
            logger.error(f"LeftUp 失败: {e}")
            return 0

    def RightDown(self) -> int:
        """右键按下"""
        try:
            return self._com.RightDown()
        except Exception as e:
            logger.error(f"RightDown 失败: {e}")
            return 0

    def RightUp(self) -> int:
        """右键释放"""
        try:
            return self._com.RightUp()
        except Exception as e:
            logger.error(f"RightUp 失败: {e}")
            return 0

    def MiddleDown(self) -> int:
        """中键按下"""
        try:
            return self._com.MiddleDown()
        except Exception as e:
            logger.error(f"MiddleDown 失败: {e}")
            return 0

    def MiddleUp(self) -> int:
        """中键释放"""
        try:
            return self._com.MiddleUp()
        except Exception as e:
            logger.error(f"MiddleUp 失败: {e}")
            return 0

    def WheelUp(self) -> int:
        """滚轮向上"""
        try:
            return self._com.WheelUp()
        except Exception as e:
            logger.error(f"WheelUp 失败: {e}")
            return 0

    def WheelDown(self) -> int:
        """滚轮向下"""
        try:
            return self._com.WheelDown()
        except Exception as e:
            logger.error(f"WheelDown 失败: {e}")
            return 0

    # ========== 键盘操作 ==========

    def KeyPress(self, vk_code: int) -> int:
        """按键"""
        try:
            return self._com.KeyPress(vk_code)
        except Exception as e:
            logger.error(f"KeyPress 失败: {e}")
            return 0

    def KeyDown(self, vk_code: int) -> int:
        """按键按下"""
        try:
            return self._com.KeyDown(vk_code)
        except Exception as e:
            logger.error(f"KeyDown 失败: {e}")
            return 0

    def KeyUp(self, vk_code: int) -> int:
        """按键释放"""
        try:
            return self._com.KeyUp(vk_code)
        except Exception as e:
            logger.error(f"KeyUp 失败: {e}")
            return 0

    def SendString(self, hwnd: int, text: str) -> int:
        """发送字符串"""
        try:
            return self._com.SendString(hwnd, text)
        except Exception as e:
            logger.error(f"SendString 失败: {e}")
            return 0

    # ========== 图像操作 ==========

    def Capture(self, x1: int, y1: int, x2: int, y2: int, file_path: str) -> int:
        """截图"""
        try:
            return self._com.Capture(x1, y1, x2, y2, file_path)
        except Exception as e:
            logger.error(f"Capture 失败: {e}")
            return 0

    def GetColor(self, x: int, y: int) -> str:
        """获取颜色"""
        try:
            return self._com.GetColor(x, y)
        except Exception as e:
            logger.error(f"GetColor 失败: {e}")
            return ""

    def FindColor(self, x1: int, y1: int, x2: int, y2: int, color1: str, color2: str, direction: int) -> Tuple[int, int, int]:
        """找色"""
        try:
            # COM 方法返回值处理
            result = self._com.FindColor(x1, y1, x2, y2, color1, color2, direction)
            # 根据返回类型处理
            if isinstance(result, tuple):
                return result
            else:
                # 可能是单个返回值或需要获取输出参数
                return (result, 0, 0)
        except Exception as e:
            logger.error(f"FindColor 失败: {e}")
            return (0, 0, 0)

    def FindMultiColor(self, x1: int, y1: int, x2: int, y2: int, colorJson: str, pointJson: str, *args) -> Tuple[int, int, int]:
        """多点找色

        兼容两种调用方式:
        - 新签名: FindMultiColor(x1, y1, x2, y2, colorJson, pointJson, sim, direction)
        - 旧签名: FindMultiColor(x1, y1, x2, y2, colorJson, pointJson, direction)
        """
        try:
            if len(args) == 1:
                sim = 1.0
                direction = args[0]
            elif len(args) == 2:
                sim, direction = args
            else:
                raise TypeError(
                    "FindMultiColor 参数错误: 期望 7 或 8 个参数 "
                    "(..., colorJson, pointJson, [sim,] direction)"
                )

            result = self._com.FindMultiColor(x1, y1, x2, y2, colorJson, pointJson, sim, direction)
            if isinstance(result, tuple):
                return result
            else:
                return (result, 0, 0)
        except Exception as e:
            logger.error(f"FindMultiColor 失败: {e}")
            return (0, 0, 0)

    def MatchWindowsFromPath(self, x1: int, y1: int, x2: int, y2: int, pic_path: str,
                              similarity: float, match_type: int, angle: float, scale: float) -> dict:
        """找图"""
        try:
            result = self._com.MatchWindowsFromPath(x1, y1, x2, y2, pic_path, similarity, match_type, angle, scale)
            # COM 返回的可能是字符串 JSON 或 dict
            if isinstance(result, str):
                return json.loads(result) if result else {}
            return result if result else {}
        except Exception as e:
            logger.error(f"MatchWindowsFromPath 失败: {e}")
            return {}

    def MatchWindowsFromPathAll(self, x1: int, y1: int, x2: int, y2: int, pic_path: str,
                                  similarity: float, match_type: int, angle: float, scale: float) -> list:
        """找图（所有匹配）"""
        try:
            result = self._com.MatchWindowsFromPathAll(x1, y1, x2, y2, pic_path, similarity, match_type, angle, scale)
            if isinstance(result, str):
                return json.loads(result) if result else []
            return result if result else []
        except Exception as e:
            logger.error(f"MatchWindowsFromPathAll 失败: {e}")
            return []

    # ========== OCR 操作 ==========

    def Ocr(self, x1: int, y1: int, x2: int, y2: int) -> str:
        """OCR 识别"""
        try:
            return self._com.Ocr(x1, y1, x2, y2)
        except Exception as e:
            logger.error(f"Ocr 失败: {e}")
            return ""

    def OcrDetails(self, x1: int, y1: int, x2: int, y2: int) -> Union[dict, str]:
        """OCR 识别（带详细信息）"""
        try:
            result = self._com.OcrDetails(x1, y1, x2, y2)
            if isinstance(result, str):
                return json.loads(result) if result else {}
            return result if result else {}
        except Exception as e:
            logger.error(f"OcrDetails 失败: {e}")
            return {}

    def OcrV5(self, x1: int, y1: int, x2: int, y2: int) -> str:
        """OCR V5 识别"""
        try:
            return self._com.OcrV5(x1, y1, x2, y2)
        except Exception as e:
            logger.error(f"OcrV5 失败: {e}")
            return ""

    def OcrV5Details(self, x1: int, y1: int, x2: int, y2: int) -> Union[dict, str]:
        """OCR V5 识别（带详细信息）"""
        try:
            result = self._com.OcrV5Details(x1, y1, x2, y2)
            if isinstance(result, str):
                return json.loads(result) if result else {}
            return result if result else {}
        except Exception as e:
            logger.error(f"OcrV5Details 失败: {e}")
            return {}

    def FindStr(self, x1: int, y1: int, x2: int, y2: int, text: str, colorJson: str,
                dict_name: str, matchVal: float) -> Tuple[int, int, int]:
        """查找文字"""
        try:
            result = self._com.FindStr(x1, y1, x2, y2, text, colorJson, dict_name, matchVal)
            if isinstance(result, tuple):
                return result
            else:
                return (result, 0, 0)
        except Exception as e:
            logger.error(f"FindStr 失败: {e}")
            return (0, -1, -1)

    # ========== 热键操作 ==========
    # 注意：热键功能通过 COM 可能有限制，部分方法可能不完全支持

    def StartHotkeyHook(self) -> int:
        """启动热键钩子"""
        try:
            return self._com.StartHotkeyHook()
        except Exception as e:
            logger.error(f"StartHotkeyHook 失败: {e}")
            return 0

    def StopHotkeyHook(self) -> int:
        """停止热键钩子"""
        try:
            return self._com.StopHotkeyHook()
        except Exception as e:
            logger.error(f"StopHotkeyHook 失败: {e}")
            return 0

    def RegisterHotkey(self, keycode: int, modifiers: int, callback=None) -> int:
        """注册热键

        COM 接口的 callback 参数是 c_longlong 类型（函数地址）
        需要将 Python 回调函数转换为 ctypes 函数指针，然后获取其地址
        """
        try:
            if callback is None:
                callback_addr = 0
            elif isinstance(callback, int):
                # 已经是地址
                callback_addr = callback
            else:
                # 将 Python 回调包装为 ctypes 函数指针，并统一返回 int
                def _hotkey_wrapper(k, m):
                    try:
                        result = callback(k, m)
                        return int(result) if result is not None else 0
                    except Exception as cb_exc:
                        logger.error(f"RegisterHotkey 回调异常: {cb_exc}")
                        return 0

                wrapped_callback = HotkeyCallback(_hotkey_wrapper)
                # 存储引用防止被垃圾回收
                key = f"RegisterHotkey_{keycode}_{modifiers}"
                self._callbacks[key] = wrapped_callback
                # 获取函数指针地址
                callback_addr = cast(wrapped_callback, c_void_p).value

            return self._com.RegisterHotkey(keycode, modifiers, callback_addr)
        except Exception as e:
            logger.error(f"RegisterHotkey 失败: {e}")
            return 0

    def UnregisterHotkey(self, keycode: int, modifiers: int) -> int:
        """取消注册热键"""
        try:
            return self._com.UnregisterHotkey(keycode, modifiers)
        except Exception as e:
            logger.error(f"UnregisterHotkey 失败: {e}")
            return 0

    def RegisterMouseButton(self, button: int, _type: int, callback=None) -> int:
        """注册鼠标按钮"""
        try:
            if callback is None:
                callback_addr = 0
            elif isinstance(callback, int):
                callback_addr = callback
            else:
                wrapped_callback = MouseCallback(callback)
                key = f"RegisterMouseButton_{button}_{_type}"
                self._callbacks[key] = wrapped_callback
                callback_addr = cast(wrapped_callback, c_void_p).value

            return self._com.RegisterMouseButton(button, _type, callback_addr)
        except Exception as e:
            logger.error(f"RegisterMouseButton 失败: {e}")
            return 0

    def UnregisterMouseButton(self, button: int, _type: int) -> int:
        """取消注册鼠标按钮"""
        try:
            return self._com.UnregisterMouseButton(button, _type)
        except Exception as e:
            logger.error(f"UnregisterMouseButton 失败: {e}")
            return 0

    def RegisterMouseWheel(self, callback=None) -> int:
        """注册鼠标滚轮"""
        try:
            if callback is None:
                callback_addr = 0
            elif isinstance(callback, int):
                callback_addr = callback
            else:
                wrapped_callback = MouseWheelCallback(callback)
                key = "RegisterMouseWheel"
                self._callbacks[key] = wrapped_callback
                callback_addr = cast(wrapped_callback, c_void_p).value

            return self._com.RegisterMouseWheel(callback_addr)
        except Exception as e:
            logger.error(f"RegisterMouseWheel 失败: {e}")
            return 0

    def UnregisterMouseWheel(self) -> int:
        """取消注册鼠标滚轮"""
        try:
            return self._com.UnregisterMouseWheel()
        except Exception as e:
            logger.error(f"UnregisterMouseWheel 失败: {e}")
            return 0

    def RegisterMouseMove(self, callback=None) -> int:
        """注册鼠标移动"""
        try:
            if callback is None:
                callback_addr = 0
            elif isinstance(callback, int):
                callback_addr = callback
            else:
                wrapped_callback = MouseMoveCallback(callback)
                key = "RegisterMouseMove"
                self._callbacks[key] = wrapped_callback
                callback_addr = cast(wrapped_callback, c_void_p).value

            return self._com.RegisterMouseMove(callback_addr)
        except Exception as e:
            logger.error(f"RegisterMouseMove 失败: {e}")
            return 0

    def UnregisterMouseMove(self) -> int:
        """取消注册鼠标移动"""
        try:
            return self._com.UnregisterMouseMove()
        except Exception as e:
            logger.error(f"UnregisterMouseMove 失败: {e}")
            return 0

    def RegisterMouseDrag(self, callback=None) -> int:
        """注册鼠标拖拽"""
        try:
            if callback is None:
                callback_addr = 0
            elif isinstance(callback, int):
                callback_addr = callback
            else:
                wrapped_callback = MouseDragCallback(callback)
                key = "RegisterMouseDrag"
                self._callbacks[key] = wrapped_callback
                callback_addr = cast(wrapped_callback, c_void_p).value

            return self._com.RegisterMouseDrag(callback_addr)
        except Exception as e:
            logger.error(f"RegisterMouseDrag 失败: {e}")
            return 0

    def UnregisterMouseDrag(self) -> int:
        """取消注册鼠标拖拽"""
        try:
            return self._com.UnregisterMouseDrag()
        except Exception as e:
            logger.error(f"UnregisterMouseDrag 失败: {e}")
            return 0

    # ========== 数据库操作 ==========

    def CreateDatabase(self, dbName: str, password: str) -> int:
        """创建数据库"""
        try:
            return self._com.CreateDatabase(dbName, password)
        except Exception as e:
            logger.error(f"CreateDatabase 失败: {e}", exc_info=True)
            return 0

    def OpenDatabase(self, dbName: str, password: str) -> int:
        """打开数据库"""
        try:
            return self._com.OpenDatabase(dbName, password)
        except Exception as e:
            logger.error(f"OpenDatabase 失败: {e}")
            return 0

    def CloseDatabase(self, db: int) -> int:
        """关闭数据库"""
        try:
            return self._com.CloseDatabase(db)
        except Exception as e:
            logger.error(f"CloseDatabase 失败: {e}")
            return 0

    def GetDatabaseError(self, db: int) -> str:
        """获取数据库错误信息"""
        try:
            return self._com.GetDatabaseError(db)
        except Exception as e:
            logger.error(f"GetDatabaseError 失败: {e}")
            return str(e)

    def InitOlaDatabase(self, db: int) -> int:
        """初始化OLA数据库结构"""
        try:
            return self._com.InitOlaDatabase(db)
        except Exception as e:
            logger.error(f"InitOlaDatabase 失败: {e}")
            return 0

    # ========== 字库操作 ==========

    def InitDictFromDir(self, db: int, dict_name: str, dict_path: str, cover: int) -> int:
        """从目录加载字库图片"""
        try:
            return self._com.InitDictFromDir(db, dict_name, dict_path, cover)
        except Exception as e:
            logger.error(f"InitDictFromDir 失败: {e}")
            return 0

    def OcrFromDict(self, x1: int, y1: int, x2: int, y2: int, color: str, dict_name: str, match_val: float) -> str:
        """从字库识别文字"""
        try:
            logger.info(f"[OcrFromDict] 调用参数: x1={x1}, y1={y1}, x2={x2}, y2={y2}, color='{color}', dict_name='{dict_name}', match_val={match_val}")
            result = self._com.OcrFromDict(x1, y1, x2, y2, color, dict_name, match_val)
            logger.info(f"[OcrFromDict] 返回结果: '{result}', type={type(result)}")
            return result
        except Exception as e:
            logger.error(f"OcrFromDict 失败: {e}")
            return ""

    def OcrFromDictDetails(self, x1: int, y1: int, x2: int, y2: int, color: str, dict_name: str, match_val: float) -> str:
        """从字库识别文字（详细信息）"""
        try:
            logger.info(f"[OcrFromDictDetails] 调用: x1={x1}, y1={y1}, x2={x2}, y2={y2}, color='{color}', dict='{dict_name}', match={match_val}")
            result = self._com.OcrFromDictDetails(x1, y1, x2, y2, color, dict_name, match_val)
            logger.info(f"[OcrFromDictDetails] 返回: {repr(result)}")
            return result
        except Exception as e:
            logger.error(f"OcrFromDictDetails 失败: {e}")
            import traceback
            traceback.print_exc()
            return ""

    def ExportDict(self, db: int, dict_name: str, export_path: str) -> int:
        """导出字库数据"""
        try:
            return self._com.ExportDict(db, dict_name, export_path)
        except Exception as e:
            logger.error(f"ExportDict 失败: {e}")
            return 0

    def ImportDictWord(self, db: int, dict_name: str, pic_file_name: str, cover: int = 1) -> int:
        """添加字库数据

        Args:
            db: 数据库对象指针
            dict_name: 字库名称
            pic_file_name: 图片文件路径，文件名（不含扩展名）即为字符内容
            cover: 是否覆盖 1=覆盖 0=不覆盖
        """
        try:
            logger.info(f"[ImportDictWord] db={db}, dict_name='{dict_name}', pic_file_name='{pic_file_name}', cover={cover}")
            result = self._com.ImportDictWord(db, dict_name, pic_file_name, cover)
            logger.info(f"[ImportDictWord] 返回: {result}")
            return result
        except Exception as e:
            logger.error(f"ImportDictWord 失败: {e}")
            import traceback
            traceback.print_exc()
            return 0

    def RemoveDict(self, db: int, dict_name: str) -> int:
        """移除字库"""
        try:
            return self._com.RemoveDict(db, dict_name)
        except Exception as e:
            logger.error(f"RemoveDict 失败: {e}")
            return 0

    def GetDictImage(self, db: int, dict_name: str, word: str, gap: int = 0, _dir: int = 0) -> int:
        """读取字库图片

        Args:
            db: 数据库句柄
            dict_name: 字库名称
            word: 要读取的文字
            gap: 文字间隔，单位为像素
            _dir: 拼接方向，0=水平拼接，1=垂直拼接

        Returns:
            图像对象指针，失败返回0
        """
        try:
            return self._com.GetDictImage(db, dict_name, word, gap, _dir)
        except Exception as e:
            logger.error(f"GetDictImage 失败: {e}")
            return 0

    def RemoveDictWord(self, db: int, dict_name: str, word: str) -> int:
        """移除字库中的单个词条

        Args:
            db: 数据库句柄
            dict_name: 字库名称
            word: 要移除的文字

        Returns:
            成功返回1，失败返回0
        """
        try:
            return self._com.RemoveDictWord(db, dict_name, word)
        except Exception as e:
            logger.error(f"RemoveDictWord 失败: {e}")
            return 0
