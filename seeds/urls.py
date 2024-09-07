"""
URL Routing for seeds
"""

from django.urls import path, include

from .rest import router
from . import views

urlpatterns = [
    path('packets/empty/', views.packets_empty, name='empty_packet'),
    path('', include(router.urls))
]
