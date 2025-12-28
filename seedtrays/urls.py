"""
URL Routing for seed trays
"""

from django.urls import path, include

from .rest import router


urlpatterns = [
    path('', include(router.urls))
]
