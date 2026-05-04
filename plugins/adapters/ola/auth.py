from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from .runtime_config import configure_ola_runtime, get_ola_registration_info


@dataclass(frozen=True)
class OLAAuthorizationResult:
    success: bool
    message: str = ""
    raw_response: str = ""
    machine_code: str = ""
    requires_activation: bool = False


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extract_machine_code(value: Any) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    match = re.search(r"当前机器码[:：]\s*([0-9a-fA-F]{16,64})", text)
    if match:
        return match.group(1).lower()

    compact = text.replace("-", "").strip().lower()
    if len(compact) in (16, 32, 40, 64) and all(ch in "0123456789abcdef" for ch in compact):
        return compact
    return ""


def extract_ola_machine_code(value: Any) -> str:
    return _extract_machine_code(value)


def _message_requires_activation(message: str, raw_response: str = "") -> bool:
    text = f"{_normalize_text(message)} {_normalize_text(raw_response)}"
    if not text:
        return False

    activation_markers = (
        "未激活",
        "请先激活",
        "未找到授权信息",
        "activate",
    )
    return any(marker in text for marker in activation_markers)


def is_ola_activation_required(message: str, raw_response: str = "") -> bool:
    return _message_requires_activation(message, raw_response)


def _read_machine_code(ola: Any) -> str:
    getter = getattr(ola, "GetMachineCode", None)
    if not callable(getter):
        return ""

    try:
        return _extract_machine_code(getter())
    except Exception:
        return ""


def _finalize_result(
    result: OLAAuthorizationResult,
    fallback_machine_code: str = "",
) -> OLAAuthorizationResult:
    machine_code = (
        _extract_machine_code(result.message)
        or _extract_machine_code(result.raw_response)
        or _extract_machine_code(fallback_machine_code)
    )
    requires_activation = (
        not result.success
        and _message_requires_activation(result.message, result.raw_response)
    )
    if (
        result.machine_code == machine_code
        and result.requires_activation == requires_activation
    ):
        return result

    return OLAAuthorizationResult(
        success=result.success,
        message=result.message,
        raw_response=result.raw_response,
        machine_code=machine_code,
        requires_activation=requires_activation,
    )


def _parse_status_response(
    raw_response: Any,
    *,
    empty_message: str,
    invalid_message_prefix: str,
    default_failure_message: str,
) -> OLAAuthorizationResult:
    raw_text = _normalize_text(raw_response)
    if not raw_text:
        return _finalize_result(
            OLAAuthorizationResult(success=False, message=empty_message)
        )

    try:
        payload = json.loads(raw_text)
    except Exception:
        return _finalize_result(
            OLAAuthorizationResult(
                success=False,
                message=f"{invalid_message_prefix}{raw_text}",
                raw_response=raw_text,
            )
        )

    try:
        status = int(payload.get("Status", 0) or 0)
    except Exception:
        status = 0

    message = _normalize_text(payload.get("Message"))
    if status == 1:
        return _finalize_result(
            OLAAuthorizationResult(
                success=True,
                message=message,
                raw_response=raw_text,
            )
        )

    return _finalize_result(
        OLAAuthorizationResult(
            success=False,
            message=message or default_failure_message,
            raw_response=raw_text,
        )
    )


def _parse_login_response(raw_response: Any) -> OLAAuthorizationResult:
    return _parse_status_response(
        raw_response,
        empty_message="OLA 登录失败：返回为空",
        invalid_message_prefix="OLA 登录失败：返回结果无法解析：",
        default_failure_message="OLA 登录失败",
    )


def _parse_activate_response(raw_response: Any) -> OLAAuthorizationResult:
    return _parse_status_response(
        raw_response,
        empty_message="OLA 激活失败：返回为空",
        invalid_message_prefix="OLA 激活失败：返回结果无法解析：",
        default_failure_message="OLA 激活失败",
    )


