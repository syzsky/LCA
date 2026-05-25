import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)
TASK_NAME = '地图选择'

ALL_MAPS = [
    # 安全区
    ("盟重省", "🏘️ 盟重省"),
    ("比奇省", "🏘️ 比奇省"),
    ("毒蛇山谷", "🏘️ 毒蛇山谷"),

    # 沃玛寺庙
    ("沃玛寺庙一层", "🟢 沃玛寺庙一层"),
    ("沃玛寺庙二层", "🟢 沃玛寺庙二层"),
    ("沃玛寺庙三层", "🟢 沃玛寺庙三层"),

    # 石墓(猪洞)
    ("石墓一层", "🟡 石墓一层(猪洞)"),
    ("石墓二层", "🟡 石墓二层"),
    ("石墓三层", "🟡 石墓三层"),
    ("石墓四层", "🟡 石墓四层"),
    ("石墓五层", "🟡 石墓五层"),

    # 祖玛寺庙
    ("祖玛寺庙一层", "🔴 祖玛寺庙一层"),
    ("祖玛寺庙二层", "🔴 祖玛寺庙二层"),
    ("祖玛寺庙三层", "🔴 祖玛寺庙三层"),
    ("祖玛寺庙四层", "🔴 祖玛寺庙四层"),
    ("祖玛寺庙五层", "🔴 祖玛寺庙五层"),
    ("祖玛寺庙七层", "🔴 祖玛寺庙七层"),

    # 死亡山谷(蜈蚣洞)
    ("死亡山谷一层", "🟢 死亡山谷一层(蜈蚣洞)"),
    ("死亡山谷二层", "🟢 死亡山谷二层"),
    ("死亡山谷三层", "🟢 死亡山谷三层"),

    # 赤月峡谷
    ("赤月峡谷东", "🔴 赤月峡谷东"),
    ("赤月峡谷西", "🔴 赤月峡谷西"),
    ("赤月峡谷南", "🔴 赤月峡谷南"),
    ("赤月峡谷北", "🔴 赤月峡谷北"),
    ("赤月恶魔巢穴", "🔴 赤月恶魔巢穴"),

    # 牛魔寺庙
    ("牛魔寺庙一层", "🔴 牛魔寺庙一层"),
    ("牛魔寺庙二层", "🔴 牛魔寺庙二层"),
    ("牛魔寺庙三层", "🔴 牛魔寺庙三层"),
    ("牛魔寺庙四层", "🔴 牛魔寺庙四层"),
    ("牛魔寺庙五层", "🔴 牛魔寺庙五层"),
    ("牛魔大殿", "🔴 牛魔大殿"),

    # 骨魔洞
    ("骨魔洞一层", "🔴 骨魔洞一层"),
    ("骨魔洞二层", "🔴 骨魔洞二层"),
    ("骨魔洞三层", "🔴 骨魔洞三层"),
    ("骨魔洞四层", "🔴 骨魔洞四层"),
    ("骨魔洞五层", "🔴 骨魔洞五层"),

    # 尸魔洞
    ("尸魔洞一层", "🔴 尸魔洞一层"),
    ("尸魔洞二层", "🔴 尸魔洞二层"),
    ("尸魔洞三层", "🔴 尸魔洞三层"),

    # 幻境
    ("幻境一层", "🟣 幻境一层"),
    ("幻境二层", "🟣 幻境二层"),
    ("幻境三层", "🟣 幻境三层"),
    ("幻境四层", "🟣 幻境四层"),
    ("幻境五层", "🟣 幻境五层"),
    ("幻境六层", "🟣 幻境六层"),
    ("幻境七层", "🟣 幻境七层"),
    ("幻境八层", "🟣 幻境八层"),
    ("幻境九层", "🟣 幻境九层"),
    ("幻境十层", "🟣 幻境十层"),

    # 其他
    ("藏宝阁", "🟣 藏宝阁"),
    ("桃源之门", "🟢 桃源之门"),
    ("猪洞七层", "🟡 猪洞七层"),
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