import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject

from utils.app_paths import get_config_path, get_user_data_dir

logger = logging.getLogger(__name__)

DEFAULT_NTFY_SERVER_URL = "https://ntfy.sh"
PUBLIC_MESSAGE_MAX_BYTES = 4096
PUBLIC_BURST_SIZE = 60.0
PUBLIC_REFILL_INTERVAL_SECONDS = 5.0
PUBLIC_DAILY_WINDOW_SECONDS = 24 * 60 * 60
PUBLIC_DAILY_MESSAGE_LIMIT = 250
_TRUNCATE_SUFFIX = "\n\n[消息已截断]"
NTFY_PRIORITY_EVENT_KEYS = ("start", "success", "failure")
NTFY_PRIORITY_LEVELS = {
    "min": {"header": "1", "label": "最低"},
    "low": {"header": "2", "label": "低"},
    "default": {"header": "3", "label": "普通"},
    "high": {"header": "4", "label": "高"},
    "max": {"header": "5", "label": "最高"},
}
_NTFY_PRIORITY_ALIASES = {
    "1": "min",
    "min": "min",
    "最低": "min",
    "2": "low",
    "low": "low",
    "低": "low",
    "3": "default",
    "default": "default",
    "normal": "default",
    "普通": "default",
    "默认": "default",
    "4": "high",
    "high": "high",
    "高": "high",
    "5": "max",
    "max": "max",
    "urgent": "max",
    "最高": "max",
    "紧急": "max",
}
DEFAULT_NTFY_PRIORITY_SETTINGS = {
    "start": "default",
    "success": "default",
    "failure": "high",
}
_NTFY_PRIORITY_ORDER = {
    "min": 1,
    "low": 2,
    "default": 3,
    "high": 4,
    "max": 5,
}
CARD_NTFY_ENABLED_PARAM = "enable_card_ntfy_push"
CARD_NTFY_PRIORITY_PARAM = "card_ntfy_push_priority"
DEFAULT_CARD_NTFY_PRIORITY = "default"
_SHARED_NTFY_DISPATCHER_LOCK = threading.Lock()
_SHARED_NTFY_PUBLISHER = None
_SHARED_NTFY_LIMITER = None


def normalize_ntfy_priority(value: Any, fallback: str = "default") -> str:
    normalized_fallback = str(fallback or "default").strip().lower() or "default"
    if normalized_fallback not in NTFY_PRIORITY_LEVELS:
        normalized_fallback = "default"

    normalized_value = str(value or "").strip().lower()
    if not normalized_value:
        return normalized_fallback
    return _NTFY_PRIORITY_ALIASES.get(normalized_value, normalized_fallback)


def normalize_ntfy_priority_settings(raw_priorities: Optional[dict]) -> Dict[str, str]:
    priorities = dict(raw_priorities or {})
    normalized: Dict[str, str] = {}
    for event_key in NTFY_PRIORITY_EVENT_KEYS:
        normalized[event_key] = normalize_ntfy_priority(
            priorities.get(event_key),
            fallback=DEFAULT_NTFY_PRIORITY_SETTINGS.get(event_key, "default"),
        )
    return normalized


def get_ntfy_priority_header_value(priority_key: Any) -> str:
    normalized_key = normalize_ntfy_priority(priority_key)
    return str(NTFY_PRIORITY_LEVELS.get(normalized_key, NTFY_PRIORITY_LEVELS["default"])["header"])


def get_ntfy_priority_options() -> List[Dict[str, str]]:
    options = []
    for key in ("min", "low", "default", "high", "max"):
        item = NTFY_PRIORITY_LEVELS[key]
        options.append(
            {
                "key": key,
                "label": str(item["label"]),
                "header": str(item["header"]),
            }
        )
    return options


