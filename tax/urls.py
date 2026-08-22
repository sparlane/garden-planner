"""URL routing for New Zealand tax controls."""

from django.urls import include, path

from .rest import GstBasisTransitionView, GstStatusView, router


urlpatterns = [
    path('gst/status/', GstStatusView.as_view()),
    path('gst/basis-transitions/', GstBasisTransitionView.as_view()),
    path('', include(router.urls)),
]
