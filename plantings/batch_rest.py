"""REST resources and explicit lifecycle actions for production batches."""

# pylint: disable=duplicate-code

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from workspaces.scoping import (
    CurrentWorkspaceSerializerMixin,
    CurrentWorkspaceViewSetMixin,
)

from .batches import (
    BatchRequest,
    SOWING_MODELS,
    activate_batch,
    batch_final_outcome_count,
    batch_lifecycle_counts,
    batch_plants_with_active_location,
    batch_posted_harvest_count,
    batch_seeds_sown,
    batch_sowing_count,
    batch_specific_plants,
    batch_unresolved_plant_ids,
    cancel_batch,
    complete_batch,
    create_and_activate_batch,
    create_batch,
    finalize_batch_output,
    lock_batch_for_sowing,
    reopen_batch,
)
from .models import GardenPlanting, ProductionBatch, ProductionBatchTransition, SpecificPlantLocation
from .yields import batch_harvest_finished_count, batch_harvest_totals


def _model_errors(error):
    """Translate model validation errors into DRF response details."""
    if hasattr(error, 'message_dict'):
        return error.message_dict
    return error.messages


def _run_domain_action(function, *args, **kwargs):
    """Invoke a batch service with field-friendly API errors."""
    try:
        return function(*args, **kwargs)
    except DjangoValidationError as exc:
        raise ValidationError(_model_errors(exc)) from exc


class ActionSerializer(serializers.Serializer):
    """Validation-only serializer base for batch lifecycle actions."""

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class ActivateBatchSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """Validate an optional supplied actual-start time."""

    actual_start = serializers.DateTimeField(required=False)
    reason = serializers.CharField(allow_blank=True, required=False, default='')


class OptionalReasonSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """Validate audit metadata for an action that does not require a reason."""

    reason = serializers.CharField(allow_blank=True, required=False, default='')


class RequiredReasonSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """Validate the reason an audited correction always requires."""

    reason = serializers.CharField(allow_blank=False, trim_whitespace=True)


class ProductionBatchTransitionSerializer(serializers.ModelSerializer):
    """Serialize one immutable lifecycle history row."""

    class Meta:
        model = ProductionBatchTransition
        fields = [
            'pk',
            'previous_status',
            'new_status',
            'created_by',
            'reason',
            'created',
        ]
        read_only_fields = fields


class BatchSowingSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Serialize one attached sowing without its model-specific write rules."""

    pk = serializers.IntegerField(read_only=True)
    sowing_type = serializers.CharField(read_only=True)
    planted = serializers.CharField(read_only=True)
    quantity = serializers.IntegerField(read_only=True)
    removed = serializers.BooleanField(read_only=True)
    seeds_used = serializers.IntegerField(read_only=True, allow_null=True)
    seed_lot = serializers.IntegerField(read_only=True, allow_null=True)
    seed_tray = serializers.IntegerField(read_only=True, allow_null=True)
    location = serializers.CharField(read_only=True, allow_null=True)
    notes = serializers.CharField(read_only=True, allow_null=True)
    cells = serializers.ListField(read_only=True)
    plants_observed = serializers.IntegerField(read_only=True)


def _describe_cells(sowing):
    """Describe one tray sowing's cell allocations and their germinations."""
    cell_plantings = sowing.cell_plantings.select_related('cell').annotate(
        observed=Count('specific_plants'),
    ).order_by('pk')
    return [
        {
            'pk': cell_planting.pk,
            'cell': cell_planting.cell_id,
            'x_position': cell_planting.cell.x_position,
            'y_position': cell_planting.cell.y_position,
            'quantity': cell_planting.quantity,
            'plants_observed': cell_planting.observed,
        }
        for cell_planting in cell_plantings
    ]


def _describe_sowing(sowing):
    """Return one sowing's batch-level summary in a display-neutral shape."""
    if isinstance(sowing, GardenPlanting):
        return {
            'pk': sowing.pk,
            'sowing_type': type(sowing).__name__,
            'planted': sowing.recorded_on.isoformat(),
            'quantity': sowing.quantity,
            'removed': sowing.finished_on is not None,
            'seeds_used': sowing.seed_packet_id,
            'seed_lot': sowing.seed_packet.stock_lot_id if sowing.seed_packet_id else None,
            'seed_tray': None,
            'location': str(sowing.garden_square or sowing.location),
            'notes': sowing.notes,
            'cells': [],
            'plants_observed': sowing.specific_plants.count(),
        }
    is_tray = hasattr(sowing, 'cell_plantings')
    location = getattr(sowing, 'location', None)
    cells = _describe_cells(sowing) if is_tray else []
    return {
        'pk': sowing.pk,
        'sowing_type': type(sowing).__name__,
        'planted': sowing.planted.isoformat(),
        'quantity': sowing.quantity,
        'removed': sowing.removed,
        'seeds_used': sowing.seeds_used_id,
        'seed_lot': sowing.seeds_used.stock_lot_id,
        'seed_tray': getattr(sowing, 'seed_tray_id', None),
        'location': None if location is None else str(location),
        'notes': sowing.notes,
        'cells': cells,
        'plants_observed': sum(cell['plants_observed'] for cell in cells),
    }


