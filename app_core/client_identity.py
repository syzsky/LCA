from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import requests

from utils.app_paths import get_app_root, get_hardware_id_path
from utils.log_message_translator import translate_log_message

logger = logging.getLogger(__name__)

_INTERNAL_AUTH_SERVER = os.getenv("AUTH_SERVER_URL", "https://example.invalid").strip().rstrip("/")
_DEFAULT_VERIFY_SSL = True
_AUTH_REQUEST_MAX_ATTEMPTS = 3
_AUTH_REQUEST_RETRY_DELAY_SECONDS = 1.0
_AUTH_CSRF_TIMEOUT_SECONDS = 8
_AUTH_REGISTER_TIMEOUT_SECONDS = 12

try:
    from urllib3.util import connection as _urllib3_connection

    _urllib3_connection.allowed_gai_family = lambda: socket.AF_INET
except Exception:
    pass

try:
    import wmi  # type: ignore

    _WMI_LIB_AVAILABLE = True
except ImportError:
    wmi = None
    _WMI_LIB_AVAILABLE = False


def _resolve_server_settings() -> tuple[str, bool | str]:
    server_url = str(_INTERNAL_AUTH_SERVER).strip().rstrip("/")
    verify_ssl = _DEFAULT_VERIFY_SSL
    if isinstance(verify_ssl, str) and not os.path.isabs(verify_ssl):
        verify_ssl = os.path.join(get_app_root(), verify_ssl)
    return server_url, verify_ssl


def sanitize_error_message(error_msg: str) -> str:
    import re

    patterns = [
        r"host='[\d\.]+', port=\d+",
        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+",
        r"HTTPConnectionPool\(host='[^']+', port=\d+\)",
        r"https?://[\d\.]+:\d+",
        r"/api/[a-zA-Z_/]+",
    ]

    sanitized_msg = str(error_msg or "")
    for pattern in patterns:
        sanitized_msg = re.sub(pattern, "[SERVER_INFO]", sanitized_msg)

    if "Read timed out" in sanitized_msg or "Connection" in sanitized_msg:
        return "连接服务端超时或服务不可用"
    if "Max retries exceeded" in sanitized_msg:
        return "服务端连接重试次数已达上限"
    if "Connection refused" in sanitized_msg:
        return "服务端拒绝连接"
    if "Name or service not known" in sanitized_msg:
        return "服务端地址解析失败"
    return translate_log_message(sanitized_msg)


def _log_registration_redirect_warning(response, context: str, server_url: str) -> None:
    try:
        if response is None:
            return
        if getattr(response, "history", None):
            try:
                chain = " -> ".join([r.url for r in response.history] + [response.url])
            except Exception:
                chain = "未知"
            logger.warning("[注册] %s 重定向链路: %s", context, chain)
        try:
            base = urlparse(server_url)
            final = urlparse(response.url)
            if base.scheme != final.scheme or base.netloc != final.netloc:
                logger.warning("[注册] %s 最终 URL 与服务端配置不一致: %s", context, response.url)
        except Exception:
            pass
    except Exception:
        pass


def _request_with_retry(request_callable, context: str, attempts: int = _AUTH_REQUEST_MAX_ATTEMPTS):
    total_attempts = max(1, int(attempts or 1))
    last_exception = None
    for attempt in range(1, total_attempts + 1):
        try:
            return request_callable()
        except requests.exceptions.Timeout as exc:
            last_exception = exc
            if attempt >= total_attempts:
                raise
            logger.warning("%s超时，准备重试（%s/%s）", context, attempt, total_attempts)
        except requests.exceptions.RequestException as exc:
            last_exception = exc
            if attempt >= total_attempts:
                raise
            logger.warning(
                "%s失败，准备重试（%s/%s）：%s",
                context,
                attempt,
                total_attempts,
                sanitize_error_message(str(exc)),
            )
        time.sleep(_AUTH_REQUEST_RETRY_DELAY_SECONDS)

    if last_exception is not None:
        raise last_exception
    raise RuntimeError(f"{context} failed without response")


