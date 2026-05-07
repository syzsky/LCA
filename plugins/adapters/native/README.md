# -*- coding: utf-8 -*-
"""
Native 插件安装说明
====================

1. 安装依赖:
   pip install pyautogui opencv-python numpy pynput pywin32 pygetwindow paddleocr paddlepaddle

2. 修改 plugins/config.json，添加 native 插件配置:
   {
     "plugin_system_enabled": true,
     "plugins": {
       "native": {
         "enabled": true,
         "priority": 5,
         "description": "纯Python原生插件(替代OLA)",
         "config": {
           "use_human_like_mouse": false,
           "ocr_language": "ch",
           "ocr_use_gpu": false
         }
       }
     }
   }

3. 修改 plugins/core/manager.py，在 load_plugin 中添加 native 分支:
   elif plugin_name == "native":
       from plugins.adapters.native.adapter import NativeAdapter
       adapter = NativeAdapter()

4. 重启 LCA 即可使用

配置说明:
- use_human_like_mouse: 是否使用贝塞尔曲线模拟人类鼠标移动轨迹
- ocr_language: OCR语言，"ch"=中英文，"en"=英文
- gpu: 是否使用 GPU 加速 OCR（需要安装 paddlepaddle-gpu）
"""
