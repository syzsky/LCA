# -*- coding: utf-8 -*-

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from market.environment import capture_environment_snapshot
from market.models import ConfigurationGuide, MarketPackageManifest, RuntimeRequirement, TargetWindowRequirement
from market.package_identity import validate_package_identity
from market.storage import get_package_archive_path
from tasks import TASK_MODULES
from tasks.task_utils import correct_image_paths, correct_single_image_path
from utils.sub_workflow_path import resolve_sub_workflow_path

_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tiff', '.webp'}
_DOC_EXTENSIONS = {'.txt', '.md', '.json', '.yaml', '.yml', '.ini', '.cfg', '.csv'}
_MODEL_EXTENSIONS = {'.onnx', '.pt', '.pth', '.engine', '.trt', '.bin'}
_MULTI_PATH_PATTERN = re.compile(r'[\r\n;]+')


@dataclass
class ExportDependencyRecord:
    kind: str
    source_path: str
    package_path: str


@dataclass
class MarketPackageExportResult:
    manifest: MarketPackageManifest
    archive_path: Path
    collected_files: List[ExportDependencyRecord] = field(default_factory=list)
    dict_names: List[str] = field(default_factory=list)
    workflow_files: List[str] = field(default_factory=list)
    task_types: List[str] = field(default_factory=list)
    protection_payload_key: str = ""