def _batch_sowings(batch):
    """Return every attached sowing across the concrete planting models."""
    sowings = []
    for model in SOWING_MODELS:
        queryset = model.objects.filter(batch=batch).select_related(
            'seeds_used',
        ).order_by('planted', 'pk')
        sowings.extend(_describe_sowing(sowing) for sowing in queryset)
    sowings.extend(
        _describe_sowing(sowing)
        for sowing in batch.garden_plantings.select_related('seed_packet').order_by('recorded_on', 'pk')
    )
    return sowings


def _current_locations(batch):
    """Describe where this batch's individual plants are living now."""
    locations = SpecificPlantLocation.objects.filter(
        specific_plant__batch=batch,
        ended__isnull=True,
    ).select_related('seed_tray_cell', 'garden_square', 'location').order_by('pk')
    return [
        {
            'specific_plant': location.specific_plant_id,
            'location_type': location.location_type,
            'seed_tray_cell': location.seed_tray_cell_id,
            'garden_square': location.garden_square_id,
            'location': location.location_id,
            'started': location.started,
            'label': str(location.seed_tray_cell or location.garden_square or location.location),
        }
        for location in locations
    ]


class ProductionBatchSerializer(
    CurrentWorkspaceSerializerMixin,
    serializers.ModelSerializer,
):
    """Serialize batch identity, lifecycle state, and output summary counts."""

    variety_name = serializers.CharField(source='variety.name', read_only=True)
    plant_name = serializers.CharField(source='variety.plant.name', read_only=True)
    code_is_generated = serializers.BooleanField(read_only=True)
    sowing_count = serializers.SerializerMethodField()
    seeds_sown = serializers.SerializerMethodField()
    plants_observed = serializers.SerializerMethodField()
    plants_with_active_location = serializers.SerializerMethodField()
    final_outcomes = serializers.SerializerMethodField()
    unresolved_plants = serializers.SerializerMethodField()

    class Meta:
        model = ProductionBatch
        fields = [
            'pk',
            'code',
            'code_is_generated',
            'variety',
            'variety_name',
            'plant_name',
            'status',
            'planned_start',
            'actual_start',
            'output_finalized_at',
            'completed_at',
            'cancelled_at',
            'notes',
            'created_by',
            'repair_state',
            'repair_details',
            'sowing_count',
            'seeds_sown',
            'plants_observed',
            'plants_with_active_location',
            'final_outcomes',
            'unresolved_plants',
            'created',
            'updated',
        ]
        read_only_fields = [
            'status',
            'actual_start',
            'output_finalized_at',
            'completed_at',
            'cancelled_at',
            'created_by',
            'repair_state',
            'repair_details',
            'created',
            'updated',
        ]

    workspace_field_lookups = {'variety': 'workspace'}

    def get_sowing_count(self, batch):
        """Return how many sowings this batch groups."""
        return batch_sowing_count(batch)

    def get_seeds_sown(self, batch):
        """Return the seeds or seed clusters sown, not the plants raised."""
        return batch_seeds_sown(batch)

    def get_plants_observed(self, batch):
        """Return the individual plants germinated from this batch."""
        return batch_specific_plants(batch).count()

    def get_plants_with_active_location(self, batch):
        """Return the plants currently occupying a tracked location."""
        return batch_plants_with_active_location(batch).count()

    def get_final_outcomes(self, batch):
        """Return how many plants have a recorded final disposition."""
        return batch_final_outcome_count(batch)

    def get_unresolved_plants(self, batch):
        """Return the plants blocking completion of this batch."""
        return batch_unresolved_plant_ids(batch)

    def _has_sowings(self):
        """Return whether the batch being edited already groups sowings."""
        return any(
            model.objects.filter(batch=self.instance).exists()
            for model in SOWING_MODELS
        )

    def validate_code(self, value):
        """Keep a typed batch code unique inside one workspace.

        A blank code is left to `create_batch` to fill in, so there is
        nothing to check for one.
        """
        code = value.strip()
        if not code:
            return code
        duplicates = ProductionBatch.objects.filter(
            workspace=self.context['view'].get_current_workspace(),
            code=code,
        )
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError(
                'Another batch in this workspace already uses that code.',
            )
        return code

    def validate(self, attrs):
        """Lock the variety once a status or a sowing depends on it."""
        if self.instance is None or 'variety' not in attrs:
            return attrs
        if attrs['variety'].pk == self.instance.variety_id:
            return attrs
        if self.instance.status != ProductionBatch.Status.PLANNED:
            raise serializers.ValidationError({
                'variety': 'Only a planned batch can change its variety.',
            })
        if self._has_sowings():
            raise serializers.ValidationError({
                'variety': 'Cannot change the variety after a sowing exists.',
            })
        return attrs

    def create(self, validated_data):
        """Create the batch and its opening transition in one operation."""
        return _run_domain_action(
            create_batch,
            self.context['view'].get_current_workspace(),
            self.context['request'].user,
            BatchRequest(
                code=validated_data.get('code', ''),
                variety=validated_data['variety'],
                planned_start=validated_data.get('planned_start'),
                notes=validated_data.get('notes', ''),
            ),
        )


