"""
Rest for Plantings
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import routers, serializers, viewsets

from .models import GardenRowDirectSowPlanting, GardenSquareDirectSowPlanting, SeedTrayPlanting, GardenSquareTransplant, SeedTrayCellPlanting, SpecificPlant, SpecificPlantLocation


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
        the provided list. Must be called inside a transaction.atomic() block.

        Semantics: omitted field -> no change; empty list -> cleared.
        """
        # Remove existing child rows
        planting.cell_plantings.all().delete()

        if objs := [
            SeedTrayCellPlanting(
                seed_tray_planting=planting,
                cell=cp['cell'],
                quantity=cp['quantity'],
            )
            for cp in cell_data
        ]:
            SeedTrayCellPlanting.objects.bulk_create(objs)

    def create(self, validated_data):
        cell_data = validated_data.pop('cell_plantings', [])
        with transaction.atomic():
            planting = SeedTrayPlanting.objects.create(**validated_data)
            if cell_data:
                self._save_cell_plantings(planting, cell_data)
        return planting

    def update(self, instance, validated_data):
        cell_data = validated_data.pop('cell_plantings', None)
        with transaction.atomic():
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

            if cell_data is not None:
                self._save_cell_plantings(instance, cell_data)

        return instance


class SpecificPlantLocationSerializer(serializers.ModelSerializer):
    """
    Serializer for SpecificPlantLocation
    """
    class Meta:
        model = SpecificPlantLocation
        fields = ['pk', 'specific_plant', 'location_type', 'seed_tray_cell', 'garden_square', 'started', 'ended', 'notes']

    def validate(self, data):  # pylint: disable=arguments-renamed
        # Delegate to the model's clean() as the single source of truth for FK/location_type consistency.
        tmp = SpecificPlantLocation(
            location_type=data.get('location_type', getattr(self.instance, 'location_type', None)),
            seed_tray_cell=data.get('seed_tray_cell', getattr(self.instance, 'seed_tray_cell', None)),
            garden_square=data.get('garden_square', getattr(self.instance, 'garden_square', None)),
        )
        try:
            tmp.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        return data


class SpecificPlantSerializer(serializers.ModelSerializer):
    """
    Serializer for SpecificPlant — includes nested location history.
    On create, automatically records the initial seed tray cell location.
    """
    locations = SpecificPlantLocationSerializer(many=True, read_only=True)

    class Meta:
        model = SpecificPlant
        fields = ['pk', 'cell_planting', 'germinated', 'notes', 'locations']

    def create(self, validated_data):
        with transaction.atomic():
            plant = SpecificPlant.objects.create(**validated_data)
            SpecificPlantLocation.objects.create(
                specific_plant=plant,
                location_type=SpecificPlantLocation.SEED_TRAY_CELL,
                seed_tray_cell=plant.cell_planting.cell,
                started=plant.germinated,
            )
        return plant


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


class SpecificPlantViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SpecificPlant
    """
    queryset = SpecificPlant.objects.prefetch_related('locations', 'locations__seed_tray_cell', 'locations__garden_square')
    serializer_class = SpecificPlantSerializer


class SpecificPlantBySeedTrayViewSet(viewsets.ReadOnlyModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SpecificPlant filtered by SeedTray
    """
    queryset = SpecificPlant.objects.prefetch_related('locations', 'locations__seed_tray_cell', 'locations__garden_square')
    serializer_class = SpecificPlantSerializer

    def get_queryset(self):
        tray_pk = self.kwargs['seed_tray_pk']
        currently_here = self.queryset.filter(
            locations__seed_tray_cell__tray__pk=tray_pk,
            locations__ended__isnull=True,
        )
        originated_here = self.queryset.filter(
            cell_planting__seed_tray_planting__seed_tray__pk=tray_pk,
        )
        return (currently_here | originated_here).distinct()


class SpecificPlantLocationViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SpecificPlantLocation

    PUT and DELETE are disabled: locations are append-only; use PATCH to set `ended`.
    """
    queryset = SpecificPlantLocation.objects.select_related('seed_tray_cell', 'garden_square')
    serializer_class = SpecificPlantLocationSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def perform_update(self, serializer):
        if 'ended' not in serializer.validated_data:
            serializer.save(ended=timezone.now())
        else:
            serializer.save()


class SpecificPlantLocationByPlantViewSet(viewsets.ReadOnlyModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SpecificPlantLocation filtered by SpecificPlant
    """
    queryset = SpecificPlantLocation.objects.select_related('seed_tray_cell', 'garden_square')
    serializer_class = SpecificPlantLocationSerializer

    def get_queryset(self):
        return self.queryset.filter(specific_plant__pk=self.kwargs['specific_plant_pk'])


router = routers.SimpleRouter()
router.register(r'directsowgardenrow', GardenRowDirectSowPlantingViewSet)
router.register(r'directsowgardensquare', GardenSquareDirectSowPlantingViewSet)
router.register(r'seedtray', SeedTrayPlantingViewSet)
router.register(r'transplantedgardensquare', GardenSquareTransplantViewSet)
router.register(r'specificplants', SpecificPlantViewSet)
router.register(r'specificplantlocations', SpecificPlantLocationViewSet)

router.register(r'seedtray-data/(?P<seed_tray_pk>[^/.]+)/plantings', SeedTrayPlantingViewSeedTraySet, basename='seedtray-plantings')
router.register(r'seedtray-data/(?P<seed_tray_pk>[^/.]+)/specificplants', SpecificPlantBySeedTrayViewSet, basename='seedtray-specificplants')
router.register(r'specificplants/(?P<specific_plant_pk>[^/.]+)/locations', SpecificPlantLocationByPlantViewSet, basename='specificplant-locations')
