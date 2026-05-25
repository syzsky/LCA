import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)
TASK_NAME = '地图选择'

# 传奇2官方标准地图(按传送NPC菜单顺序)
ALL_MAPS = [
    # 盟重传送
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
    ("石墓七层", "🟡 石墓七层"),

    # 蜈蚣洞(死亡山谷)
    ("蜈蚣洞一层", "🟢 蜈蚣洞一层"),
    ("蜈蚣洞二层", "🟢 蜈蚣洞二层"),
    ("蜈蚣洞三层", "🟢 蜈蚣洞三层"),
    ("死亡棺材", "🟢 死亡棺材"),

    # 祖玛寺庙
    ("祖玛寺庙一层", "🔴 祖玛寺庙一层"),
    ("祖玛寺庙二层", "🔴 祖玛寺庙二层"),
    ("祖玛寺庙三层", "🔴 祖玛寺庙三层"),
    ("祖玛寺庙四层", "🔴 祖玛寺庙四层"),
    ("祖玛寺庙五层", "🔴 祖玛寺庙五层"),
    ("祖玛寺庙七层", "🔴 祖玛寺庙七层"),

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

    # 魔龙城
    ("魔龙城", "🔴 魔龙城"),
    ("魔龙殿", "🔴 魔龙殿"),
    ("魔龙沼泽", "🔴 魔龙沼泽"),

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
    ("猪洞七层", "🟡 猪洞七层(桃园)"),
    ("将军殿", "🔴 将军殿"),
    ("炼狱走廊", "🔴 炼狱走廊"),
]


class MapSelectorTask(object):
    NODE_TYPE = "MapSelector"

    def __init__(self, config=None):
        self.selected_map = ALL_MAPS[0][0]
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
        return "选择要挂机的地图。提供对应按钮截图即可自动进图。"

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
    import time as _time
    logger.info("开始执行地图选择任务")

    selected_display = params.get('selected_map', ALL_MAPS[0][1])

    selected_map = ALL_MAPS[0][0]
    for map_val, map_display in ALL_MAPS:
        if map_display == selected_display or map_val == selected_display:
            selected_map = map_val
            break

    logger.info(f"用户选择地图: {selected_map}")

    # 存储到 counters
    counters["target_map"] = selected_map

    # 尝试存到全局变量
    try:
        from task_workflow.runtime_var_store import context as var_context
        var_context.set_global_var("target_map", selected_map)
        logger.info(f"已设置 target_map = {selected_map}")
    except Exception:
        pass

    # 获取窗口句柄
    target_hwnd = kwargs.get('target_hwnd')
    logger.info(f"准备进入 {selected_map}，窗口句柄: {target_hwnd}")

    # --- NPC传送员截图路径 ---
    npc_image = "images/templates/npc_传送员.png"
    # --- 地图按钮截图路径 ---
    btn_image = f"images/templates/btn_{selected_map}.png"
    # --- 进图验证截图路径 ---
    verify_image = f"images/templates/entered_{selected_map}.png"

    logger.info(f"NPC图片: {npc_image}")
    logger.info(f"按钮图片: {btn_image}")
    logger.info(f"验证图片: {verify_image}")

    # 提示用户截图命名规则
    logger.info("=" * 50)
    logger.info(f"📌 如需自动进 {selected_map}，请准备截图：")
    logger.info(f"  1. {npc_image}  — NPC传送员截图")
    logger.info(f"  2. {btn_image}  — 菜单中{selected_map}的按钮")
    logger.info(f"  3. {verify_image} — 进图后界面截图")
    logger.info("=" * 50)

    _time.sleep(0.5)
    return True, '执行下一步', None