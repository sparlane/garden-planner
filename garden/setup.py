"""Server support for the guided garden setup.

The wizard itself is a frontend concern: it walks a gardener through the
workspace profile, one garden area, its physical scale, and a bed or two, all
through the resources those things already have. This module supplies the one
thing that has no home of its own — the ordinary household places a garden
needs before seed, tray, and input workflows will work.

It is a garden endpoint rather than a locations one because installing them is
part of setting up a garden, not part of maintaining a catalog. The locations
app owns what they are; this only asks for them.
"""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from locations.defaults import ensure_household_locations
from locations.rest import LocationSerializer
from workspaces.current import get_current_workspace


class HouseholdLocationsView(APIView):
    """Install the ordinary places a household garden needs."""

    def post(self, request):
        """Create any household place that is missing and report them all.

        Asking twice is not an error and creates nothing the second time, so
        the wizard can be left and resumed at this step without doubling up
        the gardener's shed.
        """
        locations = ensure_household_locations(get_current_workspace())
        return Response(
            LocationSerializer(locations, many=True, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )
