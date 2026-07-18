"""
Rest related classes for seed trays
"""
from itertools import product

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers, viewsets
from rest_framework_nested import routers

from .models import SeedTrayModel, SeedTray, SeedTrayCell


class SeedTrayModelSerializer(serializers.ModelSerializer):
    """
    Serializer for a SeedTrayModel
    """
    class Meta:
        model = SeedTrayModel
        fields = ['pk', 'identifier', 'description', 'height', 'x_size', 'y_size', 'x_cells', 'y_cells', 'cell_size_ml']

    def validate(self, data):  # pylint: disable=arguments-renamed
        """Keep cell-grid dimensions stable after trays have been created."""
        errors = {}
        if self.instance is not None and self.instance.seedtray_set.exists():
            for field in ('x_cells', 'y_cells'):
                if field in data and data[field] != getattr(self.instance, field):
                    errors[field] = 'Cannot change cell dimensions after trays have been created.'
        if errors:
            raise serializers.ValidationError(errors)
        return data


class SeedTraySerializer(serializers.ModelSerializer):
    """
    Serializer for a Seed Tray
    """
    class Meta:
        model = SeedTray
        fields = ['pk', 'model', 'created', 'notes']

    def validate(self, data):  # pylint: disable=arguments-renamed
        """A created tray keeps the model that defined its generated cell grid."""
        if self.instance is not None and 'model' in data:
            if data['model'].pk != self.instance.model_id:
                raise serializers.ValidationError({
                    'model': 'Cannot change the model of an existing tray.'
                })
        return data

    def create(self, validated_data):
        """
        Override create to automatically generate SeedTrayCells
        """
        with transaction.atomic():
            seed_tray = super().create(validated_data)
            model = seed_tray.model

            # Create a cell for each position in the tray
            cells = [
                SeedTrayCell(tray=seed_tray, x_position=x, y_position=y)
                for x, y in product(range(model.x_cells), range(model.y_cells))
            ]
            SeedTrayCell.objects.bulk_create(cells)
        return seed_tray


class SeedTrayCellSerializer(serializers.ModelSerializer):
    """
    Serializer for a Seed Tray Cell
    """
    class Meta:
        model = SeedTrayCell
        fields = ['pk', 'tray', 'x_position', 'y_position']

    def validate(self, data):  # pylint: disable=arguments-renamed
        """Keep a cell on its original tray and within that tray's grid."""
        parent_tray = self.context.get('parent_tray')
        tray = parent_tray or data.get('tray') or getattr(self.instance, 'tray', None)

        if self.instance is not None and 'tray' in data:
            if data['tray'].pk != self.instance.tray_id:
                raise serializers.ValidationError({
                    'tray': 'Cannot move an existing cell to another tray.'
                })

        if tray is None:
            return data

        x_position = data.get('x_position', getattr(self.instance, 'x_position', None))
        y_position = data.get('y_position', getattr(self.instance, 'y_position', None))
        errors = {}
        if x_position is not None and x_position >= tray.model.x_cells:
            errors['x_position'] = f'Must be less than {tray.model.x_cells}.'
        if y_position is not None and y_position >= tray.model.y_cells:
            errors['y_position'] = f'Must be less than {tray.model.y_cells}.'
        if errors:
            raise serializers.ValidationError(errors)
        return data


class NestedSeedTrayCellSerializer(SeedTrayCellSerializer):
    """Cell serializer whose tray is supplied by the nested URL."""
    class Meta(SeedTrayCellSerializer.Meta):
        extra_kwargs = {'tray': {'read_only': True}}


class SeedTrayModelsViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SeedTrayModels
    """
    queryset = SeedTrayModel.objects.all()
    serializer_class = SeedTrayModelSerializer


class SeedTrayAllViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of all SeedTrays
    """
    queryset = SeedTray.objects.all()
    serializer_class = SeedTraySerializer


class SeedTrayCellViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of all SeedTrayCells
    """
    queryset = SeedTrayCell.objects.all()
    serializer_class = SeedTrayCellSerializer


class SeedTrayCellFilteredViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SeedTrayCells filtered by tray
    """
    queryset = SeedTrayCell.objects.all()
    serializer_class = NestedSeedTrayCellSerializer
    _parent_tray = None

    def get_parent_tray(self):
        """Resolve and cache the tray identified by the nested URL."""
        if self._parent_tray is None:
            self._parent_tray = get_object_or_404(
                SeedTray.objects.select_related('model'),
                pk=self.kwargs['seedtray_pk'],
            )
        return self._parent_tray

    def get_queryset(self):
        return self.queryset.filter(tray=self.get_parent_tray())

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['parent_tray'] = self.get_parent_tray()
        return context

    def perform_create(self, serializer):
        serializer.save(tray=self.get_parent_tray())


router = routers.SimpleRouter()
router.register(r'seedtraymodels', SeedTrayModelsViewSet)
router.register(r'seedtrays', SeedTrayAllViewSet)
router.register(r'seedtraycells', SeedTrayCellViewSet)

filtered_router = routers.NestedSimpleRouter(router, r'seedtrays', lookup='seedtray')
filtered_router.register(r'cells', SeedTrayCellFilteredViewSet, basename='seedtray-cells')