def get_card_ntfy_push_param_definitions() -> Dict[str, Dict[str, Any]]:
    priority_options = {
        option["key"]: f"{option['label']} ({option['header']})"
        for option in get_ntfy_priority_options()
    }
    return {
        "---card_ntfy_push---": {
            "type": "separator",
            "label": "消息推送",
        },
        CARD_NTFY_ENABLED_PARAM: {
            "label": "启用推送",
            "type": "checkbox",
            "default": False,
            "tooltip": "启用后，本卡片每次执行结束都会立即发送一条 ntfy 消息。",
        },
        CARD_NTFY_PRIORITY_PARAM: {
            "label": "推送等级",
            "type": "select",
            "options": priority_options,
            "default": DEFAULT_CARD_NTFY_PRIORITY,
            "condition": {"param": CARD_NTFY_ENABLED_PARAM, "value": True},
            "tooltip": "仅控制本卡片消息等级，实际发送仍受全局 ntfy 设置和最低等级限制。",
        },
    }


def normalize_card_ntfy_push_settings(raw_params: Optional[dict]) -> Dict[str, Any]:
    params = dict(raw_params) if isinstance(raw_params, dict) else {}
    return {
        "enabled": bool(params.get(CARD_NTFY_ENABLED_PARAM, False)),
        "priority": normalize_ntfy_priority(
            params.get(CARD_NTFY_PRIORITY_PARAM),
            fallback=DEFAULT_CARD_NTFY_PRIORITY,
        ),
    }


def resolve_ntfy_priority_for_event(settings: Dict[str, Any], event_key: str) -> str:
    normalized_settings = normalize_ntfy_settings(settings)
    priorities = normalized_settings.get("priorities", {})
    if not isinstance(priorities, dict):
        priorities = DEFAULT_NTFY_PRIORITY_SETTINGS
    fallback = DEFAULT_NTFY_PRIORITY_SETTINGS.get(str(event_key or "").strip().lower(), "default")
    return normalize_ntfy_priority(priorities.get(event_key), fallback=fallback)


def get_ntfy_priority_rank(priority_key: Any) -> int:
    normalized_key = normalize_ntfy_priority(priority_key)
    return int(_NTFY_PRIORITY_ORDER.get(normalized_key, _NTFY_PRIORITY_ORDER["default"]))


def is_ntfy_priority_allowed(settings: Dict[str, Any], priority_key: Any) -> bool:
    normalized_settings = normalize_ntfy_settings(settings)
    minimum_priority = normalize_ntfy_priority(normalized_settings.get("minimum_priority"), fallback="min")
    return get_ntfy_priority_rank(priority_key) >= get_ntfy_priority_rank(minimum_priority)


def normalize_ntfy_settings(raw_settings: Optional[dict]) -> Dict[str, Any]:
    settings = dict(raw_settings or {})

    server_url = str(settings.get("server_url") or DEFAULT_NTFY_SERVER_URL).strip()
    if not server_url:
        server_url = DEFAULT_NTFY_SERVER_URL
    server_url = server_url.rstrip("/")

    topic = str(settings.get("topic") or "").strip()
    token = str(settings.get("token") or "").strip()

    return {
        "enabled": bool(settings.get("enabled", False)),
        "server_url": server_url,
        "topic": topic,
        "token": token,
        "enforce_public_limits": bool(settings.get("enforce_public_limits", True)),
        "priorities": normalize_ntfy_priority_settings(settings.get("priorities")),
        "minimum_priority": normalize_ntfy_priority(settings.get("minimum_priority"), fallback="min"),
    }


def get_default_ntfy_settings() -> Dict[str, Any]:
    return normalize_ntfy_settings({})


