from .parameter_panel_favorites_action_mixin import ParameterPanelFavoritesActionMixin
from .parameter_panel_favorites_item_mixin import ParameterPanelFavoritesItemMixin
from .parameter_panel_favorites_market_mixin import ParameterPanelFavoritesMarketMixin
from .parameter_panel_favorites_storage_mixin import ParameterPanelFavoritesStorageMixin
from .parameter_panel_favorites_view_mixin import ParameterPanelFavoritesViewMixin


class ParameterPanelFavoritesMixin(
    ParameterPanelFavoritesViewMixin,
    ParameterPanelFavoritesMarketMixin,
    ParameterPanelFavoritesItemMixin,
    ParameterPanelFavoritesActionMixin,
    ParameterPanelFavoritesStorageMixin,
):
    pass
