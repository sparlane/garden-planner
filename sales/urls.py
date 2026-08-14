"""URL routing for customer sales."""

from django.urls import include, path
from rest_framework import routers


router = routers.SimpleRouter()

urlpatterns = [path('', include(router.urls))]