class MarketPackageExporter:
    def __init__(self, config_data: Optional[Dict[str, Any]] = None):
        self._config_data = dict(config_data or {})

    def build_package(
        self,
        entry_workflow_path: str | Path,
        manifest: MarketPackageManifest | Dict[str, Any],
        output_path: str | Path | None = None,
    ) -> MarketPackageExportResult:
        entry_path = Path(entry_workflow_path).expanduser().resolve()
        if not entry_path.exists() or not entry_path.is_file():
            raise FileNotFoundError(f'入口工作流不存在: {entry_path}')

        base_manifest = manifest if isinstance(manifest, MarketPackageManifest) else MarketPackageManifest.from_dict(manifest)
        base_manifest.package_id, base_manifest.version = validate_package_identity(base_manifest.package_id, base_manifest.version)
        if not base_manifest.title:
            raise ValueError('title 不能为空')

        target_archive = Path(output_path).expanduser().resolve() if output_path else get_package_archive_path(base_manifest.package_id, base_manifest.version)
        target_archive.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix='lca_market_export_') as temp_dir:
            staging_root = Path(temp_dir)
            collector = _WorkflowDependencyCollector(entry_path=entry_path, staging_root=staging_root)
            try:
                entry_package_path = collector.collect_workflow(entry_path, is_entry=True)
                collector.finalize_dict_index()

                built_manifest = self._build_manifest(base_manifest, collector, entry_package_path)
                manifest_path = staging_root / 'manifest.json'
                manifest_path.write_text(
                    json.dumps(built_manifest.to_dict(), ensure_ascii=False, indent=2),
                    encoding='utf-8',
                )

                tmp_archive = target_archive.with_suffix(target_archive.suffix + '.tmp')
                if tmp_archive.exists():
                    tmp_archive.unlink()
                self._create_archive(staging_root, tmp_archive)
                os.replace(tmp_archive, target_archive)
            finally:
                collector.close()

        return MarketPackageExportResult(
            manifest=built_manifest,
            archive_path=target_archive,
            collected_files=list(collector.dependency_records),
            dict_names=sorted(collector.dict_records.keys()),
            workflow_files=sorted(path.replace('\\', '/') for path in collector.workflow_path_map.values()),
            task_types=sorted(collector.required_task_types),
            protection_payload_key='',
        )

    def _build_manifest(
        self,
        base_manifest: MarketPackageManifest,
        collector: '_WorkflowDependencyCollector',
        entry_package_path: Path,
    ) -> MarketPackageManifest:
        snapshot = capture_environment_snapshot(config_data=self._config_data)
        runtime_requirement = RuntimeRequirement.from_dict(base_manifest.runtime_requirement.to_dict())
        runtime_requirement.execution_mode = snapshot.execution_mode or runtime_requirement.execution_mode
        runtime_requirement.screenshot_engine = snapshot.screenshot_engine or runtime_requirement.screenshot_engine
        runtime_requirement.plugin_required = runtime_requirement.plugin_required or bool(collector.dict_records)
        if snapshot.preferred_plugin and not runtime_requirement.plugin_id:
            runtime_requirement.plugin_id = snapshot.preferred_plugin
        if snapshot.plugin_settings and not runtime_requirement.plugin_settings_template:
            runtime_requirement.plugin_settings_template = dict(snapshot.plugin_settings)
        runtime_requirement.required_task_types = sorted(collector.required_task_types)

        target_window = TargetWindowRequirement.from_dict(runtime_requirement.target_window.to_dict())
        if snapshot.bound_window_class_name and not target_window.class_names:
            target_window.class_names = [snapshot.bound_window_class_name]
        if snapshot.bound_window_title and not target_window.title_keywords:
            target_window.title_keywords = [snapshot.bound_window_title]
        if snapshot.bound_window_client_width and target_window.client_width is None:
            target_window.client_width = snapshot.bound_window_client_width
        if snapshot.bound_window_client_height and target_window.client_height is None:
            target_window.client_height = snapshot.bound_window_client_height
        if snapshot.bound_window_dpi and target_window.dpi is None:
            target_window.dpi = snapshot.bound_window_dpi
        if snapshot.bound_window_scale_factor and target_window.scale_factor is None:
            target_window.scale_factor = snapshot.bound_window_scale_factor
        target_window.multi_instance_support = target_window.multi_instance_support or len(self._get_bound_windows()) > 1
        runtime_requirement.target_window = target_window

        guide = ConfigurationGuide.from_dict(base_manifest.configuration_guide.to_dict())
        if not guide.summary:
            guide.summary = '导入后先执行预检，再按共享平台包说明绑定目标窗口。'
        if not guide.required_steps:
            guide.required_steps = self._build_required_steps(runtime_requirement)
        if not guide.target_window_notes:
            guide.target_window_notes = self._build_window_notes(target_window)

        extra = dict(base_manifest.extra)
        extra['dependency_summary'] = {
            'workflow_files': sorted(path.replace('\\', '/') for path in collector.workflow_path_map.values()),
            'dicts': [record.to_dict() for record in collector.dict_records.values()],
            'task_types': sorted(collector.required_task_types),
        }

        built_manifest = MarketPackageManifest.from_dict(base_manifest.to_dict())
        built_manifest.entry_workflow = entry_package_path.as_posix()
        if not built_manifest.min_client_version:
            built_manifest.min_client_version = snapshot.app_version
        built_manifest.runtime_requirement = runtime_requirement
        built_manifest.configuration_guide = guide
        built_manifest.protection.enabled = False
        built_manifest.protection.scheme = ''
        built_manifest.protection.payload_path = ''
        built_manifest.protection.payload_sha256 = ''
        built_manifest.protection.payload_size = 0
        built_manifest.protection.requires_online_key = False
        built_manifest.protection.edit_requires_owner_auth = False
        built_manifest.protection.public_files = []
        built_manifest.extra = extra
        return built_manifest

    def _build_required_steps(self, runtime_requirement: RuntimeRequirement) -> List[str]:
        steps: List[str] = []
        if runtime_requirement.execution_mode:
            steps.append(f'执行模式保持为: {runtime_requirement.execution_mode}')
        if runtime_requirement.screenshot_engine:
            steps.append(f'截图引擎保持为: {runtime_requirement.screenshot_engine}')
        if runtime_requirement.plugin_required:
            plugin_name = runtime_requirement.plugin_id or '当前脚本要求的插件'
            steps.append(f'启用插件模式，并确认首选插件为: {plugin_name}')
        return steps

    def _build_window_notes(self, target_window: TargetWindowRequirement) -> List[str]:
        notes: List[str] = []
        if target_window.class_names:
            notes.append('窗口类名: ' + ' / '.join(target_window.class_names))
        if target_window.title_keywords:
            notes.append('窗口标题关键词: ' + ' / '.join(target_window.title_keywords))
        if target_window.client_width and target_window.client_height:
            notes.append(f'客户区尺寸: {target_window.client_width} x {target_window.client_height}')
        if target_window.dpi:
            notes.append(f'DPI: {target_window.dpi}')
        return notes

    def _get_bound_windows(self) -> List[Dict[str, Any]]:
        bound_windows = self._config_data.get('bound_windows')
        if not isinstance(bound_windows, list):
            return []
        return [item for item in bound_windows if isinstance(item, dict) and item.get('enabled', True)]

    @staticmethod
    def _create_archive(staging_root: Path, archive_path: Path) -> None:
        with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as package_zip:
            for file_path in sorted(staging_root.rglob('*')):
                if not file_path.is_file():
                    continue
                package_zip.write(file_path, file_path.relative_to(staging_root).as_posix())


