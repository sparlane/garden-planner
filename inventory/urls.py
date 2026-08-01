"""URL routing for inventory catalog resources."""

from django.urls import include, path

from .rest import UnitRegistryView, router


urlpatterns = [
    path('units/', UnitRegistryView.as_view(), name='inventory-units'),
    path('', include(router.urls)),
]
