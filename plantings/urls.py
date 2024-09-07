"""
URL Router for plantings
"""
from django.urls import path, include

from .rest import router
from . import views

urlpatterns = [
    path('seedtray/current/', views.seedtray_current, name='current_seedtray'),
    path('seedtray/complete/', views.seedtray_complete, name='complete_seedtray'),
    path('garden/squares/current/', views.gardensquare_current, name='current_gardensquare'),
    path('', include(router.urls))
]
