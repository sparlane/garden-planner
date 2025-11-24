"""
URL Routing for supplies
"""

from django.urls import path, include

from .rest import router
from . import views

urlpatterns = [
    path('', include(router.urls))
]
