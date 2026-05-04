from ..parameter_panel_support import *


class ParameterPanelFavoritesMarketCleanupMixin:
        def remove_market_package_favorites(self, package_id: str, version: str, emit_state: bool = False) -> int:

            """移除指定共享平台包版本在收藏中的所有工作流。"""

            safe_package_id = str(package_id or '').strip()

            safe_version = str(version or '').strip()

            if not safe_package_id or not safe_version:

                return 0



            self._load_favorites_data()



            removed_filepaths = []

            remaining_favorites = []

            for favorite in self._favorites:

                filepath = str(favorite.get('filepath') or '').strip()

                if filepath and package_scope_matches_value(filepath, safe_package_id, safe_version):

                    removed_filepaths.append(filepath)

                    continue

                remaining_favorites.append(favorite)



            if not removed_filepaths:

                return 0



            self._favorites = remaining_favorites

            self._save_favorites_config()

            if getattr(self, '_favorites_mode', False):

                self._refresh_favorites_list()

            if emit_state:

                for filepath in removed_filepaths:

                    self.workflow_check_changed.emit(filepath, False)

            return len(removed_filepaths)

        def _on_market_package_uninstalled(self, package_id: str, version: str) -> None:

            try:

                self.remove_market_package_favorites(package_id, version, emit_state=False)

            except Exception as e:

                logger.error(f"卸载脚本后清理收藏失败: {e}", exc_info=True)



            main_window = self._resolve_market_host_window()

            if main_window is not None and hasattr(main_window, '_cleanup_uninstalled_market_package'):

                try:

                    main_window._cleanup_uninstalled_market_package(package_id, version)

                except Exception as e:

                    logger.error(f"卸载脚本后关闭已打开标签失败: {e}", exc_info=True)
