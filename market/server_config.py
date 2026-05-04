# -*- coding: utf-8 -*-

from __future__ import annotations

import os
from pathlib import Path
from typing import Union

from app_core.app_config import UPDATE_SERVER


DEFAULT_MARKET_AUTH_SERVER_URL = os.getenv("AUTH_SERVER_URL", "https://example.invalid").strip() or "https://example.invalid"
DEFAULT_MARKET_VERIFY_SSL: Union[str, bool] = os.getenv("MARKET_VERIFY_SSL", "true").strip() or "true"
DEFAULT_MARKET_UPDATE_SERVER_URL = os.getenv("MARKET_UPDATE_SERVER_BASE", UPDATE_SERVER).strip() or UPDATE_SERVER


def _normalize_base_url(base_url: str) -> str:
    return str(base_url or "").strip().rstrip("/")


def _strip_market_suffix(base_url: str) -> str:
    normalized = _normalize_base_url(base_url)
    if normalized.lower().endswith("/market"):
        return normalized[:-7]
    return normalized


def get_market_auth_server_base(base_url: str = "") -> str:
    return _normalize_base_url(base_url or DEFAULT_MARKET_AUTH_SERVER_URL)


def get_market_update_server_base(base_url: str = "") -> str:
    return _strip_market_suffix(base_url or DEFAULT_MARKET_UPDATE_SERVER_URL)


def get_market_storage_base(base_url: str = "") -> str:
    update_base = get_market_update_server_base(base_url)
    if not update_base:
        return ""
    return f"{update_base}/market"


def get_market_verify_ssl(verify_ssl: Union[str, bool, None] = None) -> Union[str, bool]:
    if isinstance(verify_ssl, bool):
        return verify_ssl
    raw_value = str(verify_ssl or DEFAULT_MARKET_VERIFY_SSL).strip()
    if raw_value.lower() in {"1", "true", "yes", "on"}:
        return True
    if not raw_value:
        return DEFAULT_MARKET_VERIFY_SSL
    cert_path = Path(raw_value)
    if not cert_path.is_absolute():
        cert_path = (Path.cwd() / cert_path).resolve()
    return str(cert_path)


MARKET_UPDATE_SERVER_BASE_URL = get_market_update_server_base()
MARKET_STORAGE_BASE_URL = get_market_storage_base()
MARKET_STORAGE_STAGING_BASE_URL = f"{MARKET_STORAGE_BASE_URL}/staging"
MARKET_STORAGE_RELEASE_BASE_URL = f"{MARKET_STORAGE_BASE_URL}/release"


def build_market_packages_api_url(auth_server_base: str = "") -> str:
    return f"{get_market_auth_server_base(auth_server_base)}/api/market/packages"


def build_market_my_packages_api_url(auth_server_base: str = "") -> str:
    return f"{get_market_auth_server_base(auth_server_base)}/api/market/my/packages"


def build_market_upload_ticket_api_url(auth_server_base: str = "") -> str:
    return f"{get_market_auth_server_base(auth_server_base)}/api/market/packages/upload-ticket"


def build_market_publish_api_url(auth_server_base: str = "") -> str:
    return f"{get_market_auth_server_base(auth_server_base)}/api/market/packages/publish"


def build_market_author_register_api_url(auth_server_base: str = "") -> str:
    return f"{get_market_auth_server_base(auth_server_base)}/api/market/account/register"


def build_market_author_login_api_url(auth_server_base: str = "") -> str:
    return f"{get_market_auth_server_base(auth_server_base)}/api/market/account/login"


def build_market_author_profile_api_url(auth_server_base: str = "") -> str:
    return f"{get_market_auth_server_base(auth_server_base)}/api/market/account/profile"


def build_market_author_logout_api_url(auth_server_base: str = "") -> str:
    return f"{get_market_auth_server_base(auth_server_base)}/api/market/account/logout"


def build_market_download_token_api_url(package_id: str, version: str, auth_server_base: str = "") -> str:
    safe_package_id = str(package_id or "").strip().strip("/")
    safe_version = str(version or "").strip().strip("/")
    return f"{get_market_auth_server_base(auth_server_base)}/api/market/packages/{safe_package_id}/{safe_version}/download"


def build_market_runtime_access_api_url(package_id: str, version: str, auth_server_base: str = "") -> str:
    safe_package_id = str(package_id or "").strip().strip("/")
    safe_version = str(version or "").strip().strip("/")
    return f"{get_market_auth_server_base(auth_server_base)}/api/market/packages/{safe_package_id}/{safe_version}/runtime-access"


def build_market_edit_access_api_url(package_id: str, version: str, auth_server_base: str = "") -> str:
    safe_package_id = str(package_id or "").strip().strip("/")
    safe_version = str(version or "").strip().strip("/")
    return f"{get_market_auth_server_base(auth_server_base)}/api/market/packages/{safe_package_id}/{safe_version}/edit-access"


def build_market_delete_package_api_url(package_id: str, version: str, auth_server_base: str = "") -> str:
    safe_package_id = str(package_id or "").strip().strip("/")
    safe_version = str(version or "").strip().strip("/")
    return f"{get_market_auth_server_base(auth_server_base)}/api/market/packages/{safe_package_id}/{safe_version}"


def build_market_package_status_api_url(package_id: str, version: str, auth_server_base: str = "") -> str:
    safe_package_id = str(package_id or "").strip().strip("/")
    safe_version = str(version or "").strip().strip("/")
    return f"{get_market_auth_server_base(auth_server_base)}/api/market/packages/{safe_package_id}/{safe_version}/status"



def build_market_package_download_url(package_id: str, version: str, filename: str = "package.lca_market.zip", base_url: str = "") -> str:
    storage_base = get_market_storage_base(base_url)
    safe_package_id = str(package_id or "").strip().strip("/")
    safe_version = str(version or "").strip().strip("/")
    safe_filename = str(filename or "package.lca_market.zip").strip().strip("/")
    return f"{storage_base}/release/{safe_package_id}/{safe_version}/{safe_filename}"


def build_market_upload_api_url(update_server_base: str = "") -> str:
    return f"{get_market_update_server_base(update_server_base)}/api/market/packages/upload"


def build_market_release_api_url(package_id: str, version: str, update_server_base: str = "") -> str:
    safe_package_id = str(package_id or "").strip().strip("/")
    safe_version = str(version or "").strip().strip("/")
    return f"{get_market_update_server_base(update_server_base)}/api/market/packages/{safe_package_id}/{safe_version}/release"


def build_market_health_api_url(update_server_base: str = "") -> str:
    return f"{get_market_update_server_base(update_server_base)}/health"
