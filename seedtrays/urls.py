"""
URL Routing for seed trays
"""

from django.urls import path, include

from .rest import router, filtered_router
from .views import SeedTrayDetailView


urlpatterns = [
    path('seedtray/<int:pk>/', SeedTrayDetailView.as_view(), name='seedtray-detail'),
    path('', include(router.urls + filtered_router.urls)),
]
