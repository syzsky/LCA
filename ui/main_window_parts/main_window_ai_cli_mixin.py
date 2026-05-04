import json
import logging
import os
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from services.ai.command_routing import command_requires_tool_execution

logger = logging.getLogger(__name__)


class MainWindowAiCliMixin:
    _AI_CLI_PARAM_NAME = "enable_ai_cli_dialog"
    _AI_CLI_RUNTIME_PARAMS = frozenset({"execution_trace", "response_format_hint"})
    _AI_CLI_POLL_MS = 250
    _AI_CLI_HISTORY_LIMIT = 12
    _AI_CLI_SESSION_STATUS_LIMIT = 80
    _AI_CLI_SESSION_OUTPUT_LIMIT = 120

    def _init_ai_cli_dialog(self) -> None:
        self._ai_cli_hub: Dict[str, Any] = {}
        self._ai_cli_sessions: Dict[str, Dict[str, Any]] = {}
        self._ai_cli_runtime_param_overrides: Dict[str, Dict[str, Any]] = {}
        self._ai_cli_input_timer = None
        try:
            from PySide6.QtCore import QTimer

            parent = self if hasattr(self, "metaObject") else None
            timer = QTimer(parent)
            timer.setInterval(int(self._AI_CLI_POLL_MS))
            timer.timeout.connect(self._poll_ai_cli_inputs)
            timer.start()
            self._ai_cli_input_timer = timer
        except Exception as exc:
            logger.debug("AI CLI 轮询启动失败: %s", exc)

    def _poll_ai_cli_inputs(self) -> None:
        hub = self._ensure_ai_cli_hub(start_if_missing=False)
        if not hub:
            return
        try:
            self._sync_ai_cli_hub_state()
            for payload in self._read_ai_cli_input_payloads(hub):
                self._handle_ai_cli_input_payload(payload)
            self._drain_ai_cli_command_queue()
            self._sync_ai_cli_hub_state()
        except Exception as exc:
            logger.debug("AI CLI 轮询失败: %s", exc)

    def _handle_ai_cli_card_started(self, card_id: int) -> None:
        context = self._resolve_ai_cli_card_context(card_id)
        if not context:
            return
        session = self._ensure_ai_cli_session(context)
        self._set_active_ai_cli_session(session)
        session.update(
            {
                "last_status": "",
                "last_trace_body": "",
                "last_output": "",
                "run_active": True,
                "base_prompt": str(context.get("base_command_prompt") or "").strip(),
            }
        )
        if session.get("current_prompt_override"):
            session["suppress_completion_dialog"] = True
        self._append_ai_cli_log(session, self._build_ai_cli_run_banner(context))
        self._sync_ai_cli_hub_state()

    def _handle_ai_cli_runtime_update(self, card_id: int, param_name: str, new_value: Any) -> None:
        if param_name not in self._AI_CLI_RUNTIME_PARAMS:
            return
        context = self._resolve_ai_cli_card_context(card_id)
        if not context:
            return
        session = self._ensure_ai_cli_session(context)
        text = "" if new_value is None else str(new_value)
        if param_name == "execution_trace":
            status_text, trace_body = self._split_ai_cli_trace_text(text)
            if status_text and status_text != str(session.get("last_status") or "").strip():
                self._append_ai_cli_log(session, f"[状态] {status_text}")
                session["last_status"] = status_text
            delta = self._extract_ai_cli_append_delta(str(session.get("last_trace_body") or ""), trace_body)
            if delta:
                compact = self._compact_ai_cli_trace_text(delta)
                if compact:
                    self._append_ai_cli_log(session, compact)
                session["last_trace_body"] = trace_body
        elif param_name == "response_format_hint":
            output = self._normalize_ai_cli_output_text(text)
            if output and output != str(session.get("last_output") or ""):
                self._append_ai_cli_log(session, f"[AI输出]\n{output}")
                session["last_output"] = output
        self._sync_ai_cli_hub_state()

    def _handle_ai_cli_card_finished(self, card_id: int, success: bool) -> None:
        context = self._resolve_ai_cli_card_context(card_id)
        if not context:
            return
        session = self._ensure_ai_cli_session(context)
        summary = str(session.get("last_output") or "").strip() or ("执行成功" if success else "执行失败")
        if str(session.get("current_run_mode") or "").strip().lower() == "chat":
            self._append_ai_cli_chat_turn(session, "assistant", summary)
        else:
            self._append_ai_cli_conversation_turn(session, "assistant", summary)
        self._append_ai_cli_log(
            session,
            f"[结果] {'执行成功' if success else '执行失败'}\n[会话结束] {self._format_ai_cli_timestamp()}",
        )
        session["run_active"] = False
        session["current_prompt_override"] = ""
        session["current_run_mode"] = ""
        self._clear_ai_cli_runtime_param_override_for_session(str(session.get("session_key") or ""))
        self._sync_ai_cli_hub_state()

    def _shutdown_ai_cli_sessions(self) -> None:
        timer = getattr(self, "_ai_cli_input_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        hub = dict(getattr(self, "_ai_cli_hub", {}) or {})
        process = hub.get("process")
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
            except Exception as exc:
                logger.debug("关闭 AI CLI 控制台失败: %s", exc)
        self._ai_cli_hub = {}
        self._ai_cli_sessions = {}
        self._ai_cli_runtime_param_overrides = {}

    def _handle_ai_cli_input_payload(self, payload: Dict[str, Any]) -> None:
        hub = self._ensure_ai_cli_hub(start_if_missing=False)
        if not hub:
            return
        text = str(payload.get("text") or "").strip()
        if not text:
            return
        command_id = str(payload.get("id") or "").strip() or f"cmd_{int(time.time() * 1000)}"
        hub["last_processed_command_id"] = command_id
        if text.startswith("/"):
            self._handle_ai_cli_cli_command(text)
        else:
            self._route_ai_cli_text_command(text)
        self._sync_ai_cli_hub_state()

    def _handle_ai_cli_cli_command(self, text: str) -> None:
        command_text = str(text or "").strip()
        verb, _, arg = command_text.partition(" ")
        normalized_verb = verb.strip().lower()
        argument = arg.strip()
        if normalized_verb == "/help":
            self._append_ai_cli_log(
                None,
                "\n".join(
                    [
                        "[帮助]",
                        "/list 查看 AI 会话",
                        "/use <会话ID> 切换会话，可用 任务ID:卡片ID 或唯一卡片ID",
                        "/send <内容> 向当前会话追加指令",
                        "/status 查看当前会话状态",
                        "/exit 关闭窗口",
                    ]
                ),
            )
            return
        if normalized_verb == "/list":
            self._append_ai_cli_log(None, self._build_ai_cli_session_list_text())
            return
        if normalized_verb == "/status":
            self._append_ai_cli_log(None, self._build_ai_cli_current_status_text())
            return
        if normalized_verb == "/use":
            session, error = self._resolve_ai_cli_target_session(argument)
            if session is None:
                self._append_ai_cli_log(None, error or "[系统] 未找到目标会话。")
                return
            self._set_active_ai_cli_session(session)
            self._append_ai_cli_log(None, f"[系统] 当前会话已切换为 {session['session_key']} {session['display_name']}")
            self._sync_ai_cli_hub_state()
            return
        if normalized_verb == "/send":
            if not argument:
                self._append_ai_cli_log(None, "[系统] /send 缺少内容。")
                return
            self._route_ai_cli_text_command(argument)
            return
        self._append_ai_cli_log(None, f"[系统] 未知命令: {command_text}")

    def _route_ai_cli_text_command(self, text: str) -> None:
        session = self._get_active_ai_cli_session()
        if session is None:
            session = self._resolve_default_ai_cli_session()
            if session is None:
                self._append_ai_cli_log(None, "[系统] 当前没有可用 AI 会话，请先执行启用 CLI 的 AI 卡片。")
                return
            self._set_active_ai_cli_session(session)
        command_text = str(text or "").strip()
        self._append_ai_cli_log(None, f"[你][{session['session_key']}] {command_text}")
        local_response = self._build_ai_cli_local_response(session, command_text)
        if local_response:
            self._append_ai_cli_local_reply(session, local_response)
            return
        command = {
            "id": f"cmd_{int(time.time() * 1000)}",
            "text": command_text,
            "route_mode": self._classify_ai_cli_route_mode(command_text),
        }
        session.setdefault("pending_user_commands", []).append(command)
        if self._is_ai_cli_workflow_busy() or session.get("run_active"):
            self._append_ai_cli_log(None, f"[系统] {session['session_key']} 已接收命令，等待当前执行结束。")

    def _drain_ai_cli_command_queue(self) -> None:
        if self._is_ai_cli_workflow_busy():
            return
        active_session = self._get_active_ai_cli_session()
        candidates: List[Dict[str, Any]] = []
        if active_session is not None and active_session.get("pending_user_commands"):
            candidates.append(active_session)
        for session in (getattr(self, "_ai_cli_sessions", {}) or {}).values():
            if session is active_session:
                continue
            if session.get("pending_user_commands"):
                candidates.append(session)
        if not candidates:
            return
        self._start_ai_cli_command_execution(candidates[0])

    def _start_ai_cli_command_execution(self, session: Dict[str, Any]) -> None:
        pending = session.get("pending_user_commands") or []
        if not pending:
            return
        command = pending.pop(0)
        session_key = str(session.get("session_key") or "")
        card_id = int(session.get("card_id") or 0)
        task_id = session.get("task_id")
        context = self._resolve_ai_cli_card_context(card_id, preferred_task_id=task_id)
        if not context:
            self._append_ai_cli_log(None, f"[系统] {session_key or card_id} 对应的 AI 卡片不可用，无法继续执行。")
            return
        self._register_ai_cli_session_context(session, context)
        self._set_active_ai_cli_session(session)
        self._activate_ai_cli_task_tab(context.get("task_id"))
        text = str(command.get("text") or "").strip()
        route_mode = str(command.get("route_mode") or "tool").strip().lower()
        if route_mode == "chat":
            history_before_turn = list(session.get("chat_history") or [])
            prompt = text
            self._append_ai_cli_chat_turn(session, "user", text)
            runtime_overrides = {
                "command_prompt": prompt,
                "ai_cli_route_mode": "chat",
                "ai_chat_history": history_before_turn,
            }
        else:
            prompt = self._build_ai_cli_follow_up_prompt(session, text)
            self._append_ai_cli_conversation_turn(session, "user", text)
            runtime_overrides = {
                "command_prompt": prompt,
                "ai_cli_route_mode": "tool",
            }
        session["current_prompt_override"] = prompt
        session["current_run_mode"] = route_mode
        self._set_ai_cli_runtime_param_override_for_session(session_key, runtime_overrides)
        self._append_ai_cli_log(None, f"[系统] {session_key} 已接收命令，开始执行。")
        self._sync_ai_cli_hub_state()
        try:
            self._handle_test_card_execution(card_id)
        except Exception as exc:
            session["current_prompt_override"] = ""
            session["current_run_mode"] = ""
            self._clear_ai_cli_runtime_param_override_for_session(session_key)
            self._append_ai_cli_log(None, f"[系统] {session_key} 启动执行失败: {exc}")

    def _ensure_ai_cli_hub(self, start_if_missing: bool = True) -> Optional[Dict[str, Any]]:
        hub = getattr(self, "_ai_cli_hub", None)
        if isinstance(hub, dict):
            process = hub.get("process")
            if process is not None and process.poll() is None:
                return hub
        if not start_if_missing:
            return None

        runtime_dir = self._ensure_ai_cli_runtime_dir()
        token = f"hub_{int(time.time() * 1000)}"
        os.makedirs(runtime_dir, exist_ok=True)
        log_path = os.path.join(runtime_dir, f"ai_cli_{token}.log")
        input_path = os.path.join(runtime_dir, f"ai_cli_{token}.inbox")
        state_path = os.path.join(runtime_dir, f"ai_cli_{token}.state.json")
        bridge_name = f"ai_cli_{token}.ps1"
        bridge_path = os.path.join(runtime_dir, bridge_name)
        launch_path = os.path.join(runtime_dir, f"ai_cli_{token}.cmd")
        for path in (log_path, input_path):
            with open(path, "w", encoding="utf-8", newline="\n") as file:
                file.write("")
        with open(bridge_path, "w", encoding="utf-8-sig", newline="\n") as file:
            file.write(
                self._build_ai_cli_bridge_script(
                    os.path.basename(log_path),
                    os.path.basename(input_path),
                    os.path.basename(state_path),
                )
            )
        with open(launch_path, "w", encoding="ascii", newline="\r\n") as file:
            file.write(self._build_ai_cli_launch_script(bridge_name, "LCA AI CLI"))
        self._write_ai_cli_state_file(
            state_path,
            {
                # 控制台进程启动后会先读取一次 state；这里必须先标记为忙碌，
                # 避免在主程序完成首轮会话同步前提前进入 Read-Host 阻塞，
                # 导致后续 AI 输出虽然写入日志文件，但控制台无法继续刷新。
                "busy": True,
                "last_processed_command_id": "",
                "active_prompt": "LCA",
            },
        )
        process = subprocess.Popen(
            ["cmd.exe", "/d", "/k", launch_path],
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0,
            cwd=os.path.dirname(launch_path) or None,
        )
        self._ai_cli_hub = {
            "log_path": log_path,
            "input_path": input_path,
            "state_path": state_path,
            "process": process,
            "input_offset": 0,
            "input_remainder": "",
            "last_state_json": "",
            "last_processed_command_id": "",
            "active_session_key": "",
        }
        return self._ai_cli_hub

    def _ensure_ai_cli_session(self, context: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_ai_cli_hub()
        session_key = str(context.get("session_key") or "")
        sessions = getattr(self, "_ai_cli_sessions", None)
        if not isinstance(sessions, dict):
            self._ai_cli_sessions = {}
            sessions = self._ai_cli_sessions
        session = sessions.get(session_key)
        if session is None:
            session = {
                "session_key": session_key,
                "task_id": context.get("task_id"),
                "card_id": int(context.get("card_id") or 0),
                "workflow_name": str(context.get("workflow_name") or "").strip(),
                "card_name": str(context.get("card_name") or "").strip(),
                "display_name": str(context.get("display_name") or "").strip(),
                "prompt_name": str(context.get("prompt_name") or "").strip(),
                "base_prompt": str(context.get("base_command_prompt") or "").strip(),
                "model": str(((context.get("parameters") or {}).get("model") or "")).strip(),
                "last_status": "",
                "last_trace_body": "",
                "last_output": "",
                "run_active": False,
                "pending_user_commands": [],
                "conversation_history": [],
                "chat_history": [],
                "current_prompt_override": "",
                "current_run_mode": "",
                "suppress_completion_dialog": False,
                "created_at": self._format_ai_cli_timestamp(),
            }
            sessions[session_key] = session
        else:
            self._register_ai_cli_session_context(session, context)
        hub = self._ensure_ai_cli_hub()
        if hub and not str(hub.get("active_session_key") or "").strip():
            hub["active_session_key"] = session_key
        return session

    def _register_ai_cli_session_context(self, session: Dict[str, Any], context: Dict[str, Any]) -> None:
        session["task_id"] = context.get("task_id")
        session["card_id"] = int(context.get("card_id") or 0)
        session["workflow_name"] = str(context.get("workflow_name") or "").strip()
        session["card_name"] = str(context.get("card_name") or "").strip()
        session["display_name"] = str(context.get("display_name") or "").strip()
        session["prompt_name"] = str(context.get("prompt_name") or "").strip()
        session["base_prompt"] = str(context.get("base_command_prompt") or "").strip()
        session["model"] = str(((context.get("parameters") or {}).get("model") or "")).strip()

    def _set_active_ai_cli_session(self, session: Dict[str, Any]) -> None:
        hub = self._ensure_ai_cli_hub()
        if hub is not None:
            hub["active_session_key"] = str(session.get("session_key") or "")

    def _get_active_ai_cli_session(self) -> Optional[Dict[str, Any]]:
        hub = self._ensure_ai_cli_hub(start_if_missing=False)
        sessions = getattr(self, "_ai_cli_sessions", {}) or {}
        if not hub:
            return None
        session_key = str(hub.get("active_session_key") or "").strip()
        if session_key:
            session = sessions.get(session_key)
            if session is not None:
                return session
        return None

    def _resolve_default_ai_cli_session(self) -> Optional[Dict[str, Any]]:
        sessions = list((getattr(self, "_ai_cli_sessions", {}) or {}).values())
        if not sessions:
            return None
        if len(sessions) == 1:
            return sessions[0]
        active = self._get_active_ai_cli_session()
        if active is not None:
            return active
        return sessions[0]

    def _resolve_ai_cli_target_session(self, token: str) -> Tuple[Optional[Dict[str, Any]], str]:
        text = str(token or "").strip()
        if not text:
            current = self._get_active_ai_cli_session()
            if current is not None:
                return current, ""
            return None, "[系统] /use 缺少目标会话。"
        sessions = getattr(self, "_ai_cli_sessions", {}) or {}
        if text in sessions:
            return sessions[text], ""
        normalized = text.lstrip("#")
        exact_card_matches = [
            session
            for session in sessions.values()
            if str(session.get("card_id") or "").strip() == normalized
        ]
        if len(exact_card_matches) == 1:
            return exact_card_matches[0], ""
        if len(exact_card_matches) > 1:
            return None, f"[系统] 卡片ID {normalized} 存在多个会话，请改用 任务ID:卡片ID。"
        return None, f"[系统] 未找到会话: {text}"

    def _append_ai_cli_log(self, session: Optional[Dict[str, Any]], text: str) -> None:
        content = str(text or "").strip()
        if not content:
            return
        hub = self._ensure_ai_cli_hub()
        if not hub:
            return
        active_session = self._get_active_ai_cli_session()
        if session is not None and active_session is not None:
            current_key = str(session.get("session_key") or "")
            active_key = str(active_session.get("session_key") or "")
            if current_key and active_key and current_key != active_key:
                content = f"[会话] {current_key} {session.get('display_name')}\n{content}"
        log_path = str(hub.get("log_path") or "").strip()
        if not log_path:
            return
        with open(log_path, "a", encoding="utf-8", newline="\n") as file:
            file.write(content)
            if not content.endswith("\n"):
                file.write("\n")
            file.write("\n")

    def _sync_ai_cli_hub_state(self) -> None:
        hub = self._ensure_ai_cli_hub(start_if_missing=False)
        if not hub:
            return
        state_path = str(hub.get("state_path") or "").strip()
        if not state_path:
            return
        active_session = self._get_active_ai_cli_session()
        payload = {
            "busy": bool(
                self._is_ai_cli_workflow_busy()
                or self._has_ai_cli_pending_activity()
            ),
            "last_processed_command_id": str(hub.get("last_processed_command_id") or ""),
            "active_prompt": self._build_ai_cli_prompt(active_session),
            "active_session_key": str((active_session or {}).get("session_key") or ""),
            "session_count": len(getattr(self, "_ai_cli_sessions", {}) or {}),
        }
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if text == str(hub.get("last_state_json") or ""):
            return
        self._write_ai_cli_state_file(state_path, payload)
        hub["last_state_json"] = text

    @staticmethod
    def _write_ai_cli_state_file(state_path: str, payload: Dict[str, Any]) -> None:
        temp_path = f"{state_path}.tmp"
        with open(temp_path, "w", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, ensure_ascii=False)
        os.replace(temp_path, state_path)

    def _has_ai_cli_pending_activity(self) -> bool:
        for session in (getattr(self, "_ai_cli_sessions", {}) or {}).values():
            if not isinstance(session, dict):
                continue
            if session.get("run_active"):
                return True
            if session.get("pending_user_commands"):
                return True
        return False

    def _read_ai_cli_input_payloads(self, hub: Dict[str, Any]) -> List[Dict[str, Any]]:
        input_path = str(hub.get("input_path") or "").strip()
        if not input_path or not os.path.exists(input_path):
            return []
        offset = int(hub.get("input_offset") or 0)
        remainder = str(hub.get("input_remainder") or "")
        if os.path.getsize(input_path) < offset:
            offset = 0
            remainder = ""
        with open(input_path, "r", encoding="utf-8", newline="") as file:
            file.seek(offset)
            chunk = file.read()
            hub["input_offset"] = file.tell()
        if not chunk:
            return []
        merged = remainder + chunk
        lines = merged.splitlines()
        if merged.endswith(("\n", "\r")):
            hub["input_remainder"] = ""
        else:
            hub["input_remainder"] = lines.pop() if lines else merged
        payloads: List[Dict[str, Any]] = []
        for raw_line in lines:
            line = str(raw_line or "").strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                payload = {"text": line}
            if isinstance(payload, dict) and str(payload.get("text") or "").strip():
                payloads.append(payload)
        return payloads

    def _append_ai_cli_conversation_turn(self, session: Dict[str, Any], role: str, content: str) -> None:
        text = str(content or "").strip()
        if not text:
            return
        history = session.setdefault("conversation_history", [])
        history.append({"role": str(role or "").strip(), "content": text})
        if len(history) > int(self._AI_CLI_HISTORY_LIMIT):
            del history[:-int(self._AI_CLI_HISTORY_LIMIT)]

    def _append_ai_cli_chat_turn(self, session: Dict[str, Any], role: str, content: str) -> None:
        text = str(content or "").strip()
        if not text:
            return
        history = session.setdefault("chat_history", [])
        history.append({"role": str(role or "").strip(), "content": text})
        if len(history) > int(self._AI_CLI_HISTORY_LIMIT):
            del history[:-int(self._AI_CLI_HISTORY_LIMIT)]

    def _classify_ai_cli_route_mode(self, text: str) -> str:
        return "tool" if command_requires_tool_execution(text) else "chat"

    def _build_ai_cli_local_response(self, session: Dict[str, Any], text: str) -> str:
        normalized = self._normalize_ai_cli_lookup_text(text)
        if not normalized:
            return ""
        if any(token in normalized for token in ("什么模型", "啥模型", "当前模型", "现在模型", "用的模型", "模型是什么")):
            model = str(session.get("model") or "").strip()
            return f"当前会话使用的模型是 {model}。" if model else "当前会话没有配置模型。"
        if any(token in normalized for token in ("当前工作流", "工作流是什么", "哪个工作流")):
            workflow_name = str(session.get("workflow_name") or "").strip() or "未命名工作流"
            return f"当前工作流是 {workflow_name}。"
        if any(token in normalized for token in ("当前卡片", "卡片是什么", "哪个卡片")):
            card_name = str(session.get("card_name") or "").strip() or "AI工具"
            return f"当前卡片是 {card_name}。"
        if any(token in normalized for token in ("当前会话", "会话是什么", "会话id", "sessionid")):
            session_key = str(session.get("session_key") or "").strip() or "未知会话"
            return f"当前会话是 {session_key}。"
        if any(token in normalized for token in ("当前状态", "现在状态", "会话状态", "状态是什么")):
            return self._build_ai_cli_current_status_text()
        return ""

    def _append_ai_cli_local_reply(self, session: Dict[str, Any], text: str) -> None:
        reply = str(text or "").strip()
        if not reply:
            return
        self._append_ai_cli_log(session, f"[助手][{session['session_key']}] {reply}")
        session["last_output"] = reply

    @staticmethod
    def _normalize_ai_cli_lookup_text(text: str) -> str:
        return "".join(str(text or "").strip().lower().split())

    def _build_ai_cli_follow_up_prompt(self, session: Dict[str, Any], text: str) -> str:
        history_lines = []
        for item in session.get("conversation_history") or []:
            role = "用户" if str(item.get("role") or "").strip().lower() == "user" else "助手"
            content = self._truncate_ai_cli_text(str(item.get("content") or "").strip(), 600)
            if content:
                history_lines.append(f"{role}: {content}")
        history_text = "\n".join(history_lines).strip() or "无"
        base_prompt = str(session.get("base_prompt") or "").strip() or "无"
        return "\n".join(
            [
                "你正在延续同一个自动化 CLI 会话。",
                "",
                "原始任务：",
                base_prompt,
                "",
                "会话历史：",
                history_text,
                "",
                "当前追加指令：",
                str(text or "").strip(),
                "",
                "要求：结合原始任务、会话历史和当前截图继续执行；如果当前追加指令与原始任务冲突，以当前追加指令为准。",
            ]
        ).strip()

    def _set_ai_cli_runtime_param_override_for_session(self, session_key: str, values: Dict[str, Any]) -> None:
        key = str(session_key or "").strip()
        if not key:
            return
        merged = dict((getattr(self, "_ai_cli_runtime_param_overrides", {}) or {}).get(key) or {})
        merged.update(dict(values or {}))
        self._ai_cli_runtime_param_overrides[key] = merged

    def _clear_ai_cli_runtime_param_override_for_session(self, session_key: str) -> None:
        key = str(session_key or "").strip()
        if not key:
            return
        (getattr(self, "_ai_cli_runtime_param_overrides", {}) or {}).pop(key, None)

    def _apply_ai_cli_runtime_parameter_overrides(self, card_id: int, params: Dict[str, Any]) -> Dict[str, Any]:
        session_key = self._resolve_ai_cli_runtime_override_key(card_id)
        merged = dict(params or {})
        if session_key:
            merged.update(dict((getattr(self, "_ai_cli_runtime_param_overrides", {}) or {}).get(session_key) or {}))
        return merged

    def _resolve_ai_cli_runtime_override_key(self, card_id: int) -> str:
        context = self._resolve_ai_cli_card_context(card_id)
        if context:
            return str(context.get("session_key") or "")
        suffix = f":{int(card_id)}"
        matches = [
            key
            for key in (getattr(self, "_ai_cli_runtime_param_overrides", {}) or {}).keys()
            if str(key or "").endswith(suffix)
        ]
        if len(matches) == 1:
            return matches[0]
        return ""

    def _consume_ai_cli_completion_dialog_suppression(self) -> bool:
        for session in (getattr(self, "_ai_cli_sessions", {}) or {}).values():
            if isinstance(session, dict) and session.get("suppress_completion_dialog"):
                session["suppress_completion_dialog"] = False
                return True
        return False

    def _build_ai_cli_session_list_text(self) -> str:
        sessions = list((getattr(self, "_ai_cli_sessions", {}) or {}).values())
        if not sessions:
            return "[会话列表]\n无"
        active_key = str(((self._ensure_ai_cli_hub(start_if_missing=False) or {}).get("active_session_key") or "")).strip()
        lines = ["[会话列表]"]
        for session in sessions:
            marker = "*" if str(session.get("session_key") or "") == active_key else "-"
            lines.append(
                f"{marker} {session['session_key']} {session['display_name']} | {self._describe_ai_cli_session(session)} | 队列:{len(session.get('pending_user_commands') or [])}"
            )
        return "\n".join(lines)

    def _build_ai_cli_current_status_text(self) -> str:
        session = self._get_active_ai_cli_session()
        if session is None:
            return "[状态] 当前没有活跃 AI 会话。"
        lines = [
            f"[状态] 当前会话: {session['session_key']} {session['display_name']}",
            f"执行状态: {self._describe_ai_cli_session(session)}",
            f"队列长度: {len(session.get('pending_user_commands') or [])}",
        ]
        last_output = self._truncate_ai_cli_text(
            str(session.get("last_output") or "").strip(),
            self._AI_CLI_SESSION_OUTPUT_LIMIT,
        )
        if last_output:
            lines.append(f"最近输出: {last_output}")
        return "\n".join(lines)

    def _describe_ai_cli_session(self, session: Dict[str, Any]) -> str:
        if session.get("run_active"):
            status = str(session.get("last_status") or "").strip() or "执行中"
            return self._truncate_ai_cli_text(status, self._AI_CLI_SESSION_STATUS_LIMIT)
        pending = len(session.get("pending_user_commands") or [])
        if pending > 0:
            return f"排队中({pending})"
        last_output = str(session.get("last_output") or "").strip()
        if last_output:
            return self._truncate_ai_cli_text(last_output, self._AI_CLI_SESSION_STATUS_LIMIT)
        return "空闲"

    def _build_ai_cli_run_banner(self, context: Dict[str, Any]) -> str:
        params = context.get("parameters") or {}
        lines = ["=" * 72, f"[会话开始] {self._format_ai_cli_timestamp()}"]
        lines.append(f"[会话] {context['session_key']}")
        if str(context.get("workflow_name") or "").strip():
            lines.append(f"[工作流] {context['workflow_name']}")
        if str(context.get("card_name") or "").strip():
            lines.append(f"[卡片] {context['card_name']}")
        if str(params.get("model") or "").strip():
            lines.append(f"[模型] {params['model']}")
        command_prompt = str(params.get("command_prompt") or "").strip()
        if command_prompt:
            lines.extend(["[指令]", command_prompt])
        lines.append("=" * 72)
        return "\n".join(lines)

    @staticmethod
    def _build_ai_cli_launch_script(bridge_file_name: str, console_title: str) -> str:
        title = MainWindowAiCliMixin._sanitize_ai_cli_console_title(console_title)
        bridge = str(bridge_file_name or "").replace('"', "")
        return (
            "@echo off\r\nsetlocal EnableExtensions\r\n"
            "\"%SystemRoot%\\System32\\chcp.com\" 65001>nul\r\n"
            f"title {title}\r\n"
            "echo [LCA AI CLI] Console ready. Waiting for session...\r\n"
            "\"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe\" -NoLogo -ExecutionPolicy Bypass -NoExit -File "
            f"\"%~dp0{bridge}\"\r\n"
        )

    @staticmethod
    def _build_ai_cli_bridge_script(log_name: str, input_name: str, state_name: str) -> str:
        esc = MainWindowAiCliMixin._escape_ai_cli_powershell_single_quote
        lines = [
            "[Console]::InputEncoding = [System.Text.Encoding]::UTF8",
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            "$ErrorActionPreference = 'SilentlyContinue'",
            f"$script:logPath = Join-Path $PSScriptRoot '{esc(log_name)}'",
            f"$script:inputPath = Join-Path $PSScriptRoot '{esc(input_name)}'",
            f"$script:statePath = Join-Path $PSScriptRoot '{esc(state_name)}'",
            "$script:lastLogOffset = 0L",
            "$script:waitingCommandId = ''",
            "$script:inputBuffer = ''",
            "$script:promptShown = $false",
            "$script:lastPromptText = ''",
            "function Read-State { try { $raw = [System.IO.File]::ReadAllText($script:statePath, [System.Text.UTF8Encoding]::new($false)); if ([string]::IsNullOrWhiteSpace($raw)) { return @{ busy = $false; last_processed_command_id = ''; active_prompt = 'LCA' } }; return ($raw | ConvertFrom-Json) } catch { return @{ busy = $false; last_processed_command_id = ''; active_prompt = 'LCA' } } }",
            "function Hide-Prompt { if (-not $script:promptShown) { return }; [Console]::WriteLine(''); $script:promptShown = $false }",
            "function Show-NewLog { if (-not (Test-Path $script:logPath)) { return }; try { $length = (Get-Item $script:logPath).Length } catch { return }; if ($length -lt $script:lastLogOffset) { $script:lastLogOffset = 0L }; if ($length -le $script:lastLogOffset) { return }; $stream = [System.IO.File]::Open($script:logPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite); try { $stream.Seek($script:lastLogOffset, [System.IO.SeekOrigin]::Begin) | Out-Null; $reader = New-Object System.IO.StreamReader($stream, [System.Text.UTF8Encoding]::new($false), $true, 4096, $true); try { $chunk = $reader.ReadToEnd() } finally { $reader.Dispose() }; $script:lastLogOffset = $stream.Position } finally { $stream.Dispose() }; if ($chunk) { if ($script:promptShown) { [Console]::WriteLine(''); $script:promptShown = $false }; [Console]::Write($chunk); if (-not $chunk.EndsWith([Environment]::NewLine)) { [Console]::WriteLine('') } } }",
            "function Submit-UserCommand([string]$text) { $id = [Guid]::NewGuid().ToString('N'); $payload = @{ id = $id; text = $text } | ConvertTo-Json -Compress; [System.IO.File]::AppendAllText($script:inputPath, $payload + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false)); $script:waitingCommandId = $id }",
            "function Ensure-Prompt([string]$prompt) { if ([string]::IsNullOrWhiteSpace($prompt)) { $prompt = 'LCA' }; if ($script:promptShown -and $script:lastPromptText -eq $prompt) { return }; if ($script:promptShown) { [Console]::WriteLine('') }; [Console]::Write('{0}: ' -f $prompt); if ($script:inputBuffer) { [Console]::Write($script:inputBuffer) }; $script:lastPromptText = $prompt; $script:promptShown = $true }",
            "function Consume-Input([string]$prompt) { Ensure-Prompt $prompt; while ([Console]::KeyAvailable) { $key = [Console]::ReadKey($true); if ($key.Key -eq [ConsoleKey]::Enter) { $text = $script:inputBuffer.Trim(); $script:inputBuffer = ''; $script:promptShown = $false; [Console]::WriteLine(''); if (-not $text) { return $null }; if ($text.ToLowerInvariant() -eq '/exit') { return '__LCA_EXIT__' }; return $text }; if ($key.Key -eq [ConsoleKey]::Backspace) { if ($script:inputBuffer.Length -gt 0) { $script:inputBuffer = $script:inputBuffer.Substring(0, $script:inputBuffer.Length - 1); [Console]::Write(\"`b `b\") }; continue }; if ($key.KeyChar -eq [char]0) { continue }; if ([char]::IsControl($key.KeyChar)) { continue }; $script:inputBuffer += $key.KeyChar; [Console]::Write($key.KeyChar) }; return $null }",
            "Write-Host '[LCA AI CLI] Console ready. 输入 /list 查看会话，/use 切换，/send 追加指令。'",
            "Write-Host '[LCA AI CLI] 命令: /help 查看帮助，/exit 关闭窗口。'",
            "while ($true) { Show-NewLog; $state = Read-State; if ($script:waitingCommandId) { Hide-Prompt; if ($state.last_processed_command_id -eq $script:waitingCommandId) { $script:waitingCommandId = '' }; Start-Sleep -Milliseconds 150; continue }; if ($state.busy) { Hide-Prompt; Start-Sleep -Milliseconds 150; continue }; $prompt = [string]$state.active_prompt; if ([string]::IsNullOrWhiteSpace($prompt)) { $prompt = 'LCA' }; $commandText = Consume-Input $prompt; if ($commandText -eq '__LCA_EXIT__') { break }; if ($null -eq $commandText) { Start-Sleep -Milliseconds 80; continue }; Submit-UserCommand $commandText; Write-Host '[系统] 已提交，等待主程序接收...' }",
        ]
        return "\n".join(lines) + "\n"

    def _build_ai_cli_prompt(self, session: Optional[Dict[str, Any]]) -> str:
        if session is None:
            return "LCA"
        prompt_name = str(session.get("prompt_name") or "").strip() or str(session.get("display_name") or "").strip()
        if not prompt_name:
            prompt_name = str(session.get("session_key") or "").strip() or "LCA"
        return f"LCA[{prompt_name}]"

    @staticmethod
    def _sanitize_ai_cli_console_title(value: str) -> str:
        text = str(value or "").strip() or "LCA AI CLI"
        for char in '<>:"/\\|?*':
            text = text.replace(char, " ")
        return " ".join(text.split())[:90] or "LCA AI CLI"

    @staticmethod
    def _escape_ai_cli_powershell_single_quote(value: str) -> str:
        return str(value or "").replace("'", "''")

    @staticmethod
    def _split_ai_cli_trace_text(trace_text: str) -> Tuple[str, str]:
        lines = str(trace_text or "").strip().splitlines()
        if not lines:
            return "", ""
        first_line = str(lines[0] or "").strip()
        prefix = "当前状态："
        if first_line.startswith(prefix):
            return first_line[len(prefix):].strip(), "\n".join(lines[1:]).strip()
        return "", "\n".join(lines).strip()

    @staticmethod
    def _extract_ai_cli_append_delta(previous_text: str, current_text: str) -> str:
        old_value = str(previous_text or "")
        new_value = str(current_text or "")
        if not new_value:
            return ""
        if not old_value:
            return new_value.strip()
        if new_value.startswith(old_value):
            return new_value[len(old_value):].lstrip("\r\n").strip()
        if old_value.startswith(new_value):
            return ""
        return f"[过程重置]\n{new_value.strip()}"

    @staticmethod
    def _normalize_ai_cli_output_text(value: Any) -> str:
        text = str(value or "").strip()
        if not text or text[:1] not in {"{", "["}:
            return text
        try:
            parsed = json.loads(text)
        except Exception:
            return text
        if isinstance(parsed, dict) and str(parsed.get("mode") or "").strip().lower() == "continuous":
            lines = [f"状态: {str(parsed.get('status') or '').strip()}"]
            rounds = parsed.get("rounds")
            if isinstance(rounds, list) and rounds:
                lines.append(f"轮数: {len(rounds)}")
            if str(parsed.get("reason") or "").strip():
                lines.append(f"结论: {parsed['reason']}")
            return "\n".join(line for line in lines if line.strip()).strip()
        try:
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            return text

    @classmethod
    def _compact_ai_cli_trace_text(cls, text: str) -> str:
        output = []
        for raw_line in str(text or "").splitlines():
            line = str(raw_line or "").strip()
            if not line:
                continue
            if line.startswith("第") and ("预期结果:" in line or "调试图:" in line):
                continue
            if line.startswith("第") and "规划状态:" in line:
                line = line.partition(" - ")[0] or line
            elif line.startswith("第") and "计划:" in line:
                prefix, _, summary = line.partition("计划:")
                line = f"{prefix.strip()}计划: {cls._truncate_ai_cli_text(summary.strip(), 120)}"
            elif line.startswith("第") and "执行:" in line:
                prefix, _, summary = line.partition("执行:")
                line = f"{prefix.strip()}执行: {cls._truncate_ai_cli_text(summary.strip(), 120)}"
            else:
                line = cls._truncate_ai_cli_text(line, 160)
            if line:
                output.append(line)
        return "\n".join(output).strip()

    @staticmethod
    def _truncate_ai_cli_text(text: str, limit: int) -> str:
        content = str(text or "").strip()
        if len(content) <= int(limit):
            return content
        return content[: max(0, int(limit) - 1)].rstrip() + "…"

    @staticmethod
    def _format_ai_cli_timestamp() -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def _resolve_ai_cli_card_context(self, card_id: int, preferred_task_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        resolved = self._find_ai_cli_card(card_id, preferred_task_id=preferred_task_id)
        if not resolved:
            return None
        task_id, card = resolved
        if str(getattr(card, "task_type", "") or "").strip() != "AI工具":
            return None
        raw_params = dict(getattr(card, "parameters", {}) or {})
        if not self._coerce_ai_cli_bool(raw_params.get(self._AI_CLI_PARAM_NAME, False)):
            return None
        session_key = self._make_ai_cli_session_key(task_id, card_id)
        params = dict(raw_params)
        params.update(dict((getattr(self, "_ai_cli_runtime_param_overrides", {}) or {}).get(session_key) or {}))
        workflow_name = self._resolve_ai_cli_workflow_name(task_id)
        card_name = str(
            getattr(card, "custom_name", None)
            or raw_params.get("name")
            or raw_params.get("description")
            or "AI工具"
        ).strip()
        display_name = self._build_ai_cli_display_name(workflow_name, card_name, card_id)
        prompt_name = self._build_ai_cli_prompt_name(workflow_name, card_name, card_id)
        return {
            "task_id": task_id,
            "card_id": int(card_id),
            "session_key": session_key,
            "card": card,
            "parameters": params,
            "base_command_prompt": str(raw_params.get("command_prompt") or "").strip(),
            "workflow_name": workflow_name,
            "card_name": card_name,
            "display_name": display_name,
            "prompt_name": prompt_name,
        }

    def _find_ai_cli_card(self, card_id: int, preferred_task_id: Optional[int] = None) -> Optional[Tuple[Optional[int], Any]]:
        task_views = getattr(getattr(self, "workflow_tab_widget", None), "task_views", {}) or {}
        candidate_task_ids: List[Optional[int]] = []
        if preferred_task_id is not None:
            candidate_task_ids.append(preferred_task_id)
        active_execution_task_id = getattr(self, "_active_execution_task_id", None)
        if active_execution_task_id is not None:
            candidate_task_ids.append(active_execution_task_id)
        active_session = self._get_active_ai_cli_session()
        if active_session is not None:
            candidate_task_ids.append(active_session.get("task_id"))
        try:
            current_task_id = self.workflow_tab_widget.get_current_task_id()
        except Exception:
            current_task_id = None
        if current_task_id is not None:
            candidate_task_ids.append(current_task_id)

        seen_task_ids = set()
        for task_id in candidate_task_ids:
            if task_id in seen_task_ids:
                continue
            seen_task_ids.add(task_id)
            view = task_views.get(task_id)
            if view is None or not hasattr(view, "cards"):
                continue
            card = view.cards.get(card_id)
            if card is not None:
                return task_id, card

        workflow_view = getattr(self, "workflow_view", None)
        if workflow_view is not None and hasattr(workflow_view, "cards"):
            card = workflow_view.cards.get(card_id)
            if card is not None:
                return current_task_id, card

        for task_id, view in task_views.items():
            if task_id in seen_task_ids:
                continue
            if view is None or not hasattr(view, "cards"):
                continue
            card = view.cards.get(card_id)
            if card is not None:
                return task_id, card
        return None

    def _activate_ai_cli_task_tab(self, task_id: Optional[int]) -> None:
        if task_id is None:
            return
        tab_widget = getattr(self, "workflow_tab_widget", None)
        if tab_widget is None:
            return
        try:
            task_to_tab = getattr(tab_widget, "task_to_tab", {}) or {}
            tab_index = task_to_tab.get(task_id)
            if tab_index is None and hasattr(tab_widget, "_rebuild_mappings"):
                tab_widget._rebuild_mappings()
                tab_index = (getattr(tab_widget, "task_to_tab", {}) or {}).get(task_id)
            if tab_index is not None and tab_widget.currentIndex() != tab_index:
                tab_widget.setCurrentIndex(tab_index)
            workflow_view = (getattr(tab_widget, "task_views", {}) or {}).get(task_id)
            if workflow_view is not None:
                self.workflow_view = workflow_view
        except Exception as exc:
            logger.debug("AI CLI 切标签失败 task=%s: %s", task_id, exc)

    def _resolve_ai_cli_workflow_name(self, task_id: Optional[int]) -> str:
        task_manager = getattr(self, "task_manager", None)
        if task_id is None or task_manager is None or not hasattr(task_manager, "get_task"):
            return ""
        try:
            task = task_manager.get_task(task_id)
        except Exception:
            task = None
        return str(getattr(task, "name", "") or "").strip()

    @staticmethod
    def _make_ai_cli_session_key(task_id: Optional[int], card_id: int) -> str:
        if task_id is None:
            return f"?:{int(card_id)}"
        return f"{int(task_id)}:{int(card_id)}"

    @staticmethod
    def _build_ai_cli_display_name(workflow_name: str, card_name: str, card_id: int) -> str:
        workflow = str(workflow_name or "").strip() or "未命名工作流"
        card = str(card_name or "").strip() or "AI工具"
        return f"{workflow}/{card}#{int(card_id)}"

    @staticmethod
    def _build_ai_cli_prompt_name(workflow_name: str, card_name: str, card_id: int) -> str:
        workflow = str(workflow_name or "").strip() or "未命名"
        card = str(card_name or "").strip() or "AI工具"
        return f"{workflow}/{card}#{int(card_id)}"

    @staticmethod
    def _ensure_ai_cli_runtime_dir() -> str:
        return os.path.join(tempfile.gettempdir(), "lca_ai_cli")

    def _is_ai_cli_workflow_busy(self) -> bool:
        control_center = getattr(self, "control_center", None)
        if control_center is not None:
            try:
                if control_center.is_any_task_running():
                    return True
            except Exception:
                pass
        thread = getattr(self, "executor_thread", None)
        if thread is not None:
            try:
                if thread.isRunning():
                    return True
            except Exception:
                pass
        return bool(getattr(self, "_execution_started_flag", False))

    @staticmethod
    def _coerce_ai_cli_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value or "").strip().lower() in {"1", "true", "yes", "on", "启用", "开启", "是"}
