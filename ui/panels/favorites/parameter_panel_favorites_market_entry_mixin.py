from ..parameter_panel_support import *


class ParameterPanelFavoritesMarketEntryMixin:
        def _on_market_open_entry_workflow(self, filepath: str):

            if not filepath:

                QMessageBox.warning(self, "脚本共享平台", "入口工作流不存在")

                return

            try:

                from market.refs import is_market_workflow_ref

                if is_market_workflow_ref(filepath):

                    self.workflow_open_requested.emit(filepath)

                    return

            except Exception:

                pass

            if not os.path.exists(filepath):

                QMessageBox.warning(self, "脚本共享平台", f"入口工作流不存在：\n{filepath}")

                return

            self.workflow_open_requested.emit(filepath)

        def _on_market_add_entry_to_favorites(self, filepath: str, display_name: str):

            status = self._add_favorite_entry(filepath, custom_name=display_name, checked=True, emit_state=True)

            if status == 'invalid':

                QMessageBox.warning(self, "脚本共享平台", f"入口工作流不存在：\n{filepath}")

                return



            self._favorites_active_view = 'favorites'

            self._update_favorites_title()

            if status == 'added':

                QMessageBox.information(self, "脚本共享平台", "已加入工作流收藏")

            elif status == 'updated':

                QMessageBox.information(self, "脚本共享平台", "已更新收藏并启用")

            else:

                QMessageBox.information(self, "脚本共享平台", "该工作流已在收藏中")
