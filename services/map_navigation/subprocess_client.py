from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from services.map_navigation.subprocess_protocol import (
    MAP_NAVIGATION_SUBPROCESS_EXE_NAME,
    MAP_NAVIGATION_SUBPROCESS_FLAG,
    MAP_NAVIGATION_SUBPROCESS_RELATIVE_DIR,
    MAP_NAVIGATION_SUBPROCESS_STANDALONE_FLAG,
    cleanup_map_navigation_subprocess_files,
    create_map_navigation_subprocess_io_paths,
    normalize_map_navigation_subprocess_request,
    read_map_navigation_subprocess_json,
    write_map_navigation_subprocess_json,
)
from services.worker_process_cleanup import cleanup_worker_processes
from utils.worker_entry import (
    build_worker_launch_command,
    build_worker_process_env,
    is_packaged_runtime,
    resolve_main_executable,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ACTIVE_LOCK = threading.RLock()


@dataclass
class MapNavigationSubprocessHandle:
    process: subprocess.Popen
    workflow_id: str
    card_id: int
    target_hwnd: int
    input_path: str
    output_path: str
    auto_close_on_workflow_finish: bool
    started_at: float

    @property
    def process_key(self) -> str:
        return os.path.basename(self.input_path)

    @property
    def pid(self) -> int:
        try:
            return int(self.process.pid or 0)
        except Exception:
            return 0


def _store_subprocess_payload(handle: MapNavigationSubprocessHandle, response: Dict[str, Any]) -> None:
    if handle.card_id <= 0:
        return
    payload = response.get("payload")
    if not isinstance(payload, dict):
        return
    payload_to_store = dict(payload)
    payload_to_store.setdefault("subprocess_pid", handle.pid)
    payload_to_store.setdefault("subprocess_key", handle.process_key)
    payload_to_store.setdefault("launched", True)
    try:
        from task_workflow.workflow_context import get_workflow_context

        context = get_workflow_context(handle.workflow_id)
        setter = getattr(context, "set_map_navigation_result", None)
        if callable(setter):
            setter(handle.card_id, payload_to_store)
    except Exception as exc:
        logger.debug("同步地图导航子程序结果失败: %s", exc)


_ACTIVE_PROCESSES: Dict[str, MapNavigationSubprocessHandle] = {}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "y", "on", "\u5f00\u542f", "\u662f"}:
        return True
    if text in {"0", "false", "no", "n", "off", "\u5173\u95ed", "\u5426"}:
        return False
    return bool(default)


def _resolve_map_navigation_subprocess_executable() -> Optional[str]:
    base_dirs: list[str] = []

    def _append_base_dir(path_text: str) -> None:
        normalized = os.path.abspath(str(path_text or "").strip())
        if not normalized or not os.path.isdir(normalized):
            return
        if normalized not in base_dirs:
            base_dirs.append(normalized)

    main_executable = resolve_main_executable()
    if main_executable:
        _append_base_dir(os.path.dirname(main_executable))
    _append_base_dir(os.path.dirname(str(sys.executable or "").strip()))
    if sys.argv:
        _append_base_dir(os.path.dirname(os.path.abspath(str(sys.argv[0] or "").strip())))

    for base_dir in base_dirs:
        candidate = os.path.join(
            base_dir,
            MAP_NAVIGATION_SUBPROCESS_RELATIVE_DIR,
            MAP_NAVIGATION_SUBPROCESS_EXE_NAME,
        )
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


def _build_map_navigation_subprocess_command(input_path: str, output_path: str) -> list[str]:
    if is_packaged_runtime():
        return build_worker_launch_command(
            worker_flag=MAP_NAVIGATION_SUBPROCESS_FLAG,
            module_name="services.map_navigation.subprocess_runner",
            standalone_flag=MAP_NAVIGATION_SUBPROCESS_STANDALONE_FLAG,
            extra_args=["--input", input_path, "--output", output_path],
            allow_main_script=False,
            require_python_executable=False,
            project_root=_PROJECT_ROOT,
        )

    dedicated_exe = _resolve_map_navigation_subprocess_executable()
    if dedicated_exe:
        return [
            dedicated_exe,
            MAP_NAVIGATION_SUBPROCESS_STANDALONE_FLAG,
            "--input",
            input_path,
            "--output",
            output_path,
        ]
    return build_worker_launch_command(
        worker_flag=MAP_NAVIGATION_SUBPROCESS_FLAG,
            module_name="services.map_navigation.subprocess_runner",
        standalone_flag=MAP_NAVIGATION_SUBPROCESS_STANDALONE_FLAG,
        extra_args=["--input", input_path, "--output", output_path],
        allow_main_script=False,
        require_python_executable=True,
        project_root=_PROJECT_ROOT,
    )


