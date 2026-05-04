import os
from ctypes import (
    # Windows 特定类型
    wintypes,

    # 核心类型
    WinDLL,           # DLL 加载
    WINFUNCTYPE,      # __stdcall 函数类型
    CFUNCTYPE,        # __cdecl 函数类型
    POINTER,          # 指针类型

    # 基础数值类型
    c_int,            # int
    c_int64,          # __int64 / longlong
    c_char_p,         # char* 字符串指针
    c_wchar_p,        # wchar_t* 宽字符串指针
    c_void_p,         # void* 通用指针

    # 其他常用类型
    c_bool,           # bool
    c_double,         # double
    c_float,          # float
)
from functools import wraps
from typing import Callable, List

from _ctypes import byref

_kernel32 = WinDLL('kernel32', use_last_error=True)

# 设置函数原型
_kernel32.LoadLibraryW.argtypes = [wintypes.LPCWSTR]
_kernel32.LoadLibraryW.restype = wintypes.HMODULE

_kernel32.GetProcAddress.argtypes = [wintypes.HMODULE, wintypes.LPCSTR]
_kernel32.GetProcAddress.restype = c_void_p

_kernel32.FreeLibrary.argtypes = [wintypes.HMODULE]
_kernel32.FreeLibrary.restype = wintypes.BOOL


# ========== 装饰器定义 ==========
def handle_string_params(func):
    """
    装饰器：自动处理字符串参数编码
    将 Python str 类型自动转换为 bytes 用于 c_char_p 参数
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        # 获取参数类型信息
        if not hasattr(func, 'argtypes'):
            return func(*args, **kwargs)

        argtypes = func.argtypes
        output_indices = getattr(func, '_output_indices', [])

        new_args = []
        arg_idx = 0

        for i, argtype in enumerate(argtypes):
            if i in output_indices:
                # 输出参数：直接传递
                if arg_idx < len(args):
                    new_args.append(args[arg_idx])
                    arg_idx += 1
                else:
                    new_args.append(None)
            else:
                # 输入参数：处理字符串编码
                if arg_idx < len(args):
                    arg = args[arg_idx]
                    # c_char_p 类型：字符串需要编码
                    if argtype == c_char_p and isinstance(arg, str):
                        new_args.append(arg.encode('utf-8'))
                    # c_wchar_p 类型：保持 Unicode
                    elif argtype == c_wchar_p and isinstance(arg, bytes):
                        new_args.append(arg.decode('utf-8'))
                    else:
                        new_args.append(arg)
                    arg_idx += 1
                else:
                    new_args.append(None)

        return func(*new_args, **kwargs)
    return wrapper


def handle_output_params(output_indices: List[int]):
    """
    装饰器：处理输出参数
    将指针类型的输出参数自动转换为返回值的一部分

    Args:
        output_indices: 输出参数在参数列表中的索引列表
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args):
            # 获取参数类型
            if not hasattr(func, 'argtypes'):
                return func(*args)

            argtypes = func.argtypes
            prepared_args = []
            output_holders = {}

            arg_idx = 0
            for i, argtype in enumerate(argtypes):
                if i in output_indices:
                    # 输出参数：创建占位对象
                    if issubclass(argtype, POINTER):
                        pointed_type = argtype._type_
                        holder = pointed_type()
                        prepared_args.append(byref(holder))
                        output_holders[i] = holder
                    else:
                        # 非指针输出参数（少见）
                        prepared_args.append(None)
                else:
                    # 输入参数
                    if arg_idx < len(args):
                        prepared_args.append(args[arg_idx])
                        arg_idx += 1
                    else:
                        prepared_args.append(None)

            # 调用原始函数
            result = func(*prepared_args)

            # 收集输出参数的值
            output_values = []
            for i in output_indices:
                if i in output_holders:
                    holder = output_holders[i]
                    if hasattr(holder, 'value'):
                        output_values.append(holder.value)
                    else:
                        output_values.append(holder)

            # 返回结果
            if output_values:
                return (result, *output_values)
            return result

        # 保存输出索引供字符串处理装饰器使用
        wrapper._output_indices = output_indices
        return wrapper
    return decorator


