"""URL routing for customer sales."""

from django.urls import include, path
from .rest import router

urlpatterns = [path('', include(router.urls))]
