# -*- coding: utf-8 -*-
from __future__ import annotations

from services.map_navigation.runtime.qt_ui import main, run_bootstrapper, run_selector_if_needed

__all__ = [
    "main",
    "run_bootstrapper",
    "run_selector_if_needed",
]


if __name__ == "__main__":
    raise SystemExit(main())
