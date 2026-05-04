# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


MARKET_MANIFEST_SCHEMA_VERSION = 2


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _as_int(value: Any) -> Optional[int]:
    if value in (None, "", False):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _as_float(value: Any) -> Optional[float]:
    if value in (None, "", False):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on", "是"}:
            return True
        if text in {"0", "false", "no", "n", "off", "否", ""}:
            return False
    return default


def _as_text_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    text = _as_text(value)
    return [text] if text else []


@dataclass
class TargetWindowRequirement:
    window_kind: str = ""
    process_names: List[str] = field(default_factory=list)
    class_names: List[str] = field(default_factory=list)
    title_keywords: List[str] = field(default_factory=list)
    client_width: Optional[int] = None
    client_height: Optional[int] = None
    min_client_width: Optional[int] = None
    max_client_width: Optional[int] = None
    min_client_height: Optional[int] = None
    max_client_height: Optional[int] = None
    dpi: Optional[int] = None
    scale_factor: Optional[float] = None
    orientation: str = ""
    multi_instance_support: bool = False
    notes: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> "TargetWindowRequirement":
        raw = data if isinstance(data, dict) else {}
        return cls(
            window_kind=_as_text(raw.get("window_kind")),
            process_names=_as_text_list(raw.get("process_names")),
            class_names=_as_text_list(raw.get("class_names")),
            title_keywords=_as_text_list(raw.get("title_keywords")),
            client_width=_as_int(raw.get("client_width")),
            client_height=_as_int(raw.get("client_height")),
            min_client_width=_as_int(raw.get("min_client_width")),
            max_client_width=_as_int(raw.get("max_client_width")),
            min_client_height=_as_int(raw.get("min_client_height")),
            max_client_height=_as_int(raw.get("max_client_height")),
            dpi=_as_int(raw.get("dpi")),
            scale_factor=_as_float(raw.get("scale_factor")),
            orientation=_as_text(raw.get("orientation")),
            multi_instance_support=_as_bool(raw.get("multi_instance_support")),
            notes=_as_text_list(raw.get("notes")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_kind": self.window_kind,
            "process_names": list(self.process_names),
            "class_names": list(self.class_names),
            "title_keywords": list(self.title_keywords),
            "client_width": self.client_width,
            "client_height": self.client_height,
            "min_client_width": self.min_client_width,
            "max_client_width": self.max_client_width,
            "min_client_height": self.min_client_height,
            "max_client_height": self.max_client_height,
            "dpi": self.dpi,
            "scale_factor": self.scale_factor,
            "orientation": self.orientation,
            "multi_instance_support": self.multi_instance_support,
            "notes": list(self.notes),
        }


@dataclass
class RuntimeRequirement:
    execution_mode: str = ""
    screenshot_engine: str = ""
    plugin_required: bool = False
    plugin_id: str = ""
    plugin_min_version: str = ""
    plugin_settings_template: Dict[str, Any] = field(default_factory=dict)
    required_models: List[str] = field(default_factory=list)
    required_task_types: List[str] = field(default_factory=list)
    target_window: TargetWindowRequirement = field(default_factory=TargetWindowRequirement)

    @classmethod
    def from_dict(cls, data: Any) -> "RuntimeRequirement":
        raw = data if isinstance(data, dict) else {}
        return cls(
            execution_mode=_as_text(raw.get("execution_mode")),
            screenshot_engine=_as_text(raw.get("screenshot_engine")),
            plugin_required=_as_bool(raw.get("plugin_required")),
            plugin_id=_as_text(raw.get("plugin_id")),
            plugin_min_version=_as_text(raw.get("plugin_min_version")),
            plugin_settings_template=dict(raw.get("plugin_settings_template") or {}),
            required_models=_as_text_list(raw.get("required_models")),
            required_task_types=_as_text_list(raw.get("required_task_types")),
            target_window=TargetWindowRequirement.from_dict(raw.get("target_window")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_mode": self.execution_mode,
            "screenshot_engine": self.screenshot_engine,
            "plugin_required": self.plugin_required,
            "plugin_id": self.plugin_id,
            "plugin_min_version": self.plugin_min_version,
            "plugin_settings_template": dict(self.plugin_settings_template),
            "required_models": list(self.required_models),
            "required_task_types": list(self.required_task_types),
            "target_window": self.target_window.to_dict(),
        }


@dataclass
class ConfigurationGuide:
    summary: str = ""
    required_steps: List[str] = field(default_factory=list)
    recommended_steps: List[str] = field(default_factory=list)
    target_window_notes: List[str] = field(default_factory=list)
    common_failures: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> "ConfigurationGuide":
        raw = data if isinstance(data, dict) else {}
        return cls(
            summary=_as_text(raw.get("summary")),
            required_steps=_as_text_list(raw.get("required_steps")),
            recommended_steps=_as_text_list(raw.get("recommended_steps")),
            target_window_notes=_as_text_list(raw.get("target_window_notes")),
            common_failures=_as_text_list(raw.get("common_failures")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "required_steps": list(self.required_steps),
            "recommended_steps": list(self.recommended_steps),
            "target_window_notes": list(self.target_window_notes),
            "common_failures": list(self.common_failures),
        }


@dataclass
class MarketPackageProtection:
    enabled: bool = False
    scheme: str = ""
    payload_path: str = ""
    payload_sha256: str = ""
    payload_size: int = 0
    requires_online_key: bool = False
    edit_requires_owner_auth: bool = True
    public_files: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> "MarketPackageProtection":
        raw = data if isinstance(data, dict) else {}
        return cls(
            enabled=_as_bool(raw.get("enabled"), default=False),
            scheme=_as_text(raw.get("scheme")),
            payload_path=_as_text(raw.get("payload_path")),
            payload_sha256=_as_text(raw.get("payload_sha256")),
            payload_size=_as_int(raw.get("payload_size")) or 0,
            requires_online_key=_as_bool(raw.get("requires_online_key"), default=False),
            edit_requires_owner_auth=_as_bool(raw.get("edit_requires_owner_auth"), default=True),
            public_files=_as_text_list(raw.get("public_files")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "scheme": self.scheme,
            "payload_path": self.payload_path,
            "payload_sha256": self.payload_sha256,
            "payload_size": self.payload_size,
            "requires_online_key": self.requires_online_key,
            "edit_requires_owner_auth": self.edit_requires_owner_auth,
            "public_files": list(self.public_files),
        }


@dataclass
class MarketPackageManifest:
    schema_version: int = MARKET_MANIFEST_SCHEMA_VERSION
    package_id: str = ""
    version: str = ""
    title: str = ""
    author: str = ""
    description: str = ""
    category: str = ""
    tags: List[str] = field(default_factory=list)
    entry_workflow: str = "workflow/main.json"
    cover_image: str = ""
    min_client_version: str = ""
    max_client_version: str = ""
    runtime_requirement: RuntimeRequirement = field(default_factory=RuntimeRequirement)
    configuration_guide: ConfigurationGuide = field(default_factory=ConfigurationGuide)
    protection: MarketPackageProtection = field(default_factory=MarketPackageProtection)
    permissions: List[str] = field(default_factory=list)
    file_hashes: Dict[str, str] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> "MarketPackageManifest":
        raw = data if isinstance(data, dict) else {}
        reserved = {
            "schema_version",
            "package_id",
            "version",
            "title",
            "author",
            "description",
            "category",
            "tags",
            "entry_workflow",
            "cover_image",
            "min_client_version",
            "max_client_version",
            "runtime_requirement",
            "configuration_guide",
            "protection",
            "permissions",
            "file_hashes",
        }
        return cls(
            schema_version=_as_int(raw.get("schema_version")) or MARKET_MANIFEST_SCHEMA_VERSION,
            package_id=_as_text(raw.get("package_id")),
            version=_as_text(raw.get("version")),
            title=_as_text(raw.get("title")),
            author=_as_text(raw.get("author")),
            description=_as_text(raw.get("description")),
            category=_as_text(raw.get("category")),
            tags=_as_text_list(raw.get("tags")),
            entry_workflow=_as_text(raw.get("entry_workflow")) or "workflow/main.json",
            cover_image=_as_text(raw.get("cover_image")),
            min_client_version=_as_text(raw.get("min_client_version")),
            max_client_version=_as_text(raw.get("max_client_version")),
            runtime_requirement=RuntimeRequirement.from_dict(raw.get("runtime_requirement")),
            configuration_guide=ConfigurationGuide.from_dict(raw.get("configuration_guide")),
            protection=MarketPackageProtection.from_dict(raw.get("protection")),
            permissions=_as_text_list(raw.get("permissions")),
            file_hashes=dict(raw.get("file_hashes") or {}),
            extra={key: value for key, value in raw.items() if key not in reserved},
        )

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "schema_version": self.schema_version,
            "package_id": self.package_id,
            "version": self.version,
            "title": self.title,
            "author": self.author,
            "description": self.description,
            "category": self.category,
            "tags": list(self.tags),
            "entry_workflow": self.entry_workflow,
            "cover_image": self.cover_image,
            "min_client_version": self.min_client_version,
            "max_client_version": self.max_client_version,
            "runtime_requirement": self.runtime_requirement.to_dict(),
            "configuration_guide": self.configuration_guide.to_dict(),
            "protection": self.protection.to_dict(),
            "permissions": list(self.permissions),
            "file_hashes": dict(self.file_hashes),
        }
        data.update(self.extra)
        return data


@dataclass
class RemoteMarketPackageSummary:
    package_id: str = ""
    version: str = ""
    title: str = ""
    category: str = ""
    summary: str = ""
    author_name: str = ""
    status: str = "draft"
    latest_version: str = ""
    visibility: str = "private"
    cover_url: str = ""
    download_url: str = ""
    can_edit: bool = False
    can_delete: bool = False
    can_run: bool = True

    @classmethod
    def from_dict(cls, data: Any) -> "RemoteMarketPackageSummary":
        raw = data if isinstance(data, dict) else {}
        return cls(
            package_id=_as_text(raw.get("package_id")),
            version=_as_text(raw.get("version")),
            title=_as_text(raw.get("title")),
            category=_as_text(raw.get("category")),
            summary=_as_text(raw.get("summary")),
            author_name=_as_text(raw.get("author_name")),
            status=_as_text(raw.get("status")) or "draft",
            latest_version=_as_text(raw.get("latest_version")),
            visibility=_as_text(raw.get("visibility")) or "private",
            cover_url=_as_text(raw.get("cover_url")),
            download_url=_as_text(raw.get("download_url")),
            can_edit=_as_bool(raw.get("can_edit"), default=False),
            can_delete=_as_bool(raw.get("can_delete"), default=False),
            can_run=_as_bool(raw.get("can_run"), default=True),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "package_id": self.package_id,
            "version": self.version,
            "title": self.title,
            "category": self.category,
            "summary": self.summary,
            "author_name": self.author_name,
            "status": self.status,
            "latest_version": self.latest_version,
            "visibility": self.visibility,
            "cover_url": self.cover_url,
            "download_url": self.download_url,
            "can_edit": self.can_edit,
            "can_delete": self.can_delete,
            "can_run": self.can_run,
        }

@dataclass
class MarketAuthorAccount:
    user_id: int = 0
    username: str = ""
    access_token: str = ""
    expires_in: int = 0
    is_admin: bool = False

    @property
    def is_logged_in(self) -> bool:
        return bool(self.user_id and self.username and self.access_token)

    @classmethod
    def from_auth_payload(cls, data: Any) -> "MarketAuthorAccount":
        raw = data if isinstance(data, dict) else {}
        user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
        return cls(
            user_id=_as_int(user.get("id")) or 0,
            username=_as_text(user.get("username")),
            access_token=_as_text(raw.get("access_token")),
            expires_in=_as_int(raw.get("expires_in")) or 0,
            is_admin=_as_bool(user.get("is_admin"), default=False),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "access_token": self.access_token,
            "expires_in": self.expires_in,
            "is_admin": self.is_admin,
        }


@dataclass
class EnvironmentSnapshot:
    app_version: str = ""
    execution_mode: str = ""
    screenshot_engine: str = ""
    plugin_enabled: bool = False
    preferred_plugin: str = ""
    plugin_settings: Dict[str, Any] = field(default_factory=dict)
    bound_window_title: str = ""
    bound_window_class_name: str = ""
    bound_window_client_width: Optional[int] = None
    bound_window_client_height: Optional[int] = None
    bound_window_dpi: Optional[int] = None
    bound_window_scale_factor: Optional[float] = None
    raw_config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PrecheckIssue:
    code: str
    severity: str
    title: str
    message: str
    current_value: Any = None
    expected_value: Any = None
    action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "current_value": self.current_value,
            "expected_value": self.expected_value,
            "action": self.action,
        }


@dataclass
class PrecheckReport:
    manifest: Optional[MarketPackageManifest] = None
    environment: Optional[EnvironmentSnapshot] = None
    issues: List[PrecheckIssue] = field(default_factory=list)

    @property
    def blocking_issues(self) -> List[PrecheckIssue]:
        return [item for item in self.issues if item.severity == "block"]

    @property
    def configure_issues(self) -> List[PrecheckIssue]:
        return [item for item in self.issues if item.severity == "configure"]

    @property
    def warning_issues(self) -> List[PrecheckIssue]:
        return [item for item in self.issues if item.severity == "warn"]

    @property
    def passed(self) -> bool:
        return not self.blocking_issues and not self.configure_issues

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "issues": [item.to_dict() for item in self.issues],
            "manifest": self.manifest.to_dict() if self.manifest else None,
        }
