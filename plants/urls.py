"""
URL Router for plants
"""
from django.urls import path, include

from .rest import router, StarterCropsView

urlpatterns = [
    # Declared before the router so the resource prefixes cannot shadow it.
    path('starters/', StarterCropsView.as_view(), name='starter-crops'),
    path('', include(router.urls))
]
