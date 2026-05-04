from .parameter_panel_favorites_market_cleanup_mixin import (
    ParameterPanelFavoritesMarketCleanupMixin,
)
from .parameter_panel_favorites_market_entry_mixin import (
    ParameterPanelFavoritesMarketEntryMixin,
)
from .parameter_panel_favorites_market_window_mixin import (
    ParameterPanelFavoritesMarketWindowMixin,
)


class ParameterPanelFavoritesMarketMixin(
    ParameterPanelFavoritesMarketWindowMixin,
    ParameterPanelFavoritesMarketEntryMixin,
    ParameterPanelFavoritesMarketCleanupMixin,
):
    pass
