"""REST resources for the shared physical location catalog."""

from django.db.models import ProtectedError
from rest_framework import routers, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from inventory.rest_query import parse_boolean
from workspaces.scoping import (
    CurrentWorkspaceSerializerMixin,
    CurrentWorkspaceViewSetMixin,
)

from .models import Location, location_full_name
from .occupancy import blocking_occupancy, location_occupancy


#: The migration-era holding location for trays whose real place is unknown.
LEGACY_TRAY_CODE = 'SYSTEM-TRAY-UNKNOWN'


def _decimal_string(value):
    """Render an optional decimal the way every other endpoint renders one."""
    return None if value is None else str(value)


def is_system_managed(location):
    """Return whether a workflow rather than an operator owns this location."""
    if location is None:
        return False
    return location.is_system_managed or location.code == LEGACY_TRAY_CODE


class LocationSerializer(
    CurrentWorkspaceSerializerMixin,
    serializers.ModelSerializer,
):
    """Serialize one current-workspace physical location."""

    full_name = serializers.SerializerMethodField()
    depth = serializers.SerializerMethodField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._names = None

    class Meta:
        model = Location
        fields = [
            'pk',
            'name',
            'code',
            'location_type',
            'parent',
            'path',
            'full_name',
            'depth',
            'display_order',
            'capacity_basis',
            'capacity_value',
            'active',
            'notes',
            'created',
            'updated',
        ]
        read_only_fields = ['path', 'full_name', 'depth', 'created', 'updated']

    workspace_field_lookups = {'parent': 'workspace'}

    def get_full_name(self, location):
        """Name the place the way an operator says it out loud.

        A bare "Bay 2" is ambiguous across three greenhouses, so the ancestors
        travel with the name rather than being looked up separately.
        """
        if self._names is None:
            self._names = dict(
                Location.objects
                .filter(workspace_id=location.workspace_id)
                .values_list('pk', 'name'),
            )
        return location_full_name(location, self._names)

    def get_depth(self, location):
        """Report how deep this location sits, so a flat list can be indented."""
        return len(location.ancestor_ids)

    def validate(self, attrs):
        """Reserve packet locations, and keep an occupied place open."""
        if self.instance and self.instance.code == LEGACY_TRAY_CODE:
            raise ValidationError({
                'location': 'The legacy tray location is system-managed.',
            })
        current_type = getattr(self.instance, 'location_type', None)
        requested_type = attrs.get('location_type', current_type)
        if requested_type == Location.LocationType.SEED_PACKET:
            raise ValidationError({
                'location_type': 'Seed packet locations are system-managed.',
            })
        if self.instance and attrs.get('active') is False and self.instance.active:
            self._reject_occupied_retirement()
        return attrs

    def _reject_occupied_retirement(self):
        """Refuse to retire a place while anything is still standing in it.

        Retiring an occupied bench would leave trays and plants recorded at a
        location no picker offers any more, which is how stock goes missing on
        paper while sitting in plain sight.
        """
        still_there = blocking_occupancy(self.instance)
        if still_there is not None:
            raise ValidationError({
                'active': (
                    f'{self.instance.name} still holds {still_there.trays} trays and '
                    f'{still_there.plants} plants. Move them before retiring it.'
                ),
            })
        if self.instance.descendants().filter(active=True).exists():
            raise ValidationError({
                'active': 'Retire the locations inside this one first.',
            })


class LocationViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Configure active physical locations without deleting used history."""

    queryset = Location.objects.all()
    serializer_class = LocationSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        queryset = super().get_queryset()
        active = parse_boolean(self.request.query_params.get('active'), 'active')
        if active is not None:
            queryset = queryset.filter(active=active)
        location_type = self.request.query_params.get('location_type')
        if location_type:
            if location_type not in Location.LocationType.values:
                raise ValidationError(
                    {'location_type': 'Select a valid location type.'},
                )
            queryset = queryset.filter(location_type=location_type)
        return queryset

    @action(detail=True)
    def occupancy(self, request, pk=None):  # pylint: disable=unused-argument
        """Report what stands here, and what remains of any capacity.

        Both the location itself and its whole subtree are reported, because a
        greenhouse's own aisle and everything on its benches are different
        answers to "what is in here" and an operator needs each of them.
        """
        location = self.get_object()
        here = location_occupancy(location)
        below = location_occupancy(location, subtree=True)
        remaining = None
        if location.capacity_basis in Location.ENFORCED_BASES:
            remaining = location.capacity_value - below.of(location.capacity_basis)
        return Response({
            'location': location.pk,
            'capacity_basis': location.capacity_basis,
            # Decimals travel as strings, as they do everywhere else in this
            # API, so the frontend never parses one into a float artifact.
            'capacity_value': _decimal_string(location.capacity_value),
            'here': here._asdict(),
            'subtree': below._asdict(),
            'remaining': _decimal_string(remaining),
        })

    def perform_destroy(self, instance):
        if is_system_managed(instance):
            raise ValidationError({
                'location': 'This location is system-managed.',
            })
        try:
            instance.delete()
        except ProtectedError as exc:
            raise ValidationError(
                {'location': 'Used locations must be deactivated, not deleted.'},
            ) from exc


# The app serves one resource mounted at its own prefix, so the viewset takes
# the empty prefix and the router must not add an API-root view: that root sits
# at the same path as the list route and would shadow it.
router = routers.SimpleRouter()
router.register(r'', LocationViewSet)
