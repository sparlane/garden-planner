"""Routes for private image attachments."""

from django.urls import include, path

from .rest import router


urlpatterns = [path('', include(router.urls))]
