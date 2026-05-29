#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
传奇2自动挂机脚本
需要准备的截图: 见下面的 TEMPLATES 配置
"""

import pyautogui
import time
import random
import os
import sys

# ==================== 配置区 ====================
# 图像模板目录 (放你的截图)
TEMPLATE_DIR = "images/"

# 需要更换的截图文件名
TEMPLATES = {
    # --- 地图相关 ---
    "map_entered": "entered_地图名.png",       # 进图确认 (如: entered_低级图.png)
    "map_select": "btn_地图名.png",            # 地图选择按钮
    
    # --- NPC相关 ---
    "npc_transporter": "npc_传送员.png",       # 传送NPC
    "btn_enter_map": "btn_进入地图.png",       # 进入地图按钮
    
    # --- 怪物相关 ---
    "monster_1": "monster_怪1.png",            # 怪物1
    "monster_2": "monster_怪2.png",            # 怪物2  
    "monster_3": "monster_怪3.png",            # 怪物3
    
    # --- 状态相关 ---
    "hp_bar": "hp_bar.png",                    # 血条位置(截图一个点)
    "mp_bar": "mp_bar.png",                    # 蓝条位置
    "bag_full": "bag_full.png",                # 背包满提示
    
    # --- 安全区 ---
    "safe_zone": "safe_zone.png",              # 安全区标志
    "city": "city.png",                        # 城市标志(盟重省)
    
    # --- 掉落物品 ---
    "drop_item": "drop_item.png",              # 掉落装备
}

# 按键配置
KEYS = {
    "attack": "f1",          # 攻击键
    "pickup": "f9",          # 拾取键
    "skill_1": "f1",         # 技能1
    "skill_2": "f2",         # 技能2
    "skill_3": "f3",         # 技能3
    "heal": "f5",            # 治疗/回城
    "buff": "f10",           # 增益技能
    "up": "up",              # 方向上
    "down": "down",          # 方向下
    "left": "left",          # 方向左
    "right": "right",        # 方向右
}

# 挂机配置
CONFIG = {
    "hp_low_percent": 30,      # 血量低于30%回城
    "mp_low_percent": 20,      # 蓝量低于20%回城
    "attack_delay": 2,         # 攻击后等待秒数
    "pickup_delay": 1,         # 拾取后等待
    "move_interval": 3,        # 移动间隔(秒)
    "search_range": None,      # 找怪区域 (x, y, w, h) 或 None
    "confidence": 0.8,         # 图像匹配置信度
    "random_offset": 3,        # 点击随机偏移像素
}

# ==================== 脚本区 ====================

class LegendBot:
    """传奇2自动挂机脚本"""
    
    def __init__(self):
        # pyautogui设置
        pyautogui.PAUSE = 0.1
        pyautogui.FAILSAFE = True
        
        self.templates = TEMPLATE_DIR
        self.running = False
        self.confidence = CONFIG["confidence"]
        
    def log(self, msg):
        """日志输出"""
        print(f"[{time.strftime('%H:%M:%S')}] {msg}")
        
    def set_search_region(self, region):
        """设置找怪区域"""
        CONFIG["search_range"] = region
        
    # ---------- 图像识别 ----------
    
    def find_image(self, name, region=None):
        """查找图片位置"""
        if name not in TEMPLATES:
            return None
            
        path = os.path.join(self.templates, TEMPLATES[name])
        if not os.path.exists(path):
            # 尝试直接用name
            path = os.path.join(self.templates, name)
            if not os.path.exists(path):
                return None
                
        try:
            if region:
                pos = pyautogui.locateCenterOnScreen(
                    path, region=region, confidence=self.confidence
                )
            else:
                pos = pyautogui.locateCenterOnScreen(
                    path, confidence=self.confidence
                )
            return pos
        except:
            return None
            
    def find_any(self, *names):
        """查找多个图片,返回第一个找到的"""
        for name in names:
            pos = self.find_image(name)
            if pos:
                return name, pos
        return None, None
    
    def click_image(self, name, offset=True):
        """点击找到的图片"""
        pos = self.find_image(name)
        if pos:
            x = pos.x + (random.randint(-CONFIG["random_offset"], CONFIG["random_offset"]) if offset else 0)
            y = pos.y + (random.randint(-CONFIG["random_offset"], CONFIG["random_offset"]) if offset else 0)
            pyautogui.click(x, y)
            return True
        return False
    
    # ---------- 像素检测 ----------
    
    def check_color(self, x, y, expected_color, tolerance=20):
        """检查像素颜色是否匹配"""
        try:
            actual = pyautogui.pixel(x, y)
            return all(abs(actual[i] - expected_color[i]) <= tolerance for i in range(3))
        except:
            return False
            
    def get_hp_percent(self):
        """获取血量百分比 (需要截图模板)"""
        # 方法1: 使用模板图片位置检测
        hp_pos = self.find_image("hp_bar")
        if hp_pos:
            color = pyautogui.pixel(hp_pos.x, hp_pos.y)
            # 红色分量高=血少, 绿色分量高=血多
            # 根据实际颜色调整
            return int(color[1] / 255 * 100)  # 假设绿色=血量
        return 100
        
    # ---------- 按键操作 ----------
    
    def press_key(self, key, count=1):
        """按键"""
        for _ in range(count):
            pyautogui.press(key)
            time.sleep(random.uniform(0.05, 0.15))
            
    def press_key_combo(self, *keys):
        """组合键"""
        pyautogui.hotkey(*keys)
        time.sleep(random.uniform(0.1, 0.2))
        
    def move_direction(self, direction, duration=0.5):
        """方向移动"""
        key = KEYS.get(direction, direction)
        end_time = time.time() + duration
        while time.time() < end_time and self.running:
            pyautogui.press(key)
            time.sleep(0.8 + random.uniform(-0.1, 0.1))
            
    def attack_target(self):
        """攻击发现的目标"""
        self.press_key(KEYS["attack"])
        time.sleep(CONFIG["attack_delay"])
        
    def pickup_items(self):
        """拾取掉落"""
        self.press_key(KEYS["pickup"])
        time.sleep(CONFIG["pickup_delay"])
        
    # ---------- 状态检测 ----------
    
    def is_in_game(self):
        """是否在游戏内"""
        # 检测是否有游戏界面特征
        return True  # 可根据实际情况修改
        
    def is_in_safe_zone(self):
        """是否在安全区"""
        return self.find_image("safe_zone") is not None
        
    def is_hp_low(self):
        """血量是否过低"""
        hp = self.get_hp_percent()
        return hp < CONFIG["hp_low_percent"]
        
    def find_monster(self):
        """查找怪物"""
        region = CONFIG["search_range"]
        names = ["monster_1", "monster_2", "monster_3"]
        return self.find_any(*names)
        
    # ---------- 核心流程 ----------
    
    def transport_to_map(self):
        """传送进图"""
        self.log("传送进图...")
        
        # 1. 找NPC
        if not self.click_image("npc_transporter"):
            self.log("未找到传送NPC")
            return False
            
        time.sleep(1)
        
        # 2. 选择地图
        if not self.click_image("map_select"):
            self.log("未找到地图选项")
            return False
            
        time.sleep(1)
        
        # 3. 进入地图
        if not self.click_image("btn_enter_map"):
            self.log("未找到进入按钮")
            return False
            
        # 4. 等待进图
        time.sleep(3)
        
        # 5. 确认进图
        if self.find_image("map_entered"):
            self.log("进入地图成功")
            return True
            
        return False
        
    def return_to_city(self):
        """回城"""
        self.log("回城...")
        self.press_key(KEYS["heal"])
        time.sleep(2)
        
        # 等待回到安全区
        for _ in range(10):
            if self.is_in_safe_zone():
                self.log("已回到安全区")
                return True
            time.sleep(1)
            
        return False
        
    def hunt_loop(self):
        """挂机打怪循环"""
        self.log("开始挂机...")
        
        no_monster_count = 0
        move_directions = ["up", "down", "left", "right"]
        
        while self.running:
            try:
                # 1. 检查血量
                if self.is_hp_low():
                    self.log("血量低,回城!")
                    self.return_to_city()
                    # 等待恢复
                    time.sleep(5)
                    continue
                    
                # 2. 查找怪物
                name, pos = self.find_monster()
                if pos:
                    self.log(f"发现怪物: {name}")
                    no_monster_count = 0
                    
                    # 点击怪物
                    x = pos.x + random.randint(-5, 5)
                    y = pos.y + random.randint(-5, 5)
                    pyautogui.click(x, y)
                    time.sleep(0.5)
                    
                    # 攻击
                    self.attack_target()
                    
                    # 拾取
                    time.sleep(1)
                    self.pickup_items()
                    
                    continue
                else:
                    no_monster_count += 1
                    
                # 3. 移动找怪
                if no_monster_count >= 2:
                    self.log("未发现怪物,移动中...")
                    # 随机移动
                    direction = random.choice(move_directions)
                    self.move_direction(duration=random.uniform(1, 3))
                    no_monster_count = 0
                    
                # 4. 短暂等待
                time.sleep(0.5)
                
            except Exception as e:
                self.log(f"错误: {e}")
                time.sleep(1)
                
    def start(self):
        """启动挂机"""
        self.running = True
        
        # 检查模板目录
        if not os.path.exists(self.templates):
            os.makedirs(self.templates)
            self.log(f"已创建模板目录: {self.templates}")
            self.log("请放入以下截图:")
            for k, v in TEMPLATES.items():
                self.log(f"  {k}: {v}")
            return
            
        self.log("=" * 40)
        self.log("传奇2自动挂机脚本启动")
        self.log("按 Ctrl+C 停止")
        self.log("=" * 40)
        
        # 主循环
        try:
            while self.running:
                # 1. 确认在游戏内
                if not self.is_in_game():
                    self.log("请先进入游戏")
                    time.sleep(5)
                    continue
                    
                # 2. 检查是否在安全区,如果是则传送
                if self.is_in_safe_zone():
                    self.log("在安全区,传送进图...")
                    if self.transport_to_map():
                        time.sleep(2)
                    else:
                        time.sleep(3)
                        continue
                        
                # 3. 开始挂机
                self.hunt_loop()
                
        except KeyboardInterrupt:
            self.stop()
            
    def stop(self):
        """停止挂机"""
        self.running = False
        self.log("挂机已停止")


# ==================== 启动入口 ====================
if __name__ == "__main__":
    bot = LegendBot()
    
    # 可选: 设置找怪区域 (x, y, width, height)
    # bot.set_search_region((300, 100, 600, 500))
    
    bot.start()