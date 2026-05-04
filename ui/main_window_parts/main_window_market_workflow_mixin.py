import os
from typing import List

from market.server_config import get_market_auth_server_base, get_market_verify_ssl


class MainWindowMarketWorkflowMixin:
    def _resolve_market_workflow(self, workflow_ref: str, access_mode: str, switch_to_tab: bool = False) -> str:
        existing_task = self.task_manager.find_task_by_filepath(workflow_ref)
        if existing_task and getattr(existing_task, 'filepath', None) and os.path.exists(existing_task.filepath):
            existing_task.read_only_mode = False
            existing_task.read_only_reason = ''
            existing_task.source_ref = workflow_ref
            existing_task.market_runtime_ref = ''
            if hasattr(self, 'workflow_tab_widget') and self.workflow_tab_widget:
                tab_index = self.workflow_tab_widget.task_to_tab.get(existing_task.task_id)
                if tab_index is not None:
                    try:
                        self.workflow_tab_widget.tabBar().setTabVisible(tab_index, True)
                    except Exception:
                        pass
                    workflow_view = self.workflow_tab_widget.task_views.get(existing_task.task_id)
                    if workflow_view is not None:
                        workflow_view.setInteractive(True)
                        workflow_view.setEnabled(True)
                        workflow_view.setToolTip('')
                    if switch_to_tab:
                        self.workflow_tab_widget.setCurrentIndex(tab_index)
            return existing_task.filepath

        manager = self._get_market_package_manager()
        resolved_path = manager.resolve_market_workflow_ref(
            workflow_ref,
            auth_server_base=get_market_auth_server_base(),
            verify_ssl=get_market_verify_ssl(),
            access_mode=access_mode,
        )
        task_id = self._find_or_import_workflow(str(resolved_path), switch_to_tab=switch_to_tab)
        if task_id is None:
            raise RuntimeError(f'导入共享平台工作流失败: {workflow_ref}')

        task = self.task_manager.get_task(task_id)
        if task is not None:
            task.read_only_mode = False
            task.read_only_reason = ''
            task.source_ref = workflow_ref
            task.market_runtime_ref = ''

        workflow_view = self.workflow_tab_widget.task_views.get(task_id)
        if workflow_view is not None:
            workflow_view.setInteractive(True)
            workflow_view.setEnabled(True)
            workflow_view.setToolTip('')

        tab_index = self.workflow_tab_widget.task_to_tab.get(task_id)
        if tab_index is not None:
            try:
                self.workflow_tab_widget.tabBar().setTabVisible(tab_index, True)
            except Exception:
                pass

        return str(getattr(task, 'filepath', '') or resolved_path)

    def _resolve_market_workflow_for_canvas(self, workflow_ref: str, switch_to_tab: bool = False) -> str:
        return self._resolve_market_workflow(workflow_ref, access_mode='edit', switch_to_tab=switch_to_tab)

    def _resolve_market_workflow_for_batch(self, workflow_ref: str, switch_to_tab: bool = False) -> str:
        return self._resolve_market_workflow(workflow_ref, access_mode='run', switch_to_tab=switch_to_tab)

    def _cleanup_uninstalled_market_package(self, package_id: str, version: str) -> None:

        safe_package_id = str(package_id or '').strip()

        safe_version = str(version or '').strip()

        if not safe_package_id or not safe_version:

            return

        from market.package_scope import package_scope_matches_value

        task_ids_to_close: List[int] = []

        for task in self.task_manager.get_all_tasks():

            candidates = [

                str(getattr(task, 'source_ref', '') or '').strip(),

                str(getattr(task, 'filepath', '') or '').strip(),

                str(getattr(task, 'market_session_dir', '') or '').strip(),

            ]

            if any(

                candidate and package_scope_matches_value(candidate, safe_package_id, safe_version)

                for candidate in candidates

            ):

                task_ids_to_close.append(task.task_id)

        if not task_ids_to_close:

            return

        for task_id in task_ids_to_close:

            if self.task_manager.get_task(task_id) is None:

                continue

            tab_index = self.workflow_tab_widget.task_to_tab.get(task_id)

            if tab_index is not None:

                self.workflow_tab_widget.close_tab_silent(tab_index)

                continue

            task = self.task_manager.get_task(task_id)

            if task is None:

                continue

            try:

                task.stop()

            except Exception:

                pass

            try:

                self.task_manager.remove_task(task_id)

            except Exception:

                pass

    def _is_market_workflow_ref(self, filepath: str) -> bool:

        try:

            from market.refs import is_market_workflow_ref

            return bool(is_market_workflow_ref(filepath))

        except Exception:

            return False

    def _get_market_package_manager(self):

        manager = getattr(self, '_market_package_manager', None)

        if manager is None:

            from market.package_manager import MarketPackageManager

            manager = MarketPackageManager()

            self._market_package_manager = manager

        return manager
