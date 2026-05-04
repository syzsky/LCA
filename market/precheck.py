# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Optional

from packaging import version

from .environment import capture_environment_snapshot
from .models import (
    EnvironmentSnapshot,
    MarketPackageManifest,
    PrecheckIssue,
    PrecheckReport,
    RuntimeRequirement,
    TargetWindowRequirement,
)


class MarketPackagePrecheckEngine:
    def run(
        self,
        manifest: MarketPackageManifest,
        environment: Optional[EnvironmentSnapshot] = None,
    ) -> PrecheckReport:
        snapshot = environment or capture_environment_snapshot()
        report = PrecheckReport(manifest=manifest, environment=snapshot)
        self._check_client_version(manifest, snapshot, report)
        self._check_runtime_requirement(manifest.runtime_requirement, snapshot, report)
        self._check_target_window(manifest.runtime_requirement.target_window, snapshot, report)
        return report

    def _check_client_version(
        self,
        manifest: MarketPackageManifest,
        environment: EnvironmentSnapshot,
        report: PrecheckReport,
    ) -> None:
        current_version = str(environment.app_version or "").strip()
        min_version = str(manifest.min_client_version or "").strip()
        max_version = str(manifest.max_client_version or "").strip()
        try:
            parsed_current = version.parse(current_version)
        except Exception:
            return

        if min_version:
            try:
                if parsed_current < version.parse(min_version):
                    report.issues.append(
                        PrecheckIssue(
                            code="client_version_too_low",
                            severity="block",
                            title="客户端版本过低",
                            message=f"当前版本 {current_version} 低于共享平台包要求的最低版本 {min_version}",
                            current_value=current_version,
                            expected_value=min_version,
                            action="update_client",
                        )
                    )
            except Exception:
                pass

        if max_version:
            try:
                if parsed_current > version.parse(max_version):
                    report.issues.append(
                        PrecheckIssue(
                            code="client_version_too_high",
                            severity="warn",
                            title="客户端版本偏高",
                            message=f"当前版本 {current_version} 高于共享平台包声明的最高兼容版本 {max_version}",
                            current_value=current_version,
                            expected_value=max_version,
                            action="review_compatibility",
                        )
                    )
            except Exception:
                pass

    def _check_runtime_requirement(
        self,
        requirement: RuntimeRequirement,
        environment: EnvironmentSnapshot,
        report: PrecheckReport,
    ) -> None:
        if requirement.execution_mode and requirement.execution_mode != environment.execution_mode:
            report.issues.append(
                PrecheckIssue(
                    code="execution_mode_mismatch",
                    severity="configure",
                    title="执行模式不匹配",
                    message="当前执行模式与共享平台包要求不一致",
                    current_value=environment.execution_mode,
                    expected_value=requirement.execution_mode,
                    action="open_execution_mode_settings",
                )
            )

        if requirement.screenshot_engine and requirement.screenshot_engine != environment.screenshot_engine:
            report.issues.append(
                PrecheckIssue(
                    code="screenshot_engine_mismatch",
                    severity="configure",
                    title="截图引擎不匹配",
                    message="当前截图引擎与共享平台包要求不一致",
                    current_value=environment.screenshot_engine,
                    expected_value=requirement.screenshot_engine,
                    action="open_screenshot_engine_settings",
                )
            )

        if requirement.plugin_required and not environment.plugin_enabled:
            report.issues.append(
                PrecheckIssue(
                    code="plugin_required",
                    severity="configure",
                    title="未启用插件模式",
                    message="该共享平台包要求启用插件模式后才能运行",
                    current_value=environment.plugin_enabled,
                    expected_value=True,
                    action="open_plugin_settings",
                )
            )

        if requirement.plugin_id and environment.preferred_plugin and requirement.plugin_id != environment.preferred_plugin:
            report.issues.append(
                PrecheckIssue(
                    code="plugin_id_mismatch",
                    severity="configure",
                    title="插件类型不匹配",
                    message="当前首选插件与共享平台包要求不一致",
                    current_value=environment.preferred_plugin,
                    expected_value=requirement.plugin_id,
                    action="open_plugin_settings",
                )
            )

    def _check_target_window(
        self,
        requirement: TargetWindowRequirement,
        environment: EnvironmentSnapshot,
        report: PrecheckReport,
    ) -> None:
        if not any([
            requirement.window_kind,
            requirement.title_keywords,
            requirement.class_names,
            requirement.client_width,
            requirement.client_height,
            requirement.min_client_width,
            requirement.max_client_width,
            requirement.min_client_height,
            requirement.max_client_height,
            requirement.dpi,
            requirement.scale_factor,
        ]):
            return

        if not environment.bound_window_title:
            report.issues.append(
                PrecheckIssue(
                    code="bound_window_missing",
                    severity="configure",
                    title="未绑定目标窗口",
                    message="该共享平台包要求先绑定目标窗口后再运行",
                    current_value="未绑定",
                    expected_value="已绑定目标窗口",
                    action="open_window_binding_settings",
                )
            )
            return

        if requirement.title_keywords:
            title_text = environment.bound_window_title.lower()
            if not any(keyword.lower() in title_text for keyword in requirement.title_keywords if keyword.strip()):
                report.issues.append(
                    PrecheckIssue(
                        code="window_title_mismatch",
                        severity="warn",
                        title="目标窗口标题不匹配",
                        message="当前已绑定窗口标题与共享平台包说明不一致",
                        current_value=environment.bound_window_title,
                        expected_value=requirement.title_keywords,
                        action="rebind_window",
                    )
                )

        if requirement.class_names and environment.bound_window_class_name:
            if environment.bound_window_class_name not in requirement.class_names:
                report.issues.append(
                    PrecheckIssue(
                        code="window_class_mismatch",
                        severity="warn",
                        title="目标窗口类名不匹配",
                        message="当前已绑定窗口类名与共享平台包说明不一致",
                        current_value=environment.bound_window_class_name,
                        expected_value=requirement.class_names,
                        action="rebind_window",
                    )
                )

        self._check_dimension(report, environment.bound_window_client_width, requirement.client_width, "client_width_exact", "客户区宽度不匹配", "adjust_window_resolution")
        self._check_dimension(report, environment.bound_window_client_height, requirement.client_height, "client_height_exact", "客户区高度不匹配", "adjust_window_resolution")
        self._check_dimension_range(report, environment.bound_window_client_width, requirement.min_client_width, requirement.max_client_width, "client_width_range", "客户区宽度超出范围", "adjust_window_resolution")
        self._check_dimension_range(report, environment.bound_window_client_height, requirement.min_client_height, requirement.max_client_height, "client_height_range", "客户区高度超出范围", "adjust_window_resolution")

        if requirement.dpi is not None and environment.bound_window_dpi is not None and requirement.dpi != environment.bound_window_dpi:
            report.issues.append(
                PrecheckIssue(
                    code="window_dpi_mismatch",
                    severity="warn",
                    title="窗口 DPI 不匹配",
                    message="当前目标窗口 DPI 与共享平台包建议值不一致",
                    current_value=environment.bound_window_dpi,
                    expected_value=requirement.dpi,
                    action="review_dpi_settings",
                )
            )

        if requirement.scale_factor is not None and environment.bound_window_scale_factor is not None:
            if abs(requirement.scale_factor - environment.bound_window_scale_factor) > 0.01:
                report.issues.append(
                    PrecheckIssue(
                        code="window_scale_factor_mismatch",
                        severity="warn",
                        title="窗口缩放不匹配",
                        message="当前目标窗口缩放与共享平台包建议值不一致",
                        current_value=environment.bound_window_scale_factor,
                        expected_value=requirement.scale_factor,
                        action="review_dpi_settings",
                    )
                )

    def _check_dimension(
        self,
        report: PrecheckReport,
        current_value: Optional[int],
        expected_value: Optional[int],
        code: str,
        title: str,
        action: str,
    ) -> None:
        if expected_value is None:
            return
        if current_value is None:
            report.issues.append(
                PrecheckIssue(
                    code=code,
                    severity="configure",
                    title=title,
                    message="当前未能读取到目标窗口客户区尺寸",
                    current_value=current_value,
                    expected_value=expected_value,
                    action=action,
                )
            )
            return
        if int(current_value) != int(expected_value):
            report.issues.append(
                PrecheckIssue(
                    code=code,
                    severity="configure",
                    title=title,
                    message="当前目标窗口客户区尺寸与共享平台包要求不一致",
                    current_value=current_value,
                    expected_value=expected_value,
                    action=action,
                )
            )

    def _check_dimension_range(
        self,
        report: PrecheckReport,
        current_value: Optional[int],
        min_value: Optional[int],
        max_value: Optional[int],
        code: str,
        title: str,
        action: str,
    ) -> None:
        if min_value is None and max_value is None:
            return
        if current_value is None:
            report.issues.append(
                PrecheckIssue(
                    code=code,
                    severity="configure",
                    title=title,
                    message="当前未能读取到目标窗口客户区尺寸",
                    current_value=current_value,
                    expected_value={"min": min_value, "max": max_value},
                    action=action,
                )
            )
            return

        out_of_range = False
        if min_value is not None and current_value < min_value:
            out_of_range = True
        if max_value is not None and current_value > max_value:
            out_of_range = True
        if not out_of_range:
            return

        report.issues.append(
            PrecheckIssue(
                code=code,
                severity="configure",
                title=title,
                message="当前目标窗口客户区尺寸超出共享平台包允许范围",
                current_value=current_value,
                expected_value={"min": min_value, "max": max_value},
                action=action,
            )
        )
