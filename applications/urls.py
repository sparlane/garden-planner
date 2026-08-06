"""URLs for the input application documents."""

from django.urls import include, path

from .rest import router

urlpatterns = [
    path('', include(router.urls)),
]
