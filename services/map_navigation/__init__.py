"""地图导航主实现命名空间。

新代码请优先从本包导入；旧路径仅保留兼容转发层。
"""

from services.map_navigation.subprocess_client import (
    cleanup_map_navigation_subprocesses,
    launch_map_navigation_subprocess,
)
from services.map_navigation.subprocess_protocol import (
    MAP_NAVIGATION_CARD_REQUEST_SOURCE,
    MAP_NAVIGATION_SUBPROCESS_EXE_NAME,
    MAP_NAVIGATION_SUBPROCESS_FLAG,
    MAP_NAVIGATION_SUBPROCESS_RELATIVE_DIR,
    MAP_NAVIGATION_SUBPROCESS_STANDALONE_FLAG,
    build_map_navigation_card_launch_context,
    cleanup_map_navigation_subprocess_files,
    create_map_navigation_subprocess_io_paths,
    get_map_navigation_subprocess_runtime_dir,
    normalize_map_navigation_subprocess_request,
    read_map_navigation_subprocess_json,
    write_map_navigation_subprocess_json,
)

__all__ = [
    "MAP_NAVIGATION_CARD_REQUEST_SOURCE",
    "MAP_NAVIGATION_SUBPROCESS_EXE_NAME",
    "MAP_NAVIGATION_SUBPROCESS_FLAG",
    "MAP_NAVIGATION_SUBPROCESS_RELATIVE_DIR",
    "MAP_NAVIGATION_SUBPROCESS_STANDALONE_FLAG",
    "build_map_navigation_card_launch_context",
    "cleanup_map_navigation_subprocess_files",
    "cleanup_map_navigation_subprocesses",
    "create_map_navigation_subprocess_io_paths",
    "get_map_navigation_subprocess_runtime_dir",
    "launch_map_navigation_subprocess",
    "normalize_map_navigation_subprocess_request",
    "read_map_navigation_subprocess_json",
    "write_map_navigation_subprocess_json",
]