@dataclass
class _DictExportRecord:
    name: str
    package_path: str
    color: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'package_path': self.package_path,
            'color': self.color,
        }


class _WorkflowDependencyCollector:
    def __init__(self, entry_path: Path, staging_root: Path):
        self.entry_path = entry_path
        self.entry_base_dir = entry_path.parent.resolve()
        self.staging_root = staging_root
        self.workflow_source_map: Dict[str, Path] = {}
        self.workflow_path_map: Dict[str, str] = {}
        self.asset_path_map: Dict[str, str] = {}
        self.dependency_records: List[ExportDependencyRecord] = []
        self.required_task_types: set[str] = set()
        self.dict_records: Dict[str, _DictExportRecord] = {}
        self._ola = None
        self._ola_db_handle = None

    def collect_workflow(self, workflow_path: Path, is_entry: bool = False) -> Path:
        source_path = workflow_path.resolve()
        cache_key = str(source_path).lower()
        cached = self.workflow_path_map.get(cache_key)
        if cached:
            return Path(cached)

        package_path = self._assign_workflow_path(source_path)
        self.workflow_source_map[cache_key] = source_path
        self.workflow_path_map[cache_key] = package_path.as_posix()

        workflow_data = _load_workflow_json(source_path)
        rewritten_data = copy.deepcopy(workflow_data)
        cards = rewritten_data.get('cards') if isinstance(rewritten_data, dict) else None
        if not isinstance(cards, list):
            raise ValueError(f'工作流格式无效: {source_path}')

        for card in cards:
            if not isinstance(card, dict):
                continue
            task_type = str(card.get('task_type') or '').strip()
            if task_type:
                self.required_task_types.add(task_type)
            params = card.get('parameters') if isinstance(card.get('parameters'), dict) else {}
            rewritten_params = self._rewrite_card_parameters(task_type, params, source_path, package_path)
            card['parameters'] = rewritten_params

        target_path = self.staging_root / package_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps(rewritten_data, ensure_ascii=False, indent=2), encoding='utf-8')

        self.dependency_records.append(
            ExportDependencyRecord(
                kind='workflow' if not is_entry else 'entry_workflow',
                source_path=str(source_path),
                package_path=package_path.as_posix(),
            )
        )
        return package_path

    def finalize_dict_index(self) -> None:
        if not self.dict_records:
            return
        dict_root = self.staging_root / 'dicts'
        dict_root.mkdir(parents=True, exist_ok=True)
        index_path = dict_root / 'index.json'
        payload = {'dicts': [record.to_dict() for record in self.dict_records.values()]}
        index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        self.dependency_records.append(
            ExportDependencyRecord(kind='dict_index', source_path='generated', package_path='dicts/index.json')
        )

    def _rewrite_card_parameters(
        self,
        task_type: str,
        params: Dict[str, Any],
        workflow_source_path: Path,
        workflow_package_path: Path,
    ) -> Dict[str, Any]:
        rewritten = dict(params or {})
        param_defs = _get_param_definitions(task_type)

        for param_name, param_def in param_defs.items():
            param_type = str(param_def.get('type') or '').strip().lower()
            if param_type == 'file' and param_name in rewritten:
                rewritten[param_name] = self._rewrite_single_file_param(
                    task_type=task_type,
                    param_name=param_name,
                    raw_value=rewritten.get(param_name),
                    workflow_source_path=workflow_source_path,
                    workflow_package_path=workflow_package_path,
                )
            elif param_type == 'multi_file' and param_name in rewritten:
                rewritten[param_name] = self._rewrite_multi_file_param(
                    task_type=task_type,
                    param_name=param_name,
                    raw_value=rewritten.get(param_name),
                    workflow_source_path=workflow_source_path,
                )

        raw_image_paths = str(rewritten.get('image_paths') or '').strip()
        if raw_image_paths:
            rewritten['image_paths'] = self._rewrite_image_paths_text(raw_image_paths)

        if task_type == '字库识别':
            dict_name_expr = str(rewritten.get('dict_name') or '').strip()
            if dict_name_expr:
                for dict_name in [item.strip() for item in dict_name_expr.split('|') if item.strip()]:
                    self._collect_dict(dict_name)

        return rewritten

    def _rewrite_single_file_param(
        self,
        task_type: str,
        param_name: str,
        raw_value: Any,
        workflow_source_path: Path,
        workflow_package_path: Path,
    ) -> Any:
        raw_text = str(raw_value or '').strip()
        if not raw_text:
            return raw_value
        if task_type == '子工作流' and param_name == 'workflow_file':
            resolved_path = resolve_sub_workflow_path(raw_text, parent_workflow_file=str(workflow_source_path))
            if not resolved_path:
                raise FileNotFoundError(f'子工作流不存在: {raw_text}')
            sub_package_path = self.collect_workflow(Path(resolved_path))
            relative_path = os.path.relpath(
                sub_package_path.as_posix(),
                start=workflow_package_path.parent.as_posix(),
            ).replace('\\', '/')
            return relative_path

        source_path = _resolve_asset_source_path(raw_text, workflow_source_path, task_type=task_type, param_name=param_name)
        package_path = self._copy_asset(source_path, task_type=task_type, param_name=param_name)
        return package_path.as_posix()

    def _rewrite_multi_file_param(
        self,
        task_type: str,
        param_name: str,
        raw_value: Any,
        workflow_source_path: Path,
    ) -> Any:
        if isinstance(raw_value, list):
            raw_items = [str(item or '').strip() for item in raw_value if str(item or '').strip()]
        else:
            raw_items = [item.strip() for item in _MULTI_PATH_PATTERN.split(str(raw_value or '').strip()) if item.strip()]
        rewritten_items: List[str] = []
        for item in raw_items:
            source_path = _resolve_asset_source_path(item, workflow_source_path, task_type=task_type, param_name=param_name)
            package_path = self._copy_asset(source_path, task_type=task_type, param_name=param_name)
            rewritten_items.append(package_path.as_posix())
        if isinstance(raw_value, list):
            return rewritten_items
        return '\n'.join(rewritten_items)

    def _rewrite_image_paths_text(self, raw_text: str) -> str:
        raw_items = [item.strip() for item in _MULTI_PATH_PATTERN.split(raw_text) if item.strip()]
        if not raw_items:
            return raw_text
        resolved_items = correct_image_paths(raw_items)
        if len(resolved_items) != len(raw_items):
            missing = [item for item in raw_items if Path(item).name not in {Path(path).name for path in resolved_items}]
            raise FileNotFoundError(f"多图资源缺失: {', '.join(missing) or raw_text}")
        package_items = [self._copy_asset(Path(item), task_type='图片点击', param_name='image_paths').as_posix() for item in resolved_items]
        return '\n'.join(package_items)

    def _assign_workflow_path(self, source_path: Path) -> Path:
        try:
            relative_path = source_path.relative_to(self.entry_base_dir)
            return Path('workflow') / relative_path
        except Exception:
            digest = hashlib.sha1(str(source_path).encode('utf-8')).hexdigest()[:8]
            return Path('workflow') / 'external' / f'{digest}_{source_path.name}'

    def _copy_asset(self, source_path: Path, task_type: str, param_name: str) -> Path:
        source_path = source_path.resolve()
        cache_key = str(source_path).lower()
        cached = self.asset_path_map.get(cache_key)
        if cached:
            return Path(cached)

        category = _determine_asset_category(source_path, task_type=task_type, param_name=param_name)
        package_path = self._build_asset_path(source_path, category)
        target_path = self.staging_root / package_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)

        self.asset_path_map[cache_key] = package_path.as_posix()
        self.dependency_records.append(
            ExportDependencyRecord(
                kind=category,
                source_path=str(source_path),
                package_path=package_path.as_posix(),
            )
        )
        return package_path

    def _build_asset_path(self, source_path: Path, category: str) -> Path:
        try:
            relative_path = source_path.relative_to(self.entry_base_dir)
            relative_parts = list(relative_path.parts)
            if relative_parts and relative_parts[0].lower() == category:
                relative_parts = relative_parts[1:]
            if relative_parts:
                candidate = Path(category, *relative_parts)
                if not (self.staging_root / candidate).exists():
                    return candidate
        except Exception:
            pass

        digest = hashlib.sha1(str(source_path).encode('utf-8')).hexdigest()[:8]
        return Path(category) / f'{digest}_{source_path.name}'

    def _collect_dict(self, dict_name: str) -> None:
        normalized_name = dict_name.strip()
        if not normalized_name or normalized_name in self.dict_records:
            return

        export_root = self.staging_root / 'dicts' / _safe_segment(normalized_name)
        export_root.mkdir(parents=True, exist_ok=True)

        ola = self._get_ola()
        db_handle = self._get_ola_db_handle(ola)
        result = ola.ExportDict(db_handle, normalized_name, str(export_root))
        if result != 1:
            raise RuntimeError(f'导出字库失败: {normalized_name}')

        color = _get_dict_color_safe(normalized_name)
        record = _DictExportRecord(
            name=normalized_name,
            package_path=(Path('dicts') / _safe_segment(normalized_name)).as_posix(),
            color=color,
        )
        self.dict_records[normalized_name] = record
        self.dependency_records.append(
            ExportDependencyRecord(
                kind='dict',
                source_path=normalized_name,
                package_path=record.package_path,
            )
        )

    def _get_ola(self):
        if self._ola is not None:
            return self._ola
        from OLA.OLAPlugCOMLoader import OLAPlugServerCOM

        ola = OLAPlugServerCOM()
        if ola.CreateCOLAPlugInterFace() != 1:
            raise RuntimeError('初始化字库导出环境失败')
        self._ola = ola
        return ola

    def close(self) -> None:
        if self._ola is None:
            return
        if self._ola_db_handle:
            try:
                self._ola.CloseDatabase(self._ola_db_handle)
            except Exception:
                pass
            self._ola_db_handle = None
        try:
            self._ola.DestroyCOLAPlugInterFace()
        except Exception:
            pass
        self._ola = None

    def _get_ola_db_handle(self, ola) -> int:
        if self._ola_db_handle:
            return self._ola_db_handle
        from tasks.dict_ocr_task import _get_dict_db_path, _open_ola_dict_database

        db_path = _get_dict_db_path()
        db_handle = _open_ola_dict_database(ola, db_path, '[市场导出]', allow_create=False)
        if not db_handle or db_handle <= 0:
            raise RuntimeError(f'打开字库数据库失败: {db_path}')
        self._ola_db_handle = db_handle
        return db_handle


