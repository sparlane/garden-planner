"""URL routes for labels and scanning."""

from django.urls import include, path

from .rest import ResolveLabelView, router


urlpatterns = [
    path('resolve/', ResolveLabelView.as_view(), name='resolve-label'),
    path('', include(router.urls)),
]
