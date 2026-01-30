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
    path('garden/squares/complete/', views.gardensquare_complete, name='complete_gardensquare'),
    path('garden/squares/transplant/complete/', views.gardensquare_transplant_complete, name='complete_gardensquare_transplant'),
    path('', include(router.urls)),
]
