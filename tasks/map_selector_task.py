import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)
TASK_NAME = '地图选择'

ALL_MAPS = [
    ("低级图", "🟢 低级图 (1~39级)"),
    ("中级图", "🟡 中级图 (40~54级)"),
    ("高级图", "🔴 高级图 (55级以上)"),
]

class MapSelectorTask(object):
    NODE_TYPE = "MapSelector"
    
    def __init__(self, config=None):
        self.selected_map = "低级图"
        self.set_config(config or {})
    
    def run(self):
        logger.info(f"已选择地图: {self.selected_map}")
        return True
    
    def get_config(self) -> dict:
        return {'selected_map': self.selected_map}
    
    def set_config(self, config: dict):
        self.selected_map = config.get('selected_map', self.selected_map)
    
    @staticmethod
    def get_display_name() -> str:
        return "地图选择"
    
    @staticmethod
    def get_category() -> str:
        return "流程控制"
    
    @staticmethod
    def get_description() -> str:
        return "手动选择要进入的挂机地图，运行后将 target_map 变量设为所选地图。"
    
    @staticmethod
    def get_icon() -> str:
        return "mdi.map-marker"


def get_params_definition() -> Dict[str, Any]:
    return {
        "selected_map": {
            "label": "选择挂机地图",
            "type": "select",
            "options": [m[1] for m in ALL_MAPS],
            "default": ALL_MAPS[0][1],
            "tooltip": "选择要进入的挂机地图"
        },
    }


def execute_task(params: Dict[str, Any], counters: Dict[str, int], execution_mode='foreground', **kwargs) -> Tuple[bool, str, Optional[int]]:
    logger.info("开始执行地图选择任务")
    
    selected_display = params.get('selected_map', ALL_MAPS[0][1])
    
    # 从显示名称找实际地图名
    selected_map = ALL_MAPS[0][0]  # 默认
    for map_val, map_display in ALL_MAPS:
        if map_display == selected_display or map_val == selected_display:
            selected_map = map_val
            break
    
    logger.info(f"用户选择地图: {selected_map}")
    
    # 将选择存入全局变量 target_map
    try:
        from task_workflow.runtime_var_store import context as var_context
        var_context.set_global_var("target_map", selected_map)
        logger.info(f"已设置变量 target_map = {selected_map}")
    except Exception as e:
        logger.warning(f"设置变量 target_map 失败: {e}")
    
    # 也存入 counters
    counters["target_map"] = selected_map
    
    return True, '执行下一步', None