def _register_process(handle: MapNavigationSubprocessHandle) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_PROCESSES[handle.process_key] = handle
    logger.info(
        "[地图导航子进程] 已注册: workflow=%s card=%s hwnd=%s pid=%s key=%s auto_close=%s",
        handle.workflow_id,
        handle.card_id,
        handle.target_hwnd,
        handle.pid,
        handle.process_key,
        bool(handle.auto_close_on_workflow_finish),
    )


def _pop_process(process_key: str) -> Optional[MapNavigationSubprocessHandle]:
    with _ACTIVE_LOCK:
        return _ACTIVE_PROCESSES.pop(process_key, None)


def _cleanup_process_files(handle: Optional[MapNavigationSubprocessHandle]) -> None:
    if handle is None:
        return
    logger.info(
        "[地图导航子进程] 清理 IO 文件: key=%s input=%s output=%s",
        handle.process_key,
        handle.input_path,
        handle.output_path,
    )
    cleanup_map_navigation_subprocess_files(handle.input_path, handle.output_path)


def _terminate_process(process: Optional[subprocess.Popen]) -> bool:
    if process is None:
        return False

    pid = 0
    try:
        pid = int(process.pid or 0)
    except Exception:
        pid = 0

    try:
        if process.poll() is not None:
            logger.info("[地图导航子进程] 进程已退出，无需终止: pid=%s", pid)
            return True
    except Exception:
        return True

    if os.name == "nt" and pid > 0:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
                check=False,
            )
            logger.info("[地图导航子进程] 已通过 taskkill 终止: pid=%s", pid)
            return True
        except Exception:
            pass

    try:
        process.terminate()
        process.wait(timeout=1.0)
        logger.info("[地图导航子进程] 已通过 terminate 终止: pid=%s", pid)
        return True
    except Exception:
        pass

    try:
        process.kill()
        process.wait(timeout=0.5)
        logger.info("[地图导航子进程] 已通过 kill 终止: pid=%s", pid)
        return True
    except Exception:
        logger.warning("[地图导航子进程] 终止失败: pid=%s", pid)
        return False


def _start_process_watcher(handle: MapNavigationSubprocessHandle) -> None:
    def _watch() -> None:
        try:
            handle.process.wait()
        except Exception:
            pass
        finally:
            logger.info(
                "[地图导航子进程] watcher 检测到退出: pid=%s code=%s key=%s",
                handle.pid,
                handle.process.poll(),
                handle.process_key,
            )
            popped = _pop_process(handle.process_key)
            _cleanup_process_files(popped)

    thread = threading.Thread(
        target=_watch,
        daemon=True,
        name=f"MapNavigationWorkerWatch-{handle.pid or handle.process_key}",
    )
    thread.start()


def _start_output_watcher(handle: MapNavigationSubprocessHandle) -> None:
    def _watch_output() -> None:
        last_mtime = -1.0
        while True:
            try:
                if os.path.exists(handle.output_path):
                    current_mtime = float(os.path.getmtime(handle.output_path))
                    if current_mtime != last_mtime:
                        response = read_map_navigation_subprocess_json(handle.output_path)
                        _store_subprocess_payload(handle, response)
                        last_mtime = current_mtime
            except Exception as exc:
                logger.debug("读取地图导航子程序输出失败: %s", exc)

            try:
                if handle.process.poll() is not None:
                    if os.path.exists(handle.output_path):
                        try:
                            response = read_map_navigation_subprocess_json(handle.output_path)
                            _store_subprocess_payload(handle, response)
                            logger.info(
                                "[地图导航子进程] 最终输出: pid=%s key=%s success=%s detail=%s",
                                handle.pid,
                                handle.process_key,
                                bool(response.get("success")),
                                str(response.get("detail", "") or "").strip(),
                            )
                        except Exception:
                            pass
                    break
            except Exception:
                break

            time.sleep(0.2)

    thread = threading.Thread(
        target=_watch_output,
        daemon=True,
        name=f"MapNavigationWorkerOutput-{handle.pid or handle.process_key}",
    )
    thread.start()
    logger.info("[地图导航子进程] 输出 watcher 已启动: pid=%s key=%s", handle.pid, handle.process_key)


