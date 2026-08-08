"""URL routing for the production-cost subledger."""

from django.urls import include, path

from .rest import router


urlpatterns = [
    path('', include(router.urls)),
]
