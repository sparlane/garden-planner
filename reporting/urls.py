"""URL routes for Nursery operational and financial reports."""

from django.urls import path

from .rest import (
    InventoryBalanceExportView,
    InventoryBalanceView,
    MovementExportView,
    MovementView,
    SerializedTrayExportView,
    SerializedTrayView,
    StocktakeVarianceExportView,
    StocktakeVarianceView,
)


urlpatterns = [
    path('inventory-balances/', InventoryBalanceView.as_view()),
    path('inventory-balances/export/', InventoryBalanceExportView.as_view()),
    path('serialized-trays/', SerializedTrayView.as_view()),
    path('serialized-trays/export/', SerializedTrayExportView.as_view()),
    path('inventory-movements/', MovementView.as_view()),
    path('inventory-movements/export/', MovementExportView.as_view()),
    path('stocktake-variances/', StocktakeVarianceView.as_view()),
    path('stocktake-variances/export/', StocktakeVarianceExportView.as_view()),
]
