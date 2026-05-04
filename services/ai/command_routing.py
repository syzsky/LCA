# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any


_DIRECT_TOOL_MARKERS = (
    "截图",
    "当前截图",
    "屏幕",
    "界面",
    "窗口",
    "页面",
    "画面",
    "坐标",
    "bbox",
    "ocr",
    "识别",
    "客户区",
    "屏幕坐标",
    "窗口坐标",
    "鼠标",
    "键盘",
)

_TOOL_ACTION_MARKERS = (
    "点击",
    "双击",
    "右击",
    "左击",
    "点开",
    "选中",
    "选择",
    "切换",
    "打开",
    "关闭",
    "进入",
    "前往",
    "拖拽",
    "滚动",
    "滑动",
    "输入",
    "键入",
    "填写",
    "发送",
    "回复",
    "粘贴",
    "复制",
    "按下",
    "按键",
    "回车",
    "tab",
)

_TOOL_TARGET_MARKERS = (
    "按钮",
    "输入框",
    "文本框",
    "编辑框",
    "消息框",
    "回复框",
    "聊天",
    "群聊",
    "消息",
    "会话",
    "标签",
    "页签",
    "浏览器",
    "地址栏",
    "搜索栏",
    "网址栏",
    "菜单",
    "列表",
    "网格",
    "图标",
    "弹窗",
    "网页",
    "网站",
    "官网",
)

_TOOL_CONTEXT_MARKERS = (
    "继续执行",
    "继续刚才",
    "根据当前截图",
    "结合当前截图",
    "下一步",
    "重试",
    "重新点击",
)


def normalize_text_for_match(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"\s+", "", text)


def command_requires_tool_execution(command_text: Any) -> bool:
    raw_text = str(command_text or "").strip()
    normalized = normalize_text_for_match(raw_text)
    if not normalized:
        return False

    if any(marker in normalized for marker in _TOOL_CONTEXT_MARKERS):
        return True
    if any(marker in normalized for marker in _DIRECT_TOOL_MARKERS):
        return True

    has_action = any(marker in normalized for marker in _TOOL_ACTION_MARKERS)
    has_target = any(marker in normalized for marker in _TOOL_TARGET_MARKERS)
    if has_action and has_target:
        return True

    if re.search(r"(?i)\b(?:https?://|www\.)", raw_text):
        return True

    return False
