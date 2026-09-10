"""
URL Router for plants
"""
from django.urls import path, include

from .rest import router, ReferenceSetView, StarterCropsView

urlpatterns = [
    # Declared before the router so the resource prefixes cannot shadow them.
    path('starters/', StarterCropsView.as_view(), name='starter-crops'),
    path('reference-set/', ReferenceSetView.as_view(), name='reference-set'),
    path('', include(router.urls))
]