def materialize_installed_package_workflows(install_dir: str | Path) -> None:
    install_root = Path(install_dir).resolve()
    workflow_root = install_root / 'workflow'
    if not workflow_root.exists():
        return

    for workflow_path in sorted(workflow_root.rglob('*.json')):
        _materialize_single_workflow(workflow_path, install_root)



def import_packaged_dicts(install_dir: str | Path) -> None:
    install_root = Path(install_dir).resolve()
    index_path = install_root / 'dicts' / 'index.json'
    if not index_path.exists():
        return

    payload = json.loads(index_path.read_text(encoding='utf-8'))
    raw_dicts = payload.get('dicts') if isinstance(payload, dict) else []
    dict_items = [item for item in raw_dicts if isinstance(item, dict) and str(item.get('name') or '').strip()]
    if not dict_items:
        return

    from OLA.OLAPlugCOMLoader import OLAPlugServerCOM
    from tasks.dict_ocr_task import (
        _get_dict_db_path,
        _load_dict_list,
        _open_ola_dict_database,
        _save_dict_list,
    )

    ola = OLAPlugServerCOM()
    if ola.CreateCOLAPlugInterFace() != 1:
        raise RuntimeError('初始化字库安装环境失败')

    db_path = _get_dict_db_path()
    db_dir = os.path.dirname(db_path)
    os.makedirs(db_dir, exist_ok=True)

    db_handle = _open_ola_dict_database(ola, db_path, '[市场安装]', allow_create=True)
    if not db_handle or db_handle <= 0:
        raise RuntimeError(f'打开字库数据库失败: {db_path}')

    try:
        existing_dicts = _load_dict_list()
        colors = _load_dict_colors_for_install()
        changed = False
        for item in dict_items:
            dict_name = str(item.get('name') or '').strip()
            package_path = str(item.get('package_path') or '').strip().replace('\\', '/')
            if not dict_name or not package_path:
                continue
            source_dir = (install_root / Path(package_path)).resolve()
            try:
                source_dir.relative_to(install_root)
            except Exception as exc:
                raise RuntimeError(f'字库路径越界: {package_path}') from exc
            if not source_dir.exists() or not source_dir.is_dir():
                raise FileNotFoundError(f'字库目录不存在: {source_dir}')
            result = ola.InitDictFromDir(db_handle, dict_name, str(source_dir), 1)
            if result != 1:
                raise RuntimeError(f'安装字库失败: {dict_name}')
            if dict_name not in existing_dicts:
                existing_dicts.append(dict_name)
                changed = True
            color = str(item.get('color') or '').strip()
            if color:
                colors[dict_name] = color
                changed = True
        if changed:
            _save_dict_list(existing_dicts)
            _save_dict_colors_for_install(colors)
    finally:
        try:
            ola.CloseDatabase(db_handle)
        except Exception:
            pass
        try:
            ola.DestroyCOLAPlugInterFace()
        except Exception:
            pass



