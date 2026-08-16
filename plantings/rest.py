"""
Rest for Plantings
"""

# The app's main REST module, alongside the batch, harvest, lifecycle,
# generation, and register modules it has already been split into.
# pylint: disable=too-many-lines

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import routers, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from locations.occupancy import check_capacity, plant_contribution
from seeds.models import SeedPacket
from workspaces.scoping import CurrentWorkspaceSerializerMixin, CurrentWorkspaceViewSetMixin

from .batch_rest import BatchedSowingSerializerMixin, InlineBatchSerializer, register_batch_routes
from .batches import lock_batch_with_plants
from .generation_rest import TrayGenerationFilterMixin, TrayGenerationSowingSerializerMixin
from .harvest_rest import register_harvest_routes
from .lifecycle import record_germination_event, record_transplant_event
from .lifecycle_rest import PlantLifecycleEventSerializer, PlantLifecycleSerializerMixin, PlantOutcomeViewSetMixin, register_lifecycle_routes
from .models import GardenRowDirectSowPlanting, GardenSquareDirectSowPlanting, NurseryObservation, SeedTrayPlanting, GardenSquareTransplant, SeedTrayCellPlanting, SpecificPlant, SpecificPlantLocation
from .register_rest import register_register_routes
from .growth_rest import NurseryObservationSerializer, register_growth_routes
from .planning_rest import register_planning_routes
from .growth import current_growth
from .cohort_rest import register_cohort_routes
from .sowing import correct_sowing_consumption, post_sowing_consumption


def _model_errors(error):
    """Translate Django validation into field-friendly REST errors."""
    if hasattr(error, 'message_dict'):
        return error.message_dict
    return error.messages


class PostedSowingSerializerMixin:
    """Post creates and lock stock-affecting generic updates."""

    stock_fields = ('planted', 'seeds_used', 'quantity')

    def _validate_stock_fields(self, attrs):
        if not self.instance or not self.instance.stock_postings.exists():
            return
        errors = {
            field: 'Use the explicit sowing correction action.'
            for field in self.stock_fields
            if field in attrs and attrs[field] != getattr(self.instance, field)
        }
        if errors:
            raise serializers.ValidationError(errors)

    def validate(self, attrs):
        """Reject silent edits once the seed movement exists."""
        self._validate_stock_fields(attrs)
        return super().validate(attrs)

    def create(self, validated_data):
        """Create the sowing and its consumption as one transaction."""
        try:
            with transaction.atomic():
                planting = super().create(validated_data)
                post_sowing_consumption(
                    planting,
                    self.context['request'].user,
                )
                return planting
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_model_errors(exc)) from exc


