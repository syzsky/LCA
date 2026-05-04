"""
更新器子进程与主进程通信逻辑。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

import requests
from packaging import version

try:
    from app_core.app_config import (
        APP_NAME,
        APP_VERSION,
        INSTALLER_URL_TEMPLATE,
        MANIFEST_URL,
        VERIFY_HASH,
        VERIFY_SIZE,
    )
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app_core.app_config import (
        APP_NAME,
        APP_VERSION,
        INSTALLER_URL_TEMPLATE,
        MANIFEST_URL,
        VERIFY_HASH,
        VERIFY_SIZE,
    )


logger = logging.getLogger(__name__)
UPDATER_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"

UPDATE_STATUS_IDLE = "idle"
UPDATE_STATUS_CHECKING = "checking"
UPDATE_STATUS_DOWNLOADING = "downloading"
UPDATE_STATUS_READY = "ready"
UPDATE_STATUS_INSTALLING = "installing"
UPDATE_STATUS_ERROR = "error"

IPC_DIR = Path(tempfile.gettempdir()) / APP_NAME
IPC_STATUS_FILE = IPC_DIR / "update_status.json"
IPC_COMMAND_FILE = IPC_DIR / "update_command.json"
IPC_LOCK = threading.Lock()


def _json_dump_atomic(path: Path, payload: Dict) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False)
    os.replace(temp_path, path)


def _creation_flag(name: str) -> int:
    return int(getattr(subprocess, name, 0) or 0)


class UpdaterIPC:
    """更新器进程间通信。"""

    @staticmethod
    def ensure_dir() -> None:
        IPC_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def write_status(status: str, data: Optional[Dict] = None) -> None:
        UpdaterIPC.ensure_dir()
        payload = {
            "status": status,
            "timestamp": time.time(),
            "data": data or {},
        }
        try:
            with IPC_LOCK:
                _json_dump_atomic(IPC_STATUS_FILE, payload)
        except Exception as exc:
            logger.error(f"写入更新状态失败: {exc}")

    @staticmethod
    def read_status() -> Optional[Dict]:
        try:
            with IPC_LOCK:
                if not IPC_STATUS_FILE.exists():
                    return None
                with open(IPC_STATUS_FILE, "r", encoding="utf-8") as file_obj:
                    return json.load(file_obj)
        except Exception as exc:
            logger.debug(f"读取更新状态失败: {exc}")
            return None

    @staticmethod
    def write_command(command: str, data: Optional[Dict] = None) -> None:
        UpdaterIPC.ensure_dir()
        payload = {
            "command": str(command or "").strip(),
            "timestamp": time.time(),
            "data": data or {},
        }
        try:
            with IPC_LOCK:
                _json_dump_atomic(IPC_COMMAND_FILE, payload)
        except Exception as exc:
            logger.error(f"写入更新命令失败: {exc}")

    @staticmethod
    def read_command() -> Optional[Dict]:
        try:
            with IPC_LOCK:
                if not IPC_COMMAND_FILE.exists():
                    return None
                with open(IPC_COMMAND_FILE, "r", encoding="utf-8") as file_obj:
                    payload = json.load(file_obj)
                IPC_COMMAND_FILE.unlink(missing_ok=True)
                return payload
        except Exception as exc:
            logger.error(f"读取更新命令失败: {exc}")
            return None

    @staticmethod
    def cleanup() -> None:
        for path in (IPC_STATUS_FILE, IPC_COMMAND_FILE):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                continue


class SimpleUpdater:
    """更新器核心逻辑。"""

    def __init__(self, progress_callback: Optional[Callable[[int, int], None]] = None):
        self.progress_callback = progress_callback
        self.latest_version: Optional[str] = None
        self.changelog: Optional[list] = None
        self.expected_size: Optional[int] = None
        self.expected_hash: Optional[str] = None
        self.download_path: Optional[Path] = None
        self._cancel_download = False

    def check_for_updates(self) -> tuple[bool, Optional[Dict]]:
        try:
            logger.info(f"检查更新，当前版本: {APP_VERSION}")
            response = requests.get(MANIFEST_URL, timeout=10)
            response.raise_for_status()
            manifest = response.json()
        except requests.RequestException as exc:
            logger.error(f"检查更新失败(网络): {exc}")
            return False, None
        except Exception as exc:
            logger.error(f"检查更新失败: {exc}")
            return False, None

        remote_version = str(manifest.get("version", "") or "").strip()
        if not remote_version:
            return False, None

        try:
            current_ver = version.parse(APP_VERSION)
            remote_ver = version.parse(remote_version)
        except Exception as exc:
            logger.error(f"版本号解析失败: {exc}")
            return False, None

        if remote_ver <= current_ver:
            return False, None

        self.latest_version = remote_version
        self.changelog = list(manifest.get("changelog", []) or [])
        self.expected_size = int(manifest.get("file_size", 0) or 0)
        self.expected_hash = str(manifest.get("sha256", "") or "").strip()

        update_info = {
            "current_version": APP_VERSION,
            "new_version": remote_version,
            "changelog": self.changelog,
            "file_size": self.expected_size,
            "sha256": self.expected_hash,
        }
        logger.info(f"发现新版本: {remote_version}")
        return True, update_info

    def _calculate_sha256(self, file_path: Path) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as file_obj:
            for chunk in iter(lambda: file_obj.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def cancel_download(self) -> None:
        self._cancel_download = True

    def download_installer(self) -> bool:
        if not self.latest_version:
            logger.error("请先调用 check_for_updates()")
            return False

        installer_url = INSTALLER_URL_TEMPLATE.format(version=self.latest_version)
        installer_filename = f"{APP_NAME}_Setup_v{self.latest_version}.exe"
        download_dir = Path(tempfile.gettempdir()) / APP_NAME
        download_dir.mkdir(parents=True, exist_ok=True)
        self.download_path = download_dir / installer_filename
        self._cancel_download = False

        try:
            logger.info(f"开始下载: {installer_url}")
            response = requests.get(installer_url, stream=True, timeout=(10, 300))
            response.raise_for_status()

            total_size = int(response.headers.get("content-length", 0) or 0)
            downloaded_size = 0
            with open(self.download_path, "wb") as file_obj:
                for chunk in response.iter_content(chunk_size=8192):
                    if self._cancel_download:
                        logger.info("下载已取消")
                        file_obj.close()
                        self.download_path.unlink(missing_ok=True)
                        return False
                    if not chunk:
                        continue
                    file_obj.write(chunk)
                    downloaded_size += len(chunk)
                    if self.progress_callback and total_size > 0:
                        self.progress_callback(downloaded_size, total_size)

            if VERIFY_SIZE and self.expected_size:
                actual_size = self.download_path.stat().st_size
                if actual_size != self.expected_size:
                    logger.error(f"下载包大小校验失败: 期望 {self.expected_size}, 实际 {actual_size}")
                    self.download_path.unlink(missing_ok=True)
                    return False

            if VERIFY_HASH and self.expected_hash:
                actual_hash = self._calculate_sha256(self.download_path)
                if actual_hash.lower() != self.expected_hash.lower():
                    logger.error("下载包哈希校验失败")
                    self.download_path.unlink(missing_ok=True)
                    return False

            logger.info(f"下载完成: {self.download_path}")
            return True
        except requests.RequestException as exc:
            logger.error(f"下载安装包失败(网络): {exc}")
            return False
        except Exception as exc:
            logger.error(f"下载安装包失败: {exc}")
            return False

    def run_installer(self, silent: bool = False) -> Optional[subprocess.Popen]:
        if not self.download_path or not self.download_path.exists():
            logger.error(f"安装包不存在: {self.download_path}")
            return None

        args = [str(self.download_path)]
        if silent:
            args.extend(["/SILENT", "/SUPPRESSMSGBOXES"])

        try:
            process = subprocess.Popen(args, creationflags=_creation_flag("CREATE_NEW_CONSOLE"))
            logger.info(f"安装程序已启动，PID={process.pid}")
            return process
        except Exception as exc:
            logger.error(f"启动安装程序失败: {exc}")
            return None

    def get_current_version(self) -> str:
        return APP_VERSION


class UpdaterDaemon:
    """更新守护进程。"""

    def __init__(self, check_interval: int = 3600, main_pid: Optional[int] = None):
        self.check_interval = max(60, int(check_interval or 3600))
        self.main_pid = int(main_pid or 0) or None
        self.updater = SimpleUpdater(progress_callback=self._on_progress)
        self._running = True
        self._download_complete = False
        self._update_info: Optional[Dict] = None

    def _is_main_process_alive(self) -> bool:
        if not self.main_pid:
            return True

        if os.name == "nt":
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {self.main_pid}"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    creationflags=_creation_flag("CREATE_NO_WINDOW"),
                )
                return str(self.main_pid) in str(result.stdout or "")
            except Exception:
                return True

        try:
            os.kill(self.main_pid, 0)
            return True
        except OSError:
            return False

    def _on_progress(self, downloaded: int, total: int) -> None:
        if total <= 0:
            return
        UpdaterIPC.write_status(
            UPDATE_STATUS_DOWNLOADING,
            {
                "downloaded": downloaded,
                "total": total,
                "percent": int(downloaded * 100 / total),
            },
        )

    def _handle_install_request(self) -> bool:
        if not self._download_complete or not self.updater.download_path:
            UpdaterIPC.write_status(UPDATE_STATUS_ERROR, {"error": "安装包未就绪"})
            return False

        process = self.updater.run_installer(silent=False)
        if not process:
            UpdaterIPC.write_status(UPDATE_STATUS_ERROR, {"error": "无法启动安装程序"})
            return False

        time.sleep(1)
        if process.poll() is not None:
            UpdaterIPC.write_status(UPDATE_STATUS_ERROR, {"error": "安装程序启动后立即退出"})
            return False

        UpdaterIPC.write_status(UPDATE_STATUS_INSTALLING)
        logger.info("安装程序已启动，更新进程退出")
        UpdaterIPC.cleanup()
        return True

    def _process_command(self) -> bool:
        payload = UpdaterIPC.read_command()
        if not payload:
            return False

        command = str(payload.get("command", "") or "").strip().lower()
        logger.info(f"收到更新命令: {command}")

        if command == "install":
            return self._handle_install_request()
        if command == "cancel":
            self.updater.cancel_download()
            self._download_complete = False
            self._update_info = None
            UpdaterIPC.write_status(UPDATE_STATUS_IDLE)
            return False
        if command == "check_now":
            self._check_and_download()
            return False
        if command == "exit":
            UpdaterIPC.cleanup()
            return True
        return False

    def _check_and_download(self) -> None:
        UpdaterIPC.write_status(UPDATE_STATUS_CHECKING)
        has_update, info = self.updater.check_for_updates()
        if not has_update or not info:
            UpdaterIPC.write_status(UPDATE_STATUS_IDLE)
            return

        self._update_info = info
        UpdaterIPC.write_status(
            UPDATE_STATUS_DOWNLOADING,
            {
                "new_version": info["new_version"],
                "file_size": info.get("file_size", 0),
                "changelog": info.get("changelog", []),
            },
        )

        if not self.updater.download_installer():
            UpdaterIPC.write_status(UPDATE_STATUS_ERROR, {"error": "下载安装包失败"})
            return

        self._download_complete = True
        UpdaterIPC.write_status(
            UPDATE_STATUS_READY,
            {
                "new_version": info["new_version"],
                "changelog": info.get("changelog", []),
                "installer_path": str(self.updater.download_path),
            },
        )
        logger.info("更新包已就绪，等待主进程确认安装")

    def run(self) -> None:
        logger.info(f"更新守护进程启动，PID={os.getpid()}, 主进程PID={self.main_pid}")
        UpdaterIPC.write_status(UPDATE_STATUS_IDLE)

        self._check_and_download()
        last_check_time = time.time()
        last_alive_check = time.time()

        while self._running:
            if self._process_command():
                break

            current_time = time.time()
            if current_time - last_alive_check >= 30:
                last_alive_check = current_time
                if not self._is_main_process_alive():
                    logger.info("主进程已退出，更新进程同步退出")
                    break

            if not self._download_complete and current_time - last_check_time >= self.check_interval:
                last_check_time = current_time
                self._check_and_download()

            time.sleep(1)

        UpdaterIPC.cleanup()
        logger.info("更新守护进程已退出")

    def stop(self) -> None:
        self._running = False


def start_updater_daemon(check_interval: int = 3600, main_pid: Optional[int] = None) -> None:
    daemon = UpdaterDaemon(check_interval=check_interval, main_pid=main_pid)

    if threading.current_thread() is threading.main_thread():
        def signal_handler(signum, frame):
            del signum, frame
            daemon.stop()

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

    daemon.run()


def spawn_updater_process(check_interval: int = 3600) -> Optional[threading.Thread]:
    try:
        main_pid = os.getpid()

        def run_daemon():
            start_updater_daemon(check_interval=check_interval, main_pid=main_pid)

        thread = threading.Thread(target=run_daemon, daemon=True, name="UpdaterDaemonThread")
        thread.start()
        logger.info("更新线程已启动")
        return thread
    except Exception as exc:
        logger.error(f"启动更新线程失败: {exc}")
        return None


def get_update_status() -> Optional[Dict]:
    return UpdaterIPC.read_status()


def send_update_command(command: str, data: Optional[Dict] = None) -> None:
    UpdaterIPC.write_command(command, data)


def request_install() -> None:
    send_update_command("install")


def cancel_update() -> None:
    send_update_command("cancel")


def check_update_now() -> None:
    send_update_command("check_now")


def stop_updater() -> None:
    send_update_command("exit")


def configure_cli_logging(daemon_mode: bool) -> None:
    """统一 CLI 日志初始化。"""
    IPC_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(IPC_DIR / "updater.log", encoding="utf-8")] if daemon_mode else [logging.StreamHandler()]
    logging.basicConfig(level=logging.INFO, format=UPDATER_LOG_FORMAT, handlers=handlers)


def run_interactive_updater() -> None:
    logger.info("=" * 60)
    logger.info(f"LCA 更新检测器 - 当前版本: {APP_VERSION}")
    logger.info("=" * 60)

    def progress(downloaded, total):
        if total > 0:
            pct = downloaded * 100 // total
            sys.stdout.write(f"\r下载进度: {pct}% ({downloaded}/{total})")
            sys.stdout.flush()

    updater = SimpleUpdater(progress_callback=progress)
    has_update, info = updater.check_for_updates()
    if not has_update or not info:
        logger.info("\n已经是最新版本")
        return

    logger.info(f"\n发现新版本: {info['new_version']}")
    logger.info("\n更新内容:")
    for item in info.get("changelog", []):
        logger.info(f"  - {item}")

    choice = input("\n是否下载并安装? (y/n): ").strip().lower()
    if choice != "y":
        return
    if updater.download_installer():
        logger.info("\n下载完成!")
        updater.run_installer(silent=False)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="LCA Updater")
    parser.add_argument("--daemon", action="store_true", help="作为守护进程运行")
    parser.add_argument("--interval", type=int, default=3600, help="检查间隔秒数")
    parser.add_argument("--main-pid", type=int, help="主进程 PID")
    args = parser.parse_args(argv)

    configure_cli_logging(args.daemon)

    if args.daemon:
        start_updater_daemon(check_interval=args.interval, main_pid=args.main_pid)
        return 0

    run_interactive_updater()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