def _materialize_single_workflow(workflow_path: Path, install_root: Path) -> None:
    workflow_data = _load_workflow_json(workflow_path)
    cards = workflow_data.get('cards') if isinstance(workflow_data, dict) else None
    if not isinstance(cards, list):
        raise ValueError(f'工作流格式无效: {workflow_path}')

    for card in cards:
        if not isinstance(card, dict):
            continue
        task_type = str(card.get('task_type') or '').strip()
        params = card.get('parameters') if isinstance(card.get('parameters'), dict) else {}
        param_defs = _get_param_definitions(task_type)
        rewritten = dict(params)
        for param_name, param_def in param_defs.items():
            param_type = str(param_def.get('type') or '').strip().lower()
            if param_type == 'file' and param_name in rewritten:
                rewritten[param_name] = _materialize_file_param(
                    task_type=task_type,
                    param_name=param_name,
                    raw_value=rewritten.get(param_name),
                    workflow_path=workflow_path,
                    install_root=install_root,
                )
            elif param_type == 'multi_file' and param_name in rewritten:
                rewritten[param_name] = _materialize_multi_file_param(
                    raw_value=rewritten.get(param_name),
                    workflow_path=workflow_path,
                    install_root=install_root,
                )

        raw_image_paths = str(rewritten.get('image_paths') or '').strip()
        if raw_image_paths:
            parts = [item.strip() for item in _MULTI_PATH_PATTERN.split(raw_image_paths) if item.strip()]
            rewritten['image_paths'] = '\n'.join(
                str(_resolve_install_asset_path(item, workflow_path, install_root, prefer_parent=False)) for item in parts
            )
        card['parameters'] = rewritten

    workflow_path.write_text(json.dumps(workflow_data, ensure_ascii=False, indent=2), encoding='utf-8')