def _load_global_config_snapshot() -> Dict[str, Any]:
    try:
        config_path = get_config_path()
        with open(config_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.debug("读取全局配置失败: %s", exc)
    return {}


def _resolve_ntfy_settings_input(config_ref: Optional[dict]) -> Dict[str, Any]:
    raw_settings = None
    if isinstance(config_ref, dict):
        raw_settings = config_ref.get("ntfy_settings") if "ntfy_settings" in config_ref else config_ref
    else:
        raw_settings = _load_global_config_snapshot().get("ntfy_settings")
    return normalize_ntfy_settings(raw_settings)


def _get_rate_limit_state_path() -> str:
    runtime_dir = os.path.join(get_user_data_dir("LCA"), "runtime")
    os.makedirs(runtime_dir, exist_ok=True)
    return os.path.join(runtime_dir, "ntfy_public_rate_limit.json")


def _truncate_utf8_text(text: str, max_bytes: int = PUBLIC_MESSAGE_MAX_BYTES) -> str:
    value = str(text or "")
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value

    suffix_bytes = _TRUNCATE_SUFFIX.encode("utf-8")
    budget = max(0, max_bytes - len(suffix_bytes))
    truncated = encoded[:budget].decode("utf-8", errors="ignore")
    return f"{truncated}{_TRUNCATE_SUFFIX}"


class NtfyPublicRateLimiter:
    def __init__(self) -> None:
        self._state_path = _get_rate_limit_state_path()
        self._lock = threading.Lock()

    def try_acquire(self) -> Tuple[bool, Optional[str], Optional[dict]]:
        now = time.time()
        with self._lock:
            state = self._load_state()
            state = self._normalize_state(state, now)

            if len(state["message_timestamps"]) >= PUBLIC_DAILY_MESSAGE_LIMIT:
                self._save_state(state)
                return False, "daily_limit", None

            if state["tokens"] < 1.0:
                self._save_state(state)
                return False, "rate_limit", None

            state["tokens"] -= 1.0
            state["message_timestamps"].append(now)
            self._save_state(state)
            return True, None, {"timestamp": now}

    def rollback(self, claim: Optional[dict]) -> None:
        if not isinstance(claim, dict):
            return
        timestamp = claim.get("timestamp")
        if not isinstance(timestamp, (int, float)):
            return

        with self._lock:
            state = self._load_state()
            state = self._normalize_state(state, time.time())
            timestamps = state["message_timestamps"]
            for index, value in enumerate(timestamps):
                if abs(value - float(timestamp)) < 1e-6:
                    timestamps.pop(index)
                    break
            state["tokens"] = min(PUBLIC_BURST_SIZE, float(state["tokens"]) + 1.0)
            self._save_state(state)

    def _load_state(self) -> Dict[str, Any]:
        try:
            if os.path.exists(self._state_path):
                with open(self._state_path, "r", encoding="utf-8") as file:
                    data = json.load(file)
                    if isinstance(data, dict):
                        return data
        except Exception as exc:
            logger.warning("读取 ntfy 限流状态失败: %s", exc)
        return {}

    def _save_state(self, state: Dict[str, Any]) -> None:
        tmp_path = f"{self._state_path}.tmp.{os.getpid()}.{int(time.time() * 1000)}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as file:
                json.dump(state, file, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._state_path)
        except Exception as exc:
            logger.warning("保存 ntfy 限流状态失败: %s", exc)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    @staticmethod
    def _normalize_state(state: Dict[str, Any], now: float) -> Dict[str, Any]:
        try:
            tokens = float(state.get("tokens", PUBLIC_BURST_SIZE))
        except (TypeError, ValueError):
            tokens = PUBLIC_BURST_SIZE

        try:
            last_refill_ts = float(state.get("last_refill_ts", now))
        except (TypeError, ValueError):
            last_refill_ts = now

        raw_timestamps = state.get("message_timestamps", [])
        message_timestamps = []
        if isinstance(raw_timestamps, list):
            cutoff = now - PUBLIC_DAILY_WINDOW_SECONDS
            for value in raw_timestamps:
                try:
                    ts = float(value)
                except (TypeError, ValueError):
                    continue
                if ts >= cutoff:
                    message_timestamps.append(ts)

        if now > last_refill_ts:
            refill = (now - last_refill_ts) / PUBLIC_REFILL_INTERVAL_SECONDS
            tokens = min(PUBLIC_BURST_SIZE, tokens + refill)
            last_refill_ts = now

        return {
            "tokens": max(0.0, tokens),
            "last_refill_ts": last_refill_ts,
            "message_timestamps": message_timestamps,
        }


def _get_shared_ntfy_dispatcher() -> Tuple["NtfyPublisher", NtfyPublicRateLimiter]:
    global _SHARED_NTFY_PUBLISHER, _SHARED_NTFY_LIMITER
    with _SHARED_NTFY_DISPATCHER_LOCK:
        if _SHARED_NTFY_PUBLISHER is None:
            _SHARED_NTFY_PUBLISHER = NtfyPublisher()
        if _SHARED_NTFY_LIMITER is None:
            _SHARED_NTFY_LIMITER = NtfyPublicRateLimiter()
    return _SHARED_NTFY_PUBLISHER, _SHARED_NTFY_LIMITER


def _enqueue_ntfy_message(
    settings: Dict[str, Any],
    title: str,
    message: str,
    priority: Any = None,
    event_key: str = "default",
    publisher: Optional["NtfyPublisher"] = None,
    limiter: Optional["NtfyPublicRateLimiter"] = None,
    on_limit_reached=None,
) -> bool:
    normalized_settings = normalize_ntfy_settings(settings)
    if not normalized_settings.get("enabled"):
        return False
    if not str(normalized_settings.get("topic") or "").strip():
        return False

    fallback_priority = resolve_ntfy_priority_for_event(normalized_settings, event_key)
    resolved_priority = normalize_ntfy_priority(priority, fallback=fallback_priority)
    if not is_ntfy_priority_allowed(normalized_settings, resolved_priority):
        logger.info(
            "ntfy 推送已被等级限制拦截: event=%s, priority=%s, minimum=%s",
            event_key,
            resolved_priority,
            normalized_settings.get("minimum_priority"),
        )
        return False

    if publisher is None or limiter is None:
        shared_publisher, shared_limiter = _get_shared_ntfy_dispatcher()
        if publisher is None:
            publisher = shared_publisher
        if limiter is None:
            limiter = shared_limiter

    claim = None
    if normalized_settings.get("enforce_public_limits", True):
        allowed, reason, claim = limiter.try_acquire()
        if not allowed:
            if callable(on_limit_reached):
                try:
                    on_limit_reached()
                except Exception:
                    pass
            logger.warning("ntfy 客户端限流已生效: %s", reason)
            return False

    publisher.enqueue(
        settings=normalized_settings,
        title=title,
        message=message,
        priority=resolved_priority,
        claim=claim,
        limiter=limiter if claim is not None else None,
    )
    return True


def publish_ntfy_message(
    title: str,
    message: str,
    priority: Any = None,
    config_ref: Optional[dict] = None,
    event_key: str = "default",
) -> bool:
    settings = _resolve_ntfy_settings_input(config_ref)
    return _enqueue_ntfy_message(
        settings=settings,
        title=title,
        message=message,
        priority=priority,
        event_key=event_key,
    )


class NtfyPublisher:
    def __init__(self) -> None:
        self._queue: "queue.Queue[Optional[dict]]" = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True, name="NtfyPublisher")
        self._worker.start()

    def enqueue(
        self,
        settings: Dict[str, Any],
        title: str,
        message: str,
        priority: str = "default",
        claim: Optional[dict] = None,
        limiter: Optional[NtfyPublicRateLimiter] = None,
    ) -> None:
        self._queue.put(
            {
                "settings": dict(settings or {}),
                "title": str(title or "").strip(),
                "message": _truncate_utf8_text(message),
                "priority": normalize_ntfy_priority(priority),
                "claim": claim,
                "limiter": limiter,
            }
        )

    def send_sync(
        self,
        settings: Dict[str, Any],
        title: str,
        message: str,
        priority: str = "default",
        claim: Optional[dict] = None,
        limiter: Optional[NtfyPublicRateLimiter] = None,
    ) -> None:
        self._send(
            {
                "settings": dict(settings or {}),
                "title": str(title or "").strip(),
                "message": _truncate_utf8_text(message),
                "priority": normalize_ntfy_priority(priority),
                "claim": claim,
                "limiter": limiter,
            }
        )

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            try:
                self._send(item)
            except Exception as exc:
                logger.warning("处理 ntfy 推送队列失败: %s", exc)

    def _send(self, item: dict) -> None:
        settings = normalize_ntfy_settings(item.get("settings"))
        topic = str(settings.get("topic") or "").strip()
        if not topic:
            return

        server_url = str(settings.get("server_url") or DEFAULT_NTFY_SERVER_URL).rstrip("/")
        topic_path = urllib.parse.quote(topic, safe="")
        publish_url = f"{server_url}/{topic_path}"

        headers = {
            "Content-Type": "text/plain; charset=utf-8",
        }
        headers["Priority"] = get_ntfy_priority_header_value(item.get("priority"))

        token = str(settings.get("token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        request = urllib.request.Request(
            url=publish_url,
            data=str(item.get("message") or "").encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status_code = int(getattr(response, "status", response.getcode()) or 0)
                if status_code < 200 or status_code >= 300:
                    raise urllib.error.HTTPError(
                        publish_url,
                        status_code,
                        f"unexpected status: {status_code}",
                        response.headers,
                        None,
                    )
        except urllib.error.HTTPError as exc:
            limiter = item.get("limiter")
            claim = item.get("claim")
            if limiter is not None:
                try:
                    limiter.rollback(claim)
                except Exception as rollback_error:
                    logger.warning("回滚 ntfy 限流额度失败: %s", rollback_error)
            logger.warning("ntfy 推送失败: HTTP %s", exc.code)
            raise
        except Exception as exc:
            limiter = item.get("limiter")
            claim = item.get("claim")
            if limiter is not None:
                try:
                    limiter.rollback(claim)
                except Exception as rollback_error:
                    logger.warning("回滚 ntfy 限流额度失败: %s", rollback_error)
            logger.warning("ntfy 推送失败: %s", exc)
            raise


class NtfyExecutionNotifier(QObject):
    def __init__(self, config_ref: dict, scope_name: str, parent=None) -> None:
        super().__init__(parent)
        self._config_ref = config_ref if isinstance(config_ref, dict) else {}
        self._scope_name = str(scope_name or "LCA").strip() or "LCA"
        self._publisher = NtfyPublisher()
        self._limiter = NtfyPublicRateLimiter()
        self._session_active = False
        self._session_name = ""
        self._session_started_ts = 0.0
        self._session_context_lines: List[str] = []
        self._pending_details = []
        self._last_detail_text = ""
        self._suppressed_push_count = 0

    def session_active(self) -> bool:
        return bool(self._session_active)

    def reload_settings(self) -> None:
        return

    def set_session_context_lines(self, context_lines: Optional[List[str]]) -> None:
        self._session_context_lines = self._normalize_context_lines(context_lines)

    def _legacy_start_session_unused(
        self,
        session_name: str,
        intro_message: str = "",
        context_lines: Optional[List[str]] = None,
    ) -> None:
        self._session_active = True
        self._session_name = str(session_name or "未命名任务").strip() or "未命名任务"
        self._session_started_ts = time.time()
        self._session_context_lines = self._normalize_context_lines(context_lines)
        self._pending_details = []
        self._last_detail_text = ""
        self._suppressed_push_count = 0

        lines = [
            f"{self._scope_name}开始执行",
            f"任务: {self._session_name}",
            f"时间: {self._format_time(self._session_started_ts)}",
        ]
        self._append_labeled_text(lines, "说明", intro_message)
        lines.extend(self._session_context_lines)
        self._publish(f"{self._scope_name} 开始", "\n".join(lines), event_key="start")

    def record_detail(self, detail: str) -> None:
        if not self._session_active:
            return

        text = str(detail or "").strip()
        if not text or text == self._last_detail_text:
            return

        self._last_detail_text = text
        timestamp = self._format_time(time.time(), "%H:%M:%S")
        self._pending_details.append(f"[{timestamp}] {text}")
        if len(self._pending_details) > 20:
            self._pending_details = self._pending_details[-20:]

    def _legacy_flush_pending_details_unused(self) -> None:
        if not self._session_active or not self._pending_details:
            return

        details = self._pending_details[-8:]
        lines = [
            f"{self._scope_name}执行中",
            f"任务: {self._session_name}",
            f"开始: {self._format_time(self._session_started_ts)}",
            "最近进度:",
        ]
        lines.extend(self._session_context_lines)
        lines.extend(f"- {detail}" for detail in details)
        if self._suppressed_push_count > 0:
            lines.append(f"本地限流已跳过 {self._suppressed_push_count} 条推送")
        self._publish(f"{self._scope_name} 进度", "\n".join(lines), event_key="progress")
        self._pending_details = []

    def _legacy_finish_session_unused(
        self,
        success: bool,
        summary: str,
        context_lines: Optional[List[str]] = None,
    ) -> None:
        if not self._session_active:
            return

        return
        ended_ts = time.time()
        details = self._pending_details[-5:]
        result_text = "成功" if success else "失败"
        resolved_context_lines = self._normalize_context_lines(context_lines) or list(self._session_context_lines)
        lines = [
            f"{self._scope_name}执行{result_text}",
            f"任务: {self._session_name}",
            f"开始: {self._format_time(self._session_started_ts)}",
            f"结束: {self._format_time(ended_ts)}",
            f"耗时: {self._format_duration(max(0.0, ended_ts - self._session_started_ts))}",
        ]
        self._append_labeled_text(lines, "结果", str(summary or "").strip() or result_text)
        lines.extend(resolved_context_lines)
        if details:
            lines.append("最后进度:")
            lines.extend(f"- {detail}" for detail in details)
        if self._suppressed_push_count > 0:
            lines.append(f"本地限流已跳过 {self._suppressed_push_count} 条推送")
        self._publish(
            f"{self._scope_name} {'完成' if success else '失败'}",
            "\n".join(lines),
            event_key="success" if success else "failure",
        )

        self._session_active = False
        self._session_name = ""
        self._session_started_ts = 0.0
        self._session_context_lines = []
        self._pending_details = []
        self._last_detail_text = ""
        self._suppressed_push_count = 0

    def _legacy_publish_unused(self, title: str, message: str, event_key: str = "default") -> bool:
        settings = normalize_ntfy_settings(self._config_ref.get("ntfy_settings"))
        return _enqueue_ntfy_message(
            settings=settings,
            title=title,
            message=message,
            event_key=event_key,
            publisher=self._publisher,
            limiter=self._limiter,
            on_limit_reached=lambda: setattr(
                self,
                "_suppressed_push_count",
                int(getattr(self, "_suppressed_push_count", 0)) + 1,
            ),
        )

    @staticmethod
    def _format_time(timestamp: float, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
        try:
            return datetime.fromtimestamp(float(timestamp)).strftime(fmt)
        except Exception:
            return "-"

    @staticmethod
    def _legacy_format_duration_unused(seconds: float) -> str:
        total_seconds = max(0, int(seconds))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}小时{minutes}分{secs}秒"
        if minutes > 0:
            return f"{minutes}分{secs}秒"
        return f"{secs}秒"

    @staticmethod
    def _normalize_context_lines(context_lines: Optional[List[str]]) -> List[str]:
        if not context_lines:
            return []

        normalized: List[str] = []
        for value in context_lines:
            text = str(value or "").strip()
            if not text or text in normalized:
                continue
            normalized.append(text)
        return normalized

    @staticmethod
    def _append_labeled_text(lines: List[str], label: str, text: str) -> None:
        content = str(text or "").strip()
        if not content:
            return

        split_lines = [line.rstrip() for line in content.splitlines() if str(line).strip()]
        if not split_lines:
            return

        lines.append(f"{label}: {split_lines[0]}")
        if len(split_lines) > 1:
            lines.extend(split_lines[1:])

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total_seconds = max(0, int(seconds))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}小时{minutes}分{secs}秒"
        if minutes > 0:
            return f"{minutes}分{secs}秒"
        return f"{secs}秒"

    def start_session(
        self,
        session_name: str,
        intro_message: str = "",
        context_lines: Optional[List[str]] = None,
    ) -> None:
        self._session_active = True
        self._session_name = str(session_name or "未命名任务").strip() or "未命名任务"
        self._session_started_ts = time.time()
        self._session_context_lines = self._normalize_context_lines(context_lines)
        self._pending_details = []
        self._last_detail_text = ""
        self._suppressed_push_count = 0

        lines = [
            f"{self._scope_name}开始执行",
            f"任务: {self._session_name}",
            f"时间: {self._format_time(self._session_started_ts)}",
        ]
        self._append_labeled_text(lines, "说明", intro_message)
        lines.extend(self._session_context_lines)
        self._publish(f"{self._scope_name} 开始", "\n".join(lines), event_key="start")

    def flush_pending_details(self) -> None:
        return

    def finish_session(
        self,
        success: bool,
        summary: str,
        context_lines: Optional[List[str]] = None,
        result_type: Optional[str] = None,
    ) -> None:
        if not self._session_active:
            return

        ended_ts = time.time()
        details = self._pending_details[-5:]
        normalized_result_type = str(result_type or "").strip().lower()
        if normalized_result_type in ("success", "completed"):
            normalized_result_type = "success"
        elif normalized_result_type in ("stop", "stopped"):
            normalized_result_type = "stopped"
        elif normalized_result_type in ("fail", "failed", "failure", "error"):
            normalized_result_type = "failure"
        else:
            normalized_result_type = "success" if success else "failure"

        if normalized_result_type == "stopped":
            result_text = "已停止"
            title_suffix = "已停止"
            event_key = "stopped"
        elif normalized_result_type == "success":
            result_text = "成功"
            title_suffix = "完成"
            event_key = "success"
        else:
            result_text = "失败"
            title_suffix = "失败"
            event_key = "failure"

        resolved_context_lines = self._normalize_context_lines(context_lines) or list(self._session_context_lines)
        lines = [
            f"{self._scope_name}执行{result_text}",
            f"任务: {self._session_name}",
            f"开始: {self._format_time(self._session_started_ts)}",
            f"结束: {self._format_time(ended_ts)}",
            f"耗时: {self._format_duration(max(0.0, ended_ts - self._session_started_ts))}",
        ]
        self._append_labeled_text(lines, "结果", str(summary or "").strip() or result_text)
        lines.extend(resolved_context_lines)
        if details:
            lines.append("最后进度:")
            lines.extend(f"- {detail}" for detail in details)
        if self._suppressed_push_count > 0:
            lines.append(f"本地限流已跳过 {self._suppressed_push_count} 条推送")
        self._publish(
            f"{self._scope_name} {title_suffix}",
            "\n".join(lines),
            event_key=event_key,
        )

        self._session_active = False
        self._session_name = ""
        self._session_started_ts = 0.0
        self._session_context_lines = []
        self._pending_details = []
        self._last_detail_text = ""
        self._suppressed_push_count = 0

    def _publish(self, title: str, message: str, event_key: str = "default") -> bool:
        settings = normalize_ntfy_settings(self._config_ref.get("ntfy_settings"))
        return _enqueue_ntfy_message(
            settings=settings,
            title=title,
            message=message,
            event_key=event_key,
            publisher=self._publisher,
            limiter=self._limiter,
            on_limit_reached=lambda: setattr(
                self,
                "_suppressed_push_count",
                int(getattr(self, "_suppressed_push_count", 0)) + 1,
            ),
        )
