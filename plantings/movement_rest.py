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
