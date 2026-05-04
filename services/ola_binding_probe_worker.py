# -*- coding: utf-8 -*-
"""OLA 绑定探测 worker。"""

from __future__ import annotations

import argparse
import socket
from typing import Any, Dict, Optional

from services.socket_message_utils import recv_message as recv_socket_message
from services.socket_message_utils import send_message as send_socket_message


def _send_message(sock: socket.socket, data: Dict[str, Any]) -> bool:
    return send_socket_message(sock, data)


def _recv_message(sock: socket.socket, timeout: float) -> Optional[Dict[str, Any]]:
    return recv_socket_message(sock=sock, timeout=timeout)


class OLABindingProbeWorker:
    def __init__(self, process_id: str):
        self.process_id = str(process_id or "ola_bind_probe_worker")

    @staticmethod
    def _handle_probe(request: Dict[str, Any]) -> Dict[str, Any]:
        try:
            hwnd = int(request.get("hwnd") or 0)
        except Exception:
            hwnd = 0
        config = request.get("config")
        if not isinstance(config, dict):
            config = {}

        if hwnd <= 0:
            return {"type": "probe", "success": False, "error": "invalid_hwnd"}

        from plugins.adapters.ola.multi_instance_manager import OLAMultiInstanceManager

        manager = OLAMultiInstanceManager()
        try:
            bind_success = bool(manager.probe_window_binding(hwnd, config))
        finally:
            try:
                manager.release_all()
            except Exception:
                pass

        return {
            "type": "probe",
            "success": True,
            "bind_success": bind_success,
        }

    def run(self, port: int) -> None:
        conn = None
        try:
            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn.settimeout(10.0)
            conn.connect(("127.0.0.1", int(port)))
            conn.settimeout(None)

            if not _send_message(conn, {"type": "ready", "success": True, "process_id": self.process_id}):
                return

            request = _recv_message(conn, timeout=30.0)
            if request is None:
                return

            command = str(request.get("command") or "").strip().upper()
            if command != "PROBE":
                _send_message(conn, {"type": "error", "success": False, "error": "unknown_command"})
                return

            _send_message(conn, self._handle_probe(request))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def run_ola_binding_probe_worker_standalone(process_id: str, port: int) -> None:
    worker = OLABindingProbeWorker(process_id=process_id)
    worker.run(port)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OLA binding probe worker")
    parser.add_argument("--ola-bind-probe-worker-standalone", action="store_true")
    parser.add_argument("--process-id", type=str, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    run_ola_binding_probe_worker_standalone(args.process_id, args.port)