class GardenRowDirectSowPlantingSerializer(BatchedSowingSerializerMixin, PostedSowingSerializerMixin, CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for GardenRowDirectSowPlanting
    """
    new_batch = InlineBatchSerializer(required=False, write_only=True)

    class Meta:
        model = GardenRowDirectSowPlanting
        fields = ['pk', 'planted', 'seeds_used', 'batch', 'new_batch', 'quantity', 'location', 'removed', 'notes']
        extra_kwargs = {'batch': {'required': False}}

    workspace_field_lookups = {
        'seeds_used': 'workspace',
        'batch': 'workspace',
        'location': 'workspace',
    }


class GardenSquareDirectSowSerializer(BatchedSowingSerializerMixin, PostedSowingSerializerMixin, CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for GardenSquareDirectSowPlanting
    """
    new_batch = InlineBatchSerializer(required=False, write_only=True)

    class Meta:
        model = GardenSquareDirectSowPlanting
        fields = ['pk', 'planted', 'seeds_used', 'batch', 'new_batch', 'quantity', 'location', 'removed', 'notes']
        extra_kwargs = {'batch': {'required': False}}

    workspace_field_lookups = {
        'seeds_used': 'workspace',
        'batch': 'workspace',
        'location': 'workspace',
    }


class SeedTrayCellPlantingNestedSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """Nested serializer for creating/updating cell plantings inside a SeedTrayPlanting"""
    class Meta:
        model = SeedTrayCellPlanting
        fields = ['pk', 'cell', 'quantity']

    workspace_field_lookups = {'cell': 'tray__workspace'}

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


class SeedTrayPlantingSerializer(TrayGenerationSowingSerializerMixin, BatchedSowingSerializerMixin, PostedSowingSerializerMixin, CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):  # pylint: disable=too-many-ancestors
    """
    Serializer for SeedTrayPlanting
    """
    cell_plantings = SeedTrayCellPlantingNestedSerializer(many=True, required=False)
    new_batch = InlineBatchSerializer(required=False, write_only=True)

    class Meta:
        model = SeedTrayPlanting
        fields = [
            'pk', 'planted', 'seeds_used', 'batch', 'new_batch', 'quantity',
            'seed_tray', 'generation', 'location', 'removed', 'notes',
            'cell_plantings',
        ]
        extra_kwargs = {
            'batch': {'required': False},
            'generation': {'required': False},
        }

    workspace_field_lookups = {
        'seeds_used': 'workspace',
        'batch': 'workspace',
        'seed_tray': 'workspace',
        'generation': 'tray__workspace',
    }

    def _get_effective_cell_plantings(self, data):
        """Return submitted replacements or the retained cell plantings."""
        if 'cell_plantings' in data:
            return data['cell_plantings']
        if self.instance is not None:
            return list(
                self.instance.cell_plantings.select_related('cell__tray')
            )
        return []

    @staticmethod
    def _get_cell(cell_planting):
        """Return the cell from submitted data or an existing model instance."""
        if isinstance(cell_planting, dict):
            return cell_planting['cell']
        return cell_planting.cell

    @staticmethod
    def _get_cell_quantity(cell_planting):
        """Return quantity from submitted data or an existing model instance."""
        if isinstance(cell_planting, dict):
            return cell_planting['quantity']
        return cell_planting.quantity

    def _get_effective_seed_tray(self, data, cells):
        """Return the effective tray and whether it was derived from a cell."""
        if 'seed_tray' in data:
            return data['seed_tray'], False

        seed_tray = getattr(self.instance, 'seed_tray', None)
        if seed_tray is None and cells:
            return cells[0].tray, True
        return seed_tray, False

    def _validate_cell_allocations(self, data, cell_plantings):
        """Validate uniqueness, parent capacity, and tray membership."""
        cells = [
            self._get_cell(cell_planting)
            for cell_planting in cell_plantings
        ]
        cell_ids = [cell.pk for cell in cells]
        if len(cell_ids) != len(set(cell_ids)):
            raise serializers.ValidationError({
                'cell_plantings': ['Each cell may only be allocated once.']
            })

        quantity = data.get('quantity', getattr(self.instance, 'quantity', None))
        allocated_quantity = sum(
            self._get_cell_quantity(cell_planting)
            for cell_planting in cell_plantings
        )
        if quantity is not None and allocated_quantity > quantity:
            raise serializers.ValidationError({
                'cell_plantings': [
                    'Cell allocation total cannot exceed planting quantity.'
                ]
            })

        if not cells:
            return

        seed_tray, seed_tray_derived = self._get_effective_seed_tray(data, cells)
        if seed_tray is None:
            raise serializers.ValidationError({
                'seed_tray': ['A planting with cell plantings must have a seed tray.']
            })

        self._validate_cells_belong_to_tray(cells, seed_tray)
        if seed_tray_derived:
            data['seed_tray'] = seed_tray

    @staticmethod
    def _validate_cells_belong_to_tray(cells, seed_tray):
        """Reject any cell outside the effective seed tray."""
        for cell in cells:
            if cell.tray_id != seed_tray.pk:
                raise serializers.ValidationError({
                    'cell_plantings': [
                        f'Cell {cell.pk} belongs to tray {cell.tray_id}, '
                        f'not tray {seed_tray.pk}.'
                    ]
                })

    def validate(self, data):  # pylint: disable=arguments-renamed
        """Keep retained or replacement cells on the effective seed tray."""
        self._validate_batch(data)
        self._validate_stock_fields(data)
        cell_plantings = self._get_effective_cell_plantings(data)
        self._validate_cell_allocations(data, cell_plantings)
        # After the cell rules, because a sowing that names only its cells has
        # its tray derived there and the generation follows from the tray.
        self._validate_generation(data)

        return data

    def _save_cell_plantings(
        self,
        planting,
        cell_data,
        existing_cell_plantings=None,
    ):
        """Apply replacement data while preserving referenced allocation rows.

        Semantics: omitted field -> no change; empty list -> cleared.
        Must be called inside a transaction.atomic() block.
        """
        existing_cell_plantings = existing_cell_plantings or list(
            planting.cell_plantings.all()
        )
        existing_by_cell = {
            cell_planting.cell_id: cell_planting
            for cell_planting in existing_cell_plantings
        }
        new_cell_plantings = []
        updated_cell_plantings = []
        for replacement in cell_data:
            cell = replacement['cell']
            if existing := existing_by_cell.pop(cell.pk, None):
                if existing.quantity != replacement['quantity']:
                    existing.quantity = replacement['quantity']
                    updated_cell_plantings.append(existing)
            else:
                new_cell_plantings.append(SeedTrayCellPlanting(
                    seed_tray_planting=planting,
                    cell=cell,
                    quantity=replacement['quantity'],
                ))

        try:
            for obsolete in existing_by_cell.values():
                obsolete.delete()
        except ProtectedError as exc:
            raise serializers.ValidationError({
                'cell_plantings': [
                    'Cannot remove a cell allocation after germination is recorded.'
                ]
            }) from exc

        if updated_cell_plantings:
            SeedTrayCellPlanting.objects.bulk_update(
                updated_cell_plantings,
                ['quantity'],
            )
        if new_cell_plantings:
            SeedTrayCellPlanting.objects.bulk_create(new_cell_plantings)

    def create(self, validated_data):
        cell_data = validated_data.pop('cell_plantings', [])
        with transaction.atomic():
            self._resolve_batch(validated_data)
            planting = SeedTrayPlanting.objects.create(**validated_data)
            if cell_data:
                self._save_cell_plantings(planting, cell_data)
            try:
                post_sowing_consumption(
                    planting,
                    self.context['request'].user,
                )
            except DjangoValidationError as exc:
                raise serializers.ValidationError(_model_errors(exc)) from exc
        return planting

    def update(self, instance, validated_data):
        cell_data = validated_data.pop('cell_plantings', None)
        with transaction.atomic():
            instance = SeedTrayPlanting.objects.select_for_update().get(pk=instance.pk)
            self.instance = instance
            existing_cell_plantings = list(
                instance.cell_plantings.select_for_update().select_related('cell__tray')
            )
            effective_cell_plantings = (
                existing_cell_plantings if cell_data is None else cell_data
            )
            self._validate_cell_allocations(
                validated_data,
                effective_cell_plantings,
            )
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

            if cell_data is not None:
                self._save_cell_plantings(
                    instance,
                    cell_data,
                    existing_cell_plantings,
                )

        return instance


class SpecificPlantLocationSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for SpecificPlantLocation
    """
    class Meta:
        model = SpecificPlantLocation
        fields = ['pk', 'specific_plant', 'location_type', 'seed_tray_cell', 'garden_square', 'location', 'started', 'ended', 'notes', 'override_reason']
        read_only_fields = ['override_reason']

    workspace_field_lookups = {
        'specific_plant': 'workspace',
        'seed_tray_cell': 'tray__workspace',
        'garden_square': 'workspace',
        'location': 'workspace',
    }

    def _get_effective_history_fields(self, data):
        """Resolve the interval and plant represented by create or partial update data."""
        specific_plant = data.get(
            'specific_plant',
            getattr(self.instance, 'specific_plant', None),
        )
        started = data.get(
            'started',
            getattr(self.instance, 'started', None),
        )
        if started is None:
            started = timezone.now()
            data['started'] = started
        ended = data.get('ended', getattr(self.instance, 'ended', None))
        return specific_plant, started, ended

    def _validate_history(self, data, *, append_only):
        """Validate this interval against the other locations for its plant."""
        specific_plant, started, ended = self._get_effective_history_fields(data)
        validate_location_history(
            specific_plant=specific_plant,
            started=started,
            ended=ended,
            exclude_pk=getattr(self.instance, 'pk', None),
            append_only=append_only,
        )

    def validate(self, data):  # pylint: disable=arguments-renamed
        if self.instance is not None and 'specific_plant' in data:
            if data['specific_plant'].pk != self.instance.specific_plant_id:
                raise serializers.ValidationError({
                    'specific_plant': 'Cannot reassign an existing location.'
                })

        specific_plant, started, ended = self._get_effective_history_fields(data)
        validate_specific_plant_location(
            location_type=data.get('location_type', _FIELD_MISSING),
            places=places_from(data),
            interval=(started, ended),
            instance=self.instance,
        )
        validate_location_history(
            specific_plant=specific_plant,
            started=started,
            ended=ended,
            exclude_pk=getattr(self.instance, 'pk', None),
            append_only=self.instance is None,
        )
        return data

    def create(self, validated_data):
        with transaction.atomic():
            SpecificPlant.objects.select_for_update().get(
                pk=validated_data['specific_plant'].pk,
            )
            self._validate_history(validated_data, append_only=True)
            return super().create(validated_data)

    def update(self, instance, validated_data):
        with transaction.atomic():
            SpecificPlant.objects.select_for_update().get(pk=instance.specific_plant_id)
            instance = SpecificPlantLocation.objects.select_for_update().get(pk=instance.pk)
            self.instance = instance
            self._validate_history(validated_data, append_only=False)
            return super().update(instance, validated_data)


class SpecificPlantMoveSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for moving a SpecificPlant to a new active location.
    """
    class Meta:
        model = SpecificPlantLocation
        fields = ['location_type', 'seed_tray_cell', 'garden_square', 'location', 'started', 'notes', 'override_reason']
        extra_kwargs = {
            'started': {'required': False},
            'override_reason': {'required': False},
        }

    workspace_field_lookups = {
        'seed_tray_cell': 'tray__workspace',
        'garden_square': 'workspace',
        'location': 'workspace',
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


class SpecificPlantSerializer(PlantLifecycleSerializerMixin, CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for SpecificPlant — includes nested location history and the
    lifecycle state derived from its recorded facts.
    On create, automatically records the initial seed tray cell location
    and the germination that started the plant's lifecycle history.
    """
    locations = SpecificPlantLocationSerializer(many=True, read_only=True)
    batch = serializers.IntegerField(source='batch_id', read_only=True)
    lifecycle_state = serializers.SerializerMethodField()
    sellable = serializers.SerializerMethodField()
    final_outcome = serializers.SerializerMethodField()
    final_outcome_at = serializers.SerializerMethodField()
    state_since = serializers.SerializerMethodField()
    first_ready_at = serializers.SerializerMethodField()
    label_code = serializers.SerializerMethodField()

    class Meta:
        model = SpecificPlant
        fields = [
            'pk',
            'cell_planting',
            'batch',
            'germinated',
            'notes',
            'label_code',
            'locations',
        ] + PlantLifecycleSerializerMixin.LIFECYCLE_FIELDS

    workspace_field_lookups = {
        'cell_planting': 'seed_tray_planting__workspace',
    }

    def get_label_code(self, plant):
        """Return the immutable physical label code currently in use."""
        from labels.services import ensure_identity  # pylint: disable=import-outside-toplevel

        identity = ensure_identity(plant)
        return identity.codes.get(status='active').code

    def validate(self, data):  # pylint: disable=arguments-renamed
        """Keep a plant attached to the cell allocation it germinated from."""
        if self.instance is not None and 'cell_planting' in data:
            if data['cell_planting'].pk != self.instance.cell_planting_id:
                raise serializers.ValidationError({
                    'cell_planting': 'Cannot reassign an existing plant.'
                })
        return data

    def create(self, validated_data):
        with transaction.atomic():
            try:
                cell_planting = SeedTrayCellPlanting.objects.select_for_update().get(
                    pk=validated_data['cell_planting'].pk,
                )
            except SeedTrayCellPlanting.DoesNotExist as exc:
                raise serializers.ValidationError({
                    'cell_planting': [
                        'This cell allocation no longer exists.'
                    ]
                }) from exc
            validated_data['cell_planting'] = cell_planting
            batch = lock_batch_with_plants(cell_planting.seed_tray_planting.batch)
            plant = SpecificPlant.objects.create(**validated_data)
            SpecificPlantLocation.objects.create(
                specific_plant=plant,
                location_type=SpecificPlantLocation.SEED_TRAY_CELL,
                seed_tray_cell=plant.cell_planting.cell,
                started=plant.germinated,
            )
            user = self.context['request'].user
            record_germination_event(plant, user)
            # A new seedling re-divides whatever its cell was carrying, so the
            # subledger is brought back in step here rather than drifting until
            # somebody asks for a report. Imported inside the call because
            # costing reads this module's app.
            from costing.services import reallocate_batch  # pylint: disable=import-outside-toplevel

            reallocate_batch(batch, user, 'germination')
        return plant


class SpecificPlantDetailSerializer(SpecificPlantSerializer):
    """Add the chronological lifecycle history one plant screen needs."""

    lifecycle_events = PlantLifecycleEventSerializer(many=True, read_only=True)
    availability_intervals = serializers.SerializerMethodField()
    growth = serializers.SerializerMethodField()
    nursery_observations = serializers.SerializerMethodField()

    class Meta(SpecificPlantSerializer.Meta):
        fields = SpecificPlantSerializer.Meta.fields + [
            'lifecycle_events', 'availability_intervals', 'growth', 'nursery_observations',
        ]

    def get_growth(self, plant):
        """Expose current Nursery facts without making them mutable plant fields."""
        growth = current_growth(plant)
        return {
            'stage': growth['stage'].pk if growth['stage'] else None,
            'stage_name': growth['stage'].name if growth['stage'] else None,
            'grade': growth['grade'].pk if growth['grade'] else None,
            'grade_name': growth['grade'].name if growth['grade'] else None,
            'container': growth['container_item'].pk if growth['container_item'] else None,
            'container_name': growth['container_name'] or None,
            'container_size': growth['container_size_label'] or None,
            'container_count': growth['container_count'],
            'height_cm': growth['height_cm'],
            'spread_cm': growth['spread_cm'],
            'root_condition': growth['root_condition'],
            'expected_ready': growth['expected_ready'],
        }

    def get_nursery_observations(self, plant):
        """Return effective and corrected facts in their immutable chronology."""
        observations = (
            NurseryObservation.objects.filter(targets__plant=plant)
            .select_related('stage', 'grade', 'container_item', 'created_by')
            .prefetch_related('targets')
        )
        return NurseryObservationSerializer(observations, many=True).data


class GardenSquareTransplantSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for GardenSquareTransplant
    """
    batch = serializers.IntegerField(
        source='original_planting.batch_id',
        read_only=True,
    )

    class Meta:
        model = GardenSquareTransplant
        fields = ['pk', 'transplanted', 'original_planting', 'batch', 'quantity', 'location', 'removed', 'notes']

    workspace_field_lookups = {
        'original_planting': 'workspace',
        'location': 'workspace',
    }


_FIELD_MISSING = object()


def places_from(data):
    """Read every kind of place a plant can be out of request data.

    Driven by the model's own field table so that adding a fourth kind of place
    reaches the API without a second list needing to be remembered.
    """
    return {
        field_name: data.get(field_name, _FIELD_MISSING)
        for field_name in SpecificPlantLocation.LOCATION_FIELDS.values()
    }


def validate_specific_plant_location(
    *,
    location_type=None,
    places=None,
    interval=None,
    instance=None,
):
    """
    Validate location fields, optionally defaulting omitted fields from an instance.
    """
    supplied = dict(places or {})
    if instance is not None:
        if location_type is _FIELD_MISSING:
            location_type = instance.location_type
        for field_name in SpecificPlantLocation.LOCATION_FIELDS.values():
            if supplied.get(field_name, _FIELD_MISSING) is _FIELD_MISSING:
                supplied[field_name] = getattr(instance, field_name)
        if interval is None:
            interval = (instance.started, instance.ended)

    location_data = {
        'location_type': None if location_type is _FIELD_MISSING else location_type,
    }
    for field_name in SpecificPlantLocation.LOCATION_FIELDS.values():
        value = supplied.get(field_name, _FIELD_MISSING)
        location_data[field_name] = None if value is _FIELD_MISSING else value
    if interval is not None:
        location_data['started'], location_data['ended'] = interval

    tmp = SpecificPlantLocation(
        **location_data,
    )
    try:
        tmp.clean()
    except DjangoValidationError as exc:
        raise serializers.ValidationError(exc.message_dict) from exc


def validate_location_history(
    *,
    specific_plant,
    started,
    ended,
    exclude_pk=None,
    append_only=False,
):
    """Reject location intervals that overlap or insert before existing history."""
    locations = SpecificPlantLocation.objects.filter(specific_plant=specific_plant)
    if exclude_pk is not None:
        locations = locations.exclude(pk=exclude_pk)

    if append_only:
        latest_location = locations.order_by('-started', '-pk').first()
        if latest_location is None:
            return
        if latest_location.ended is None or started < latest_location.ended:
            raise serializers.ValidationError({
                'started': 'New locations must start at or after existing history ends.'
            })
        return

    overlapping = locations.filter(Q(ended__isnull=True) | Q(ended__gt=started))
    if ended is not None:
        overlapping = overlapping.filter(started__lt=ended)
    if overlapping.exists():
        raise serializers.ValidationError({
            'started': 'Location interval overlaps another location.'
        })


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


def _check_destination_capacity(destination, override_reason, plant):
    """Refuse a bench that is full, or that cannot measure a single plant.

    Locks the destination and every capacitated ancestor before counting, so
    two plants racing for the last space cannot both read it as free.
    """
    if not destination.active:
        raise serializers.ValidationError({'location': 'The location is inactive.'})
    try:
        check_capacity(destination, plant_contribution(plant), override_reason)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(
            {'location': _model_errors(exc).get('destination', exc.messages)},
        ) from exc


def move_specific_plant(plant, move_data, user=None):
    """
    Move a plant by ending its active location and creating the new one atomically.

    A move into a garden square is also the moment the plant is planted out, so
    the matching lifecycle fact is appended in the same transaction. Only a
    garden square counts: moving a plant onto a nursery bench is still nursery
    work, and calling it planting out would close a production batch early.
    """
    started = move_data.get('started') or timezone.now()
    move_payload = {**move_data, 'started': started}
    planted_out = move_payload.get('location_type') == SpecificPlantLocation.GARDEN_SQUARE
    destination = move_payload.get('location')
    with transaction.atomic():
        plant = get_object_or_404(
            SpecificPlant.objects.select_for_update(),
            pk=plant.pk,
            workspace=plant.workspace,
        )
        if destination is not None:
            _check_destination_capacity(
                destination, move_payload.get('override_reason', ''), plant,
            )
        if planted_out:
            try:
                record_transplant_event(plant, user, started)
            except DjangoValidationError as exc:
                raise serializers.ValidationError(_model_errors(exc)) from exc
        active_location = get_single_active_location_for_update(plant)

        if active_location:
            if started < active_location.started:
                raise serializers.ValidationError({
                    'started': 'Move cannot start before the active location.'
                })
            active_location.ended = started
            active_location.save(update_fields=['ended'])
        else:
            validate_location_history(
                specific_plant=plant,
                started=started,
                ended=None,
                append_only=True,
            )

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


class SowingCorrectionSerializer(
    CurrentWorkspaceSerializerMixin,
    serializers.Serializer,
):
    """Validate replacement packet/quantity intent and an audit reason."""

    seeds_used = serializers.PrimaryKeyRelatedField(
        queryset=SeedPacket.objects.all(),
        required=False,
    )
    quantity = serializers.IntegerField(min_value=1, required=False)
    reason = serializers.CharField(allow_blank=False, trim_whitespace=True)

    workspace_field_lookups = {'seeds_used': 'workspace'}

    def validate(self, attrs):
        """Require at least one stock-affecting replacement value."""
        if 'seeds_used' not in attrs and 'quantity' not in attrs:
            raise serializers.ValidationError({
                'detail': 'Supply a replacement packet or quantity.',
            })
        return attrs

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class SowingCorrectionViewSetMixin:  # pylint: disable=too-few-public-methods
    """Expose one common correction action for concrete sowing resources."""

    @action(detail=True, methods=['post'], url_path='correct-sowing')
    def correct_sowing(self, request, pk=None):  # pylint: disable=unused-argument
        """Reverse and replace the current linked consumption movement."""
        serializer = SowingCorrectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        try:
            result = correct_sowing_consumption(
                self.get_object(),
                request.user,
                seeds_used=values.get('seeds_used'),
                quantity=values.get('quantity'),
                reason=values['reason'],
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_model_errors(exc)) from exc
        return Response({
            'planting': self.get_serializer(result['planting']).data,
            'original_movement': result['original_movement'],
            'reversal_movement': result['reversal_movement'],
            'replacement_movement': result['replacement_movement'],
        })


class PostedSowingDestroyMixin:  # pylint: disable=too-few-public-methods
    """Preserve a planting once immutable stock history refers to it."""

    def perform_destroy(self, instance):
        """Reject generic deletion after a consumption was posted."""
        if instance.stock_postings.exists():
            raise serializers.ValidationError({
                'detail': 'Posted sowings cannot be deleted.',
            })
        super().perform_destroy(instance)


class GardenRowDirectSowPlantingViewSet(
    SowingCorrectionViewSetMixin,
    PostedSowingDestroyMixin,
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """
    ViewSet of GardenRowDirectSowPlanting
    """
    queryset = GardenRowDirectSowPlanting.objects.all()
    serializer_class = GardenRowDirectSowPlantingSerializer


class GardenSquareDirectSowPlantingViewSet(
    SowingCorrectionViewSetMixin,
    PostedSowingDestroyMixin,
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """
    ViewSet of GardenSquareDirectSowPlanting
    """
    queryset = GardenSquareDirectSowPlanting.objects.all()
    serializer_class = GardenSquareDirectSowSerializer


class ProtectedSeedTrayPlantingDestroyMixin:  # pylint: disable=too-few-public-methods
    """Return a domain error when dependent records protect a planting."""

    def perform_destroy(self, instance):
        """Delete a planting unless protected dependents still refer to it."""
        if instance.stock_postings.exists():
            raise serializers.ValidationError({
                'detail': ['Posted sowings cannot be deleted.'],
            })
        try:
            instance.delete()
        except ProtectedError as exc:
            raise serializers.ValidationError({
                'detail': [
                    'Cannot delete a seed tray planting while dependent records exist.'
                ]
            }) from exc


class SeedTrayPlantingViewSet(
    SowingCorrectionViewSetMixin,
    ProtectedSeedTrayPlantingDestroyMixin,
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SeedTrayPlanting
    """
    queryset = SeedTrayPlanting.objects.all()
    serializer_class = SeedTrayPlantingSerializer


class SeedTrayPlantingViewSeedTraySet(
    TrayGenerationFilterMixin,
    SowingCorrectionViewSetMixin,
    ProtectedSeedTrayPlantingDestroyMixin,
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SeedTrayPlanting filtered by SeedTray
    """
    queryset = SeedTrayPlanting.objects.all()
    serializer_class = SeedTrayPlantingSerializer

    def get_queryset(self):
        tray = self.get_parent_seed_tray()
        return self.filter_by_generation(
            super().get_queryset().filter(seed_tray=tray),
            tray,
        )

    def perform_create(self, serializer):
        serializer.save(
            workspace=self.get_current_workspace(),
            seed_tray=self.get_parent_seed_tray(),
        )


class GardenSquareTransplantViewSet(CurrentWorkspaceViewSetMixin, viewsets.ReadOnlyModelViewSet):  # pylint: disable=too-many-ancestors
    """
    Read-only access to legacy aggregate transplant records.

    New transplant workflows move individual SpecificPlant records instead.
    """
    queryset = GardenSquareTransplant.objects.all()
    serializer_class = GardenSquareTransplantSerializer


class SpecificPlantViewSet(PlantOutcomeViewSetMixin, CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SpecificPlant
    """
    queryset = SpecificPlant.objects.prefetch_related('locations', 'locations__seed_tray_cell', 'locations__garden_square', 'lifecycle_events')
    serializer_class = SpecificPlantSerializer

    def get_serializer_class(self):
        """Use the richer serializer for a single plant."""
        if self.action == 'retrieve':
            return SpecificPlantDetailSerializer
        return SpecificPlantSerializer

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
            user=request.user,
        )
        return Response(SpecificPlantLocationSerializer(location).data, status=status.HTTP_201_CREATED)


class SpecificPlantBySeedTrayViewSet(TrayGenerationFilterMixin, CurrentWorkspaceViewSetMixin, viewsets.ReadOnlyModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SpecificPlant filtered by SeedTray
    """
    queryset = SpecificPlant.objects.prefetch_related('locations', 'locations__seed_tray_cell', 'locations__garden_square', 'lifecycle_events')
    serializer_class = SpecificPlantSerializer
    generation_lookup = 'cell_planting__seed_tray_planting__generation'

    def get_queryset(self):
        tray = self.get_parent_seed_tray()
        queryset = super().get_queryset()
        currently_here = queryset.filter(
            locations__seed_tray_cell__tray__pk=tray.pk,
            locations__ended__isnull=True,
        )
        originated_here = self.filter_by_generation(
            queryset.filter(cell_planting__seed_tray_planting__seed_tray__pk=tray.pk),
            tray,
        )
        return (currently_here | originated_here).distinct()


class SpecificPlantLocationViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SpecificPlantLocation

    PUT and DELETE are disabled: PATCH edits fields and the end action closes a location.
    """
    queryset = SpecificPlantLocation.objects.select_related('seed_tray_cell', 'garden_square')
    serializer_class = SpecificPlantLocationSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    workspace_lookup = 'specific_plant__workspace'
    bind_workspace_on_create = False

    @action(detail=True, methods=['post'])
    def end(self, request, pk=None):  # pylint: disable=unused-argument
        """End an active location once without replacing an existing end time."""
        with transaction.atomic():
            location = get_object_or_404(
                SpecificPlantLocation.objects.select_for_update(),
                pk=pk,
                specific_plant__workspace=self.get_current_workspace(),
            )
            self.check_object_permissions(request, location)
            if location.ended is None:
                ended = timezone.now()
                validate_specific_plant_location(
                    location_type=location.location_type,
                    interval=(location.started, ended),
                    instance=location,
                )
                location.ended = ended
                location.save(update_fields=['ended'])

        return Response(self.get_serializer(location).data)


class SpecificPlantLocationByPlantViewSet(CurrentWorkspaceViewSetMixin, viewsets.ReadOnlyModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SpecificPlantLocation filtered by SpecificPlant
    """
    queryset = SpecificPlantLocation.objects.select_related('seed_tray_cell', 'garden_square')
    serializer_class = SpecificPlantLocationSerializer
    workspace_lookup = 'specific_plant__workspace'
    bind_workspace_on_create = False

    def get_queryset(self):
        plant = get_object_or_404(
            SpecificPlant,
            pk=self.kwargs['specific_plant_pk'],
            workspace=self.get_current_workspace(),
        )
        return super().get_queryset().filter(specific_plant=plant)


router = routers.SimpleRouter()
register_batch_routes(router)
router.register(r'directsowgardenrow', GardenRowDirectSowPlantingViewSet)
router.register(r'directsowgardensquare', GardenSquareDirectSowPlantingViewSet)
router.register(r'seedtray', SeedTrayPlantingViewSet)
router.register(r'transplantedgardensquare', GardenSquareTransplantViewSet)
router.register(r'specificplants', SpecificPlantViewSet)
router.register(r'specificplantlocations', SpecificPlantLocationViewSet)

router.register(r'seedtray-data/(?P<seed_tray_pk>[^/.]+)/plantings', SeedTrayPlantingViewSeedTraySet, basename='seedtray-plantings')
router.register(r'seedtray-data/(?P<seed_tray_pk>[^/.]+)/specificplants', SpecificPlantBySeedTrayViewSet, basename='seedtray-specificplants')
router.register(r'specificplants/(?P<specific_plant_pk>[^/.]+)/locations', SpecificPlantLocationByPlantViewSet, basename='specificplant-locations')
register_lifecycle_routes(router)
register_harvest_routes(router)
register_register_routes(router)
register_growth_routes(router)
register_cohort_routes(router)
register_planning_routes(router)


def _register_bulk_routes():
    """Import after the move serializer that bulk payload validation reuses."""
    from .bulk_rest import register_bulk_operation_routes  # pylint: disable=import-outside-toplevel

    register_bulk_operation_routes(router)


_register_bulk_routes()