def get_hardware_id() -> Optional[str]:
    logger.info("正在根据运行时标识源重新生成硬件 ID")

    hardware_id_path = get_hardware_id_path()
    ids: dict[str, str] = {}

    if _WMI_LIB_AVAILABLE and os.name == "nt" and wmi is not None:
        try:
            client = wmi.WMI()
            wmi_uuids = [item.UUID for item in client.Win32_ComputerSystemProduct() if item.UUID]
            if wmi_uuids:
                wmi_uuid = str(wmi_uuids[0]).replace("-", "").lower()
                if len(wmi_uuid) == 32 and all(ch in "0123456789abcdef" for ch in wmi_uuid):
                    ids["wmi"] = hashlib.sha256(wmi_uuid.encode("utf-8")).hexdigest()
                else:
                    logger.warning("WMI UUID 格式异常: %s", wmi_uuids[0])
            else:
                logger.warning("WMI 未返回 UUID")
        except Exception as exc:
            logger.warning("读取 WMI UUID 失败: %s", exc)
    elif not _WMI_LIB_AVAILABLE:
        logger.warning("WMI 依赖不可用，已跳过 WMI 硬件 ID 来源")

    if "wmi" not in ids:
        try:
            system_info = f"{platform.system()}-{platform.machine()}-{socket.gethostname()}"
            try:
                import multiprocessing

                system_info += f"-{multiprocessing.cpu_count()}"
            except Exception:
                pass
            ids["system"] = hashlib.sha256(system_info.encode("utf-8")).hexdigest()
        except Exception as exc:
            logger.warning("构建系统信息硬件 ID 失败: %s", exc)

    if ids:
        selected_id = ids.get("wmi") or ids.get("system") or next(iter(ids.values()))
        try:
            if os.path.exists(hardware_id_path):
                with open(hardware_id_path, "r", encoding="utf-8") as file:
                    saved_id = file.read().strip()
                if saved_id and saved_id != selected_id:
                    logger.warning("检测到硬件 ID 文件与当前运行值不一致，将刷新为最新值")
        except Exception as exc:
            logger.warning("读取现有硬件 ID 文件失败: %s", exc)

        try:
            with open(hardware_id_path, "w", encoding="utf-8") as file:
                file.write(selected_id)
        except Exception as exc:
            logger.warning("写入硬件 ID 失败: %s", exc)
        return selected_id

    fallback_seed = f"{platform.node()}-{uuid.uuid4()}"
    fallback_id = hashlib.sha256(fallback_seed.encode("utf-8")).hexdigest()
    logger.warning("所有硬件 ID 来源均失败，将使用临时生成的回退 ID")
    try:
        with open(hardware_id_path, "w", encoding="utf-8") as file:
            file.write(fallback_id)
    except Exception as exc:
        logger.warning("写入回退硬件 ID 失败: %s", exc)
    return fallback_id


def _extract_register_error_message(resp_json: Optional[dict], default_msg: str) -> str:
    if not isinstance(resp_json, dict):
        return default_msg
    for key in ("message", "error", "ban_reason"):
        value = resp_json.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    detail = resp_json.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    if isinstance(detail, dict):
        for key in ("message", "error", "detail"):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return default_msg