def _matches_handle(
    handle: MapNavigationSubprocessHandle,
    *,
    workflow_id: Optional[str] = None,
    card_id: Optional[int] = None,
    target_hwnd: Optional[int] = None,
    auto_close_only: bool = False,
) -> bool:
    if auto_close_only and not handle.auto_close_on_workflow_finish:
        return False
    if workflow_id is not None and handle.workflow_id != str(workflow_id or "").strip():
        return False
    if card_id is not None and handle.card_id != int(card_id):
        return False
    if target_hwnd is not None and handle.target_hwnd != int(target_hwnd):
        return False
    return True


def _collect_matching_handles(
    *,
    workflow_id: Optional[str] = None,
    card_id: Optional[int] = None,
    target_hwnd: Optional[int] = None,
    auto_close_only: bool = False,
) -> list[MapNavigationSubprocessHandle]:
    matched: list[MapNavigationSubprocessHandle] = []
    with _ACTIVE_LOCK:
        for process_key, handle in list(_ACTIVE_PROCESSES.items()):
            if not _matches_handle(
                handle,
                workflow_id=workflow_id,
                card_id=card_id,
                target_hwnd=target_hwnd,
                auto_close_only=auto_close_only,
            ):
                continue
            matched.append(_ACTIVE_PROCESSES.pop(process_key))
    return matched


def _cleanup_handles(handles: list[MapNavigationSubprocessHandle]) -> int:
    cleaned = 0
    for handle in handles:
        if _terminate_process(handle.process):
            cleaned += 1
        _cleanup_process_files(handle)
    return cleaned


def _collect_stale_handles_locked() -> list[MapNavigationSubprocessHandle]:
    stale_handles: list[MapNavigationSubprocessHandle] = []
    for process_key, handle in list(_ACTIVE_PROCESSES.items()):
        is_running = False
        try:
            is_running = handle.process.poll() is None
        except Exception:
            is_running = False
        if is_running:
            continue
        stale_handles.append(_ACTIVE_PROCESSES.pop(process_key))
    return stale_handles


def _is_duplicate_launch(
    handle: MapNavigationSubprocessHandle,
    *,
    workflow_id: str,
    card_id: int,
    target_hwnd: int,
) -> bool:
    same_card = card_id > 0 and handle.workflow_id == workflow_id and handle.card_id == card_id
    same_window = target_hwnd > 0 and handle.target_hwnd == target_hwnd
    return bool(same_card or same_window)


def _find_conflicting_handle(
    *,
    workflow_id: str,
    card_id: int,
    target_hwnd: int,
) -> Optional[MapNavigationSubprocessHandle]:
    stale_handles: list[MapNavigationSubprocessHandle] = []
    conflict: Optional[MapNavigationSubprocessHandle] = None
    with _ACTIVE_LOCK:
        stale_handles = _collect_stale_handles_locked()
        for handle in _ACTIVE_PROCESSES.values():
            if _is_duplicate_launch(
                handle,
                workflow_id=workflow_id,
                card_id=card_id,
                target_hwnd=target_hwnd,
            ):
                conflict = handle
                break
    for stale_handle in stale_handles:
        _cleanup_process_files(stale_handle)
    return conflict


def _build_duplicate_launch_detail(
    handle: MapNavigationSubprocessHandle,
    *,
    workflow_id: str,
    card_id: int,
    target_hwnd: int,
) -> str:
    reasons: list[str] = []
    if card_id > 0 and handle.workflow_id == workflow_id and handle.card_id == card_id:
        reasons.append(f"工作流 {workflow_id} 的卡片 {card_id}")
    if target_hwnd > 0 and handle.target_hwnd == target_hwnd:
        reasons.append(f"窗口 {target_hwnd}")
    reason_text = "、".join(reasons) or "当前请求"
    return (
        f"地图导航子程序已在运行，禁止重复启动: {reason_text}"
        f" (pid={handle.pid}, key={handle.process_key})"
    )


