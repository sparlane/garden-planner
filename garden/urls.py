"""
URL Router for Gardens
"""
from django.urls import path, include

from .rest import router
from .setup import HouseholdLocationsView

urlpatterns = [
    # Declared before the router so the resource prefixes cannot shadow it.
    path('setup/household-locations/', HouseholdLocationsView.as_view(), name='household-locations'),
    path('', include(router.urls))
]
