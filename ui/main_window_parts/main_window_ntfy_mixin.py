import logging
from typing import Any, Dict, List, Optional

from utils.ntfy_push import NtfyExecutionNotifier, publish_ntfy_message

logger = logging.getLogger(__name__)


class MainWindowNtfyMixin:
    _NTFY_WINDOW_PREVIEW_LIMIT = 6

    def _init_ntfy_notifier(self):
        try:
            self._ntfy_notifier = NtfyExecutionNotifier(self.config, "主窗口", parent=self)
        except Exception as exc:
            self._ntfy_notifier = None
            logger.warning("初始化 ntfy 通知器失败: %s", exc)
        self._reset_ntfy_execution_context()

    def _reset_ntfy_execution_context(self):
        self._ntfy_session_context_lines: List[str] = []
        self._ntfy_failure_context_lines: List[str] = []
        self._ntfy_failure_message = ""
        self._ntfy_last_workflow_name = ""
        self._ntfy_last_target_title = ""
        self._ntfy_last_target_hwnd = None

    def _get_ntfy_config_ref(self) -> Dict[str, Any]:
        config_ref = getattr(self, "config", None)
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
            logger.warning("发送转发 ntfy 推送失败: %s", exc)
            return False

    def _get_ntfy_active_task(self):
        task_id = getattr(self, "_active_execution_task_id", None)
        try:
            if task_id is None and hasattr(self, "workflow_tab_widget") and self.workflow_tab_widget:
                task_id = self.workflow_tab_widget.get_current_task_id()
        except Exception:
            task_id = task_id

        try:
            if task_id is not None and hasattr(self, "task_manager") and self.task_manager:
                return self.task_manager.get_task(task_id)
        except Exception:
            return None
        return None

    def _resolve_ntfy_session_name(self, fallback: str = "当前工作流") -> str:
        try:
            task = self._get_ntfy_active_task()
            if task is not None:
                task_name = str(getattr(task, "name", "") or "").strip()
                if task_name:
                    return task_name
        except Exception:
            pass
        return str(fallback or "当前工作流").strip() or "当前工作流"

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

    def _get_ntfy_enabled_bound_windows(self) -> List[Dict[str, Any]]:
        windows = []
        for window_info in getattr(self, "bound_windows", []) or []:
            if not isinstance(window_info, dict):
                continue
            if not window_info.get("enabled", True):
                continue
            windows.append(window_info)
        return windows

    def _build_ntfy_bound_window_lines(
        self,
        bound_windows: Optional[List[Dict[str, Any]]] = None,
    ) -> List[str]:
        windows = bound_windows if isinstance(bound_windows, list) else self._get_ntfy_enabled_bound_windows()
        if not windows:
            return []

        lines: List[str] = []
        if len(windows) == 1:
            window_info = windows[0]
            label = self._format_ntfy_window_label(window_info.get("title"), window_info.get("hwnd"))
            if label:
                lines.append(f"绑定窗口: {label}")
            return lines

        lines.append(f"绑定窗口数: {len(windows)}")
        preview_windows = windows[: self._NTFY_WINDOW_PREVIEW_LIMIT]
        for index, window_info in enumerate(preview_windows, start=1):
            label = self._format_ntfy_window_label(window_info.get("title"), window_info.get("hwnd"))
            if not label:
                continue
            lines.append(f"绑定窗口{index}: {label}")

        hidden_count = len(windows) - len(preview_windows)
        if hidden_count > 0:
            lines.append(f"其余窗口: {hidden_count} 个")
        return lines

    def _build_ntfy_session_context_lines(
        self,
        workflow_name: str = "",
        target_window_title: Any = None,
        target_hwnd: Any = None,
        bound_windows: Optional[List[Dict[str, Any]]] = None,
    ) -> List[str]:
        resolved_workflow_name = str(workflow_name or "").strip() or self._resolve_ntfy_session_name()
        target_label = self._format_ntfy_window_label(target_window_title, target_hwnd)

        lines: List[str] = [f"工作流: {resolved_workflow_name}"]
        if target_label:
            lines.append(f"目标窗口: {target_label}")
        lines.extend(self._build_ntfy_bound_window_lines(bound_windows))
        return lines

    def _update_ntfy_session_context(
        self,
        workflow_name: str = "",
        target_window_title: Any = None,
        target_hwnd: Any = None,
        bound_windows: Optional[List[Dict[str, Any]]] = None,
    ) -> List[str]:
        task = self._get_ntfy_active_task()
        resolved_workflow_name = str(workflow_name or "").strip()
        if not resolved_workflow_name and task is not None:
            resolved_workflow_name = str(getattr(task, "name", "") or "").strip()
        if not resolved_workflow_name:
            resolved_workflow_name = self._resolve_ntfy_session_name()

        resolved_target_title = str(target_window_title or "").strip()
        resolved_target_hwnd = self._normalize_ntfy_hwnd(target_hwnd)

        if task is not None:
            if not resolved_target_title:
                resolved_target_title = str(getattr(task, "target_window_title", "") or "").strip()
            if resolved_target_hwnd is None:
                resolved_target_hwnd = self._normalize_ntfy_hwnd(getattr(task, "target_hwnd", None))

        if not resolved_target_title:
            resolved_target_title = str(getattr(self, "_forced_target_title", "") or "").strip()
        if resolved_target_hwnd is None:
            resolved_target_hwnd = self._normalize_ntfy_hwnd(getattr(self, "_forced_target_hwnd", None))

        if not resolved_target_title:
            resolved_target_title = str(getattr(self, "current_target_window_title", "") or "").strip()

        resolved_bound_windows = bound_windows if isinstance(bound_windows, list) else self._get_ntfy_enabled_bound_windows()

        self._ntfy_last_workflow_name = resolved_workflow_name
        self._ntfy_last_target_title = resolved_target_title
        self._ntfy_last_target_hwnd = resolved_target_hwnd
        self._ntfy_session_context_lines = self._build_ntfy_session_context_lines(
            workflow_name=resolved_workflow_name,
            target_window_title=resolved_target_title,
            target_hwnd=resolved_target_hwnd,
            bound_windows=resolved_bound_windows,
        )

        notifier = getattr(self, "_ntfy_notifier", None)
        if notifier is not None:
            try:
                notifier.set_session_context_lines(self._ntfy_session_context_lines)
            except Exception as exc:
                logger.warning("刷新 ntfy 会话上下文失败: %s", exc)
        return list(self._ntfy_session_context_lines)

    def _resolve_ntfy_card_context(self, card_id: Any) -> Dict[str, str]:
        resolved = {
            "task_type": "",
            "card_name": "",
        }
        try:
            target_card_id = int(card_id)
        except (TypeError, ValueError):
            return resolved

        candidate_views = []
        try:
            if hasattr(self, "workflow_tab_widget") and self.workflow_tab_widget:
                candidate_views.extend(
                    workflow_view
                    for workflow_view in getattr(self.workflow_tab_widget, "task_views", {}).values()
                    if workflow_view is not None
                )
        except Exception:
            pass

        workflow_view = getattr(self, "workflow_view", None)
        if workflow_view is not None:
            candidate_views.append(workflow_view)

        for view in candidate_views:
            try:
                cards = getattr(view, "cards", None) or {}
                if target_card_id not in cards:
                    continue
                card = cards.get(target_card_id)
                if card is None:
                    continue
                resolved["task_type"] = str(getattr(card, "task_type", "") or "").strip()
                custom_name = str(getattr(card, "custom_name", "") or "").strip()
                if not custom_name:
                    parameters = getattr(card, "parameters", None)
                    if isinstance(parameters, dict):
                        custom_name = str(
                            parameters.get("custom_name")
                            or parameters.get("name")
                            or parameters.get("description")
                            or ""
                        ).strip()
                resolved["card_name"] = custom_name
                return resolved
            except Exception:
                continue
        return resolved

    def _remember_ntfy_failure_detail(
        self,
        error_message: str = "",
        workflow_name: str = "",
        window_title: Any = None,
        window_hwnd: Any = None,
        card_id: Any = None,
        card_name: str = "",
        task_type: str = "",
    ):
        lines: List[str] = []
        resolved_workflow_name = str(workflow_name or "").strip() or str(self._ntfy_last_workflow_name or "").strip()
        if resolved_workflow_name:
            lines.append(f"失败工作流: {resolved_workflow_name}")

        resolved_window_title = str(window_title or "").strip() or str(self._ntfy_last_target_title or "").strip()
        resolved_window_hwnd = self._normalize_ntfy_hwnd(window_hwnd)
        if resolved_window_hwnd is None:
            resolved_window_hwnd = self._normalize_ntfy_hwnd(self._ntfy_last_target_hwnd)
        window_label = self._format_ntfy_window_label(resolved_window_title, resolved_window_hwnd)
        if window_label:
            lines.append(f"失败窗口: {window_label}")

        normalized_card_id = None
        try:
            if card_id is not None:
                normalized_card_id = int(card_id)
        except (TypeError, ValueError):
            normalized_card_id = None

        if normalized_card_id is not None:
            card_context = self._resolve_ntfy_card_context(normalized_card_id)
            resolved_task_type = str(task_type or "").strip() or card_context.get("task_type", "")
            resolved_card_name = str(card_name or "").strip() or card_context.get("card_name", "")

            card_parts = []
            if resolved_task_type:
                card_parts.append(resolved_task_type)
            if resolved_card_name:
                card_parts.append(resolved_card_name)

            card_line = f"失败步骤: {normalized_card_id}"
            if card_parts:
                card_line += f"（{' / '.join(card_parts)}）"
            lines.append(card_line)

        clean_error_message = str(error_message or "").strip()
        if clean_error_message:
            lines.append(f"失败详情: {clean_error_message}")
            self._ntfy_failure_message = clean_error_message

        for line in lines:
            if line and line not in self._ntfy_failure_context_lines:
                self._ntfy_failure_context_lines.append(line)

    def _build_ntfy_finish_context_lines(self, success: bool, result_type: str = "") -> List[str]:
        lines = list(self._ntfy_session_context_lines)
        normalized_result = str(result_type or "").strip().lower()
        if not success and normalized_result != "stopped":
            for line in self._ntfy_failure_context_lines:
                if line and line not in lines:
                    lines.append(line)
        return lines

    def _start_ntfy_execution_session(
        self,
        session_name: str = "",
        intro_message: str = "",
        context_lines: Optional[List[str]] = None,
    ):
        notifier = getattr(self, "_ntfy_notifier", None)
        if notifier is None:
            return
        try:
            resolved_name = str(session_name or "").strip() or self._resolve_ntfy_session_name()
            resolved_context = (
                list(context_lines)
                if isinstance(context_lines, list)
                else self._update_ntfy_session_context(workflow_name=resolved_name)
            )
            if isinstance(context_lines, list):
                self._ntfy_session_context_lines = list(resolved_context)
            if notifier.session_active():
                notifier.set_session_context_lines(resolved_context)
                return
            notifier.start_session(
                resolved_name,
                intro_message=intro_message,
                context_lines=resolved_context,
            )
        except Exception as exc:
            logger.warning("启动 ntfy 执行会话失败: %s", exc)

    def _record_ntfy_execution_detail(self, detail: str):
        notifier = getattr(self, "_ntfy_notifier", None)
        if notifier is None:
            return
        try:
            notifier.record_detail(detail)
        except Exception as exc:
            logger.warning("记录 ntfy 执行详情失败: %s", exc)

    def _finish_ntfy_execution_session(
        self,
        success: bool,
        summary: str,
        context_lines: Optional[List[str]] = None,
        result_type: str = "",
    ):
        notifier = getattr(self, "_ntfy_notifier", None)
        if notifier is None:
            self._reset_ntfy_execution_context()
            return
        try:
            normalized_result = str(result_type or "").strip().lower()
            resolved_context = (
                list(context_lines)
                if isinstance(context_lines, list)
                else self._build_ntfy_finish_context_lines(bool(success), normalized_result)
            )
            notifier.finish_session(
                bool(success),
                str(summary or "").strip(),
                context_lines=resolved_context,
                result_type=normalized_result,
            )
        except Exception as exc:
            logger.warning("结束 ntfy 执行会话失败: %s", exc)
        finally:
            self._reset_ntfy_execution_context()
