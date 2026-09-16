"""Shared move payload for individual and bulk plant endpoints."""

from rest_framework import serializers

from workspaces.scoping import CurrentWorkspaceSerializerMixin

from .models import SpecificPlantLocation
from .movement import validate_specific_plant_location


class SpecificPlantMoveSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for moving a SpecificPlant to a new active location.
    """
    class Meta:
        model = SpecificPlantLocation
        fields = ['location_type', 'seed_tray_cell', 'garden_square', 'location', 'container_unit', 'started', 'notes', 'override_reason']
        extra_kwargs = {
            'started': {'required': False},
            'override_reason': {'required': False},
        }

    workspace_field_lookups = {
        'seed_tray_cell': 'tray__workspace',
        'garden_square': 'workspace',
        'location': 'workspace',
        'container_unit': 'workspace',
    }

    def validate(self, data):  # pylint: disable=arguments-renamed
        validate_specific_plant_location(
            location_type=data.get('location_type'),
            places={
                field_name: data.get(field_name)
                for field_name in SpecificPlantLocation.LOCATION_FIELDS.values()
            },
        )
        return data


class RepotPairingSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One seedling and the numbered pot it is being stood in."""

    plant = serializers.IntegerField(min_value=1)
    container_unit = serializers.IntegerField(min_value=1)


class BulkRepotSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A whole repotting run, paired here rather than one destination for all.

    The bulk move on the register sends every selected plant to one place,
    which is what a bench or a garden square is. A pot is not: the run pairs
    each seedling with the number written on the pot it went into, so the
    table of pairings is the request.
    """

    placements = RepotPairingSerializer(many=True, allow_empty=False)
    started = serializers.DateTimeField(required=False, allow_null=True, default=None)
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    override_reason = serializers.CharField(required=False, allow_blank=True, default='')
