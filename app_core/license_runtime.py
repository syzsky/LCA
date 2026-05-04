import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from app_core.client_identity import _resolve_server_settings, sanitize_error_message
from app_core.runtime_security import run_runtime_guard as _run_guard
from app_core.runtime_security import run_runtime_validator as _run_validator
from app_core.runtime_security import set_runtime_guard
from app_core.runtime_security import set_runtime_validator

logger = logging.getLogger(__name__)

AUTH_ENDPOINT = "/api/ping_auth"
_AUTH_REQUEST_MAX_ATTEMPTS = 3
_AUTH_REQUEST_RETRY_DELAY_SECONDS = 1.0
_HANDSHAKE_TIMEOUT_SECONDS = 8
_LEGACY_VALIDATE_TIMEOUT_SECONDS = 8


def set_validation_session(session_token: Optional[str] = None) -> str:
    token = str(session_token or "").strip() or secrets.token_hex(32)
    sys._auth_session_token = token
    sys._last_validation_time = time.time()
    return token


def _get_server_settings() -> tuple[str, str]:
    return _resolve_server_settings()


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


def check_network_connectivity() -> bool:
    try:
        test_hosts = [
            ("8.8.8.8", 53),
            ("1.1.1.1", 53),
            ("208.67.222.222", 53),
        ]
        for host, port in test_hosts:
            try:
                with socket.create_connection((host, port), timeout=3):
                    pass
                return True
            except Exception:
                continue
        return False
    except Exception as exc:
        logger.warning(f"网络连通性检查失败：{exc}")
        return False


def validate_license_with_server_v2(hw_id: str, key: str) -> tuple[bool, int, str, dict]:
    _run_guard()
    if not _run_validator(hw_id, key):
        return False, 400, "invalid", {}

    server_url, verify_ssl = _get_server_settings()
    extra_info: dict = {}

    try:
        current_time = int(datetime.now(timezone.utc).timestamp())
        client_nonce = secrets.token_hex(16)

        initiate_data = {
            "hardware_id": hw_id,
            "client_nonce": client_nonce,
            "client_timestamp": current_time,
        }

        response = _request_with_retry(
            lambda: requests.post(
                f"{server_url}/api/v2.1/client/handshake/initiate",
                json=initiate_data,
                timeout=_HANDSHAKE_TIMEOUT_SECONDS,
                verify=verify_ssl,
                allow_redirects=False,
            ),
            "v2.1 握手初始化请求",
        )

        if response.status_code != 200:
            logger.error(f"v2.1 握手初始化失败：{response.status_code}")
            return False, response.status_code, "unknown", {}

        init_response = response.json()
        if not init_response.get("success"):
            logger.error(f"v2.1 握手初始化被拒绝：{init_response.get('message')}")
            return False, 400, "unknown", {}

        handshake_token = init_response.get("handshake_token")
        server_challenge = init_response.get("server_challenge")
        server_nonce = init_response.get("server_nonce")
        server_timestamp = init_response.get("server_timestamp")
        token_hmac = init_response.get("token_hmac")

        secret_key = str(os.environ.get("AUTH_SECRET_KEY", "") or "").strip()
        if len(secret_key) < 24 or secret_key.lower() == "default-secret-key-change-in-production":
            logger.error("v2.1 握手使用的 AUTH_SECRET_KEY 无效")
            return False, 500, "unknown", {}

        data_for_response = f"{server_challenge}|{key}|{server_nonce}|{client_nonce}|{server_timestamp}"
        client_response = hmac.new(
            secret_key.encode(),
            data_for_response.encode(),
            hashlib.sha256,
        ).hexdigest()

        auth_data = {
            "hardware_id": hw_id,
            "license_key": key,
            "handshake_token": handshake_token,
            "server_challenge": server_challenge,
            "server_nonce": server_nonce,
            "server_timestamp": server_timestamp,
            "client_response": client_response,
            "client_timestamp": current_time,
            "client_nonce": client_nonce,
            "token_hmac": token_hmac,
        }

        response = _request_with_retry(
            lambda: requests.post(
                f"{server_url}/api/v2.1/client/handshake/authenticate",
                json=auth_data,
                timeout=_HANDSHAKE_TIMEOUT_SECONDS,
                verify=verify_ssl,
                allow_redirects=False,
            ),
            "v2.1 握手认证请求",
        )

        status_code = response.status_code
        if status_code == 200:
            try:
                response_json = response.json()
            except json.JSONDecodeError:
                logger.error("v2.1 握手认证返回了无效 JSON")
                return False, status_code, "unknown", {"error": "返回了无效 JSON"}

            if response_json.get("success"):
                license_type = response_json.get("license_type", "unknown")
                extra_info = {
                    "validation_mode": response_json.get("validation_mode", "full"),
                    "license_validation_enabled": response_json.get("license_validation_enabled", True),
                    "remaining_days": response_json.get("remaining_days"),
                    "is_permanent": response_json.get("is_permanent", False),
                    "expires_at": response_json.get("expires_at"),
                    "server_time": response_json.get("server_time"),
                    "api_version": response_json.get("api_version", "2.1"),
                    "session_token": response_json.get("session_token"),
                }
                return True, status_code, license_type, extra_info

            error_code = response_json.get("error_code", "UNKNOWN")
            message = response_json.get("message", "validation failed")
            logger.error(f"v2.1 握手认证失败：{error_code} - {message}")
            return False, status_code, "unknown", {"error_code": error_code, "message": message}

        if status_code in (401, 403):
            logger.error(f"v2.1 握手认证被拒绝：{status_code}")
            return False, status_code, "unknown", {}

        logger.error(f"v2.1 握手认证失败：HTTP {status_code}")
        return False, status_code, "unknown", {}
    except requests.exceptions.Timeout:
        logger.error("v2.1 握手请求超时")
        return False, 0, "unknown", {}
    except requests.exceptions.RequestException as exc:
        sanitized_error = sanitize_error_message(str(exc))
        logger.error(f"v2.1 握手网络错误：{sanitized_error}")
        return False, 0, "unknown", {"message": sanitized_error}
    except Exception as exc:
        logger.error(f"v2.1 握手发生未预期错误：{exc}", exc_info=True)
        return False, 0, "unknown", {}


