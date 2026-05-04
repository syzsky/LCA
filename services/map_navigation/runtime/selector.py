# -*- coding: utf-8 -*-
from __future__ import annotations

from services.map_navigation.runtime.qt_ui import run_selector_main


def main() -> int:
    return int(run_selector_main())


if __name__ == "__main__":
    raise SystemExit(main())