class ProductionBatchDetailSerializer(ProductionBatchSerializer):
    """Add the grouped cultivation detail one batch screen needs."""

    sowings = serializers.SerializerMethodField()
    current_locations = serializers.SerializerMethodField()
    lifecycle_counts = serializers.SerializerMethodField()
    harvest_count = serializers.SerializerMethodField()
    harvest_totals = serializers.SerializerMethodField()
    plants_harvest_finished = serializers.SerializerMethodField()
    transitions = ProductionBatchTransitionSerializer(many=True, read_only=True)

    class Meta(ProductionBatchSerializer.Meta):
        fields = ProductionBatchSerializer.Meta.fields + [
            'sowings',
            'current_locations',
            'lifecycle_counts',
            'harvest_count',
            'harvest_totals',
            'plants_harvest_finished',
            'transitions',
        ]

    def get_sowings(self, batch):
        """Return each attached sowing with its lot, cells, and germinations."""
        return BatchSowingSerializer(_batch_sowings(batch), many=True).data

    def get_harvest_count(self, batch):
        """Return how many harvests still count as output from this batch."""
        return batch_posted_harvest_count(batch)

    def get_harvest_totals(self, batch):
        """Return this batch's yield, one total per unit family.

        Count, mass, and volume stay in separate entries; they are never added
        together into a single figure.
        """
        return batch_harvest_totals(batch)

    def get_plants_harvest_finished(self, batch):
        """Return how many plants this batch harvested out.

        Narrower than `final_outcomes`, which also counts plants that failed,
        were culled, were donated, or were retained.
        """
        return batch_harvest_finished_count(batch)

    def get_lifecycle_counts(self, batch):
        """Return how many of this batch's plants sit in each derived state."""
        return batch_lifecycle_counts(batch)

    def get_current_locations(self, batch):
        """Return where this batch's individual plants are living now."""
        return _current_locations(batch)


class InlineBatchSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Validate the batch a sowing form creates alongside its sowing.

    The variety and actual start are derived from the sowing itself, so a
    caller supplies only the descriptive fields.
    """

    code = serializers.CharField(
        max_length=64,
        allow_blank=True,
        required=False,
        default='',
        trim_whitespace=True,
    )
    planned_start = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default='')


class BatchedSowingSerializerMixin:
    """Attach each sowing to exactly one production batch for life.

    Concrete sowing serializers declare the `new_batch` field themselves
    because DRF only collects declared fields from serializer bases.
    """

    def _validate_batch(self, attrs):
        """Require exactly one batch choice and keep an existing one fixed."""
        if self.instance is not None:
            if 'new_batch' in attrs:
                raise serializers.ValidationError({
                    'new_batch': 'An existing sowing already has a batch.',
                })
            if 'batch' in attrs and attrs['batch'].pk != self.instance.batch_id:
                raise serializers.ValidationError({
                    'batch': 'Cannot move a sowing between batches.',
                })
            return

        chosen = [field for field in ('batch', 'new_batch') if attrs.get(field)]
        if len(chosen) != 1:
            raise serializers.ValidationError({
                'batch': 'Supply exactly one of an existing batch or a new batch.',
            })

    def validate(self, attrs):
        """Apply the batch guard before the remaining sowing rules."""
        self._validate_batch(attrs)
        return super().validate(attrs)

    def _resolve_batch(self, validated_data):
        """Create or lock the batch this new sowing joins.

        Must be called inside the transaction that creates the sowing so no
        work can attach to a batch that is finalizing concurrently.
        """
        workspace = validated_data['workspace']
        packet = validated_data['seeds_used']
        inline = validated_data.pop('new_batch', None)
        try:
            if inline is None:
                validated_data['batch'] = lock_batch_for_sowing(
                    validated_data['batch'],
                    packet,
                    workspace,
                )
                return
            planted = validated_data.setdefault('planted', timezone.now())
            validated_data['batch'] = create_and_activate_batch(
                workspace,
                self.context['request'].user,
                BatchRequest(
                    code=inline['code'],
                    variety=packet.seeds.plant_variety,
                    planned_start=inline.get('planned_start'),
                    notes=inline.get('notes', ''),
                ),
                actual_start=planted,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_model_errors(exc)) from exc

    def create(self, validated_data):
        """Resolve the batch before the sowing and its consumption exist."""
        with transaction.atomic():
            self._resolve_batch(validated_data)
            return super().create(validated_data)


class ProductionBatchViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Manage batch identity and post explicit lifecycle transitions."""

    queryset = ProductionBatch.objects.select_related(
        'variety__plant',
    ).prefetch_related('transitions')
    serializer_class = ProductionBatchSerializer
    http_method_names = ['get', 'post', 'patch', 'put', 'head', 'options']
    bind_workspace_on_create = False

    def get_serializer_class(self):
        """Use the richer serializer for a single batch."""
        if self.action == 'retrieve':
            return ProductionBatchDetailSerializer
        return ProductionBatchSerializer

    def get_queryset(self):
        """Apply the status, variety, and repair filters the screens use."""
        queryset = super().get_queryset()
        status_filter = self.request.query_params.get('status')
        if status_filter:
            valid = {choice.value for choice in ProductionBatch.Status}
            if status_filter not in valid:
                raise ValidationError({'status': 'Select a valid batch status.'})
            queryset = queryset.filter(status=status_filter)
        variety = self.request.query_params.get('variety')
        if variety:
            if not variety.isdigit():
                raise ValidationError({'variety': 'Enter a variety ID.'})
            queryset = queryset.filter(variety_id=int(variety))
        code = self.request.query_params.get('code', '').strip()
        if code:
            queryset = queryset.filter(code__icontains=code)
        if self.request.query_params.get('needs_repair') == 'true':
            queryset = queryset.filter(
                repair_state=ProductionBatch.RepairState.NEEDS_REPAIR,
            )
        return queryset

    def _detail_response(self, batch):
        """Return one batch through the detail contract after an action."""
        batch.refresh_from_db()
        return Response(ProductionBatchDetailSerializer(batch).data)

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):  # pylint: disable=unused-argument
        """Start a planned batch at a supplied or current time."""
        values = self._action_values(request, ActivateBatchSerializer)
        batch = _run_domain_action(
            activate_batch,
            self.get_object(),
            request.user,
            actual_start=values.get('actual_start'),
            reason=values['reason'],
        )
        return self._detail_response(batch)

    @action(detail=True, methods=['post'], url_path='finalize-output')
    def finalize_output(self, request, pk=None):  # pylint: disable=unused-argument
        """Declare that no further seedlings will come from this batch."""
        values = self._action_values(request, OptionalReasonSerializer)
        batch = _run_domain_action(
            finalize_batch_output,
            self.get_object(),
            request.user,
            reason=values['reason'],
        )
        return self._detail_response(batch)

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):  # pylint: disable=unused-argument
        """Complete a batch whose outputs all have final dispositions."""
        values = self._action_values(request, OptionalReasonSerializer)
        batch = _run_domain_action(
            complete_batch,
            self.get_object(),
            request.user,
            reason=values['reason'],
        )
        return self._detail_response(batch)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):  # pylint: disable=unused-argument
        """Abandon a batch that produced no tracked individual outputs."""
        values = self._action_values(request, RequiredReasonSerializer)
        batch = _run_domain_action(
            cancel_batch,
            self.get_object(),
            request.user,
            values['reason'],
        )
        return self._detail_response(batch)

    @action(detail=True, methods=['post'])
    def reopen(self, request, pk=None):  # pylint: disable=unused-argument
        """Correct a lifecycle mistake by stepping one status back."""
        values = self._action_values(request, RequiredReasonSerializer)
        batch = _run_domain_action(
            reopen_batch,
            self.get_object(),
            request.user,
            values['reason'],
        )
        return self._detail_response(batch)

    @staticmethod
    def _action_values(request, serializer_class):
        """Validate one action payload and return its cleaned values."""
        serializer = serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data


def register_batch_routes(router):
    """Attach the production batch resources to the planting API router."""
    router.register(r'batches', ProductionBatchViewSet)
