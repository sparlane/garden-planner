"""URL routing for New Zealand tax controls."""

from django.urls import include, path

from .rest import GstStatusView, router


urlpatterns = [
    path('gst/status/', GstStatusView.as_view()),
    path('', include(router.urls)),
]