def _materialize_file_param(
    task_type: str,
    param_name: str,
    raw_value: Any,
    workflow_path: Path,
    install_root: Path,
) -> Any:
    raw_text = str(raw_value or '').strip()
    if not raw_text or raw_text.startswith('memory://'):
        return raw_value
    if task_type == '子工作流' and param_name == 'workflow_file':
        return str(_resolve_install_asset_path(raw_text, workflow_path, install_root, prefer_parent=True))
    return str(_resolve_install_asset_path(raw_text, workflow_path, install_root, prefer_parent=False))



def _materialize_multi_file_param(raw_value: Any, workflow_path: Path, install_root: Path) -> Any:
    if isinstance(raw_value, list):
        return [str(_resolve_install_asset_path(str(item), workflow_path, install_root, prefer_parent=False)) for item in raw_value if str(item or '').strip()]
    parts = [item.strip() for item in _MULTI_PATH_PATTERN.split(str(raw_value or '').strip()) if item.strip()]
    return '\n'.join(str(_resolve_install_asset_path(item, workflow_path, install_root, prefer_parent=False)) for item in parts)



def _resolve_install_asset_path(raw_path: str, workflow_path: Path, install_root: Path, prefer_parent: bool) -> Path:
    candidate_path = Path(str(raw_path or '').strip())
    if candidate_path.is_absolute():
        return candidate_path

    if prefer_parent:
        resolved = (workflow_path.parent / candidate_path).resolve()
        try:
            resolved.relative_to(install_root)
        except Exception as exc:
            raise RuntimeError(f'安装路径越界: {raw_path}') from exc
        return resolved

    resolved = (install_root / candidate_path).resolve()
    try:
        resolved.relative_to(install_root)
    except Exception as exc:
        raise RuntimeError(f'安装路径越界: {raw_path}') from exc
    return resolved



