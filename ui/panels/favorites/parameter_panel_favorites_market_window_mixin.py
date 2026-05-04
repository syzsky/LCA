from ..parameter_panel_support import *


class ParameterPanelFavoritesMarketWindowMixin:
        def _ensure_market_window(self) -> MarketWindow:

            if self._market_window is not None:

                return self._market_window



            resolved_main_window = self._resolve_market_host_window()

            window_parent = resolved_main_window or self

            self._market_window = MarketWindow(

                self._get_market_runtime_config(),

                config_provider=self._get_market_runtime_config,

                config_applier=self._apply_market_runtime_config,

                parent=window_parent,

            )

            self._market_window.entry_workflow_open_requested.connect(self._on_market_open_entry_workflow)

            self._market_window.entry_workflow_favorite_requested.connect(self._on_market_add_entry_to_favorites)

            self._market_window.package_uninstalled.connect(self._on_market_package_uninstalled)

            self._market_window.destroyed.connect(lambda *_: setattr(self, '_market_window', None))

            return self._market_window

        def _open_market_window(self):

            market_window = self._ensure_market_window()

            market_window.show_window()

        def _resolve_market_host_window(self):

            return getattr(self, 'main_window', None) or getattr(self, 'parent_window', None)

        def _get_market_runtime_config(self) -> Dict[str, Any]:

            config_data: Dict[str, Any] = {}

            main_window = self._resolve_market_host_window()

            if main_window is not None:

                for attr_name in ('config', 'current_config'):

                    attr_value = getattr(main_window, attr_name, None)

                    if isinstance(attr_value, dict):

                        config_data = dict(attr_value)

                        break

                if 'bound_windows' not in config_data and hasattr(main_window, 'get_bound_windows'):

                    try:

                        config_data['bound_windows'] = main_window.get_bound_windows()

                    except Exception:

                        pass

            return config_data

        def _apply_market_runtime_config(self, settings: Dict[str, Any]) -> None:

            normalized_settings = dict(settings or {})

            if not normalized_settings:

                return

            main_window = self._resolve_market_host_window()

            if main_window is not None and hasattr(main_window, "_apply_global_settings"):

                main_window._apply_global_settings(normalized_settings)

                return

            if main_window is not None and isinstance(getattr(main_window, "config", None), dict):

                main_window.config.update(normalized_settings)
