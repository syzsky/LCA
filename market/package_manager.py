# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

import requests

from .exporter import (
    MarketPackageExportResult,
    MarketPackageExporter,
    import_packaged_dicts,
    materialize_installed_package_workflows,
)
from .models import (
    MarketAuthorAccount,
    MarketPackageManifest,
    PrecheckReport,
    RemoteMarketPackageSummary,
)
from .protection import extract_protected_payload, is_manifest_protected, load_archive_secret
from .refs import parse_market_workflow_ref
from .package_identity import to_safe_storage_segment, validate_package_identity
from .package_archive import load_manifest_from_archive, validate_archive_basic_structure
from .precheck import MarketPackagePrecheckEngine
from .server_config import (
    build_market_author_login_api_url,
    build_market_author_logout_api_url,
    build_market_author_profile_api_url,
    build_market_author_register_api_url,
    build_market_download_token_api_url,
    build_market_edit_access_api_url,
    build_market_delete_package_api_url,
    build_market_package_status_api_url,
    build_market_my_packages_api_url,
    build_market_upload_ticket_api_url,
    build_market_runtime_access_api_url,
    build_market_package_download_url,
    build_market_packages_api_url,
    build_market_publish_api_url,
    build_market_upload_api_url,
    get_market_verify_ssl,
)
from .storage import (
    get_installed_manifest_path,
    create_package_runtime_session_dir,
    get_market_cache_download_path,
    get_market_installed_root,
    get_market_packages_root,
    get_market_runtime_root,
    get_package_archive_path,
    get_package_install_dir,
    get_package_user_override_path,
)