def authorize_ola_instance(ola: Any) -> OLAAuthorizationResult:
    user_code, soft_code, feature_list = get_ola_registration_info()
    if not user_code or not soft_code:
        return _finalize_result(
            OLAAuthorizationResult(success=False, message="缺少 OLA 授权配置")
        )

    login = getattr(ola, "Login", None)
    if not callable(login):
        return _finalize_result(
            OLAAuthorizationResult(success=False, message="当前 OLA 实例不支持 Login 授权接口")
        )

    machine_code = _read_machine_code(ola)

    try:
        raw_response = login(user_code, soft_code, feature_list, "", "")
    except Exception as exc:
        return _finalize_result(
            OLAAuthorizationResult(success=False, message=f"OLA 登录接口调用失败：{exc}"),
            fallback_machine_code=machine_code,
        )

    return _finalize_result(
        _parse_login_response(raw_response),
        fallback_machine_code=machine_code,
    )


def activate_ola_instance(
    ola: Any,
    license_key: str,
    *,
    soft_version: str = "",
    dealer_code: str = "",
) -> OLAAuthorizationResult:
    normalized_license_key = _normalize_text(license_key)
    if not normalized_license_key:
        return _finalize_result(
            OLAAuthorizationResult(success=False, message="缺少 OLA 激活码")
        )

    user_code, soft_code, _ = get_ola_registration_info()
    if not user_code or not soft_code:
        return _finalize_result(
            OLAAuthorizationResult(success=False, message="缺少 OLA 授权配置")
        )

    activate = getattr(ola, "Activate", None)
    if not callable(activate):
        return _finalize_result(
            OLAAuthorizationResult(success=False, message="当前 OLA 实例不支持 Activate 激活接口")
        )

    machine_code = _read_machine_code(ola)

    try:
        raw_response = activate(
            user_code,
            soft_code,
            _normalize_text(soft_version),
            _normalize_text(dealer_code),
            normalized_license_key,
        )
    except Exception as exc:
        return _finalize_result(
            OLAAuthorizationResult(success=False, message=f"OLA 激活接口调用失败：{exc}"),
            fallback_machine_code=machine_code,
        )

    activation_result = _finalize_result(
        _parse_activate_response(raw_response),
        fallback_machine_code=machine_code,
    )
    if not activation_result.success:
        return activation_result

    authorization_result = authorize_ola_instance(ola)
    if authorization_result.success:
        return authorization_result

    if authorization_result.message:
        return authorization_result

    return _finalize_result(
        OLAAuthorizationResult(success=False, message="OLA 激活成功，但登录校验失败"),
        fallback_machine_code=machine_code,
    )


def _create_ola_server_for_probe(config: Optional[dict] = None) -> Any:
    if config:
        configure_ola_runtime(config)

    from . import adapter as ola_adapter

    if not ola_adapter._try_import_ola():
        raise RuntimeError("插件运行环境不可用")

    if ola_adapter._OLAPlugServer is None:
        raise RuntimeError("插件实例工厂初始化失败")

    return ola_adapter._OLAPlugServer()


def _release_ola_server_for_probe(ola: Any) -> None:
    if ola is None:
        return

    destroy = getattr(ola, "DestroyCOLAPlugInterFace", None)
    if callable(destroy):
        try:
            destroy()
        except Exception:
            pass


def probe_ola_authorization(config: Optional[dict] = None) -> OLAAuthorizationResult:
    ola = None
    try:
        ola = _create_ola_server_for_probe(config)
        return authorize_ola_instance(ola)
    except Exception as exc:
        return _finalize_result(
            OLAAuthorizationResult(success=False, message=f"插件初始化失败：{exc}")
        )
    finally:
        _release_ola_server_for_probe(ola)


def activate_ola_authorization(
    config: Optional[dict],
    license_key: str,
    *,
    soft_version: str = "",
    dealer_code: str = "",
) -> OLAAuthorizationResult:
    ola = None
    try:
        ola = _create_ola_server_for_probe(config)
        return activate_ola_instance(
            ola,
            license_key,
            soft_version=soft_version,
            dealer_code=dealer_code,
        )
    except Exception as exc:
        return _finalize_result(
            OLAAuthorizationResult(success=False, message=f"插件初始化失败：{exc}")
        )
    finally:
        _release_ola_server_for_probe(ola)