def validate_license_with_server(hw_id: str, key: str) -> tuple[bool, int, str]:
    _run_guard()
    if not _run_validator(hw_id, key):
        return False, 400, "invalid"

    server_url, verify_ssl = _get_server_settings()
    headers = {
        "X-Hardware-ID": hw_id,
        "Authorization": f"Bearer {key}",
    }
    status_code = 0
    max_retries = _AUTH_REQUEST_MAX_ATTEMPTS
    retry_delay = 1

    for attempt in range(max_retries):
        try:
            response = requests.get(
                f"{server_url}{AUTH_ENDPOINT}",
                headers=headers,
                timeout=_LEGACY_VALIDATE_TIMEOUT_SECONDS,
                verify=verify_ssl,
                allow_redirects=False,
            )
            status_code = response.status_code

            response_json = None
            if 200 <= status_code < 300 or status_code == 401:
                try:
                    response_json = response.json()
                except json.JSONDecodeError:
                    logger.warning("旧版许可证校验返回了无效 JSON")

            if status_code == 200:
                license_type = "unknown"
                if response_json and isinstance(response_json, dict):
                    license_type = response_json.get("license_type", "unknown")
                return True, status_code, license_type

            if status_code == 401:
                error_msg = ""
                if response_json and isinstance(response_json, dict):
                    error_msg = str(
                        response_json.get("message")
                        or response_json.get("error")
                        or response_json.get("detail")
                        or ""
                    ).strip()
                logger.warning(f"旧版许可证校验被拒绝：{error_msg}")
                return False, status_code, "unknown"

            logger.error(f"旧版许可证校验失败：HTTP {status_code}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                return False, status_code, "unknown"
        except requests.exceptions.SSLError as exc:
            sanitized_error = sanitize_error_message(str(exc))
            logger.error(f"旧版许可证校验 SSL 错误：{sanitized_error}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                return False, status_code, "unknown"
        except requests.exceptions.RequestException as exc:
            sanitized_error = sanitize_error_message(str(exc))
            logger.error(f"旧版许可证校验网络错误：{sanitized_error}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                return False, status_code, "unknown"
        except Exception as exc:
            logger.error(f"旧版许可证校验发生未预期错误：{exc}", exc_info=True)
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                return False, status_code, "unknown"

    logger.error("旧版许可证校验已耗尽重试次数")
    return False, status_code, "unknown"


def bind_license_to_hwid(hw_id: str, license_key: str, session: requests.Session) -> bool:
    server_url, verify_ssl = _get_server_settings()
    bind_endpoint = "/api/licensing/bind_license"
    csrf_token_value = None

    try:
        csrf_response = session.get(
            f"{server_url}/api/get_csrf_for_client",
            timeout=10,
            verify=verify_ssl,
        )
        csrf_response.raise_for_status()
        response_json = csrf_response.json()
        csrf_token_value = response_json.get("csrf_token")

        if not csrf_token_value and not session.cookies:
            logger.error("绑定请求缺少 CSRF Token 或会话 Cookie")
            return False
    except requests.exceptions.RequestException as exc:
        sanitized_error = sanitize_error_message(str(exc))
        logger.error(f"获取绑定所需的 CSRF Token 失败：{sanitized_error}")
        return False
    except Exception as exc:
        logger.error(f"获取 CSRF Token 时发生未预期错误：{exc}", exc_info=True)
        return False

    headers = {
        "Referer": server_url,
        "Authorization": f"Bearer {license_key}",
    }
    if csrf_token_value:
        headers["X-CSRFToken"] = csrf_token_value

    payload = {
        "hardware_id": hw_id,
        "license_key": license_key,
    }

    try:
        response = session.post(
            f"{server_url}{bind_endpoint}",
            json=payload,
            headers=headers,
            timeout=15,
            verify=verify_ssl,
        )
        status_code = response.status_code

        response_data = None
        try:
            response_data = response.json()
        except json.JSONDecodeError:
            logger.warning("绑定响应返回了无效 JSON")

        if status_code == 200:
            return True
        if status_code in (400, 401, 404, 409):
            error_msg = response.text[:100]
            if isinstance(response_data, dict):
                error_msg = str(response_data.get("error") or error_msg)
            logger.warning(f"绑定请求被拒绝：{status_code} - {error_msg}")
            return False

        error_msg = response.text[:100]
        if isinstance(response_data, dict):
            error_msg = str(response_data.get("error") or error_msg)
        logger.error(f"绑定请求失败：{status_code} - {error_msg}")
        return False
    except requests.exceptions.RequestException as exc:
        sanitized_error = sanitize_error_message(str(exc))
        logger.error(f"绑定请求网络错误：{sanitized_error}")
        return False
    except Exception as exc:
        logger.error(f"绑定请求发生未预期错误：{exc}", exc_info=True)
        return False


def enforce_online_validation(hardware_id: str, license_key: str) -> tuple:
    try:
        _run_guard()

        if not check_network_connectivity():
            logger.critical("在线校验失败：网络不可用")
            return False, 503, None

        is_valid, status_code, license_type, extra_info = validate_license_with_server_v2(
            hardware_id,
            license_key,
        )

        if is_valid:
            set_validation_session(extra_info.get("session_token"))
            return True, status_code, license_type

        logger.critical(f"在线校验失败：{status_code}")
        return False, status_code, None
    except Exception as exc:
        logger.critical(f"在线校验发生未预期错误：{exc}", exc_info=True)
        return False, 500, None
