import logging
from typing import Any, Dict, List, Optional

from utils.ntfy_push import NtfyExecutionNotifier, publish_ntfy_message

logger = logging.getLogger(__name__)


class ControlCenterNtfyMixin:
    _NTFY_WINDOW_PREVIEW_LIMIT = 6
    _NTFY_WORKFLOW_PREVIEW_LIMIT = 6

    def _init_ntfy_notifier(self):
        config_ref = {}
        try:
            if self.parent_window is not None and isinstance(getattr(self.parent_window, "config", None), dict):
                config_ref = self.parent_window.config
        except Exception:
            config_ref = {}

        try:
            self._ntfy_notifier = NtfyExecutionNotifier(config_ref, "中控", parent=self)
        except Exception as exc:
            self._ntfy_notifier = None
            logger.warning("初始化中控 ntfy 通知器失败: %s", exc)

        self._ntfy_batch_execution_active = False
        self._ntfy_batch_success = True
        self._reset_ntfy_batch_context()

    def _reset_ntfy_batch_context(self):
        self._ntfy_batch_context_lines: List[str] = []
        self._ntfy_batch_failure_lines: List[str] = []
        self._ntfy_batch_failure_message = ""

    def _get_ntfy_config_ref(self) -> Dict[str, Any]:
        parent_window = getattr(self, "parent_window", None)
        config_ref = getattr(parent_window, "config", None)
        return config_ref if isinstance(config_ref, dict) else {}

    def _publish_forwarded_ntfy_message(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        title = str(payload.get("title") or "").strip()
        message = str(payload.get("message") or "").strip()
        if not title or not message:
            return False
        try:
            return publish_ntfy_message(
                title=title,
                message=message,
                priority=payload.get("priority"),
                config_ref=self._get_ntfy_config_ref(),
                event_key=str(payload.get("event_key") or "default").strip() or "default",
            )
        except Exception as exc:
            logger.warning("发送中控转发 ntfy 推送失败: %s", exc)
            return False

    @staticmethod
    def _normalize_ntfy_hwnd(hwnd: Any) -> Optional[int]:
        if hwnd in (None, "", 0, "0", False):
            return None
        try:
            normalized = int(hwnd)
        except (TypeError, ValueError):
            return None
        return normalized if normalized > 0 else None

    @classmethod
    def _format_ntfy_window_label(cls, title: Any, hwnd: Any) -> str:
        clean_title = str(title or "").strip()
        normalized_hwnd = cls._normalize_ntfy_hwnd(hwnd)
        if clean_title and normalized_hwnd is not None:
            return f"{clean_title} (HWND: {normalized_hwnd})"
        if clean_title:
            return clean_title
        if normalized_hwnd is not None:
            return f"HWND: {normalized_hwnd}"
        return ""

    def _find_ntfy_window_info(self, window_id: Any = None, window_title: Any = None, window_hwnd: Any = None) -> Dict[str, Any]:
        normalized_window_id = str(window_id or "").strip()
        normalized_title = str(window_title or "").strip()
        normalized_hwnd = self._normalize_ntfy_hwnd(window_hwnd)

        for window_info in getattr(self, "sorted_windows", []) or []:
            if not isinstance(window_info, dict):
                continue
            candidate_hwnd = self._normalize_ntfy_hwnd(window_info.get("hwnd"))
            candidate_title = str(window_info.get("title") or "").strip()
            candidate_id = str(window_info.get("hwnd", "" if window_info.get("hwnd") is not None else "")).strip()

            if normalized_window_id and candidate_id == normalized_window_id:
                return window_info
            if normalized_hwnd is not None and candidate_hwnd == normalized_hwnd:
                return window_info
            if normalized_title and candidate_title == normalized_title:
                return window_info

        return {
            "title": normalized_title,
            "hwnd": normalized_hwnd,
        }

    def _build_ntfy_batch_context_lines(
        self,
        window_ids: Optional[List[str]] = None,
        workflow_names: Optional[List[str]] = None,
        window_title: Any = None,
        window_hwnd: Any = None,
    ) -> List[str]:
        lines: List[str] = []
        normalized_ids = {str(item).strip() for item in (window_ids or []) if str(item).strip()}

        window_infos: List[Dict[str, Any]] = []
        if normalized_ids:
            for window_id in normalized_ids:
                window_infos.append(self._find_ntfy_window_info(window_id=window_id))
        elif window_title or window_hwnd:
            window_infos.append(self._find_ntfy_window_info(window_title=window_title, window_hwnd=window_hwnd))
        else:
            for window_info in getattr(self, "sorted_windows", []) or []:
                if isinstance(window_info, dict):
                    window_infos.append(window_info)

        window_infos = [
            window_info
            for window_info in window_infos
            if isinstance(window_info, dict) and (window_info.get("title") or window_info.get("hwnd"))
        ]

        if len(window_infos) == 1:
            window_label = self._format_ntfy_window_label(window_infos[0].get("title"), window_infos[0].get("hwnd"))
            if window_label:
                lines.append(f"目标窗口: {window_label}")
        elif window_infos:
            lines.append(f"窗口数: {len(window_infos)}")
            preview_windows = window_infos[: self._NTFY_WINDOW_PREVIEW_LIMIT]
            for index, window_info in enumerate(preview_windows, start=1):
                window_label = self._format_ntfy_window_label(window_info.get("title"), window_info.get("hwnd"))
                if not window_label:
                    continue
                lines.append(f"窗口{index}: {window_label}")
            hidden_count = len(window_infos) - len(preview_windows)
            if hidden_count > 0:
                lines.append(f"其余窗口: {hidden_count} 个")

        normalized_workflows = []
        for workflow_name in workflow_names or []:
            clean_name = str(workflow_name or "").strip()
            if clean_name and clean_name not in normalized_workflows:
                normalized_workflows.append(clean_name)

        if len(normalized_workflows) == 1:
            lines.append(f"工作流: {normalized_workflows[0]}")
        elif normalized_workflows:
            lines.append(f"工作流数: {len(normalized_workflows)}")
            preview_workflows = normalized_workflows[: self._NTFY_WORKFLOW_PREVIEW_LIMIT]
            for index, workflow_name in enumerate(preview_workflows, start=1):
                lines.append(f"工作流{index}: {workflow_name}")
            hidden_count = len(normalized_workflows) - len(preview_workflows)
            if hidden_count > 0:
                lines.append(f"其余工作流: {hidden_count} 个")

        return lines

    def _remember_ntfy_batch_failure(
        self,
        window_id: Any = None,
        workflow_name: str = "",
        message: str = "",
        window_title: Any = None,
        window_hwnd: Any = None,
    ):
        window_info = self._find_ntfy_window_info(window_id=window_id, window_title=window_title, window_hwnd=window_hwnd)
        lines: List[str] = []

        window_label = self._format_ntfy_window_label(window_info.get("title"), window_info.get("hwnd"))
        if window_label:
            lines.append(f"失败窗口: {window_label}")

        clean_workflow_name = str(workflow_name or "").strip()
        if clean_workflow_name:
            lines.append(f"失败工作流: {clean_workflow_name}")

        clean_message = str(message or "").strip()
        if clean_message:
            lines.append(f"失败详情: {clean_message}")
            self._ntfy_batch_failure_message = clean_message

        for line in lines:
            if line and line not in self._ntfy_batch_failure_lines:
                self._ntfy_batch_failure_lines.append(line)

    def _build_ntfy_batch_finish_context_lines(self, success: bool) -> List[str]:
        lines = list(self._ntfy_batch_context_lines)
        if not success:
            for line in self._ntfy_batch_failure_lines:
                if line and line not in lines:
                    lines.append(line)
        return lines

    def _start_ntfy_batch_session(
        self,
        session_name: str,
        intro_message: str = "",
        context_lines: Optional[List[str]] = None,
    ):
        notifier = getattr(self, "_ntfy_notifier", None)
        if notifier is None:
            return
        try:
            resolved_context = (
                list(context_lines)
                if isinstance(context_lines, list)
                else self._build_ntfy_batch_context_lines()
            )
            self._ntfy_batch_context_lines = resolved_context
            if notifier.session_active():
                self._ntfy_batch_execution_active = True
                notifier.set_session_context_lines(resolved_context)
                return
            self._ntfy_batch_execution_active = True
            self._ntfy_batch_success = True
            self._reset_ntfy_batch_context()
            self._ntfy_batch_context_lines = resolved_context
            notifier.start_session(
                str(session_name or "中控执行").strip() or "中控执行",
                intro_message=intro_message,
                context_lines=resolved_context,
            )
        except Exception as exc:
            logger.warning("启动中控 ntfy 会话失败: %s", exc)

    def _record_ntfy_batch_detail(self, detail: str):
        notifier = getattr(self, "_ntfy_notifier", None)
        if notifier is None or not getattr(self, "_ntfy_batch_execution_active", False):
            return
        try:
            notifier.record_detail(detail)
        except Exception as exc:
            logger.warning("记录中控 ntfy 详情失败: %s", exc)

    def _mark_ntfy_batch_failed(self):
        self._ntfy_batch_success = False

    def _finish_ntfy_batch_session(
        self,
        success: bool,
        summary: str,
        context_lines: Optional[List[str]] = None,
    ):
        notifier = getattr(self, "_ntfy_notifier", None)
        if notifier is None or not getattr(self, "_ntfy_batch_execution_active", False):
            self._reset_ntfy_batch_context()
            return
        try:
            final_success = bool(success) and bool(getattr(self, "_ntfy_batch_success", True))
            resolved_context = (
                list(context_lines)
                if isinstance(context_lines, list)
                else self._build_ntfy_batch_finish_context_lines(final_success)
            )
            notifier.finish_session(
                final_success,
                str(summary or "").strip(),
                context_lines=resolved_context,
            )
        except Exception as exc:
            logger.warning("结束中控 ntfy 会话失败: %s", exc)
        finally:
            self._ntfy_batch_execution_active = False
            self._ntfy_batch_success = True
            self._reset_ntfy_batch_context()