def _launch_map_navigation_subprocess_legacy(request_payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(request_payload, dict):
        raise ValueError("\u5730\u56fe\u5bfc\u822a\u5b50\u7a0b\u5e8f\u8bf7\u6c42\u65e0\u6548")

    workflow_id = str(request_payload.get("workflow_id", "") or "").strip() or "default"
    card_id = int(request_payload.get("card_id", 0) or 0)
    target_hwnd = int(request_payload.get("target_hwnd", 0) or 0)
    params = request_payload.get("params")
    if not isinstance(params, dict):
        raise ValueError("\u5730\u56fe\u5bfc\u822a\u5b50\u7a0b\u5e8f\u53c2\u6570\u65e0\u6548")
    logger.info(
        "[地图导航子进程] 收到启动请求: workflow=%s card=%s hwnd=%s bundle=%s",
        workflow_id,
        card_id,
        target_hwnd,
        str(params.get("bundle_path", "") or "").strip(),
    )

    # 同一工作流/卡片/窗口只保留一个地图导航子程序，重复触发时重建。
    replaced_count = _cleanup_handles(
        _collect_matching_handles(
            workflow_id=workflow_id,
            card_id=card_id,
            target_hwnd=target_hwnd,
        )
    )
    if replaced_count:
        logger.info("[地图导航子进程] 已清理旧实例: count=%s", replaced_count)

    input_path, output_path = create_map_navigation_subprocess_io_paths(workflow_id, card_id)
    process: Optional[subprocess.Popen] = None
    handle: Optional[MapNavigationSubprocessHandle] = None

    try:
        write_map_navigation_subprocess_json(input_path, request_payload)
        command = _build_map_navigation_subprocess_command(input_path, output_path)
        child_env = build_worker_process_env(project_root=_PROJECT_ROOT)
        logger.info(
            "[地图导航子进程] 准备启动: input=%s output=%s command=%s",
            input_path,
            output_path,
            command,
        )

        creation_flags = 0
        startupinfo = None
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=_PROJECT_ROOT,
            env=child_env,
            creationflags=creation_flags,
            startupinfo=startupinfo,
        )

        handle = MapNavigationSubprocessHandle(
            process=process,
            workflow_id=workflow_id,
            card_id=card_id,
            target_hwnd=target_hwnd,
            input_path=input_path,
            output_path=output_path,
            auto_close_on_workflow_finish=_coerce_bool(
                params.get("close_on_workflow_finish", False),
                False,
            ),
            started_at=time.monotonic(),
        )
        _register_process(handle)
        _start_process_watcher(handle)
        _start_output_watcher(handle)
        logger.info(
            "[地图导航子进程] 启动成功: pid=%s key=%s input=%s output=%s",
            handle.pid,
            handle.process_key,
            input_path,
            output_path,
        )
        return {
            "success": True,
            "workflow_id": workflow_id,
            "card_id": card_id,
            "target_hwnd": target_hwnd,
            "pid": handle.pid,
            "process_key": handle.process_key,
            "input_path": input_path,
            "output_path": output_path,
            "auto_close_on_workflow_finish": handle.auto_close_on_workflow_finish,
            "replaced_count": replaced_count,
        }
    except Exception:
        logger.exception("[地图导航子进程] 启动失败")
        if process is not None:
            _terminate_process(process)
        if handle is not None:
            _pop_process(handle.process_key)
            _cleanup_process_files(handle)
        else:
            cleanup_map_navigation_subprocess_files(input_path, output_path)
        raise


