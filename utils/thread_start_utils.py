#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, MutableMapping


THREAD_START_TASK_TYPE = "线程起点"
LEGACY_START_TASK_TYPE = "起点"


def normalize_thread_start_task_type(task_type: Any) -> str:
    text = str(task_type or "").strip()
    if text == LEGACY_START_TASK_TYPE:
        return THREAD_START_TASK_TYPE
    return text


def is_thread_start_task_type(task_type: Any) -> bool:
    return str(task_type or "").strip() == THREAD_START_TASK_TYPE


def normalize_card_task_type(card: Any) -> Any:
    if isinstance(card, MutableMapping):
        normalized = normalize_thread_start_task_type(card.get("task_type"))
        if normalized:
            card["task_type"] = normalized
        return card

    if hasattr(card, "task_type"):
        normalized = normalize_thread_start_task_type(getattr(card, "task_type", ""))
        if normalized:
            setattr(card, "task_type", normalized)
    return card
