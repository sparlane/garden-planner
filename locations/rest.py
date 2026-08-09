"""REST resources for the shared physical location catalog."""

from django.db.models import ProtectedError
from rest_framework import routers, serializers, viewsets
from rest_framework.exceptions import ValidationError

from inventory.rest_query import parse_boolean
from workspaces.scoping import (
    CurrentWorkspaceSerializerMixin,
    CurrentWorkspaceViewSetMixin,
)

from .models import Location, location_full_name


#: The migration-era holding location for trays whose real place is unknown.
LEGACY_TRAY_CODE = 'SYSTEM-TRAY-UNKNOWN'


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
        """Reserve packet-container locations for the seed workflow."""
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
        return attrs


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
