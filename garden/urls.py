"""
URL Router for Gardens
"""
from django.urls import path, include

from .rest import router

urlpatterns = [
    path('', include(router.urls))
]
