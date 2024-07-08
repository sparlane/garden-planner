"""
URL Router for plantings
"""
from django.urls import path, include

from .rest import router
from . import views

urlpatterns = [
    path('seedtray/current/', views.seedtray_current, name='current_seedtray'),
    path('', include(router.urls))
]
