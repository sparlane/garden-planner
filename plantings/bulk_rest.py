"""REST preview, execution, and audit resources for bulk plant work."""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from applications.rest import ApplicationDraftSerializer
from inventory.models import InventoryItem
from inventory.units import UnitCode
from workspaces.models import Workspace
from workspaces.scoping import (
    CurrentWorkspaceSerializerMixin,
    CurrentWorkspaceViewSetMixin,
    RequireWorkspaceModeMixin,
)

from .bulk_operations import (
    ACTION_EVENTS,
    BulkOperationConflict,
    concrete_request,
    execute_bulk_operation,
    preview_bulk_operation,
)
from .models import (
    BulkPlantOperation,
    BulkPlantOperationResult,
    GrowthStage,
    PlantGrade,
    SeedTrayCellPlanting,
)
from .rest import SpecificPlantMoveSerializer


MAX_BULK_PLANTS = 5000


class GerminationPayloadSerializer(
    CurrentWorkspaceSerializerMixin,
    serializers.Serializer,
):  # pylint: disable=abstract-method
    """Validate the tray-cell source and number of plants observed."""

    cell_planting = serializers.PrimaryKeyRelatedField(
        queryset=SeedTrayCellPlanting.objects.all(),
        required=False,
    )
    cell_plantings = serializers.PrimaryKeyRelatedField(
        queryset=SeedTrayCellPlanting.objects.all(),
        many=True,
        required=False,
    )
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_BULK_PLANTS)
    notes = serializers.CharField(allow_blank=True, required=False, default='')
    workspace_field_lookups = {
        'cell_planting': 'seed_tray_planting__workspace',
        'cell_plantings': 'seed_tray_planting__workspace',
    }

    def validate(self, attrs):
        """Normalize one or many cell allocations into one bounded selection."""
        singular = attrs.pop('cell_planting', None)
        allocations = attrs.get('cell_plantings', [])
        if singular is not None and allocations:
            raise ValidationError({
                'cell_plantings': 'Choose either one cell allocation or a list, not both.',
            })
        if singular is not None:
            allocations = [singular]
        if not allocations:
            raise ValidationError({'cell_plantings': 'Choose at least one cell allocation.'})
        allocation_ids = [allocation.pk for allocation in allocations]
        if len(set(allocation_ids)) != len(allocation_ids):
            raise ValidationError({'cell_plantings': 'Each cell allocation may only be selected once.'})
        if len(allocations) * attrs['quantity'] > MAX_BULK_PLANTS:
            raise ValidationError({
                'quantity': f'A germination operation may create at most {MAX_BULK_PLANTS} plants.',
            })
        attrs['cell_plantings'] = allocations
        return attrs

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class NurseryFactPayloadSerializer(
    CurrentWorkspaceSerializerMixin, serializers.Serializer,
):  # pylint: disable=abstract-method
    """Validate one stage or grade chosen for a reviewed plant selection."""

    stage = serializers.PrimaryKeyRelatedField(
        queryset=GrowthStage.objects.all(), required=False,
    )
    grade = serializers.PrimaryKeyRelatedField(
        queryset=PlantGrade.objects.all(), required=False,
    )
    notes = serializers.CharField(allow_blank=True, required=False, default='')
    workspace_field_lookups = {'stage': 'workspace', 'grade': 'workspace'}

    def validate(self, attrs):  # pylint: disable=too-many-branches
        field = self.context['field']
        unwanted = 'grade' if field == 'stage' else 'stage'
        if field not in attrs or unwanted in attrs:
            raise ValidationError({field: f'Choose exactly one {field}.'})
        return attrs


class RepotPayloadSerializer(
    CurrentWorkspaceSerializerMixin, serializers.Serializer,
):  # pylint: disable=abstract-method
    """Validate a container assignment and the stock document funding it."""

    container_item = serializers.PrimaryKeyRelatedField(
        queryset=InventoryItem.objects.all(),
    )
    container_count = serializers.IntegerField(min_value=1)
    application = ApplicationDraftSerializer()
    notes = serializers.CharField(allow_blank=True, required=False, default='')
    workspace_field_lookups = {'container_item': 'workspace'}

    def validate(self, attrs):
        item = attrs['container_item']
        if item.category != InventoryItem.Category.POT_CONTAINER:
            raise ValidationError({'container_item': 'Choose a pot or container item.'})
        if item.base_unit != UnitCode.EACH:
            raise ValidationError({'container_item': 'Container stock must be measured in each.'})
        matching = [
            line for line in attrs['application']['lines']
            if line['item'] == item
        ]
        if len(matching) != 1:
            raise ValidationError({'application': 'Include exactly one line for the assigned container.'})
        line = matching[0]
        if line['applied_quantity'] != attrs['container_count'] or line.get('unit_code') != UnitCode.EACH:
            raise ValidationError({'application': 'The container line must consume the assigned count in each.'})
        if any(line.get('targets') or line.get('tray') for line in attrs['application']['lines']):
            raise ValidationError({'application': 'Repot targets come from the reviewed plant selection.'})
        return attrs


class BulkOperationRequestSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Validate both previews and confirmed requests."""

    idempotency_key = serializers.UUIDField(required=False)
    action = serializers.ChoiceField(choices=BulkPlantOperation.Action.choices)
    atomicity = serializers.ChoiceField(choices=BulkPlantOperation.Atomicity.choices)
    occurred_at = serializers.DateTimeField(required=False)
    reason = serializers.CharField(allow_blank=True, required=False, default='')
    plants = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=True,
        max_length=MAX_BULK_PLANTS,
        required=False,
        default=list,
    )
    selection_source = serializers.JSONField(required=False, default=dict)
    action_payload = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):  # pylint: disable=too-many-branches
        """Choose and validate the action-specific payload contract."""
        action_name = attrs['action']
        plants = attrs['plants']
        payload = attrs['action_payload']
        if action_name == BulkPlantOperation.Action.GERMINATE:
            if plants:
                raise ValidationError({'plants': 'Germination selects a cell allocation, not existing plants.'})
            if attrs['atomicity'] != BulkPlantOperation.Atomicity.ALL_OR_NOTHING:
                raise ValidationError({'atomicity': 'Germination is always all or nothing.'})
            payload_serializer = GerminationPayloadSerializer(data=payload)
        elif action_name == BulkPlantOperation.Action.MOVE:
            if not plants:
                raise ValidationError({'plants': 'Select at least one plant.'})
            payload_serializer = SpecificPlantMoveSerializer(data=payload)
        elif action_name in {BulkPlantOperation.Action.STAGE, BulkPlantOperation.Action.GRADE}:
            if not plants:
                raise ValidationError({'plants': 'Select at least one plant.'})
            field = 'stage' if action_name == BulkPlantOperation.Action.STAGE else 'grade'
            model = GrowthStage if field == 'stage' else PlantGrade
            payload_serializer = NurseryFactPayloadSerializer(data=payload, context={
                'field': field,
                'model': model,
            })
        elif action_name == BulkPlantOperation.Action.REPOT:
            if not plants:
                raise ValidationError({'plants': 'Select at least one plant.'})
            if attrs['atomicity'] != BulkPlantOperation.Atomicity.ALL_OR_NOTHING:
                raise ValidationError({'atomicity': 'Repotting is always all or nothing.'})
            payload_serializer = RepotPayloadSerializer(data=payload)
        else:
            if action_name not in ACTION_EVENTS:
                raise ValidationError({'action': 'Select a supported action.'})
            if not plants:
                raise ValidationError({'plants': 'Select at least one plant.'})
            if payload:
                raise ValidationError({'action_payload': 'This action takes no additional fields.'})
            attrs['action_payload'] = {}
            return attrs
        payload_serializer.is_valid(raise_exception=True)
        attrs['action_payload'] = dict(payload_serializer.validated_data)
        return attrs

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class BulkPlantOperationResultSerializer(serializers.ModelSerializer):
    """One concrete plant's independently linked result."""

    class Meta:
        model = BulkPlantOperationResult
        fields = [
            'plant',
            'status',
            'errors',
            'lifecycle_event',
            'location',
            'nursery_observation',
        ]
        read_only_fields = fields


class BulkPlantOperationSerializer(serializers.ModelSerializer):
    """A completed bulk operation and every selected plant's result."""

    results = BulkPlantOperationResultSerializer(many=True, read_only=True)

    class Meta:
        model = BulkPlantOperation
        fields = [
            'pk',
            'idempotency_key',
            'action',
            'atomicity',
            'occurred_at',
            'reason',
            'selection_source',
            'action_payload',
            'created_by',
            'created',
            'results',
        ]
        read_only_fields = fields


def _domain_error(error):
    """Translate a model/service validation error into a REST error."""
    if hasattr(error, 'message_dict'):
        return ValidationError(error.message_dict)
    return ValidationError(error.messages)


class BulkPlantOperationViewSet(
    RequireWorkspaceModeMixin,
    CurrentWorkspaceViewSetMixin,
    viewsets.ReadOnlyModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Preview, execute, and read immutable Nursery bulk operations."""

    required_workspace_modes = (Workspace.Mode.NURSERY,)
    queryset = BulkPlantOperation.objects.prefetch_related('results')
    serializer_class = BulkPlantOperationSerializer

    def get_required_workspace_modes(self):
        """Keep nursery work gated while allowing shared tray germination."""
        if all((
            self.action in {'create', 'preview'},
            self.request.data.get('action') == BulkPlantOperation.Action.GERMINATE,
        )):
            return (Workspace.Mode.GARDEN, Workspace.Mode.NURSERY)
        return super().get_required_workspace_modes()

    def _request_values(self, request, require_key):
        serializer = BulkOperationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        if require_key and 'idempotency_key' not in values:
            raise ValidationError({'idempotency_key': 'This field is required.'})
        return concrete_request(**values)

    @action(detail=False, methods=['post'])
    def preview(self, request):
        """Resolve eligibility and effects without writing an audit or plant fact."""
        operation_request = self._request_values(request, require_key=False)
        try:
            preview = preview_bulk_operation(
                self.get_current_workspace(),
                operation_request,
            )
        except DjangoValidationError as exc:
            raise _domain_error(exc) from exc
        return Response(preview)

    def create(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        """Execute a confirmed plan once and replay completed retries."""
        operation_request = self._request_values(request, require_key=True)
        try:
            operation, replayed = execute_bulk_operation(
                self.get_current_workspace(),
                request.user,
                operation_request,
            )
        except BulkOperationConflict as exc:
            return Response(
                {'detail': 'The bulk operation has conflicts.', **exc.preview},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except DjangoValidationError as exc:
            raise _domain_error(exc) from exc
        response_status = status.HTTP_200_OK if replayed else status.HTTP_201_CREATED
        return Response(self.get_serializer(operation).data, status=response_status)


def register_bulk_operation_routes(router):
    """Attach bulk execution and audit endpoints to the planting router."""
    router.register(
        r'bulk-operations',
        BulkPlantOperationViewSet,
        basename='bulk-plant-operation',
    )
