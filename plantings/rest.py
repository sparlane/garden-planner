"""
Rest for Plantings
"""
from django.db import transaction
from rest_framework import routers, serializers, viewsets

from .models import GardenRowDirectSowPlanting, GardenSquareDirectSowPlanting, SeedTrayPlanting, GardenSquareTransplant, SeedTrayCellPlanting


class GardenRowDirectSowPlantingSerializer(serializers.ModelSerializer):
    """
    Serializer for GardenRowDirectSowPlanting
    """
    class Meta:
        model = GardenRowDirectSowPlanting
        fields = ['pk', 'planted', 'seeds_used', 'quantity', 'location', 'removed', 'notes']


class GardenSquareDirectSowSerializer(serializers.ModelSerializer):
    """
    Serializer for GardenSquareDirectSowPlanting
    """
    class Meta:
        model = GardenSquareDirectSowPlanting
        fields = ['pk', 'planted', 'seeds_used', 'quantity', 'location', 'removed', 'notes']


class SeedTrayCellPlantingNestedSerializer(serializers.ModelSerializer):
    """Nested serializer for creating/updating cell plantings inside a SeedTrayPlanting"""
    class Meta:
        model = SeedTrayCellPlanting
        fields = ['pk', 'cell', 'quantity']


class SeedTrayPlantingSerializer(serializers.ModelSerializer):
    """
    Serializer for SeedTrayPlanting
    """
    cell_plantings = SeedTrayCellPlantingNestedSerializer(many=True, required=False)

    class Meta:
        model = SeedTrayPlanting
        fields = [
            'pk', 'planted', 'seeds_used', 'quantity',
            'seed_tray', 'location', 'removed', 'notes', 'cell_plantings',
        ]

    def validate(self, data):  # pylint: disable=arguments-renamed
        """Validate that all cell plantings belong to the same seed tray.

        For partial updates the `seed_tray` may not be present in `data`,
        so it is derived from the existing instance or from cells.
        """
        cell_plantings = data.get('cell_plantings', [])
        if not cell_plantings:
            return data

        # 1. Determine effective seed_tray: payload → instance → first cell
        seed_tray = data.get('seed_tray') or getattr(self.instance, 'seed_tray', None)

        if not seed_tray:
            first_cell_entry = cell_plantings[0]
            first_cell = first_cell_entry.get('cell')
            if not first_cell:
                raise serializers.ValidationError({
                    'cell_plantings': 'Cannot derive seed_tray from empty cell entry'
                })
            seed_tray = first_cell.tray

        # 2. Validate all cells belong to the determined tray
        for cp in cell_plantings:
            cell = cp.get('cell')
            if not cell:
                raise serializers.ValidationError({'cell_plantings': 'Invalid cell entry'})
            if cell.tray_id != seed_tray.id:
                raise serializers.ValidationError({
                    'cell_plantings': (
                        f'Cell {cell.pk} belongs to tray {cell.tray.pk}, '
                        f'not tray {seed_tray.pk}'
                    )
                })

        # 3. Set derived seed_tray in data if not already present
        if not data.get('seed_tray'):
            data['seed_tray'] = seed_tray

        return data

    def _save_cell_plantings(self, planting, cell_data):
        """Replace all cell plantings for a planting using bulk_create.

        This simplifies the logic: delete existing entries and bulk-create
        the provided list. Wrapped in transaction.atomic() to ensure the
        replacement is atomic from the DB perspective.

        Semantics: omitted field -> no change; empty list -> cleared.
        """
        with transaction.atomic():
            # Remove existing child rows
            planting.cell_plantings.all().delete()

            # Bulk-create new ones (DRF passes `cell` as SeedTrayCell instances)
            objs = [
                SeedTrayCellPlanting(
                    seed_tray_planting=planting,
                    cell=cp['cell'],
                    quantity=cp['quantity'],
                )
                for cp in cell_data
            ]
            if objs:
                SeedTrayCellPlanting.objects.bulk_create(objs)

    def create(self, validated_data):
        cell_data = validated_data.pop('cell_plantings', [])
        planting = SeedTrayPlanting.objects.create(**validated_data)
        if cell_data:
            self._save_cell_plantings(planting, cell_data)
        return planting

    def update(self, instance, validated_data):
        cell_data = validated_data.pop('cell_plantings', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if cell_data is not None:
            self._save_cell_plantings(instance, cell_data)

        return instance


class GardenSquareTransplantSerializer(serializers.ModelSerializer):
    """
    Serializer for GardenSquareTransplant
    """
    class Meta:
        model = GardenSquareTransplant
        fields = ['pk', 'transplanted', 'original_planting', 'quantity', 'location', 'removed', 'notes']


class GardenRowDirectSowPlantingViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of GardenRowDirectSowPlanting
    """
    queryset = GardenRowDirectSowPlanting.objects.all()
    serializer_class = GardenRowDirectSowPlantingSerializer


class GardenSquareDirectSowPlantingViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of GardenSquareDirectSowPlanting
    """
    queryset = GardenSquareDirectSowPlanting.objects.all()
    serializer_class = GardenSquareDirectSowSerializer


class SeedTrayPlantingViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SeedTrayPlanting
    """
    queryset = SeedTrayPlanting.objects.all()
    serializer_class = SeedTrayPlantingSerializer


class SeedTrayPlantingViewSeedTraySet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SeedTrayPlanting filtered by SeedTray
    """
    queryset = SeedTrayPlanting.objects.all()
    serializer_class = SeedTrayPlantingSerializer

    def get_queryset(self):
        return self.queryset.filter(seed_tray__pk=self.kwargs['seed_tray_pk'])


class GardenSquareTransplantViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of GardenSquareTransplant
    """
    queryset = GardenSquareTransplant.objects.all()
    serializer_class = GardenSquareTransplantSerializer


router = routers.SimpleRouter()
router.register(r'directsowgardenrow', GardenRowDirectSowPlantingViewSet)
router.register(r'directsowgardensquare', GardenSquareDirectSowPlantingViewSet)
router.register(r'seedtray', SeedTrayPlantingViewSet)
router.register(r'transplantedgardensquare', GardenSquareTransplantViewSet)

router.register(r'seedtray-data/(?P<seed_tray_pk>[^/.]+)/plantings', SeedTrayPlantingViewSeedTraySet, basename='seedtray-plantings')
