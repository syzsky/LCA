# -*- coding: utf-8 -*-
"""OLA 绑定探测子进程调度。"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
import time
from typing import Any, Dict, Optional

from services.socket_message_utils import recv_message as recv_socket_message
from services.socket_message_utils import send_message as send_socket_message
from utils.worker_entry import build_worker_launch_command, build_worker_process_env

logger = logging.getLogger(__name__)


def _read_float_env(name: str, default: float, min_value: float, max_value: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except Exception:
        return default
    return max(min_value, min(max_value, value))


_WORKER_CONNECT_TIMEOUT = _read_float_env("OLA_BIND_PROBE_CONNECT_TIMEOUT_SEC", 4.0, 0.2, 30.0)
_WORKER_READY_TIMEOUT = _read_float_env("OLA_BIND_PROBE_READY_TIMEOUT_SEC", 8.0, 0.2, 30.0)
_PROBE_TIMEOUT = _read_float_env("OLA_BIND_PROBE_TIMEOUT_SEC", 1.5, 0.1, 10.0)


def _send_message(sock: socket.socket, data: Dict[str, Any]) -> bool:
    return send_socket_message(sock, data)


def _recv_message(sock: socket.socket, timeout: float) -> Optional[Dict[str, Any]]:
    return recv_socket_message(sock=sock, timeout=timeout)


def _build_worker_command(process_id: str, port: int) -> list:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return build_worker_launch_command(
        worker_flag="--ola-bind-probe-worker",
        module_name="services.ola_binding_probe_worker",
        standalone_flag="--ola-bind-probe-worker-standalone",
        extra_args=["--process-id", process_id, "--port", str(port)],
        allow_main_script=True,
        require_python_executable=True,
        project_root=project_root,
    )


def _terminate_process(process: Optional[subprocess.Popen]) -> None:
    if process is None:
        return

    pid = None
    try:
        pid = int(process.pid)
    except Exception:
        pid = None

    if os.name == "nt" and pid:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
                check=False,
            )
            return
        except Exception:
            pass

    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.8)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=0.3)
                except Exception:
                    pass
    except Exception:
        pass


def probe_ola_window_binding(hwnd: int, config: Optional[dict] = None, timeout: Optional[float] = None) -> bool:
    """在独立子进程中探测 OLA 绑定，避免主进程被单次 BindWindow 阻塞。"""
    try:
        hwnd = int(hwnd or 0)
    except Exception:
        hwnd = 0
    if hwnd <= 0:
        return False

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    process_id = f"ola_bind_probe_{time.time_ns()}_{threading.get_ident()}"
    request = {
        "command": "PROBE",
        "hwnd": hwnd,
        "config": dict(config or {}),
    }
    probe_timeout = max(0.1, float(timeout if timeout is not None else _PROBE_TIMEOUT))

    server_socket = None
    conn = None
    process = None
    try:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(("127.0.0.1", 0))
        server_socket.listen(1)
        port = int(server_socket.getsockname()[1])

        child_env = build_worker_process_env(project_root=project_root)
        cmd = _build_worker_command(process_id, port)

        creation_flags = 0
        startupinfo = None
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=project_root,
            env=child_env,
            creationflags=creation_flags,
            startupinfo=startupinfo,
        )

        server_socket.settimeout(_WORKER_CONNECT_TIMEOUT)
        conn, _ = server_socket.accept()

        ready = _recv_message(conn, timeout=_WORKER_READY_TIMEOUT)
        if not ready or not ready.get("success"):
            return False

        if not _send_message(conn, request):
            return False

        response = _recv_message(conn, timeout=probe_timeout)
        if not response or not response.get("success"):
            return False

        return bool(response.get("bind_success", False))
    except Exception as exc:
        logger.warning("OLA 绑定探测子进程异常: hwnd=%s, error=%s", hwnd, exc)
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if server_socket is not None:
            try:
                server_socket.close()
            except Exception:
                pass
        _terminate_process(process)