def launch_map_navigation_subprocess(request_payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized_request = normalize_map_navigation_subprocess_request(
        request_payload,
        require_card_origin=True,
    )
    workflow_id = str(normalized_request["workflow_id"])
    card_id = int(normalized_request["card_id"])
    target_hwnd = int(normalized_request["target_hwnd"])
    params = dict(normalized_request["params"])
    logger.info(
        "[鍦板浘瀵艰埅瀛愯繘绋媇 鏀跺埌鍚姩璇锋眰: workflow=%s card=%s hwnd=%s bundle=%s",
        workflow_id,
        card_id,
        target_hwnd,
        str(params.get("bundle_path", "") or "").strip(),
    )

    conflict = _find_conflicting_handle(
        workflow_id=workflow_id,
        card_id=card_id,
        target_hwnd=target_hwnd,
    )
    if conflict is not None:
        detail = _build_duplicate_launch_detail(
            conflict,
            workflow_id=workflow_id,
            card_id=card_id,
            target_hwnd=target_hwnd,
        )
        logger.warning("[鍦板浘瀵艰埅瀛愯繘绋媇 鎷掔粷閲嶅鍚姩: %s", detail)
        raise RuntimeError(detail)

    input_path, output_path = create_map_navigation_subprocess_io_paths(workflow_id, card_id)
    process: Optional[subprocess.Popen] = None
    handle: Optional[MapNavigationSubprocessHandle] = None
    replaced_count = 0

    try:
        write_map_navigation_subprocess_json(input_path, normalized_request)
        command = _build_map_navigation_subprocess_command(input_path, output_path)
        child_env = build_worker_process_env(project_root=_PROJECT_ROOT)
        logger.info(
            "[鍦板浘瀵艰埅瀛愯繘绋媇 鍑嗗鍚姩: input=%s output=%s command=%s",
            input_path,
            output_path,
            command,
        )

        creation_flags = 0
        startupinfo = None
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=_PROJECT_ROOT,
            env=child_env,
            creationflags=creation_flags,
            startupinfo=startupinfo,
        )

        handle = MapNavigationSubprocessHandle(
            process=process,
            workflow_id=workflow_id,
            card_id=card_id,
            target_hwnd=target_hwnd,
            input_path=input_path,
            output_path=output_path,
            auto_close_on_workflow_finish=_coerce_bool(
                params.get("close_on_workflow_finish", False),
                False,
            ),
            started_at=time.monotonic(),
        )
        _register_process(handle)
        _start_process_watcher(handle)
        _start_output_watcher(handle)
        logger.info(
            "[鍦板浘瀵艰埅瀛愯繘绋媇 鍚姩鎴愬姛: pid=%s key=%s input=%s output=%s",
            handle.pid,
            handle.process_key,
            input_path,
            output_path,
        )
        return {
            "success": True,
            "workflow_id": workflow_id,
            "card_id": card_id,
            "target_hwnd": target_hwnd,
            "pid": handle.pid,
            "process_key": handle.process_key,
            "input_path": input_path,
            "output_path": output_path,
            "auto_close_on_workflow_finish": handle.auto_close_on_workflow_finish,
            "replaced_count": replaced_count,
        }
    except Exception:
        logger.exception("[鍦板浘瀵艰埅瀛愯繘绋媇 鍚姩澶辫触")
        if process is not None:
            _terminate_process(process)
        if handle is not None:
            _pop_process(handle.process_key)
            _cleanup_process_files(handle)
        else:
            cleanup_map_navigation_subprocess_files(input_path, output_path)
        raise


def cleanup_map_navigation_subprocesses(
    main_pid: Optional[int] = None,
    *,
    workflow_id: Optional[str] = None,
    card_id: Optional[int] = None,
    target_hwnd: Optional[int] = None,
    auto_close_only: bool = False,
    include_orphans: bool = True,
) -> int:
    logger.info(
        "[地图导航子进程] 请求清理: workflow=%s card=%s hwnd=%s auto_close_only=%s include_orphans=%s",
        workflow_id,
        card_id,
        target_hwnd,
        bool(auto_close_only),
        bool(include_orphans),
    )
    cleaned = _cleanup_handles(
        _collect_matching_handles(
            workflow_id=workflow_id,
            card_id=card_id,
            target_hwnd=target_hwnd,
            auto_close_only=auto_close_only,
        )
    )

    should_cleanup_orphans = bool(
        include_orphans
        and workflow_id is None
        and card_id is None
        and target_hwnd is None
        and not auto_close_only
    )
    if not should_cleanup_orphans:
        return cleaned

    try:
        cleaned += cleanup_worker_processes(
            worker_flags=(
                MAP_NAVIGATION_SUBPROCESS_FLAG,
                MAP_NAVIGATION_SUBPROCESS_STANDALONE_FLAG,
                MAP_NAVIGATION_SUBPROCESS_EXE_NAME,
            ),
            project_root=_PROJECT_ROOT,
            main_pid=main_pid,
        )
    except Exception as exc:
        logger.debug("\u6e05\u7406\u5730\u56fe\u5bfc\u822a\u5b50\u7a0b\u5e8f\u5931\u8d25: %s", exc)

    logger.info("[地图导航子进程] 清理完成: cleaned=%s", cleaned)
    return cleaned