def attempt_client_registration(hw_id: str, session: requests.Session) -> dict:
    if not hw_id or not isinstance(hw_id, str) or len(hw_id) != 64:
        logger.critical("硬件 ID 格式无效: %s", hw_id)
        return {"success": False, "is_banned": False, "error": "invalid_hwid_format"}

    server_url, verify_ssl = _resolve_server_settings()
    csrf_token = None

    try:
        try:
            csrf_response = _request_with_retry(
                lambda: session.get(
                    f"{server_url}/api/get_csrf_for_client",
                    timeout=_AUTH_CSRF_TIMEOUT_SECONDS,
                    verify=verify_ssl,
                ),
                "CSRF 请求",
            )
            if csrf_response.status_code == 404:
                logger.error("服务端缺少 CSRF 接口")
                return {"success": False, "is_banned": False}
            csrf_response.raise_for_status()
            response_json = csrf_response.json()
            csrf_token = response_json.get("csrf_token")
            if not csrf_token and not session.cookies:
                logger.error("CSRF 请求完成，但未返回 token 或会话 cookies")
                return {"success": False, "is_banned": False}
        except requests.exceptions.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", "unknown")
            logger.error("CSRF 请求返回 HTTP 错误: %s", status_code)
            return {"success": False, "is_banned": False}
        except requests.exceptions.RequestException as exc:
            logger.error("CSRF 请求失败: %s", sanitize_error_message(str(exc)))
            return {"success": False, "is_banned": False}
        except Exception as exc:
            logger.error("获取 CSRF token 时发生未预期异常: %s", exc, exc_info=True)
            return {"success": False, "is_banned": False}

        headers = {"Referer": server_url}
        if csrf_token:
            headers["X-CSRFToken"] = csrf_token
        else:
            logger.warning("CSRF token 不可用，注册请求将不携带 X-CSRFToken")

        v2_payload = {
            "hardware_id": hw_id,
            "client_info": {
                "os": platform.system(),
                "version": platform.version(),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "client_version": "2.0.0",
        }

        try:
            response = _request_with_retry(
                lambda: session.post(
                    f"{server_url}/api/v2/client/register",
                    json=v2_payload,
                    headers=headers,
                    timeout=_AUTH_REGISTER_TIMEOUT_SECONDS,
                    verify=verify_ssl,
                ),
                "v2 客户端注册请求",
            )
            _log_registration_redirect_warning(response, "v2 register", server_url)
            status_code = response.status_code
            logger.info("客户端注册 v2 响应状态: %s", status_code)

            response_json = None
            try:
                response_json = response.json()
            except json.JSONDecodeError:
                logger.warning("客户端注册 v2 响应不是有效的 JSON")

            if status_code == 200:
                if response_json:
                    if response_json.get("status") == "banned":
                        ban_reason = str(response_json.get("message") or "").strip()
                        logger.critical("硬件 ID 已被封禁: %s", ban_reason)
                        return {
                            "success": False,
                            "is_banned": True,
                            "ban_reason": ban_reason,
                            "license_validation_enabled": True,
                        }
                    return {
                        "success": True,
                        "is_banned": False,
                        "license_validation_enabled": response_json.get("license_validation_enabled", True),
                    }
                return {
                    "success": True,
                    "is_banned": False,
                    "license_validation_enabled": True,
                }

            if status_code == 404:
                logger.warning("v2 注册接口不可用，准备回退到 v1")
                return _attempt_v1_registration(hw_id, session, csrf_token, server_url, verify_ssl)

            if status_code == 403:
                error_code = ""
                if isinstance(response_json, dict):
                    error_code = str(response_json.get("error_code", "") or "").strip().upper()
                is_banned = bool(isinstance(response_json, dict) and response_json.get("is_banned", False)) or error_code == "HARDWARE_BANNED"
                error_msg = _extract_register_error_message(response_json, "request rejected")
                if is_banned:
                    logger.critical("硬件 ID 已被封禁: %s", error_msg)
                    return {"success": False, "is_banned": True, "ban_reason": error_msg, "status_code": status_code, "error": error_msg}
                return {"success": False, "is_banned": False, "status_code": status_code, "error": error_msg}

            if status_code == 429:
                error_msg = _extract_register_error_message(response_json, "client quota exceeded")
                return {"success": False, "is_banned": False, "status_code": status_code, "error": error_msg}

            logger.warning("v2 注册失败，状态码 %s；准备回退到 v1", status_code)
            return _attempt_v1_registration(hw_id, session, csrf_token, server_url, verify_ssl)

        except requests.exceptions.RequestException as exc:
            logger.error("v2 注册请求失败: %s", sanitize_error_message(str(exc)))
            return _attempt_v1_registration(hw_id, session, csrf_token, server_url, verify_ssl)
        except Exception as exc:
            logger.error("v2 注册过程中发生未预期异常: %s", exc, exc_info=True)
            return _attempt_v1_registration(hw_id, session, csrf_token, server_url, verify_ssl)

    except requests.exceptions.RequestException as exc:
        logger.error("硬件 ID 注册请求失败: %s", sanitize_error_message(str(exc)))
        return {"success": False, "is_banned": False}
    except Exception as exc:
        logger.error("硬件 ID 注册过程中发生未预期异常: %s", exc, exc_info=True)
        return {"success": False, "is_banned": False}


def _attempt_v1_registration(
    hw_id: str,
    session: requests.Session,
    csrf_token: Optional[str],
    server_url: str,
    verify_ssl: str,
) -> dict:
    logger.info("正在使用 v1 注册回退链路")

    try:
        headers = {"Referer": server_url}
        if csrf_token:
            headers["X-CSRFToken"] = csrf_token

        response = _request_with_retry(
            lambda: session.post(
                f"{server_url}/api/licensing/register_client",
                json={"hardware_id": hw_id},
                headers=headers,
                timeout=_AUTH_REGISTER_TIMEOUT_SECONDS,
                verify=verify_ssl,
            ),
            "v1 客户端注册请求",
        )
        _log_registration_redirect_warning(response, "v1 register", server_url)

        status_code = response.status_code
        logger.info("客户端注册 v1 响应状态: %s", status_code)

        response_json = None
        try:
            response_json = response.json()
        except json.JSONDecodeError:
            logger.warning("客户端注册 v1 响应不是有效的 JSON")

        if status_code in (200, 201, 409):
            if response_json and response_json.get("is_banned", False):
                ban_reason = response_json.get("ban_reason", "硬件 ID 已被封禁")
                logger.critical("硬件 ID 已被封禁: %s", ban_reason)
                return {
                    "success": False,
                    "is_banned": True,
                    "ban_reason": ban_reason,
                    "license_validation_enabled": response_json.get("license_validation_enabled", True),
                }
            return {
                "success": True,
                "is_banned": False,
                "license_validation_enabled": response_json.get("license_validation_enabled", True) if response_json else True,
            }

        if status_code == 403:
            error_code = ""
            if isinstance(response_json, dict):
                error_code = str(response_json.get("error_code", "") or "").strip().upper()
            is_banned = bool(isinstance(response_json, dict) and response_json.get("is_banned", False)) or error_code == "HARDWARE_BANNED"
            error_msg = "request rejected"
            if isinstance(response_json, dict):
                error_msg = str(
                    response_json.get("message")
                    or response_json.get("error")
                    or response_json.get("ban_reason")
                    or response_json.get("detail")
                    or error_msg
                ).strip()
            if is_banned:
                logger.critical("硬件 ID 已被封禁: %s", error_msg)
                return {"success": False, "is_banned": True, "ban_reason": error_msg, "status_code": status_code, "error": error_msg}
            return {"success": False, "is_banned": False, "status_code": status_code, "error": error_msg}

        if status_code == 429:
            error_msg = "client quota exceeded"
            if isinstance(response_json, dict):
                error_msg = str(
                    response_json.get("message")
                    or response_json.get("error")
                    or response_json.get("detail")
                    or error_msg
                ).strip()
            return {"success": False, "is_banned": False, "status_code": status_code, "error": error_msg}

        error_msg = "未知服务端错误"
        if isinstance(response_json, dict):
            error_msg = str(
                response_json.get("message")
                or response_json.get("error")
                or response_json.get("detail")
                or error_msg
            )
        elif response.text:
            error_msg = response.text[:100]
        return {"success": False, "is_banned": False, "status_code": status_code, "error": str(error_msg)}

    except requests.exceptions.RequestException as exc:
        logger.error("v1 注册请求失败: %s", sanitize_error_message(str(exc)))
        return {"success": False, "is_banned": False}
    except Exception as exc:
        logger.error("v1 注册过程中发生未预期异常: %s", exc, exc_info=True)
        return {"success": False, "is_banned": False}
