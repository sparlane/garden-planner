"""URL routing for the shared physical location catalog."""

from django.urls import include, path

from .rest import router


urlpatterns = [
    path('', include(router.urls)),
]
