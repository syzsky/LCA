# -*- coding: utf-8 -*-
"""
窗口角色检测卡片 - 读取当前绑定窗口的角色标签并设置为工作流变量
用于多职业挂机时区分当前窗口属于哪个职业
"""
import logging
from typing import Dict, Any, Optional

from tasks import task_utils
from task_workflow.variable_resolver import initialize_variable

logger = logging.getLogger(__name__)

TASK_NAME = "窗口角色检测"


def get_params_definition() -> Dict[str, Dict[str, Any]]:
    base_params = {
        "variable_name": {
            "label": "输出变量名",
            "type": "text",
            "default": "职业类型",
            "tooltip": "检测到的角色标签将存储到此变量中，供后续条件分支使用"
        },
        "default_role": {
            "label": "未检测到角色时的默认值",
            "type": "text",
            "default": "未知",
            "tooltip": "如果窗口没有设置角色标签，使用此默认值"
        },
        **task_utils.get_standard_action_params()
    }
    return base_params


def execute(params: Dict[str, Any], context, counters: Dict[str, Any],
            current_card_id: Optional[int] = None) -> Dict[str, Any]:
    """
    执行窗口角色检测：
    1. 从执行上下文中获取当前绑定的窗口信息
    2. 读取窗口的 role 字段
    3. 将角色标签设置为工作流变量
    """
    try:
        # 获取当前窗口句柄（从执行上下文）
        target_hwnd = getattr(context, 'target_hwnd', None)
        if not target_hwnd:
            # 尝试从 runtime vars 获取
            target_hwnd = context.runtime_vars_manifest.get('target_hwnd') if hasattr(context, 'runtime_vars_manifest') else None
        
        # 获取绑定窗口列表（从全局或上下文）
        bound_windows = getattr(context, 'bound_windows', None)
        if not bound_windows and hasattr(context, 'global_vars'):
            bound_windows = context.global_vars.get('bound_windows', None)
        
        # 如果找不到绑定窗口信息，尝试从主窗口获取
        if not bound_windows and hasattr(context, 'main_window'):
            mw = context.main_window
            if hasattr(mw, 'bound_windows'):
                bound_windows = mw.bound_windows
        
        detected_role = None
        
        if bound_windows and isinstance(bound_windows, list):
            # 查找当前窗口对应的角色
            for win in bound_windows:
                if win.get('hwnd') == target_hwnd:
                    detected_role = win.get('role', '')
                    break
            
            # 如果没通过 hwnd 匹配到，且只有一个窗口，用第一个窗口的角色
            if detected_role is None and len(bound_windows) == 1:
                detected_role = bound_windows[0].get('role', '')
        
        # 如果还是没检测到，使用默认值
        if not detected_role:
            detected_role = params.get('default_role', '未知')
        
        # 设置工作流变量
        var_name = params.get('variable_name', '职业类型').strip()
        if var_name:
            initialize_variable(var_name, detected_role, context=context)
            logger.info(f"[窗口角色检测] 检测到角色: {detected_role}, 已设置变量 {var_name}")
        else:
            logger.warning("[窗口角色检测] 变量名为空，跳过设置")
        
        return {
            "success": True,
            "message": f"检测到角色: {detected_role}",
            "role": detected_role,
            "variable_name": var_name
        }
        
    except Exception as e:
        logger.error(f"[窗口角色检测] 执行失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"角色检测失败: {str(e)}",
            "role": params.get('default_role', '未知')
        }