# 接口参数定义
class OLAPlugDLLHelper:

    # 定义DLL
    DLL = "OLAPlug_x64.dll"
    _dll = WinDLL(os.path.join(os.path.dirname(os.path.abspath(__file__)), DLL))
    _dll_base = _dll._handle

    # 回调函数持久化使用
    callbacks = {}

    # 声明回调函数
    DrawGuiButtonCallback = WINFUNCTYPE(None, c_int64)
    DrawGuiMouseCallback = WINFUNCTYPE(None, c_int64, c_int, c_int, c_int)
    DownloadCallback = WINFUNCTYPE(None, c_int64, c_int64, c_int64, c_int64)
    TcpClientCallback = WINFUNCTYPE(None, c_int64, c_int, c_int64, c_int, c_int64)
    TcpServerCallback = WINFUNCTYPE(None, c_int64, c_int64, c_int, c_int64, c_int, c_int64)
    HotkeyCallback = WINFUNCTYPE(c_int, c_int, c_int)
    MouseCallback = WINFUNCTYPE(None, c_int, c_int, c_int, c_int)
    MouseWheelCallback = WINFUNCTYPE(None, c_int, c_int, c_int, c_int)
    MouseMoveCallback = WINFUNCTYPE(None, c_int, c_int)
    MouseDragCallback = WINFUNCTYPE(None, c_int, c_int)

    # 函数签名，格式为 (rva, restype, argtypes)
    function_signatures = {
        "CreateCOLAPlugInterFace": (0x00000000, c_int64, []), 
        "DestroyCOLAPlugInterFace": (0x00000000, c_int, [c_int64]), 
        "Ver": (0x00000000, c_int64, []), 
        "GetPlugInfo": (0x00000000, c_int64, [c_int]), 
        "SetPath": (0x00000000, c_int, [c_int64, c_char_p]), 
        "GetPath": (0x00000000, c_int64, [c_int64]), 
        "GetMachineCode": (0x00000000, c_int64, [c_int64]), 
        "GetBasePath": (0x00000000, c_int64, [c_int64]), 
        "Reg": (0x00000000, c_int, [c_char_p, c_char_p, c_char_p]), 
        "BindWindow": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p, c_char_p, c_int]), 
        "BindWindowEx": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p, c_char_p, c_char_p, c_int]), 
        "UnBindWindow": (0x00000000, c_int, [c_int64]), 
        "GetBindWindow": (0x00000000, c_int64, [c_int64]), 
        "ReleaseWindowsDll": (0x00000000, c_int, [c_int64, c_int64]), 
        "FreeStringPtr": (0x00000000, c_int, [c_int64]), 
        "FreeMemoryPtr": (0x00000000, c_int, [c_int64]), 
        "GetStringSize": (0x00000000, c_int, [c_int64]), 
        "GetStringFromPtr": (0x00000000, c_int, [c_int64, c_char_p, c_int]), 
        "Delay": (0x00000000, c_int, [c_int]), 
        "Delays": (0x00000000, c_int, [c_int, c_int]), 
        "SetUAC": (0x00000000, c_int, [c_int64, c_int]), 
        "CheckUAC": (0x00000000, c_int, [c_int64]), 
        "RunApp": (0x00000000, c_int, [c_int64, c_char_p, c_int]), 
        "ExecuteCmd": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_int]), 
        "GetConfig": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "SetConfig": (0x00000000, c_int, [c_int64, c_char_p]), 
        "SetConfigByKey": (0x00000000, c_int, [c_int64, c_char_p, c_char_p]), 
        "SendDropFiles": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "SetDefaultEncode": (0x00000000, c_int, [c_int, c_int]), 
        "GetLastError": (0x00000000, c_int, []), 
        "GetLastErrorString": (0x00000000, c_int64, []), 
        "HideModule": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "UnhideModule": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetRandomNumber": (0x00000000, c_int, [c_int64, c_int, c_int]), 
        "GetRandomDouble": (0x00000000, c_double, [c_int64, c_double, c_double]), 
        "ExcludePos": (0x00000000, c_int64, [c_int64, c_char_p, c_int, c_int, c_int, c_int, c_int]), 
        "FindNearestPos": (0x00000000, c_int64, [c_int64, c_char_p, c_int, c_int, c_int]), 
        "SortPosDistance": (0x00000000, c_int64, [c_int64, c_char_p, c_int, c_int, c_int]), 
        "GetDenseRect": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int, POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_int)]), 
        "PathPlanning": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int, c_int, c_int, c_double, c_double]), 
        "CreateGraph": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "GetGraph": (0x00000000, c_int64, [c_int64, c_int64]), 
        "AddEdge": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p, c_double, c_bool]), 
        "GetShortestPath": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p]), 
        "GetShortestDistance": (0x00000000, c_double, [c_int64, c_int64, c_char_p, c_char_p]), 
        "ClearGraph": (0x00000000, c_int, [c_int64, c_int64]), 
        "DeleteGraph": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetNodeCount": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetEdgeCount": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetShortestPathToAllNodes": (0x00000000, c_int64, [c_int64, c_int64, c_char_p]), 
        "GetMinimumSpanningTree": (0x00000000, c_int64, [c_int64, c_int64]), 
        "GetDirectedPathToAllNodes": (0x00000000, c_int64, [c_int64, c_int64, c_char_p]), 
        "GetMinimumArborescence": (0x00000000, c_int64, [c_int64, c_int64, c_char_p]), 
        "CreateGraphFromCoordinates": (0x00000000, c_int64, [c_int64, c_char_p, c_bool, c_double, c_bool]), 
        "AddCoordinateNode": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_double, c_double, c_bool, c_double, c_bool]), 
        "GetNodeCoordinates": (0x00000000, c_int64, [c_int64, c_int64, c_char_p]), 
        "SetNodeConnection": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p, c_bool, c_double]), 
        "GetNodeConnectionStatus": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p]), 
        "AsmCall": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_int, c_int64]), 
        "Assemble": (0x00000000, c_int64, [c_int64, c_char_p, c_int64, c_int, c_int]), 
        "Disassemble": (0x00000000, c_int64, [c_int64, c_char_p, c_int64, c_int, c_int, c_int]), 
        "Login": (0x00000000, c_int64, [c_char_p, c_char_p, c_char_p, c_char_p, c_char_p]), 
        "Activate": (0x00000000, c_int64, [c_char_p, c_char_p, c_char_p, c_char_p, c_char_p]), 
        "DmaAddDevice": (0x00000000, c_int64, [c_int64, c_int]), 
        "DmaAddDeviceEx": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "DmaRemoveDevice": (0x00000000, c_int, [c_int64, c_int64]), 
        "DmaGetPidFromName": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "DmaGetPidList": (0x00000000, c_int64, [c_int64, c_int64]), 
        "DmaGetProcessInfo": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "DmaGetModuleBase": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p]), 
        "DmaGetModuleSize": (0x00000000, c_int, [c_int64, c_int64, c_int, c_char_p]), 
        "DmaGetProcAddress": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p, c_char_p]), 
        "DmaScatterCreate": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "DmaScatterPrepare": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_int]), 
        "DmaScatterExecute": (0x00000000, c_int, [c_int64, c_int64]), 
        "DmaScatterRead": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_int64, c_int]), 
        "DmaScatterClear": (0x00000000, c_int, [c_int64, c_int64]), 
        "DmaScatterClose": (0x00000000, c_int, [c_int64, c_int64]), 
        "DmaFindData": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p, c_char_p]), 
        "DmaFindDataEx": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p, c_char_p, c_int, c_int, c_int]), 
        "DmaFindDouble": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p, c_double, c_double]), 
        "DmaFindDoubleEx": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p, c_double, c_double, c_int, c_int, c_int]), 
        "DmaFindFloat": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p, c_float, c_float]), 
        "DmaFindFloatEx": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p, c_float, c_float, c_int, c_int, c_int]), 
        "DmaFindInt": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p, c_int64, c_int64, c_int]), 
        "DmaFindIntEx": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p, c_int64, c_int64, c_int, c_int, c_int, c_int]), 
        "DmaFindString": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p, c_char_p, c_int]), 
        "DmaFindStringEx": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p, c_char_p, c_int, c_int, c_int, c_int]), 
        "DmaReadData": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p, c_int]), 
        "DmaReadDataAddr": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int64, c_int]), 
        "DmaReadDataAddrToBin": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int64, c_int]), 
        "DmaReadDataToBin": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p, c_int]), 
        "DmaReadDouble": (0x00000000, c_double, [c_int64, c_int64, c_int, c_char_p]), 
        "DmaReadDoubleAddr": (0x00000000, c_double, [c_int64, c_int64, c_int, c_int64]), 
        "DmaReadFloat": (0x00000000, c_float, [c_int64, c_int64, c_int, c_char_p]), 
        "DmaReadFloatAddr": (0x00000000, c_float, [c_int64, c_int64, c_int, c_int64]), 
        "DmaReadInt": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p, c_int]), 
        "DmaReadIntAddr": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int64, c_int]), 
        "DmaReadString": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p, c_int, c_int]), 
        "DmaReadStringAddr": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int64, c_int, c_int]), 
        "DmaWriteData": (0x00000000, c_int, [c_int64, c_int64, c_int, c_char_p, c_char_p]), 
        "DmaWriteDataFromBin": (0x00000000, c_int, [c_int64, c_int64, c_int, c_char_p, c_int64, c_int]), 
        "DmaWriteDataAddr": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int64, c_char_p]), 
        "DmaWriteDataAddrFromBin": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int64, c_int64, c_int]), 
        "DmaWriteDouble": (0x00000000, c_int, [c_int64, c_int64, c_int, c_char_p, c_double]), 
        "DmaWriteDoubleAddr": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int64, c_double]), 
        "DmaWriteFloat": (0x00000000, c_int, [c_int64, c_int64, c_int, c_char_p, c_float]), 
        "DmaWriteFloatAddr": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int64, c_float]), 
        "DmaWriteInt": (0x00000000, c_int, [c_int64, c_int64, c_int, c_char_p, c_int, c_int64]), 
        "DmaWriteIntAddr": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int64, c_int, c_int64]), 
        "DmaWriteString": (0x00000000, c_int, [c_int64, c_int64, c_int, c_char_p, c_int, c_char_p]), 
        "DmaWriteStringAddr": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int64, c_int, c_char_p]), 
        "DrawGuiCleanup": (0x00000000, c_int, [c_int64]), 
        "DrawGuiSetGuiActive": (0x00000000, c_int, [c_int64, c_int]), 
        "DrawGuiIsGuiActive": (0x00000000, c_int, [c_int64]), 
        "DrawGuiSetGuiClickThrough": (0x00000000, c_int, [c_int64, c_int]), 
        "DrawGuiIsGuiClickThrough": (0x00000000, c_int, [c_int64]), 
        "DrawGuiRectangle": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_int, c_double]), 
        "DrawGuiCircle": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_double]), 
        "DrawGuiLine": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_double]), 
        "DrawGuiText": (0x00000000, c_int64, [c_int64, c_char_p, c_int, c_int, c_char_p, c_int, c_int]), 
        "DrawGuiImage": (0x00000000, c_int64, [c_int64, c_char_p, c_int, c_int]), 
        "DrawGuiImagePtr": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int]), 
        "DrawGuiWindow": (0x00000000, c_int64, [c_int64, c_char_p, c_int, c_int, c_int, c_int, c_int]), 
        "DrawGuiPanel": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int, c_int, c_int]), 
        "DrawGuiButton": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_int, c_int, c_int, c_int]), 
        "DrawGuiSetPosition": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int]), 
        "DrawGuiSetSize": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int]), 
        "DrawGuiSetColor": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int, c_int, c_int]), 
        "DrawGuiSetAlpha": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "DrawGuiSetDrawMode": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "DrawGuiSetLineThickness": (0x00000000, c_int, [c_int64, c_int64, c_double]), 
        "DrawGuiSetFont": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_int]), 
        "DrawGuiSetTextAlign": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "DrawGuiSetText": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "DrawGuiSetWindowTitle": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "DrawGuiSetWindowStyle": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "DrawGuiSetWindowTopMost": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "DrawGuiSetWindowTransparency": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "DrawGuiDeleteObject": (0x00000000, c_int, [c_int64, c_int64]), 
        "DrawGuiClearAll": (0x00000000, c_int, [c_int64]), 
        "DrawGuiSetVisible": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "DrawGuiSetZOrder": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "DrawGuiSetParent": (0x00000000, c_int, [c_int64, c_int64, c_int64]), 
        "DrawGuiSetButtonCallback": (0x00000000, c_int, [c_int64, c_int64, DrawGuiButtonCallback]), 
        "DrawGuiSetMouseCallback": (0x00000000, c_int, [c_int64, c_int64, DrawGuiMouseCallback]), 
        "DrawGuiGetDrawObjectType": (0x00000000, c_int, [c_int64, c_int64]), 
        "DrawGuiGetPosition": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int), POINTER(c_int)]), 
        "DrawGuiGetSize": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int), POINTER(c_int)]), 
        "DrawGuiIsPointInObject": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int]), 
        "SetMemoryMode": (0x00000000, c_int, [c_int64, c_int]), 
        "ExportDriver": (0x00000000, c_int, [c_int64, c_char_p, c_int]), 
        "LoadDriver": (0x00000000, c_int, [c_int64, c_char_p, c_char_p]), 
        "UnloadDriver": (0x00000000, c_int, [c_int64, c_char_p]), 
        "DriverTest": (0x00000000, c_int, [c_int64]), 
        "LoadPdb": (0x00000000, c_int, [c_int64]), 
        "GetPdbDownloadUrls": (0x00000000, c_int64, [c_int64]), 
        "HideProcess": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "ProtectProcess": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "ProtectProcess2": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "AddProtectPID": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_int64]), 
        "RemoveProtectPID": (0x00000000, c_int, [c_int64, c_int64]), 
        "AddAllowPID": (0x00000000, c_int, [c_int64, c_int64]), 
        "RemoveAllowPID": (0x00000000, c_int, [c_int64, c_int64]), 
        "FakeProcess": (0x00000000, c_int, [c_int64, c_int64, c_int64]), 
        "ProtectWindow": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "KeOpenProcess": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int64)]), 
        "KeOpenThread": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int64)]), 
        "StartSecurityGuard": (0x00000000, c_int, [c_int64]), 
        "ProtectFileTestDriver": (0x00000000, c_int, [c_int64]), 
        "ProtectFileEnableDriver": (0x00000000, c_int, [c_int64]), 
        "ProtectFileDisableDriver": (0x00000000, c_int, [c_int64]), 
        "ProtectFileStartFilter": (0x00000000, c_int, [c_int64]), 
        "ProtectFileStopFilter": (0x00000000, c_int, [c_int64]), 
        "ProtectFileAddProtectedPath": (0x00000000, c_int, [c_int64, c_char_p, c_int, c_int]), 
        "ProtectFileRemoveProtectedPath": (0x00000000, c_int, [c_int64, c_char_p]), 
        "ProtectFileClearProtectedPaths": (0x00000000, c_int, [c_int64]), 
        "ProtectFileQueryProtectedPath": (0x00000000, c_int, [c_int64, c_char_p, POINTER(c_int)]), 
        "ProtectFileAddWhitelist": (0x00000000, c_int, [c_int64, c_int64]), 
        "ProtectFileRemoveWhitelist": (0x00000000, c_int, [c_int64, c_int64]), 
        "ProtectFileClearWhitelist": (0x00000000, c_int, [c_int64]), 
        "ProtectFileQueryWhitelist": (0x00000000, c_int, [c_int64, c_int64]), 
        "ProtectFileAddBlacklist": (0x00000000, c_int, [c_int64, c_int64]), 
        "ProtectFileRemoveBlacklist": (0x00000000, c_int, [c_int64, c_int64]), 
        "ProtectFileClearBlacklist": (0x00000000, c_int, [c_int64]), 
        "ProtectFileQueryBlacklist": (0x00000000, c_int, [c_int64, c_int64]), 
        "EnabletVtDriver": (0x00000000, c_int, [c_int64, c_int]), 
        "VtFakeWriteData": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p]), 
        "VtFakeWriteDataFromBin": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_int64, c_int]), 
        "VtFakeWriteDataAddr": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_char_p]), 
        "VtFakeWriteDataAddrFromBin": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_int64, c_int]), 
        "VtUnFakeMemoryAddr": (0x00000000, c_int, [c_int64, c_int64, c_int64]), 
        "VtUnFakeMemory": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "VipProtectEnableDriver": (0x00000000, c_int, [c_int64]), 
        "VipProtectDisableDriver": (0x00000000, c_int, [c_int64]), 
        "VipProtectAddProtect": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_int, c_int]), 
        "VipProtectRemoveProtect": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "VipProtectClearAll": (0x00000000, c_int, [c_int64]), 
        "VipProtectAddWhitelist": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "VipProtectRemoveWhitelist": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "VipProtectClearWhitelist": (0x00000000, c_int, [c_int64]), 
        "VipProtectAddBlacklist": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "VipProtectRemoveBlacklist": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "VipProtectClearBlacklist": (0x00000000, c_int, [c_int64]), 
        "GenerateRSAKey": (0x00000000, c_int, [c_int64, c_char_p, c_char_p, c_int, c_int]), 
        "ConvertRSAPublicKey": (0x00000000, c_int64, [c_int64, c_char_p, c_int, c_int]), 
        "ConvertRSAPrivateKey": (0x00000000, c_int64, [c_int64, c_char_p, c_int, c_int]), 
        "EncryptWithRsa": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_int]), 
        "DecryptWithRsa": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_int]), 
        "SignWithRsa": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_int, c_int]), 
        "VerifySignWithRsa": (0x00000000, c_int, [c_int64, c_char_p, c_char_p, c_int, c_int, c_char_p]), 
        "AESEncrypt": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p]), 
        "AESDecrypt": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p]), 
        "AESEncryptEx": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_char_p, c_int, c_int]), 
        "AESDecryptEx": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_char_p, c_int, c_int]), 
        "MD5Encrypt": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "SHAHash": (0x00000000, c_int64, [c_int64, c_char_p, c_int]), 
        "HMAC": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_int]), 
        "GenerateRandomBytes": (0x00000000, c_int64, [c_int64, c_int, c_int]), 
        "GenerateGuid": (0x00000000, c_int64, [c_int64, c_int]), 
        "Base64Encode": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "Base64Decode": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "PBKDF2": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_int, c_int, c_int]), 
        "MD5File": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "SHAFile": (0x00000000, c_int64, [c_int64, c_char_p, c_int]), 
        "CreateFolder": (0x00000000, c_int, [c_int64, c_char_p]), 
        "DeleteFolder": (0x00000000, c_int, [c_int64, c_char_p]), 
        "GetFolderList": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p]), 
        "IsDirectory": (0x00000000, c_int, [c_int64, c_char_p]), 
        "IsFile": (0x00000000, c_int, [c_int64, c_char_p]), 
        "CreateFile": (0x00000000, c_int, [c_int64, c_char_p]), 
        "DeleteFile": (0x00000000, c_int, [c_int64, c_char_p]), 
        "CopyFile": (0x00000000, c_int, [c_int64, c_char_p, c_char_p]), 
        "MoveFile": (0x00000000, c_int, [c_int64, c_char_p, c_char_p]), 
        "RenameFile": (0x00000000, c_int, [c_int64, c_char_p, c_char_p]), 
        "GetFileSize": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "GetFileList": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p]), 
        "GetFileName": (0x00000000, c_int64, [c_int64, c_char_p, c_int]), 
        "ToAbsolutePath": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "ToRelativePath": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "FileOrDirectoryExists": (0x00000000, c_int, [c_int64, c_char_p]), 
        "ReadFileString": (0x00000000, c_int64, [c_int64, c_char_p, c_int]), 
        "ReadBytesFromFile": (0x00000000, c_int64, [c_int64, c_char_p, c_int, c_int64]), 
        "WriteBytesToFile": (0x00000000, c_int, [c_int64, c_char_p, c_int64, c_int]), 
        "WriteStringToFile": (0x00000000, c_int, [c_int64, c_char_p, c_char_p, c_int]), 
        "StartHotkeyHook": (0x00000000, c_int, [c_int64]), 
        "StopHotkeyHook": (0x00000000, c_int, [c_int64]), 
        "RegisterHotkey": (0x00000000, c_int, [c_int64, c_int, c_int, HotkeyCallback]), 
        "UnregisterHotkey": (0x00000000, c_int, [c_int64, c_int, c_int]), 
        "RegisterMouseButton": (0x00000000, c_int, [c_int64, c_int, c_int, MouseCallback]), 
        "UnregisterMouseButton": (0x00000000, c_int, [c_int64, c_int, c_int]), 
        "RegisterMouseWheel": (0x00000000, c_int, [c_int64, MouseWheelCallback]), 
        "UnregisterMouseWheel": (0x00000000, c_int, [c_int64]), 
        "RegisterMouseMove": (0x00000000, c_int, [c_int64, MouseMoveCallback]), 
        "UnregisterMouseMove": (0x00000000, c_int, [c_int64]), 
        "RegisterMouseDrag": (0x00000000, c_int, [c_int64, MouseDragCallback]), 
        "UnregisterMouseDrag": (0x00000000, c_int, [c_int64]), 
        "Inject": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_int, c_int]), 
        "InjectFromUrl": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_int, c_int]), 
        "InjectFromBuffer": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_int, c_int, c_int]), 
        "JsonCreateObject": (0x00000000, c_int64, []), 
        "JsonCreateArray": (0x00000000, c_int64, []), 
        "JsonParse": (0x00000000, c_int64, [c_char_p, POINTER(c_int)]), 
        "JsonStringify": (0x00000000, c_int64, [c_int64, c_int, POINTER(c_int)]), 
        "JsonFree": (0x00000000, c_int, [c_int64]), 
        "JsonGetValue": (0x00000000, c_int64, [c_int64, c_char_p, POINTER(c_int)]), 
        "JsonGetArrayItem": (0x00000000, c_int64, [c_int64, c_int, POINTER(c_int)]), 
        "JsonGetString": (0x00000000, c_int64, [c_int64, c_char_p, POINTER(c_int)]), 
        "JsonGetNumber": (0x00000000, c_double, [c_int64, c_char_p, POINTER(c_int)]), 
        "JsonGetBool": (0x00000000, c_int, [c_int64, c_char_p, POINTER(c_int)]), 
        "JsonGetSize": (0x00000000, c_int, [c_int64, POINTER(c_int)]), 
        "JsonSetValue": (0x00000000, c_int, [c_int64, c_char_p, c_int64]), 
        "JsonArrayAppend": (0x00000000, c_int, [c_int64, c_int64]), 
        "JsonSetString": (0x00000000, c_int, [c_int64, c_char_p, c_char_p]), 
        "JsonSetNumber": (0x00000000, c_int, [c_int64, c_char_p, c_double]), 
        "JsonSetBool": (0x00000000, c_int, [c_int64, c_char_p, c_int]), 
        "JsonDeleteKey": (0x00000000, c_int, [c_int64, c_char_p]), 
        "JsonClear": (0x00000000, c_int, [c_int64]), 
        "ParseMatchImageJson": (0x00000000, c_int, [c_char_p, POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_double), POINTER(c_double), POINTER(c_int)]), 
        "GetMatchImageAllCount": (0x00000000, c_int, [c_char_p]), 
        "ParseMatchImageAllJson": (0x00000000, c_int, [c_char_p, c_int, POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_double), POINTER(c_double), POINTER(c_int)]), 
        "GetResultCount": (0x00000000, c_int, [c_char_p]), 
        "GenerateMouseTrajectory": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int]), 
        "KeyDown": (0x00000000, c_int, [c_int64, c_int]), 
        "KeyUp": (0x00000000, c_int, [c_int64, c_int]), 
        "KeyPress": (0x00000000, c_int, [c_int64, c_int]), 
        "LeftDown": (0x00000000, c_int, [c_int64]), 
        "LeftUp": (0x00000000, c_int, [c_int64]), 
        "MoveTo": (0x00000000, c_int, [c_int64, c_int, c_int]), 
        "MoveToWithoutSimulator": (0x00000000, c_int, [c_int64, c_int, c_int]), 
        "RightClick": (0x00000000, c_int, [c_int64]), 
        "RightDoubleClick": (0x00000000, c_int, [c_int64]), 
        "RightDown": (0x00000000, c_int, [c_int64]), 
        "RightUp": (0x00000000, c_int, [c_int64]), 
        "GetCursorShape": (0x00000000, c_int64, [c_int64]), 
        "GetCursorImage": (0x00000000, c_int64, [c_int64]), 
        "KeyPressStr": (0x00000000, c_int, [c_int64, c_char_p, c_int]), 
        "SendString": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "SendStringEx": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_int, c_int]), 
        "KeyPressChar": (0x00000000, c_int, [c_int64, c_char_p]), 
        "KeyDownChar": (0x00000000, c_int, [c_int64, c_char_p]), 
        "KeyUpChar": (0x00000000, c_int, [c_int64, c_char_p]), 
        "MoveR": (0x00000000, c_int, [c_int64, c_int, c_int]), 
        "MiddleClick": (0x00000000, c_int, [c_int64]), 
        "MoveToEx": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int]), 
        "GetCursorPos": (0x00000000, c_int, [c_int64, POINTER(c_int), POINTER(c_int)]), 
        "MiddleUp": (0x00000000, c_int, [c_int64]), 
        "MiddleDown": (0x00000000, c_int, [c_int64]), 
        "MiddleDoubleClick": (0x00000000, c_int, [c_int64]), 
        "LeftClick": (0x00000000, c_int, [c_int64]), 
        "LeftDoubleClick": (0x00000000, c_int, [c_int64]), 
        "WheelUp": (0x00000000, c_int, [c_int64]), 
        "WheelDown": (0x00000000, c_int, [c_int64]), 
        "WaitKey": (0x00000000, c_int, [c_int64, c_int, c_int]), 
        "EnableMouseAccuracy": (0x00000000, c_int, [c_int64, c_int]), 
        "GenerateInvoluteMouseTrajectory": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_double, c_double]), 
        "LogShutdown": (0x00000000, c_int, [c_int64, c_int64]), 
        "LogSetFilePath": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "LogSetPattern": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "LogSetMaxFileSize": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "LogSetMaxFiles": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "LogSetLevel": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "LogGetLevel": (0x00000000, c_int, [c_int64, c_int64]), 
        "LogSetTarget": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "LogSetAsync": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "LogSetColorMode": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "LogSetLevelColor": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int]), 
        "LogResetLevelColors": (0x00000000, c_int, [c_int64, c_int64]), 
        "LogSetFlushInterval": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "LogTrace": (0x00000000, c_int, [c_int64, c_char_p]), 
        "LogDebug": (0x00000000, c_int, [c_int64, c_char_p]), 
        "LogInfo": (0x00000000, c_int, [c_int64, c_char_p]), 
        "LogWarn": (0x00000000, c_int, [c_int64, c_char_p]), 
        "LogError": (0x00000000, c_int, [c_int64, c_char_p]), 
        "LogCritical": (0x00000000, c_int, [c_int64, c_char_p]), 
        "LogFlush": (0x00000000, c_int, [c_int64, c_int64]), 
        "LogCreateInstance": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "LogDestroyInstance": (0x00000000, c_int, [c_int64, c_int64]), 
        "LogSetBaseDirectory": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "LogSetDirMode": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "LogSetModuleName": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "LogSetFileNamePattern": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "LogSetRotationMode": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "LogSetAppendMode": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "LogTraceEx": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "LogDebugEx": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "LogInfoEx": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "LogWarnEx": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "LogErrorEx": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "LogCriticalEx": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "LogRotateFile": (0x00000000, c_int, [c_int64, c_int64]), 
        "LogCleanupOldFiles": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "LogGetCurrentFilePath": (0x00000000, c_int64, [c_int64, c_int64]), 
        "LogGetCurrentFileSize": (0x00000000, c_int64, [c_int64, c_int64]), 
        "LogGetTotalFilesCount": (0x00000000, c_int, [c_int64, c_int64]), 
        "DoubleToData": (0x00000000, c_int64, [c_int64, c_double]), 
        "FloatToData": (0x00000000, c_int64, [c_int64, c_float]), 
        "StringToData": (0x00000000, c_int64, [c_int64, c_char_p, c_int]), 
        "Int64ToInt32": (0x00000000, c_int, [c_int64, c_int64]), 
        "Int32ToInt64": (0x00000000, c_int64, [c_int64, c_int]), 
        "FindData": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p]), 
        "FindDataEx": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p, c_int, c_int, c_int]), 
        "FindDouble": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_double, c_double]), 
        "FindDoubleEx": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_double, c_double, c_int, c_int, c_int]), 
        "FindFloat": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_float, c_float]), 
        "FindFloatEx": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_float, c_float, c_int, c_int, c_int]), 
        "FindInt": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_int64, c_int64, c_int]), 
        "FindIntEx": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_int64, c_int64, c_int, c_int, c_int, c_int]), 
        "FindString": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p, c_int]), 
        "FindStringEx": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p, c_int, c_int, c_int, c_int]), 
        "ReadData": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_int]), 
        "ReadDataAddr": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_int]), 
        "ReadDataAddrToBin": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_int]), 
        "ReadDataToBin": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_int]), 
        "ReadDouble": (0x00000000, c_double, [c_int64, c_int64, c_char_p]), 
        "ReadDoubleAddr": (0x00000000, c_double, [c_int64, c_int64, c_int64]), 
        "ReadFloat": (0x00000000, c_float, [c_int64, c_int64, c_char_p]), 
        "ReadFloatAddr": (0x00000000, c_float, [c_int64, c_int64, c_int64]), 
        "ReadInt": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_int]), 
        "ReadIntAddr": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_int]), 
        "ReadString": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_int, c_int]), 
        "ReadStringAddr": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_int, c_int]), 
        "WriteData": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p]), 
        "WriteDataFromBin": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_int64, c_int]), 
        "WriteDataAddr": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_char_p]), 
        "WriteDataAddrFromBin": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_int64, c_int]), 
        "WriteDouble": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_double]), 
        "WriteDoubleAddr": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_double]), 
        "WriteFloat": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_float]), 
        "WriteFloatAddr": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_float]), 
        "WriteInt": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_int, c_int64]), 
        "WriteIntAddr": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_int, c_int64]), 
        "WriteString": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_int, c_char_p]), 
        "WriteStringAddr": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_int, c_char_p]), 
        "SetMemoryHwndAsProcessId": (0x00000000, c_int, [c_int64, c_int]), 
        "FreeProcessMemory": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetModuleBaseAddr": (0x00000000, c_int64, [c_int64, c_int64, c_char_p]), 
        "GetModuleSize": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "GetRemoteApiAddress": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p]), 
        "VirtualAllocEx": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_int, c_int]), 
        "VirtualFreeEx": (0x00000000, c_int, [c_int64, c_int64, c_int64]), 
        "VirtualProtectEx": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_int, c_int, POINTER(c_int)]), 
        "VirtualQueryEx": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_int64]), 
        "CreateRemoteThread": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_int64, c_int, POINTER(c_int64)]), 
        "CloseHandle": (0x00000000, c_int, [c_int64, c_int64]), 
        "HookRemoteApi": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_int64, c_int64]), 
        "UnhookRemoteApi": (0x00000000, c_int, [c_int64, c_int64, c_int64]), 
        "HttpDownloadFile": (0x00000000, c_int, [c_int64, c_char_p, c_char_p, MouseDragCallback, c_int64]), 
        "HttpDownloadFileEx": (0x00000000, c_int, [c_int64, c_char_p, c_char_p, MouseDragCallback, c_int64, c_int, c_int, c_int]), 
        "HttpGet": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "HttpPost": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_char_p]), 
        "HttpRequestEx": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_char_p, c_char_p, c_char_p, POINTER(c_int)]), 
        "TcpClientCreate": (0x00000000, c_int64, [c_int64, TcpClientCallback, c_int64, c_int]), 
        "TcpClientConnect": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_int]), 
        "TcpClientSend": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_int]), 
        "TcpClientDisconnect": (0x00000000, c_int, [c_int64, c_int64]), 
        "TcpClientDestroy": (0x00000000, c_int, [c_int64, c_int64]), 
        "TcpServerCreate": (0x00000000, c_int64, [c_int64, c_char_p, c_int, TcpServerCallback, c_int64, c_int]), 
        "TcpServerSend": (0x00000000, c_int, [c_int64, c_int64, c_int64, c_int64, c_int]), 
        "TcpServerDisconnect": (0x00000000, c_int, [c_int64, c_int64, c_int64]), 
        "TcpServerStop": (0x00000000, c_int, [c_int64, c_int64]), 
        "TcpServerGetClientAddress": (0x00000000, c_int64, [c_int64, c_int64, c_int64]), 
        "TcpServerGetAllConnectionIds": (0x00000000, c_int64, [c_int64, c_int64]), 
        "TcpServerDestroy": (0x00000000, c_int, [c_int64, c_int64]), 
        "Ocr": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int]), 
        "OcrFromPtr": (0x00000000, c_int64, [c_int64, c_int64]), 
        "OcrFromBmpData": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "OcrDetails": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int]), 
        "OcrFromPtrDetails": (0x00000000, c_int64, [c_int64, c_int64]), 
        "OcrFromBmpDataDetails": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "OcrV5": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int]), 
        "OcrV5Details": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int]), 
        "OcrV5FromPtr": (0x00000000, c_int64, [c_int64, c_int64]), 
        "OcrV5FromPtrDetails": (0x00000000, c_int64, [c_int64, c_int64]), 
        "GetOcrConfig": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "SetOcrConfig": (0x00000000, c_int, [c_int64, c_char_p]), 
        "SetOcrConfigByKey": (0x00000000, c_int, [c_int64, c_char_p, c_char_p]), 
        "OcrFromDict": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_char_p, c_double]), 
        "OcrFromDictDetails": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_char_p, c_double]), 
        "OcrFromDictPtr": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p, c_double]), 
        "OcrFromDictPtrDetails": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p, c_double]), 
        "FindStr": (0x00000000, c_int, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_char_p, c_char_p, c_double, POINTER(c_int), POINTER(c_int)]), 
        "FindStrDetail": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_char_p, c_char_p, c_double]), 
        "FindStrAll": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_char_p, c_char_p, c_double]), 
        "FindStrFromPtr": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p, c_char_p, c_double]), 
        "FindStrFromPtrAll": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p, c_char_p, c_double]), 
        "FastNumberOcrFromPtr": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p, c_double]), 
        "FastNumberOcr": (0x00000000, c_int, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_char_p, c_double]), 
        "ImportTxtDict": (0x00000000, c_int, [c_int64, c_char_p, c_char_p]), 
        "ExportTxtDict": (0x00000000, c_int, [c_int64, c_char_p, c_char_p]), 
        "Capture": (0x00000000, c_int, [c_int64, c_int, c_int, c_int, c_int, c_char_p]), 
        "GetScreenDataBmp": (0x00000000, c_int, [c_int64, c_int, c_int, c_int, c_int, POINTER(c_int64), POINTER(c_int)]), 
        "GetScreenData": (0x00000000, c_int, [c_int64, c_int, c_int, c_int, c_int, POINTER(c_int64), POINTER(c_int), POINTER(c_int)]), 
        "GetScreenDataPtr": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int]), 
        "CaptureGif": (0x00000000, c_int, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_int, c_int]), 
        "LockDisplay": (0x00000000, c_int, [c_int64, c_int]), 
        "SetSnapCacheTime": (0x00000000, c_int, [c_int64, c_int]), 
        "GetImageData": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int64), POINTER(c_int), POINTER(c_int)]), 
        "MatchImageFromPath": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_double, c_int, c_double, c_double]), 
        "MatchImageFromPathAll": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_double, c_int, c_double, c_double]), 
        "MatchImagePtrFromPath": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_double, c_int, c_double, c_double]), 
        "MatchImagePtrFromPathAll": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_double, c_int, c_double, c_double]), 
        "GetColor": (0x00000000, c_int64, [c_int64, c_int, c_int]), 
        "GetColorPtr": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int]), 
        "CopyImage": (0x00000000, c_int64, [c_int64, c_int64]), 
        "FreeImagePath": (0x00000000, c_int, [c_int64, c_char_p]), 
        "FreeImageAll": (0x00000000, c_int, [c_int64]), 
        "LoadImage": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "LoadImageFromBmpData": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "LoadImageFromRGBData": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int64, c_int]), 
        "FreeImagePtr": (0x00000000, c_int, [c_int64, c_int64]), 
        "MatchWindowsFromPtr": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_int64, c_double, c_int, c_double, c_double]), 
        "MatchImageFromPtr": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_double, c_int, c_double, c_double]), 
        "MatchImageFromPtrAll": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_double, c_int, c_double, c_double]), 
        "MatchWindowsFromPtrAll": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_int64, c_double, c_int, c_double, c_double]), 
        "MatchWindowsFromPath": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_double, c_int, c_double, c_double]), 
        "MatchWindowsFromPathAll": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_double, c_int, c_double, c_double]), 
        "MatchWindowsThresholdFromPtr": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_int64, c_double, c_double, c_double]), 
        "MatchWindowsThresholdFromPtrAll": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_int64, c_double, c_double, c_double]), 
        "MatchWindowsThresholdFromPath": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_char_p, c_double, c_double, c_double]), 
        "MatchWindowsThresholdFromPathAll": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_char_p, c_double, c_double, c_double]), 
        "ShowMatchWindow": (0x00000000, c_int, [c_int64, c_int]), 
        "CalculateSSIM": (0x00000000, c_double, [c_int64, c_int64, c_int64]), 
        "CalculateHistograms": (0x00000000, c_double, [c_int64, c_int64, c_int64]), 
        "CalculateMSE": (0x00000000, c_double, [c_int64, c_int64, c_int64]), 
        "SaveImageFromPtr": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "ReSize": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int]), 
        "FindColor": (0x00000000, c_int, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_char_p, c_int, POINTER(c_int), POINTER(c_int)]), 
        "FindColorList": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_char_p]), 
        "FindColorEx": (0x00000000, c_int, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_int, POINTER(c_int), POINTER(c_int)]), 
        "FindColorListEx": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p]), 
        "CmpMultiColor": (0x00000000, c_int, [c_int64, c_char_p, c_double]), 
        "CmpMultiColorPtr": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_double]), 
        "FindMultiColor": (0x00000000, c_int, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_char_p, c_double, c_int, POINTER(c_int), POINTER(c_int)]), 
        "FindMultiColorList": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_char_p, c_double]), 
        "FindMultiColorFromPtr": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p, c_double, c_int, POINTER(c_int), POINTER(c_int)]), 
        "FindMultiColorListFromPtr": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p, c_double]), 
        "GetImageSize": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int), POINTER(c_int)]), 
        "FindColorBlock": (0x00000000, c_int, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_int, c_int, c_int, POINTER(c_int), POINTER(c_int)]), 
        "FindColorBlockPtr": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_int, c_int, c_int, POINTER(c_int), POINTER(c_int)]), 
        "FindColorBlockList": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_int, c_int, c_int, c_int]), 
        "FindColorBlockListPtr": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_int, c_int, c_int, c_int]), 
        "FindColorBlockEx": (0x00000000, c_int, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_int, c_int, c_int, c_int, POINTER(c_int), POINTER(c_int)]), 
        "FindColorBlockPtrEx": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_int, c_int, c_int, c_int, POINTER(c_int), POINTER(c_int)]), 
        "FindColorBlockListEx": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_int, c_int, c_int, c_int, c_int]), 
        "FindColorBlockListPtrEx": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_int, c_int, c_int, c_int, c_int]), 
        "GetColorNum": (0x00000000, c_int, [c_int64, c_int, c_int, c_int, c_int, c_char_p]), 
        "GetColorNumPtr": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "Cropped": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int, c_int, c_int]), 
        "GetThresholdImageFromMultiColorPtr": (0x00000000, c_int64, [c_int64, c_int64, c_char_p]), 
        "GetThresholdImageFromMultiColor": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p]), 
        "IsSameImage": (0x00000000, c_int, [c_int64, c_int64, c_int64]), 
        "ShowImage": (0x00000000, c_int, [c_int64, c_int64]), 
        "ShowImageFromFile": (0x00000000, c_int, [c_int64, c_char_p]), 
        "SetColorsToNewColor": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p]), 
        "RemoveOtherColors": (0x00000000, c_int64, [c_int64, c_int64, c_char_p]), 
        "DrawRectangle": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int, c_int, c_int, c_int, c_char_p]), 
        "DrawCircle": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int, c_int, c_int, c_char_p]), 
        "DrawFillPoly": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p]), 
        "DecodeQRCode": (0x00000000, c_int64, [c_int64, c_int64]), 
        "CreateQRCode": (0x00000000, c_int64, [c_int64, c_char_p, c_int]), 
        "CreateQRCodeEx": (0x00000000, c_int64, [c_int64, c_char_p, c_int, c_int, c_int, c_int, c_int]), 
        "MatchAnimationFromPtr": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_int64, c_double, c_int, c_double, c_double, c_int, c_int, c_int]), 
        "MatchAnimationFromPath": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_double, c_int, c_double, c_double, c_int, c_int, c_int]), 
        "RemoveImageDiff": (0x00000000, c_int64, [c_int64, c_int64, c_int64]), 
        "GetImageBmpData": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int64), POINTER(c_int)]), 
        "GetImagePngData": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int64), POINTER(c_int)]), 
        "FreeImageData": (0x00000000, c_int, [c_int64, c_int64]), 
        "ScalePixels": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "CreateImage": (0x00000000, c_int64, [c_int64, c_int, c_int, c_char_p]), 
        "SetPixel": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int, c_char_p]), 
        "SetPixelList": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p]), 
        "ConcatImage": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_int, c_char_p, c_int]), 
        "CoverImage": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_int, c_int, c_double]), 
        "RotateImage": (0x00000000, c_int64, [c_int64, c_int64, c_double]), 
        "ImageToBase64": (0x00000000, c_int64, [c_int64, c_int64]), 
        "Base64ToImage": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "Hex2ARGB": (0x00000000, c_int, [c_int64, c_char_p, POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_int)]), 
        "Hex2RGB": (0x00000000, c_int, [c_int64, c_char_p, POINTER(c_int), POINTER(c_int), POINTER(c_int)]), 
        "ARGB2Hex": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int]), 
        "RGB2Hex": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int]), 
        "Hex2HSV": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "RGB2HSV": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int]), 
        "CmpColor": (0x00000000, c_int, [c_int64, c_int, c_int, c_char_p, c_char_p]), 
        "CmpColorPtr": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int, c_char_p, c_char_p]), 
        "CmpColorEx": (0x00000000, c_int, [c_int64, c_int, c_int, c_char_p]), 
        "CmpColorPtrEx": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int, c_char_p]), 
        "CmpColorHexEx": (0x00000000, c_int, [c_int64, c_char_p, c_char_p]), 
        "CmpColorHex": (0x00000000, c_int, [c_int64, c_char_p, c_char_p, c_char_p]), 
        "GetConnectedComponents": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_int]), 
        "DetectPointerDirection": (0x00000000, c_double, [c_int64, c_int64, c_int, c_int]), 
        "DetectPointerDirectionByFeatures": (0x00000000, c_double, [c_int64, c_int64, c_int64, c_int, c_int, c_bool]), 
        "FastMatch": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_double, c_int, c_double, c_double]), 
        "FastROI": (0x00000000, c_int64, [c_int64, c_int64]), 
        "GetROIRegion": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_int)]), 
        "GetForegroundPoints": (0x00000000, c_int64, [c_int64, c_int64]), 
        "ConvertColor": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "Threshold": (0x00000000, c_int64, [c_int64, c_int64, c_double, c_double, c_int]), 
        "RemoveIslands": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "MorphGradient": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "MorphTophat": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "MorphBlackhat": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "Dilation": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "Erosion": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "GaussianBlur": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "Sharpen": (0x00000000, c_int64, [c_int64, c_int64]), 
        "CannyEdge": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "Flip": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "MorphOpen": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "MorphClose": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "Skeletonize": (0x00000000, c_int64, [c_int64, c_int64]), 
        "ImageStitchFromPath": (0x00000000, c_int64, [c_int64, c_char_p, POINTER(c_int64)]), 
        "ImageStitchCreate": (0x00000000, c_int64, [c_int64]), 
        "ImageStitchAppend": (0x00000000, c_int, [c_int64, c_int64, c_int64]), 
        "ImageStitchGetResult": (0x00000000, c_int64, [c_int64, c_int64, POINTER(c_int64)]), 
        "ImageStitchFree": (0x00000000, c_int, [c_int64, c_int64]), 
        "BitPacking": (0x00000000, c_int64, [c_int64, c_int64]), 
        "BitUnpacking": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "SetImageCache": (0x00000000, c_int, [c_int]), 
        "FindImageFromPtr": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_char_p, c_double, c_int]), 
        "FindImageFromPtrAll": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_char_p, c_double]), 
        "FindImageFromPath": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_char_p, c_double, c_int]), 
        "FindImageFromPathAll": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_char_p, c_double]), 
        "FindWindowsFromPtr": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_int64, c_char_p, c_double, c_int]), 
        "FindWindowsFromPtrAll": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_int64, c_char_p, c_double]), 
        "FindWindowsFromPath": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_char_p, c_double, c_int]), 
        "FindWindowsFromPathAll": (0x00000000, c_int64, [c_int64, c_int, c_int, c_int, c_int, c_char_p, c_char_p, c_double]), 
        "RegistryOpenKey": (0x00000000, c_int64, [c_int64, c_int, c_char_p]), 
        "RegistryCreateKey": (0x00000000, c_int64, [c_int64, c_int, c_char_p]), 
        "RegistryCloseKey": (0x00000000, c_int, [c_int64, c_int64]), 
        "RegistryKeyExists": (0x00000000, c_int, [c_int64, c_int, c_char_p]), 
        "RegistryDeleteKey": (0x00000000, c_int, [c_int64, c_int, c_char_p, c_int]), 
        "RegistrySetString": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p]), 
        "RegistryGetString": (0x00000000, c_int64, [c_int64, c_int64, c_char_p]), 
        "RegistrySetDword": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_int]), 
        "RegistryGetDword": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "RegistrySetQword": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_int64]), 
        "RegistryGetQword": (0x00000000, c_int64, [c_int64, c_int64, c_char_p]), 
        "RegistryDeleteValue": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "RegistryEnumSubKeys": (0x00000000, c_int64, [c_int64, c_int64]), 
        "RegistryEnumValues": (0x00000000, c_int64, [c_int64, c_int64]), 
        "RegistrySetEnvironmentVariable": (0x00000000, c_int, [c_int64, c_char_p, c_char_p, c_int]), 
        "RegistryGetEnvironmentVariable": (0x00000000, c_int64, [c_int64, c_char_p, c_int]), 
        "RegistryGetUserRegistryPath": (0x00000000, c_int64, [c_int64]), 
        "RegistryGetSystemRegistryPath": (0x00000000, c_int64, [c_int64]), 
        "RegistryBackupToFile": (0x00000000, c_int, [c_int64, c_int, c_char_p, c_char_p]), 
        "RegistryRestoreFromFile": (0x00000000, c_int, [c_int64, c_char_p]), 
        "RegistryCompareKeys": (0x00000000, c_int64, [c_int64, c_int, c_char_p, c_int, c_char_p]), 
        "RegistrySearchKeys": (0x00000000, c_int64, [c_int64, c_int, c_char_p, c_char_p, c_int]), 
        "RegistryGetInstalledSoftware": (0x00000000, c_int64, [c_int64]), 
        "RegistryGetWindowsVersion": (0x00000000, c_int64, [c_int64]), 
        "CreateDatabase": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p]), 
        "OpenDatabase": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p]), 
        "OpenMemoryDatabase": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_char_p]), 
        "GetDatabaseError": (0x00000000, c_int64, [c_int64, c_int64]), 
        "CloseDatabase": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetAllTableNames": (0x00000000, c_int64, [c_int64, c_int64]), 
        "GetTableInfo": (0x00000000, c_int64, [c_int64, c_int64, c_char_p]), 
        "GetTableInfoDetail": (0x00000000, c_int64, [c_int64, c_int64, c_char_p]), 
        "ExecuteSql": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "ExecuteScalar": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "ExecuteReader": (0x00000000, c_int64, [c_int64, c_int64, c_char_p]), 
        "Read": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetDataCount": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetColumnCount": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetColumnName": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "GetColumnIndex": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "GetColumnType": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "Finalize": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetDouble": (0x00000000, c_double, [c_int64, c_int64, c_int]), 
        "GetInt32": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "GetInt64": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "GetString": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "GetDoubleByColumnName": (0x00000000, c_double, [c_int64, c_int64, c_char_p]), 
        "GetInt32ByColumnName": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "GetInt64ByColumnName": (0x00000000, c_int64, [c_int64, c_int64, c_char_p]), 
        "GetStringByColumnName": (0x00000000, c_int64, [c_int64, c_int64, c_char_p]), 
        "InitOlaDatabase": (0x00000000, c_int, [c_int64, c_int64]), 
        "InitOlaImageFromDir": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_int]), 
        "RemoveOlaImageFromDir": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "ExportOlaImageDir": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p]), 
        "ImportOlaImage": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p, c_int]), 
        "GetOlaImage": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p]), 
        "RemoveOlaImage": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p]), 
        "SetDbConfig": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p]), 
        "GetDbConfig": (0x00000000, c_int64, [c_int64, c_int64, c_char_p]), 
        "RemoveDbConfig": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "SetDbConfigEx": (0x00000000, c_int, [c_int64, c_char_p, c_char_p]), 
        "GetDbConfigEx": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "RemoveDbConfigEx": (0x00000000, c_int, [c_int64, c_char_p]), 
        "InitDictFromDir": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p, c_int]), 
        "InitDictFromTxt": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p, c_int]), 
        "ImportDictWord": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p, c_int]), 
        "ExportDict": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p]), 
        "RemoveDict": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "RemoveDictWord": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_char_p]), 
        "GetDictImage": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p, c_int, c_int]), 
        "OpenVideo": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "OpenCamera": (0x00000000, c_int64, [c_int64, c_int]), 
        "CloseVideo": (0x00000000, c_int, [c_int64, c_int64]), 
        "IsVideoOpened": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetVideoInfo": (0x00000000, c_int64, [c_int64, c_int64]), 
        "GetVideoWidth": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetVideoHeight": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetVideoFPS": (0x00000000, c_double, [c_int64, c_int64]), 
        "GetVideoTotalFrames": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetVideoDuration": (0x00000000, c_double, [c_int64, c_int64]), 
        "GetCurrentFrameIndex": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetCurrentTimestamp": (0x00000000, c_double, [c_int64, c_int64]), 
        "ReadNextFrame": (0x00000000, c_int64, [c_int64, c_int64]), 
        "ReadFrameAtIndex": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "ReadFrameAtTime": (0x00000000, c_int64, [c_int64, c_int64, c_double]), 
        "ReadCurrentFrame": (0x00000000, c_int64, [c_int64, c_int64]), 
        "SeekToFrame": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "SeekToTime": (0x00000000, c_int, [c_int64, c_int64, c_double]), 
        "SeekToBeginning": (0x00000000, c_int, [c_int64, c_int64]), 
        "SeekToEnd": (0x00000000, c_int, [c_int64, c_int64]), 
        "ExtractFramesToFiles": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int, c_int, c_char_p, c_char_p, c_int]), 
        "ExtractFramesByInterval": (0x00000000, c_int, [c_int64, c_int64, c_double, c_char_p, c_char_p]), 
        "ExtractKeyFrames": (0x00000000, c_int, [c_int64, c_int64, c_double, c_int, c_char_p, c_char_p]), 
        "SaveCurrentFrame": (0x00000000, c_int, [c_int64, c_int64, c_char_p, c_int]), 
        "SaveFrameAtIndex": (0x00000000, c_int, [c_int64, c_int64, c_int, c_char_p, c_int]), 
        "FrameToBase64": (0x00000000, c_int64, [c_int64, c_int64, c_char_p]), 
        "CalculateFrameSimilarity": (0x00000000, c_double, [c_int64, c_int64, c_int64]), 
        "GetVideoInfoFromPath": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "IsValidVideoFile": (0x00000000, c_int, [c_int64, c_char_p]), 
        "ExtractSingleFrame": (0x00000000, c_int64, [c_int64, c_char_p, c_int]), 
        "ExtractThumbnail": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "ConvertVideo": (0x00000000, c_int, [c_int64, c_char_p, c_char_p, c_char_p, c_double]), 
        "ResizeVideo": (0x00000000, c_int, [c_int64, c_char_p, c_char_p, c_int, c_int]), 
        "TrimVideo": (0x00000000, c_int, [c_int64, c_char_p, c_char_p, c_double, c_double]), 
        "CreateVideoFromImages": (0x00000000, c_int, [c_int64, c_char_p, c_char_p, c_double, c_char_p]), 
        "DetectSceneChanges": (0x00000000, c_int64, [c_int64, c_char_p, c_double]), 
        "CalculateAverageBrightness": (0x00000000, c_double, [c_int64, c_char_p]), 
        "DetectMotion": (0x00000000, c_int64, [c_int64, c_char_p, c_double]), 
        "SetWindowState": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "FindWindow": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p]), 
        "GetClipboard": (0x00000000, c_int64, [c_int64]), 
        "SetClipboard": (0x00000000, c_int, [c_int64, c_char_p]), 
        "SendPaste": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetWindow": (0x00000000, c_int64, [c_int64, c_int64, c_int]), 
        "GetWindowTitle": (0x00000000, c_int64, [c_int64, c_int64]), 
        "GetWindowClass": (0x00000000, c_int64, [c_int64, c_int64]), 
        "GetWindowRect": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_int)]), 
        "GetWindowProcessPath": (0x00000000, c_int64, [c_int64, c_int64]), 
        "GetWindowState": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "GetForegroundWindow": (0x00000000, c_int64, [c_int64]), 
        "GetWindowProcessId": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetClientSize": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int), POINTER(c_int)]), 
        "GetMousePointWindow": (0x00000000, c_int64, [c_int64]), 
        "GetSpecialWindow": (0x00000000, c_int64, [c_int64, c_int]), 
        "GetClientRect": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_int)]), 
        "SetWindowText": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "SetWindowSize": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int]), 
        "SetClientSize": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int]), 
        "SetWindowTransparent": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "FindWindowEx": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p]), 
        "FindWindowByProcess": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_char_p]), 
        "MoveWindow": (0x00000000, c_int, [c_int64, c_int64, c_int, c_int]), 
        "GetScaleFromWindows": (0x00000000, c_double, [c_int64, c_int64]), 
        "GetWindowDpiAwarenessScale": (0x00000000, c_double, [c_int64, c_int64]), 
        "EnumProcess": (0x00000000, c_int64, [c_int64, c_char_p]), 
        "EnumWindow": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p, c_int]), 
        "EnumWindowByProcess": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_char_p, c_int]), 
        "EnumWindowByProcessId": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p, c_int]), 
        "EnumWindowSuper": (0x00000000, c_int64, [c_int64, c_char_p, c_int, c_int, c_char_p, c_int, c_int, c_int]), 
        "GetPointWindow": (0x00000000, c_int64, [c_int64, c_int, c_int]), 
        "GetProcessInfo": (0x00000000, c_int64, [c_int64, c_int64]), 
        "ShowTaskBarIcon": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "FindWindowByProcessId": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p]), 
        "GetWindowThreadId": (0x00000000, c_int64, [c_int64, c_int64]), 
        "FindWindowSuper": (0x00000000, c_int64, [c_int64, c_char_p, c_int, c_int, c_char_p, c_int, c_int]), 
        "ClientToScreen": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int), POINTER(c_int)]), 
        "ScreenToClient": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int), POINTER(c_int)]), 
        "GetForegroundFocus": (0x00000000, c_int64, [c_int64]), 
        "SetWindowDisplay": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "IsDisplayDead": (0x00000000, c_int, [c_int64, c_int, c_int, c_int, c_int, c_int]), 
        "GetWindowsFps": (0x00000000, c_int, [c_int64, c_int, c_int, c_int, c_int]), 
        "TerminateProcess": (0x00000000, c_int, [c_int64, c_int64]), 
        "TerminateProcessTree": (0x00000000, c_int, [c_int64, c_int64]), 
        "GetCommandLine": (0x00000000, c_int64, [c_int64, c_int64]), 
        "CheckFontSmooth": (0x00000000, c_int, [c_int64]), 
        "SetFontSmooth": (0x00000000, c_int, [c_int64, c_int]), 
        "EnableDebugPrivilege": (0x00000000, c_int, [c_int64]), 
        "SystemStart": (0x00000000, c_int, [c_int64, c_char_p, c_char_p]), 
        "CreateChildProcess": (0x00000000, c_int, [c_int64, c_char_p, c_char_p, c_char_p, c_int, c_int]), 
        "GetProcessIconImage": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int]), 
        "XmlCreateDocument": (0x00000000, c_int64, []), 
        "XmlParse": (0x00000000, c_int64, [c_char_p, POINTER(c_int)]), 
        "XmlParseFile": (0x00000000, c_int64, [c_char_p, POINTER(c_int)]), 
        "XmlToString": (0x00000000, c_int64, [c_int64, c_int, POINTER(c_int)]), 
        "XmlSaveToFile": (0x00000000, c_int, [c_int64, c_char_p, c_int, POINTER(c_int)]), 
        "XmlFree": (0x00000000, c_int, [c_int64]), 
        "XmlGetRootElement": (0x00000000, c_int64, [c_int64, POINTER(c_int)]), 
        "XmlCreateElement": (0x00000000, c_int64, [c_int64, c_char_p, POINTER(c_int)]), 
        "XmlInsertRootElement": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int)]), 
        "XmlAppendChild": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int)]), 
        "XmlGetFirstChild": (0x00000000, c_int64, [c_int64, POINTER(c_int)]), 
        "XmlGetNextSibling": (0x00000000, c_int64, [c_int64, POINTER(c_int)]), 
        "XmlFindElement": (0x00000000, c_int64, [c_int64, c_char_p, POINTER(c_int)]), 
        "XmlGetElementName": (0x00000000, c_int64, [c_int64, POINTER(c_int)]), 
        "XmlGetElementText": (0x00000000, c_int64, [c_int64, POINTER(c_int)]), 
        "XmlSetElementText": (0x00000000, c_int, [c_int64, c_char_p, POINTER(c_int)]), 
        "XmlRemoveChild": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int)]), 
        "XmlInsertBefore": (0x00000000, c_int, [c_int64, c_int64, c_int64, POINTER(c_int)]), 
        "XmlInsertAfter": (0x00000000, c_int, [c_int64, c_int64, c_int64, POINTER(c_int)]), 
        "XmlGetParent": (0x00000000, c_int64, [c_int64, POINTER(c_int)]), 
        "XmlGetPreviousSibling": (0x00000000, c_int64, [c_int64, POINTER(c_int)]), 
        "XmlGetLastChild": (0x00000000, c_int64, [c_int64, POINTER(c_int)]), 
        "XmlCloneElement": (0x00000000, c_int64, [c_int64, c_int64, POINTER(c_int)]), 
        "XmlHasChildren": (0x00000000, c_int, [c_int64, POINTER(c_int)]), 
        "XmlGetAttribute": (0x00000000, c_int64, [c_int64, c_char_p, POINTER(c_int)]), 
        "XmlSetAttribute": (0x00000000, c_int, [c_int64, c_char_p, c_char_p, POINTER(c_int)]), 
        "XmlGetAttributeInt": (0x00000000, c_int, [c_int64, c_char_p, POINTER(c_int)]), 
        "XmlSetAttributeInt": (0x00000000, c_int, [c_int64, c_char_p, c_int, POINTER(c_int)]), 
        "XmlGetAttributeDouble": (0x00000000, c_double, [c_int64, c_char_p, POINTER(c_int)]), 
        "XmlSetAttributeDouble": (0x00000000, c_int, [c_int64, c_char_p, c_double, POINTER(c_int)]), 
        "XmlGetAttributeBool": (0x00000000, c_int, [c_int64, c_char_p, POINTER(c_int)]), 
        "XmlSetAttributeBool": (0x00000000, c_int, [c_int64, c_char_p, c_int, POINTER(c_int)]), 
        "XmlGetAttributeInt64": (0x00000000, c_int64, [c_int64, c_char_p, POINTER(c_int)]), 
        "XmlSetAttributeInt64": (0x00000000, c_int, [c_int64, c_char_p, c_int64, POINTER(c_int)]), 
        "XmlHasAttribute": (0x00000000, c_int, [c_int64, c_char_p, POINTER(c_int)]), 
        "XmlDeleteAttribute": (0x00000000, c_int, [c_int64, c_char_p, POINTER(c_int)]), 
        "XmlGetAttributeNames": (0x00000000, c_int64, [c_int64, POINTER(c_int)]), 
        "XmlGetAttributeCount": (0x00000000, c_int, [c_int64, POINTER(c_int)]), 
        "XmlSetCDATA": (0x00000000, c_int, [c_int64, c_int64, c_char_p, POINTER(c_int)]), 
        "XmlAddComment": (0x00000000, c_int, [c_int64, c_int64, c_char_p, POINTER(c_int)]), 
        "XmlSetDeclaration": (0x00000000, c_int, [c_int64, c_char_p, c_char_p, c_int, POINTER(c_int)]), 
        "XmlQueryElement": (0x00000000, c_int64, [c_int64, c_char_p, POINTER(c_int)]), 
        "XmlGetChildCount": (0x00000000, c_int, [c_int64, POINTER(c_int)]), 
        "XmlGetChildCountByName": (0x00000000, c_int, [c_int64, c_char_p, POINTER(c_int)]), 
        "XmlGetChildByIndex": (0x00000000, c_int64, [c_int64, c_int, POINTER(c_int)]), 
        "XmlGetChildByNameAndIndex": (0x00000000, c_int64, [c_int64, c_char_p, c_int, POINTER(c_int)]), 
        "XmlFindElementByAttribute": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_char_p, POINTER(c_int)]), 
        "XmlGetElementDepth": (0x00000000, c_int, [c_int64, POINTER(c_int)]), 
        "XmlGetElementPath": (0x00000000, c_int64, [c_int64, POINTER(c_int)]), 
        "XmlCompareElements": (0x00000000, c_int, [c_int64, c_int64, c_int, POINTER(c_int)]), 
        "XmlMergeDocuments": (0x00000000, c_int, [c_int64, c_int64, POINTER(c_int)]), 
        "XmlValidate": (0x00000000, c_int, [c_int64, POINTER(c_int)]), 
        "XmlGetObjectCount": (0x00000000, c_int, []), 
        "XmlCleanupAll": (0x00000000, c_int, []), 
        "YoloLoadModel": (0x00000000, c_int64, [c_int64, c_char_p, c_char_p, c_char_p, c_char_p, c_int, c_int, c_int]), 
        "YoloReleaseModel": (0x00000000, c_int, [c_int64, c_int64]), 
        "YoloLoadModelMemory": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int, c_int, c_int]), 
        "YoloInfer": (0x00000000, c_int64, [c_int64, c_int64, c_int64]), 
        "YoloIsModelValid": (0x00000000, c_int, [c_int64, c_int64]), 
        "YoloListModels": (0x00000000, c_int64, [c_int64]), 
        "YoloGetModelInfo": (0x00000000, c_int64, [c_int64, c_int64]), 
        "YoloSetModelConfig": (0x00000000, c_int, [c_int64, c_int64, c_char_p]), 
        "YoloGetModelConfig": (0x00000000, c_int64, [c_int64, c_int64]), 
        "YoloWarmup": (0x00000000, c_int, [c_int64, c_int64, c_int]), 
        "YoloDetect": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int, c_int, c_int, c_char_p, c_double, c_double, c_int]), 
        "YoloDetectSimple": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int, c_int, c_int]), 
        "YoloDetectFromPtr": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_char_p, c_double, c_double, c_int]), 
        "YoloDetectFromFile": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p, c_double, c_double, c_int]), 
        "YoloDetectFromBase64": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p, c_double, c_double, c_int]), 
        "YoloDetectBatch": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_char_p, c_double, c_double, c_int]), 
        "YoloClassify": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int, c_int, c_int, c_int]), 
        "YoloClassifyFromPtr": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_int]), 
        "YoloClassifyFromFile": (0x00000000, c_int64, [c_int64, c_int64, c_char_p, c_int]), 
        "YoloSegment": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int, c_int, c_int, c_double, c_double]), 
        "YoloSegmentFromPtr": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_double, c_double]), 
        "YoloPose": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int, c_int, c_int, c_double, c_double]), 
        "YoloPoseFromPtr": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_double, c_double]), 
        "YoloObb": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int, c_int, c_int, c_double, c_double]), 
        "YoloObbFromPtr": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_double, c_double]), 
        "YoloKeyPoint": (0x00000000, c_int64, [c_int64, c_int64, c_int, c_int, c_int, c_int, c_double, c_double]), 
        "YoloKeyPointFromPtr": (0x00000000, c_int64, [c_int64, c_int64, c_int64, c_double, c_double]), 
        "YoloGetInferenceStats": (0x00000000, c_int64, [c_int64, c_int64]), 
        "YoloResetStats": (0x00000000, c_int, [c_int64, c_int64]), 
        "YoloGetLastError": (0x00000000, c_int64, [c_int64]), 
        "YoloClearError": (0x00000000, c_int, [c_int64]), 
    }

    # 带出参的函数配置
    output_configs = {
    "GetDenseRect": {
        "outputs": [4, 5, 6, 7],
        "output_names": ["x1", "y1", "x2", "y2"]
    },
    "DrawGuiGetPosition": {
        "outputs": [2, 3],
        "output_names": ["x", "y"]
    },
    "DrawGuiGetSize": {
        "outputs": [2, 3],
        "output_names": ["width", "height"]
    },
    "KeOpenProcess": {
        "outputs": [2],
        "output_names": ["process_handle"]
    },
    "KeOpenThread": {
        "outputs": [2],
        "output_names": ["thread_handle"]
    },
    "ProtectFileQueryProtectedPath": {
        "outputs": [2],
        "output_names": ["mode"]
    },
    "JsonParse": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "JsonStringify": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "JsonGetValue": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "JsonGetArrayItem": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "JsonGetString": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "JsonGetNumber": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "JsonGetBool": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "JsonGetSize": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "ParseMatchImageJson": {
        "outputs": [1, 2, 3, 4, 5, 6, 7, 8],
        "output_names": ["matchState", "x", "y", "width", "height", "matchVal", "angle", "index"]
    },
    "ParseMatchImageAllJson": {
        "outputs": [2, 3, 4, 5, 6, 7, 8, 9],
        "output_names": ["matchState", "x", "y", "width", "height", "matchVal", "angle", "index"]
    },
    "GetCursorPos": {
        "outputs": [1, 2],
        "output_names": ["x", "y"]
    },
    "VirtualProtectEx": {
        "outputs": [5],
        "output_names": ["oldProtect"]
    },
    "CreateRemoteThread": {
        "outputs": [5],
        "output_names": ["lpThreadId"]
    },
    "HttpRequestEx": {
        "outputs": [6],
        "output_names": ["status_code"]
    },
    "FindStr": {
        "outputs": [9, 10],
        "output_names": ["outX", "outY"]
    },
    "GetScreenDataBmp": {
        "outputs": [5, 6],
        "output_names": ["data", "dataLen"]
    },
    "GetScreenData": {
        "outputs": [5, 6, 7],
        "output_names": ["data", "dataLen", "stride"]
    },
    "GetImageData": {
        "outputs": [2, 3, 4],
        "output_names": ["data", "size", "stride"]
    },
    "FindColor": {
        "outputs": [8, 9],
        "output_names": ["x", "y"]
    },
    "FindColorEx": {
        "outputs": [7, 8],
        "output_names": ["x", "y"]
    },
    "FindMultiColor": {
        "outputs": [9, 10],
        "output_names": ["x", "y"]
    },
    "FindMultiColorFromPtr": {
        "outputs": [6, 7],
        "output_names": ["x", "y"]
    },
    "GetImageSize": {
        "outputs": [2, 3],
        "output_names": ["width", "height"]
    },
    "FindColorBlock": {
        "outputs": [9, 10],
        "output_names": ["x", "y"]
    },
    "FindColorBlockPtr": {
        "outputs": [6, 7],
        "output_names": ["x", "y"]
    },
    "FindColorBlockEx": {
        "outputs": [10, 11],
        "output_names": ["x", "y"]
    },
    "FindColorBlockPtrEx": {
        "outputs": [7, 8],
        "output_names": ["x", "y"]
    },
    "GetImageBmpData": {
        "outputs": [2, 3],
        "output_names": ["data", "size"]
    },
    "GetImagePngData": {
        "outputs": [2, 3],
        "output_names": ["data", "size"]
    },
    "Hex2ARGB": {
        "outputs": [2, 3, 4, 5],
        "output_names": ["a", "r", "g", "b"]
    },
    "Hex2RGB": {
        "outputs": [2, 3, 4],
        "output_names": ["r", "g", "b"]
    },
    "GetROIRegion": {
        "outputs": [2, 3, 4, 5],
        "output_names": ["x1", "y1", "x2", "y2"]
    },
    "ImageStitchFromPath": {
        "outputs": [2],
        "output_names": ["trajectory"]
    },
    "ImageStitchGetResult": {
        "outputs": [2],
        "output_names": ["trajectory"]
    },
    "GetWindowRect": {
        "outputs": [2, 3, 4, 5],
        "output_names": ["x1", "y1", "x2", "y2"]
    },
    "GetClientSize": {
        "outputs": [2, 3],
        "output_names": ["width", "height"]
    },
    "GetClientRect": {
        "outputs": [2, 3, 4, 5],
        "output_names": ["x1", "y1", "x2", "y2"]
    },
    "ClientToScreen": {
        "outputs": [2, 3],
        "output_names": ["x", "y"]
    },
    "ScreenToClient": {
        "outputs": [2, 3],
        "output_names": ["x", "y"]
    },
    "XmlParse": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "XmlParseFile": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "XmlToString": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlSaveToFile": {
        "outputs": [3],
        "output_names": ["err"]
    },
    "XmlGetRootElement": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "XmlCreateElement": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlInsertRootElement": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlAppendChild": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlGetFirstChild": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "XmlGetNextSibling": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "XmlFindElement": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlGetElementName": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "XmlGetElementText": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "XmlSetElementText": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlRemoveChild": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlInsertBefore": {
        "outputs": [3],
        "output_names": ["err"]
    },
    "XmlInsertAfter": {
        "outputs": [3],
        "output_names": ["err"]
    },
    "XmlGetParent": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "XmlGetPreviousSibling": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "XmlGetLastChild": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "XmlCloneElement": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlHasChildren": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "XmlGetAttribute": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlSetAttribute": {
        "outputs": [3],
        "output_names": ["err"]
    },
    "XmlGetAttributeInt": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlSetAttributeInt": {
        "outputs": [3],
        "output_names": ["err"]
    },
    "XmlGetAttributeDouble": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlSetAttributeDouble": {
        "outputs": [3],
        "output_names": ["err"]
    },
    "XmlGetAttributeBool": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlSetAttributeBool": {
        "outputs": [3],
        "output_names": ["err"]
    },
    "XmlGetAttributeInt64": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlSetAttributeInt64": {
        "outputs": [3],
        "output_names": ["err"]
    },
    "XmlHasAttribute": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlDeleteAttribute": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlGetAttributeNames": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "XmlGetAttributeCount": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "XmlSetCDATA": {
        "outputs": [3],
        "output_names": ["err"]
    },
    "XmlAddComment": {
        "outputs": [3],
        "output_names": ["err"]
    },
    "XmlSetDeclaration": {
        "outputs": [4],
        "output_names": ["err"]
    },
    "XmlQueryElement": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlGetChildCount": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "XmlGetChildCountByName": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlGetChildByIndex": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlGetChildByNameAndIndex": {
        "outputs": [3],
        "output_names": ["err"]
    },
    "XmlFindElementByAttribute": {
        "outputs": [4],
        "output_names": ["err"]
    },
    "XmlGetElementDepth": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "XmlGetElementPath": {
        "outputs": [1],
        "output_names": ["err"]
    },
    "XmlCompareElements": {
        "outputs": [3],
        "output_names": ["err"]
    },
    "XmlMergeDocuments": {
        "outputs": [2],
        "output_names": ["err"]
    },
    "XmlValidate": {
        "outputs": [1],
        "output_names": ["err"]
    },
    }

    _cached_functions = {}

    @classmethod
    def _get_raw_function(cls, function_name: str) -> Callable:
        """
        获取原始 ctypes 函数对象（未装饰）

        Args:
            function_name: 函数名

        Returns:
            原始函数对象
        """
        if function_name not in cls.function_signatures:
            raise AttributeError(f"签名中未找到函数：{function_name}")

        rva, restype, argtypes = cls.function_signatures[function_name]

        # 获取函数地址
        if rva == 0:
            # 使用 GetProcAddress 按名称查找
            func_address = _kernel32.GetProcAddress(
                cls._dll_base,
                function_name.encode('utf-8')
            )
            if not func_address:
                raise RuntimeError(f"获取函数地址失败：{function_name}")
        else:
            # 使用基址 + 偏移
            func_address = cls._dll_base + rva

        # 创建函数类型
        FuncType = WINFUNCTYPE(restype, *argtypes)
        raw_func = FuncType(func_address)

        # 保存 argtypes 供装饰器使用
        raw_func.argtypes = argtypes

        return raw_func

    @classmethod
    def _apply_decorators(cls, func: Callable, function_name: str) -> Callable:
        """
        应用装饰器到函数

        装饰器执行顺序（从内到外）：
        1. handle_output_params: 处理输出参数
        2. handle_string_params: 处理字符串编码

        Args:
            func: 原始函数
            function_name: 函数名

        Returns:
            装饰后的函数
        """
        # 1. 应用输出参数装饰器
        if function_name in cls.output_configs:
            output_indices = cls.output_configs[function_name].get('outputs', [])
            if output_indices:
                func = handle_output_params(output_indices)(func)

        # 2. 应用字符串编码装饰器
        func = handle_string_params(func)

        return func

    # ========== 公共方法 ==========
    @classmethod
    def get_function(cls, function_name: str) -> Callable:
        """
        获取函数（带缓存和装饰器）

        Args:
            function_name: 函数名

        Returns:
            装饰后的函数对象
        """
        # 检查缓存
        bound_accessor = getattr(cls, function_name, None)
        if callable(bound_accessor):
            class_attr = cls.__dict__.get(function_name)
            if class_attr is not None:
                return bound_accessor

        if function_name in cls._cached_functions:
            return cls._cached_functions[function_name]

        # 获取原始函数
        raw_func = cls._get_raw_function(function_name)

        # 应用装饰器
        wrapped_func = cls._apply_decorators(raw_func, function_name)

        # 缓存
        cls._cached_functions[function_name] = wrapped_func

        return wrapped_func


def _build_helper_method(function_name: str) -> classmethod:
    def _method(cls, *args):
        wrapped_func = cls._cached_functions.get(function_name)
        if wrapped_func is None:
            raw_func = cls._get_raw_function(function_name)
            wrapped_func = cls._apply_decorators(raw_func, function_name)
            cls._cached_functions[function_name] = wrapped_func
        return wrapped_func(*args)

    _method.__name__ = function_name
    return classmethod(_method)


for _function_name in tuple(OLAPlugDLLHelper.function_signatures):
    if _function_name not in OLAPlugDLLHelper.__dict__:
        setattr(OLAPlugDLLHelper, _function_name, _build_helper_method(_function_name))

del _function_name

