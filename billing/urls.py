"""URL routing for taxable supply and correction documents."""

from django.urls import include, path

from .rest import router


urlpatterns = [path('', include(router.urls))]
