"""
Rest for Plantings
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import routers, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

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

    def validate(self, data):  # pylint: disable=arguments-renamed
        """Require complete entries because the parent replaces rather than patches them."""
        missing_fields = {
            field: 'This field is required.'
            for field in ('cell', 'quantity')
            if field not in data
        }
        if missing_fields:
            raise serializers.ValidationError(missing_fields)
        return data


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

    def _get_effective_cells(self, data):
        """Return submitted replacement cells or the retained instance cells."""
        if 'cell_plantings' in data:
            return [cell_planting['cell'] for cell_planting in data['cell_plantings']]
        if self.instance is not None:
            return [
                cell_planting.cell
                for cell_planting in self.instance.cell_plantings.select_related('cell__tray')
            ]
        return []

    def _get_effective_seed_tray(self, data, cells):
        """Return the effective tray and whether it was derived from a cell."""
        if 'seed_tray' in data:
            return data['seed_tray'], False

        seed_tray = getattr(self.instance, 'seed_tray', None)
        if seed_tray is None and cells:
            return cells[0].tray, True
        return seed_tray, False

    @staticmethod
    def _validate_cells_belong_to_tray(cells, seed_tray):
        """Reject any cell outside the effective seed tray."""
        for cell in cells:
            if cell.tray_id != seed_tray.pk:
                raise serializers.ValidationError({
                    'cell_plantings': (
                        f'Cell {cell.pk} belongs to tray {cell.tray_id}, '
                        f'not tray {seed_tray.pk}.'
                    )
                })

    def validate(self, data):  # pylint: disable=arguments-renamed
        """Keep retained or replacement cells on the effective seed tray."""
        cells = self._get_effective_cells(data)
        if not cells:
            return data

        seed_tray, seed_tray_derived = self._get_effective_seed_tray(data, cells)
        if seed_tray is None:
            raise serializers.ValidationError({
                'seed_tray': 'A planting with cell plantings must have a seed tray.'
            })

        self._validate_cells_belong_to_tray(cells, seed_tray)
        if seed_tray_derived:
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
        validate_specific_plant_location(
            location_type=data.get('location_type', _FIELD_MISSING),
            seed_tray_cell=data.get('seed_tray_cell', _FIELD_MISSING),
            garden_square=data.get('garden_square', _FIELD_MISSING),
            instance=self.instance,
        )
        return data


class SpecificPlantMoveSerializer(serializers.ModelSerializer):
    """
    Serializer for moving a SpecificPlant to a new active location.
    """
    class Meta:
        model = SpecificPlantLocation
        fields = ['location_type', 'seed_tray_cell', 'garden_square', 'started', 'notes']
        extra_kwargs = {
            'started': {'required': False},
        }

    def validate(self, data):  # pylint: disable=arguments-renamed
        validate_specific_plant_location(
            location_type=data.get('location_type'),
            seed_tray_cell=data.get('seed_tray_cell'),
            garden_square=data.get('garden_square'),
        )
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


_FIELD_MISSING = object()


def validate_specific_plant_location(*, location_type=None, seed_tray_cell=None, garden_square=None, instance=None):
    """
    Validate location fields, optionally defaulting omitted fields from an instance.
    """
    if instance is not None:
        if location_type is _FIELD_MISSING:
            location_type = instance.location_type
        if seed_tray_cell is _FIELD_MISSING:
            seed_tray_cell = instance.seed_tray_cell
        if garden_square is _FIELD_MISSING:
            garden_square = instance.garden_square

    tmp = SpecificPlantLocation(
        location_type=None if location_type is _FIELD_MISSING else location_type,
        seed_tray_cell=None if seed_tray_cell is _FIELD_MISSING else seed_tray_cell,
        garden_square=None if garden_square is _FIELD_MISSING else garden_square,
    )
    try:
        tmp.clean()
    except DjangoValidationError as exc:
        raise serializers.ValidationError(exc.message_dict) from exc


def get_single_active_location_for_update(plant):
    """
    Lock and return the current active location for a plant.
    """
    active_locations = list(
        SpecificPlantLocation.objects
        .select_for_update()
        .filter(specific_plant=plant, ended__isnull=True)
    )
    if len(active_locations) > 1:
        raise serializers.ValidationError({
            'specific_plant': 'Plant has multiple active locations.'
        })
    if active_locations:
        return active_locations[0]
    return None


def is_active_location_integrity_error(exc):
    """
    Return whether an integrity error came from the active-location constraint.
    """
    cause = getattr(exc, '__cause__', None)
    diag = getattr(cause, 'diag', None)
    if getattr(diag, 'constraint_name', None) == 'unique_active_location_per_plant':
        return True

    message = ' '.join(str(arg) for arg in exc.args)
    names_constraint = 'unique_active_location_per_plant' in message
    names_sqlite_column = 'plantings_specificplantlocation.specific_plant_id' in message
    return names_constraint or names_sqlite_column


def move_specific_plant(plant, move_data):
    """
    Move a plant by ending its active location and creating the new one atomically.
    """
    started = move_data.get('started') or timezone.now()
    move_payload = {**move_data, 'started': started}
    with transaction.atomic():
        plant = get_object_or_404(SpecificPlant.objects.select_for_update(), pk=plant.pk)
        active_location = get_single_active_location_for_update(plant)

        if active_location:
            active_location.ended = started
            active_location.save(update_fields=['ended'])

        try:
            return SpecificPlantLocation.objects.create(
                specific_plant=plant,
                **move_payload,
            )
        except IntegrityError as exc:
            if not is_active_location_integrity_error(exc):
                raise
            raise serializers.ValidationError({
                'specific_plant': 'Move must leave exactly one active location.'
            }) from exc


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

    @action(detail=True, methods=['post'])
    def move(self, request, pk=None):  # pylint: disable=unused-argument
        """
        Move a plant by ending its active location and creating the new one atomically.
        """
        serializer = SpecificPlantMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        plant = self.get_object()
        location = move_specific_plant(
            plant,
            dict(serializer.validated_data),
        )
        return Response(SpecificPlantLocationSerializer(location).data, status=status.HTTP_201_CREATED)


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