def _load_workflow_json(workflow_path: Path) -> Dict[str, Any]:
    try:
        return json.loads(workflow_path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise RuntimeError(f'读取工作流失败: {workflow_path}\n{exc}') from exc



def _get_param_definitions(task_type: str) -> Dict[str, Dict[str, Any]]:
    module = TASK_MODULES.get(task_type)
    if module is None or not hasattr(module, 'get_params_definition'):
        return {}
    try:
        param_defs = module.get_params_definition()
    except Exception:
        return {}
    return param_defs if isinstance(param_defs, dict) else {}



def _resolve_asset_source_path(raw_path: str, workflow_source_path: Path, task_type: str, param_name: str) -> Path:
    text = str(raw_path or '').strip()
    if not text:
        raise FileNotFoundError('空资源路径')
    if text.startswith('memory://'):
        raise RuntimeError(f'资源仍在内存中，无法打包: {text}')

    direct_path = Path(text)
    if direct_path.is_absolute() and direct_path.exists() and direct_path.is_file():
        return direct_path.resolve()

    image_like = _is_image_like(text, task_type=task_type, param_name=param_name)
    if image_like:
        resolved_image = correct_single_image_path(text)
        if resolved_image:
            return Path(resolved_image).resolve()

    for candidate in (workflow_source_path.parent / text, Path.cwd() / text):
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()

    raise FileNotFoundError(f'资源不存在: {text}')



def _determine_asset_category(source_path: Path, task_type: str, param_name: str) -> str:
    suffix = source_path.suffix.lower()
    lowered_param = param_name.lower()
    if _is_image_like(str(source_path), task_type=task_type, param_name=param_name):
        return 'images'
    if suffix in _DOC_EXTENSIONS or 'prompt' in lowered_param:
        return 'docs'
    if suffix in _MODEL_EXTENSIONS or 'model' in lowered_param:
        return 'models'
    return 'assets'



def _is_image_like(path_text: str, task_type: str, param_name: str) -> bool:
    lowered_path = str(path_text or '').lower()
    lowered_param = str(param_name or '').lower()
    if any(lowered_path.endswith(ext) for ext in _IMAGE_EXTENSIONS):
        return True
    if 'image' in lowered_param or '图片' in lowered_param:
        return True
    return task_type in {'图片点击', '模拟鼠标操作'}



def _calculate_file_hashes(staging_root: Path) -> Dict[str, str]:
    hashes: Dict[str, str] = {}
    for file_path in sorted(staging_root.rglob('*')):
        if not file_path.is_file():
            continue
        relative_path = file_path.relative_to(staging_root).as_posix()
        if relative_path == 'manifest.json':
            continue
        hashes[relative_path] = _sha256_of_file(file_path)
    return hashes


def _sha256_of_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            if chunk:
                digest.update(chunk)
    return digest.hexdigest()



def _safe_segment(value: str) -> str:
    text = str(value or '').strip()
    if not text:
        return 'unknown'
    cleaned = []
    for char in text:
        if char.isalnum() or char in {'-', '_', '.'}:
            cleaned.append(char)
        else:
            cleaned.append('_')
    return ''.join(cleaned).strip('._') or 'unknown'



def _get_dict_color_safe(dict_name: str) -> str:
    try:
        from tasks.dict_ocr_task import _get_dict_color
        return str(_get_dict_color(dict_name) or '').strip()
    except Exception:
        return ''



def _load_dict_colors_for_install() -> Dict[str, Any]:
    try:
        from tasks.dict_ocr_task import _get_dict_color_path
        path = Path(_get_dict_color_path())
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}



def _save_dict_colors_for_install(colors: Dict[str, Any]) -> None:
    from tasks.dict_ocr_task import _get_dict_color_path

    path = Path(_get_dict_color_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(colors or {}, ensure_ascii=False, indent=2), encoding='utf-8')
