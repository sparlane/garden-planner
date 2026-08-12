"""URL routes for plant-health resources."""

from django.urls import include, path

from .rest import router

urlpatterns = [path('', include(router.urls))]
