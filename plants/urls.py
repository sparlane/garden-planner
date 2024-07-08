"""
URL Router for plants
"""
from django.urls import path, include

from .rest import router

urlpatterns = [
    path('', include(router.urls))
]