class MarketPackageManager:
    def __init__(self):
        self._precheck_engine = MarketPackagePrecheckEngine()

    @staticmethod
    def _build_author_headers(author_token: str = "") -> Dict[str, str]:
        token = str(author_token or "").strip()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _normalize_server_detail(detail) -> str:
        if isinstance(detail, str):
            return detail.strip()
        if isinstance(detail, dict):
            parts: list[str] = []
            for key, value in detail.items():
                normalized_value = MarketPackageManager._normalize_server_detail(value)
                if normalized_value:
                    parts.append(f"{key}: {normalized_value}" if key else normalized_value)
            return '；'.join(parts).strip()
        if isinstance(detail, list):
            parts = [MarketPackageManager._normalize_server_detail(item) for item in detail]
            return '；'.join(part for part in parts if part).strip()
        return str(detail or '').strip()

    @staticmethod
    def _map_server_error_message(detail: str) -> str:
        text = str(detail or '').strip()
        if not text:
            return ''
        mapping = {
            'package_edit_forbidden': '当前作者没有该脚本的编辑权限',
            'package_id_owned_by_other_author': '包ID已被其他作者占用，请更换包ID或使用原作者账号登录后再发布',
            'package_delete_forbidden': '当前作者没有该脚本的删除权限',
            'package_delete_not_allowed': '已发布的版本不允许删除',
            'package_not_found': '共享平台包不存在',
            'package_version_not_found': '共享平台包版本不存在',
            'invalid_market_upload_ticket': '上传票据无效，请重新发起发布',
            'market_upload_ticket_mismatch': '上传票据与当前包ID或版本不匹配，请重新发起发布',
            'market_upload_ticket_expired': '上传票据已过期，请重新发起发布',
            'invalid_market_update_token': '更新服务器未授权当前发布请求',
            'manifest_identity_mismatch': '上传包内的包ID或版本与当前发布信息不一致',
            'manifest_invalid': '共享平台包清单无效',
            'manifest_missing': '共享平台包缺少清单文件',
            'invalid_archive': '共享平台包压缩文件无效',
            'invalid_archive_member_path': '共享平台包内部文件路径无效',
            'staging_package_invalid': '上传的共享平台包无效',
            'staging_package_not_found': '上传缓存不存在，请重新发布',
            'staging_sha256_mismatch': '上传文件校验失败，请重新发布',
            'staging_file_size_mismatch': '上传文件大小校验失败，请重新发布',
            'release_version_already_exists': '该版本已存在，请修改版本号后再发布',
            '需要作者登录': '请先登录作者账号',
            '作者会话无效或已过期': '作者登录已失效，请重新登录',
            '用户名或密码错误': '用户名或密码错误',
            'invalid_market_package_status_action': '脚本状态操作无效',
            'package_status_transition_not_allowed': '当前脚本状态不允许执行该操作',
        }
        return mapping.get(text, text)

    @classmethod
    def _get_payload_error_message(cls, payload, fallback: str = '') -> str:
        if isinstance(payload, dict):
            detail = cls._normalize_server_detail(payload.get('detail') or payload.get('message') or '')
        else:
            detail = cls._normalize_server_detail(payload)
        if detail:
            return cls._map_server_error_message(detail)
        return str(fallback or '').strip()

    @classmethod
    def _build_request_error_message(cls, action: str, exc: Exception, fallback: str = '') -> str:
        detail = ''
        status_code = 0
        response = getattr(exc, 'response', None)
        if response is not None:
            try:
                status_code = int(getattr(response, 'status_code', 0) or 0)
            except Exception:
                status_code = 0
            try:
                detail = cls._get_payload_error_message(response.json())
            except Exception:
                detail = ''
        if status_code == 404 and detail in {'Not Found', '404 Not Found'}:
            detail = ''
        if detail:
            return f'{action}: {detail}'
        if status_code == 404:
            return f'{action}: 请求接口不存在或资源不存在'
        fallback_text = str(fallback or '').strip()
        if fallback_text:
            return f'{action}: {fallback_text}'
        return f'{action}: {exc}'

    def load_manifest_from_archive(self, archive_path: str | Path) -> MarketPackageManifest:
        return load_manifest_from_archive(archive_path)

    def build_package_from_workflow(
        self,
        entry_workflow_path: str | Path,
        manifest: MarketPackageManifest | Dict,
        output_path: str | Path | None = None,
        config_data: Optional[Dict] = None,
    ) -> MarketPackageExportResult:
        exporter = MarketPackageExporter(config_data=config_data)
        return exporter.build_package(entry_workflow_path=entry_workflow_path, manifest=manifest, output_path=output_path)

    def publish_package_archive(
        self,
        archive_path: str | Path,
        auth_server_base: str = '',
        update_server_base: str = '',
        verify_ssl=None,
        changelog: str = '',
        release_notes: str = '',
        author_token: str = '',
        timeout: int = 120,
    ) -> Dict:
        archive = Path(archive_path).expanduser().resolve()
        ok, errors = validate_archive_basic_structure(archive)
        if not ok:
            raise ValueError('共享平台包结构无效: ' + '; '.join(errors))

        manifest = load_manifest_from_archive(archive)
        package_id, version = self._validate_package_identity(manifest.package_id, manifest.version)
        upload_result = self.upload_package_to_update_server(
            archive_path=archive,
            package_id=package_id,
            version=version,
            auth_server_base=auth_server_base,
            update_server_base=update_server_base,
            verify_ssl=verify_ssl,
            author_token=author_token,
            timeout=timeout,
        )
        publish_result = self.publish_package_metadata(
            manifest=manifest,
            upload_result=upload_result,
            archive_path=archive,
            auth_server_base=auth_server_base,
            verify_ssl=verify_ssl,
            changelog=changelog,
            release_notes=release_notes,
            author_token=author_token,
            timeout=timeout,
        )
        return {
            'manifest': manifest.to_dict(),
            'upload': upload_result,
            'publish': publish_result,
        }

    def upload_package_to_update_server(
        self,
        archive_path: str | Path,
        package_id: str,
        version: str,
        auth_server_base: str = '',
        update_server_base: str = '',
        verify_ssl=None,
        author_token: str = '',
        timeout: int = 120,
    ) -> Dict:
        safe_package_id, safe_version = self._validate_package_identity(package_id, version)
        archive = Path(archive_path).expanduser().resolve()
        if not archive.exists() or not archive.is_file():
            raise FileNotFoundError(f'共享平台包不存在: {archive}')

        api_url = build_market_upload_api_url(update_server_base)
        if not api_url:
            raise RuntimeError('更新服务器地址未配置')

        upload_ticket = self._request_upload_ticket(
            safe_package_id,
            safe_version,
            auth_server_base=auth_server_base,
            verify_ssl=verify_ssl,
            author_token=author_token,
            timeout=min(timeout, 30),
        )
        request_verify = get_market_verify_ssl(verify_ssl)

        try:
            with archive.open('rb') as handle:
                response = requests.post(
                    api_url,
                    data=handle,
                    headers={
                        'Content-Type': 'application/octet-stream',
                        'X-Market-Package-Id': safe_package_id,
                        'X-Market-Package-Version': safe_version,
                        'X-Market-Package-Filename': archive.name,
                        'X-Market-Upload-Ticket': upload_ticket,
                    },
                    timeout=timeout,
                    verify=request_verify,
                )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(self._build_request_error_message('上传共享平台包到更新服务器失败', exc)) from exc

        if not isinstance(payload, dict):
            raise RuntimeError('更新服务器返回数据无效')
        if payload.get('success') is False:
            raise RuntimeError(self._get_payload_error_message(payload, '更新服务器上传失败'))
        return payload

    def _request_upload_ticket(
        self,
        package_id: str,
        version: str,
        auth_server_base: str = '',
        verify_ssl=None,
        author_token: str = '',
        timeout: int = 20,
    ) -> str:
        if not str(author_token or '').strip():
            raise RuntimeError('发布脚本前必须先登录作者账号')
        request_verify = get_market_verify_ssl(verify_ssl)
        api_url = build_market_upload_ticket_api_url(auth_server_base)
        payload = {
            'package_id': str(package_id or '').strip(),
            'version': str(version or '').strip(),
        }
        try:
            response = requests.post(
                api_url,
                json=payload,
                headers=self._build_author_headers(author_token),
                timeout=timeout,
                verify=request_verify,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise RuntimeError(self._build_request_error_message('获取共享平台包上传票据失败', exc)) from exc
        if not isinstance(data, dict):
            raise RuntimeError('授权服务器返回上传票据无效')
        if data.get('success') is False:
            raise RuntimeError(self._get_payload_error_message(data, '获取上传票据失败'))
        ticket = str(data.get('upload_ticket') or '').strip()
        if not ticket:
            raise RuntimeError('授权服务器未返回上传票据')
        return ticket

    def publish_package_metadata(
        self,
        manifest: MarketPackageManifest,
        upload_result: Dict,
        archive_path: str | Path,
        auth_server_base: str = '',
        verify_ssl=None,
        changelog: str = '',
        release_notes: str = '',
        author_token: str = '',
        timeout: int = 60,
    ) -> Dict:
        request_verify = get_market_verify_ssl(verify_ssl)
        archive = Path(archive_path).expanduser().resolve()
        package_id, version = self._validate_package_identity(manifest.package_id, manifest.version)
        api_url = build_market_publish_api_url(auth_server_base)
        file_sha256 = str(upload_result.get('file_sha256') or '').strip() or self._calculate_file_sha256(archive)
        file_size = int(upload_result.get('file_size') or 0) or int(archive.stat().st_size)
        local_secret = load_archive_secret(archive)
        protection_payload_key = ''
        if is_manifest_protected(manifest):
            protection_payload_key = str(local_secret.get('protection_payload_key') or '').strip()
            if not protection_payload_key:
                raise RuntimeError('受保护共享平台包缺少本地发布密钥，请重新打包后再发布')
        payload = {
            'package_id': package_id,
            'version': version,
            'title': manifest.title,
            'category': manifest.category,
            'summary': manifest.description or manifest.title,
            'manifest': manifest.to_dict(),
            'file_sha256': file_sha256,
            'file_size': file_size,
            'storage_path': str(upload_result.get('storage_path') or '').strip(),
            'cover_path': str(upload_result.get('cover_path') or '').strip(),
            'changelog': str(changelog or '').strip(),
            'release_notes': str(release_notes or '').strip(),
            'protection_payload_key': protection_payload_key,
        }
        try:
            response = requests.post(
                api_url,
                json=payload,
                headers=self._build_author_headers(author_token),
                timeout=timeout,
                verify=request_verify,
            )
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            raise RuntimeError(self._build_request_error_message('提交共享平台包审核失败', exc)) from exc

        if not isinstance(result, dict):
            raise RuntimeError('授权服务器返回数据无效')
        if result.get('success') is False:
            raise RuntimeError(self._get_payload_error_message(result, '提交审核失败'))
        return result

    def install_package_from_archive(self, archive_path: str | Path, copy_archive: bool = True) -> MarketPackageManifest:
        archive = Path(archive_path)
        ok, errors = validate_archive_basic_structure(archive)
        if not ok:
            raise ValueError('共享平台包结构无效: ' + '; '.join(errors))

        manifest = load_manifest_from_archive(archive)
        install_dir = get_package_install_dir(manifest.package_id, manifest.version)
        archive_output = get_package_archive_path(manifest.package_id, manifest.version)

        if install_dir.exists():
            shutil.rmtree(install_dir, ignore_errors=True)
        install_dir.mkdir(parents=True, exist_ok=True)

        copied_archive = False
        try:
            with zipfile.ZipFile(archive, 'r') as package_zip:
                self._safe_extract_archive(package_zip, install_dir)
            if not is_manifest_protected(manifest):
                self._post_install_package(install_dir)

            if copy_archive:
                archive_output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(archive, archive_output)
                copied_archive = True
            return manifest
        except Exception:
            shutil.rmtree(install_dir, ignore_errors=True)
            if copied_archive and archive_output.exists():
                try:
                    archive_output.unlink()
                except OSError:
                    pass
            raise

    def load_installed_manifest(self, package_id: str, version: str) -> Optional[MarketPackageManifest]:
        manifest_path = get_installed_manifest_path(package_id, version)
        if not manifest_path.exists():
            return None
        try:
            raw = json.loads(manifest_path.read_text(encoding='utf-8'))
        except Exception:
            return None
        return MarketPackageManifest.from_dict(raw)

    def list_installed_manifests(self) -> List[MarketPackageManifest]:
        manifests: List[MarketPackageManifest] = []
        installed_root = get_market_installed_root()
        if not installed_root.exists():
            return manifests

        for package_dir in installed_root.iterdir():
            if not package_dir.is_dir():
                continue
            for version_dir in package_dir.iterdir():
                if not version_dir.is_dir():
                    continue
                manifest_path = version_dir / 'manifest.json'
                if not manifest_path.exists():
                    continue
                try:
                    raw = json.loads(manifest_path.read_text(encoding='utf-8'))
                    manifests.append(MarketPackageManifest.from_dict(raw))
                except Exception:
                    continue

        manifests.sort(key=lambda item: (item.package_id, item.version))
        return manifests


    def uninstall_installed_package(self, package_id: str, version: str) -> None:
        raw_package_id = str(package_id or '').strip()
        raw_version = str(version or '').strip()

        package_keys: list[str] = []
        version_keys: list[str] = []
        for value in (to_safe_storage_segment(raw_package_id), raw_package_id):
            value = str(value or '').strip()
            if value and value not in package_keys:
                package_keys.append(value)
        for value in (to_safe_storage_segment(raw_version), raw_version):
            value = str(value or '').strip()
            if value and value not in version_keys:
                version_keys.append(value)

        override_path = get_package_user_override_path(package_id)
        runtime_root = get_market_runtime_root()

        for package_key in package_keys:
            for version_key in version_keys:
                install_dir = get_market_installed_root() / package_key / version_key
                archive_dir = get_market_packages_root() / package_key / version_key
                if install_dir.exists():
                    shutil.rmtree(install_dir, ignore_errors=True)
                if archive_dir.exists():
                    shutil.rmtree(archive_dir, ignore_errors=True)

        if override_path.exists():
            try:
                override_path.unlink()
            except OSError:
                pass

        if runtime_root.exists():
            for access_mode_dir in runtime_root.iterdir():
                if not access_mode_dir.is_dir():
                    continue
                for package_key in package_keys:
                    for version_key in version_keys:
                        version_runtime_dir = access_mode_dir / package_key / version_key
                        if version_runtime_dir.exists():
                            shutil.rmtree(version_runtime_dir, ignore_errors=True)
                    package_runtime_dir = access_mode_dir / package_key
                    if package_runtime_dir.exists():
                        try:
                            next(package_runtime_dir.iterdir())
                        except StopIteration:
                            shutil.rmtree(package_runtime_dir, ignore_errors=True)

        parent_dirs = []
        for package_key in package_keys:
            parent_dirs.extend([
                get_market_installed_root() / package_key,
                get_market_packages_root() / package_key,
            ])
        for parent_dir in parent_dirs:
            if parent_dir.exists():
                try:
                    next(parent_dir.iterdir())
                except StopIteration:
                    shutil.rmtree(parent_dir, ignore_errors=True)

    def register_author_account(
        self,
        username: str,
        password: str,
        auth_server_base: str = '',
        verify_ssl=None,
        timeout: int = 20,
    ) -> MarketAuthorAccount:
        return self._request_author_account(
            build_market_author_register_api_url(auth_server_base),
            username=username,
            password=password,
            verify_ssl=verify_ssl,
            timeout=timeout,
        )

    def login_author_account(
        self,
        username: str,
        password: str,
        auth_server_base: str = '',
        verify_ssl=None,
        timeout: int = 20,
    ) -> MarketAuthorAccount:
        return self._request_author_account(
            build_market_author_login_api_url(auth_server_base),
            username=username,
            password=password,
            verify_ssl=verify_ssl,
            timeout=timeout,
        )

    def load_author_profile(
        self,
        author_token: str,
        auth_server_base: str = '',
        verify_ssl=None,
        timeout: int = 15,
    ) -> MarketAuthorAccount:
        request_verify = get_market_verify_ssl(verify_ssl)
        api_url = build_market_author_profile_api_url(auth_server_base)
        try:
            response = requests.get(
                api_url,
                headers=self._build_author_headers(author_token),
                timeout=timeout,
                verify=request_verify,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(self._build_request_error_message('\u52a0\u8f7d\u811a\u672c\u5217\u8868\u5931\u8d25', exc)) from exc
        if not isinstance(payload, dict):
            raise RuntimeError('\u4f5c\u8005\u8d26\u53f7\u4fe1\u606f\u8fd4\u56de\u65e0\u6548')
        return MarketAuthorAccount.from_auth_payload(payload)

    def logout_author_account(
        self,
        author_token: str,
        auth_server_base: str = '',
        verify_ssl=None,
        timeout: int = 15,
    ) -> None:
        request_verify = get_market_verify_ssl(verify_ssl)
        api_url = build_market_author_logout_api_url(auth_server_base)
        try:
            response = requests.post(
                api_url,
                headers=self._build_author_headers(author_token),
                timeout=timeout,
                verify=request_verify,
            )
            response.raise_for_status()
        except Exception as exc:
            raise RuntimeError(self._build_request_error_message('\u9000\u51fa\u4f5c\u8005\u8d26\u53f7\u5931\u8d25', exc)) from exc

    def _request_author_account(
        self,
        api_url: str,
        username: str,
        password: str,
        verify_ssl=None,
        timeout: int = 20,
    ) -> MarketAuthorAccount:
        request_verify = get_market_verify_ssl(verify_ssl)
        payload = {
            'username': str(username or '').strip(),
            'password': str(password or ''),
        }
        try:
            response = requests.post(api_url, json=payload, timeout=timeout, verify=request_verify)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise RuntimeError(self._build_request_error_message('\u4f5c\u8005\u8d26\u53f7\u8bf7\u6c42\u5931\u8d25', exc)) from exc
        if not isinstance(data, dict):
            raise RuntimeError('\u4f5c\u8005\u8d26\u53f7\u63a5\u53e3\u8fd4\u56de\u65e0\u6548')
        if data.get('success') is False:
            raise RuntimeError(self._get_payload_error_message(data, '\u4f5c\u8005\u8d26\u53f7\u8bf7\u6c42\u5931\u8d25'))
        account = MarketAuthorAccount.from_auth_payload(data)
        if not account.is_logged_in:
            raise RuntimeError('\u4f5c\u8005\u8d26\u53f7\u767b\u5f55\u7ed3\u679c\u65e0\u6548')
        return account

    def list_remote_packages(
        self,
        auth_server_base: str = '',
        verify_ssl=None,
        timeout: int = 15,
        author_token: str = '',
        mine_only: bool = False,
    ) -> List[RemoteMarketPackageSummary]:
        request_verify = get_market_verify_ssl(verify_ssl)
        api_url = build_market_my_packages_api_url(auth_server_base) if mine_only else build_market_packages_api_url(auth_server_base)
        try:
            response = requests.get(
                api_url,
                headers=self._build_author_headers(author_token),
                timeout=timeout,
                verify=request_verify,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(self._build_request_error_message('加载脚本列表失败', exc)) from exc

        raw_items = payload.get('items') if isinstance(payload, dict) else []
        return [RemoteMarketPackageSummary.from_dict(item) for item in raw_items if isinstance(item, dict)]

    def delete_remote_package(
        self,
        package_id: str,
        version: str,
        auth_server_base: str = '',
        verify_ssl=None,
        author_token: str = '',
        timeout: int = 20,
    ) -> Dict:
        if not str(author_token or '').strip():
            raise RuntimeError('删除脚本前必须先登录作者账号')
        safe_package_id, safe_version = self._validate_package_identity(package_id, version)
        request_verify = get_market_verify_ssl(verify_ssl)
        api_url = build_market_delete_package_api_url(safe_package_id, safe_version, auth_server_base)
        try:
            response = requests.delete(
                api_url,
                headers=self._build_author_headers(author_token),
                timeout=timeout,
                verify=request_verify,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(self._build_request_error_message('删除脚本失败', exc)) from exc
        if not isinstance(payload, dict):
            raise RuntimeError('授权服务器返回数据无效')
        if payload.get('success') is False:
            raise RuntimeError(self._get_payload_error_message(payload, '删除脚本失败'))
        return payload

    def update_remote_package_status(
        self,
        package_id: str,
        version: str,
        action: str,
        auth_server_base: str = '',
        verify_ssl=None,
        author_token: str = '',
        timeout: int = 20,
    ) -> RemoteMarketPackageSummary:
        safe_package_id, safe_version = self._validate_package_identity(package_id, version)
        api_url = build_market_package_status_api_url(safe_package_id, safe_version, auth_server_base)
        request_verify = get_market_verify_ssl(verify_ssl)
        payload = {'action': str(action or '').strip().lower()}
        try:
            response = requests.post(
                api_url,
                json=payload,
                headers=self._build_author_headers(author_token),
                timeout=timeout,
                verify=request_verify,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise RuntimeError(self._build_request_error_message('更新脚本状态失败', exc)) from exc
        if not isinstance(data, dict):
            raise RuntimeError('授权服务器返回数据无效')
        if data.get('success') is False:
            raise RuntimeError(self._get_payload_error_message(data, '更新脚本状态失败'))
        package_payload = data.get('package') if isinstance(data.get('package'), dict) else data
        return RemoteMarketPackageSummary.from_dict(package_payload)
    def _request_package_access(
        self,
        package_id: str,
        version: str,
        access_mode: str,
        auth_server_base: str = '',
        verify_ssl=None,
        author_token: str = '',
        timeout: int = 20,
    ) -> Dict:
        request_verify = get_market_verify_ssl(verify_ssl)
        if access_mode == 'edit':
            api_url = build_market_edit_access_api_url(package_id, version, auth_server_base=auth_server_base)
        else:
            api_url = build_market_runtime_access_api_url(package_id, version, auth_server_base=auth_server_base)
        try:
            response = requests.post(
                api_url,
                headers=self._build_author_headers(author_token),
                timeout=timeout,
                verify=request_verify,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            action = f"获取共享平台包{'编辑' if access_mode == 'edit' else '运行'}授权失败"
            raise RuntimeError(self._build_request_error_message(action, exc)) from exc
        if not isinstance(payload, dict):
            raise RuntimeError('授权服务器返回数据无效')
        if payload.get('success') is False:
            raise RuntimeError(self._get_payload_error_message(payload, '获取授权失败'))
        return payload

    def materialize_installed_entry_workflow(
        self,
        package_id: str,
        version: str,
        entry_workflow: str = 'workflow/main.json',
        access_mode: str = 'run',
        auth_server_base: str = '',
        verify_ssl=None,
        author_token: str = '',
        timeout: int = 30,
    ) -> Path:
        safe_package_id, safe_version = self._validate_package_identity(package_id, version)
        manifest = self.load_installed_manifest(safe_package_id, safe_version)
        if manifest is None:
            raise FileNotFoundError(f'未安装共享平台包: {safe_package_id} {safe_version}')

        if not is_manifest_protected(manifest):
            workflow_path = self.get_installed_entry_workflow_path(
                safe_package_id,
                safe_version,
                entry_workflow=entry_workflow or manifest.entry_workflow,
            )
            if workflow_path is None:
                raise FileNotFoundError('未找到已安装入口工作流')
            return workflow_path

        resolved_access_mode = str(access_mode or 'run').strip().lower() or 'run'

        access_payload = self._request_package_access(
            safe_package_id,
            safe_version,
            access_mode=resolved_access_mode,
            auth_server_base=auth_server_base,
            verify_ssl=verify_ssl,
            author_token=author_token,
            timeout=timeout,
        )
        payload_key = str(access_payload.get('payload_key') or '').strip()
        if not payload_key:
            raise RuntimeError('授权服务器未返回解密密钥')

        install_dir = get_package_install_dir(safe_package_id, safe_version)
        session_dir = create_package_runtime_session_dir(safe_package_id, safe_version, access_mode=resolved_access_mode)
        try:
            extract_protected_payload(install_dir, manifest, payload_key, session_dir)
            self._post_install_package(session_dir)
            relative_entry = Path(str(entry_workflow or manifest.entry_workflow or 'workflow/main.json').strip().replace('\\', '/'))
            if relative_entry.is_absolute():
                raise RuntimeError('入口工作流路径非法')
            entry_path = (session_dir / relative_entry).resolve()
            entry_path.relative_to(session_dir.resolve())
            if not entry_path.exists() or not entry_path.is_file():
                raise FileNotFoundError(f'未找到解密后的入口工作流: {entry_path}')
            return entry_path
        except Exception:
            shutil.rmtree(session_dir, ignore_errors=True)
            raise

    def resolve_market_workflow_ref(
        self,
        workflow_ref: str,
        auth_server_base: str = '',
        verify_ssl=None,
        author_token: str = '',
        access_mode: str = 'run',
        timeout: int = 30,
    ) -> Path:
        ref_info = parse_market_workflow_ref(workflow_ref)
        if not ref_info:
            raise ValueError('无效的共享平台工作流引用')
        return self.materialize_installed_entry_workflow(
            ref_info['package_id'],
            ref_info['version'],
            entry_workflow=ref_info.get('entry_workflow') or 'workflow/main.json',
            access_mode=access_mode,
            auth_server_base=auth_server_base,
            verify_ssl=verify_ssl,
            author_token=author_token,
            timeout=timeout,
        )

    def download_remote_package(
        self,
        package_id: str,
        version: str,
        download_url: str = '',
        auth_server_base: str = '',
        update_server_base: str = '',
        verify_ssl=None,
        timeout: int = 60,
    ) -> Path:
        safe_package_id, safe_version = self._validate_package_identity(package_id, version)

        request_verify = get_market_verify_ssl(verify_ssl)
        resolved_download_url = str(download_url or '').strip()
        if not resolved_download_url:
            resolved_download_url = self._fetch_download_url(
                safe_package_id,
                safe_version,
                auth_server_base=auth_server_base,
                verify_ssl=request_verify,
            )
        if not resolved_download_url:
            resolved_download_url = build_market_package_download_url(
                safe_package_id,
                safe_version,
                base_url=update_server_base,
            )

        output_path = get_market_cache_download_path(safe_package_id, safe_version)
        tmp_path = output_path.with_suffix(output_path.suffix + '.tmp')

        try:
            with requests.get(resolved_download_url, stream=True, timeout=timeout, verify=request_verify) as response:
                response.raise_for_status()
                with tmp_path.open('wb') as output_file:
                    for chunk in response.iter_content(chunk_size=65536):
                        if chunk:
                            output_file.write(chunk)
            os.replace(tmp_path, output_path)
        except Exception as exc:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            raise RuntimeError(self._build_request_error_message('下载共享平台包失败', exc)) from exc

        return output_path

    def _fetch_download_url(
        self,
        package_id: str,
        version: str,
        auth_server_base: str = '',
        verify_ssl=None,
        timeout: int = 15,
    ) -> str:
        request_verify = get_market_verify_ssl(verify_ssl)
        token_url = build_market_download_token_api_url(package_id, version, auth_server_base=auth_server_base)
        try:
            response = requests.get(token_url, timeout=timeout, verify=request_verify)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(self._build_request_error_message('获取共享平台包下载地址失败', exc)) from exc
        if not isinstance(payload, dict):
            return ''
        return str(payload.get('download_url') or '').strip()

    def get_installed_entry_workflow_path(
        self,
        package_id: str,
        version: str,
        entry_workflow: str = 'workflow/main.json',
    ) -> Optional[Path]:
        safe_package_id = str(package_id or '').strip()
        safe_version = str(version or '').strip()
        safe_entry_workflow = str(entry_workflow or 'workflow/main.json').strip().replace('\\', '/')
        if not safe_package_id or not safe_version or not safe_entry_workflow:
            return None

        relative_path = Path(safe_entry_workflow)
        if relative_path.is_absolute():
            return None

        manifest = self.load_installed_manifest(safe_package_id, safe_version)
        if manifest is None:
            return None
        if is_manifest_protected(manifest):
            return None

        install_dir = get_package_install_dir(safe_package_id, safe_version).resolve()
        workflow_path = (install_dir / relative_path).resolve()
        try:
            workflow_path.relative_to(install_dir)
        except Exception:
            return None
        return workflow_path if workflow_path.exists() else None

    def run_precheck(self, manifest: MarketPackageManifest) -> PrecheckReport:
        return self._precheck_engine.run(manifest)

    def save_user_override(self, package_id: str, payload: Dict) -> Path:
        path = get_package_user_override_path(package_id)
        path.write_text(json.dumps(payload or {}, ensure_ascii=False, indent=2), encoding='utf-8')
        return path

    def load_user_override(self, package_id: str) -> Dict:
        path = get_package_user_override_path(package_id)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return {}

    @staticmethod
    def _validate_package_identity(package_id: str, version: str) -> tuple[str, str]:
        return validate_package_identity(package_id, version)

    @staticmethod
    def _calculate_file_sha256(file_path: Path) -> str:
        digest = hashlib.sha256()
        with file_path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                if chunk:
                    digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _safe_extract_archive(package_zip: zipfile.ZipFile, install_dir: Path) -> None:
        base_dir = install_dir.resolve()
        for member in package_zip.infolist():
            member_name = str(member.filename or '').replace('\\', '/').strip('/')
            if not member_name:
                continue
            target_path = (base_dir / member_name).resolve()
            try:
                target_path.relative_to(base_dir)
            except Exception as exc:
                raise ValueError(f'共享平台包包含非法路径: {member.filename}') from exc
            if member.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with package_zip.open(member, 'r') as source_handle, target_path.open('wb') as target_handle:
                shutil.copyfileobj(source_handle, target_handle)

    @staticmethod
    def _post_install_package(install_dir: Path) -> None:
        materialize_installed_package_workflows(install_dir)
        import_packaged_dicts(install_dir)
