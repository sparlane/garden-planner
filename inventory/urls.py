"""URL routing for inventory catalog resources."""

from django.urls import include, path

from .balance_rest import BalanceView
from .rest import UnitRegistryView, router


urlpatterns = [
    path('units/', UnitRegistryView.as_view(), name='inventory-units'),
    path('balances/', BalanceView.as_view(), name='inventory-balances'),
    path('', include(router.urls)),
]
