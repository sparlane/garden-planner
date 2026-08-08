"""REST surface for drafting, previewing, posting, and reversing applications."""

# pylint: disable=duplicate-code

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import routers, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from garden.models import GardenArea, GardenBed, GardenRow, GardenSquare
from inventory.ledger import quantize_quantity
from inventory.models import (
    InventoryItem,
    InventoryLocation,
    InventoryUnit,
    ItemUnitConversion,
    StockLot,
)
from inventory.rest_query import parse_datetime, parse_integer
from plantings.models import ProductionBatch, SpecificPlant
from seedtrays.models import SeedTray, SeedTrayCell
from workspaces.scoping import CurrentWorkspaceSerializerMixin, CurrentWorkspaceViewSetMixin

from .models import InputApplication, InputApplicationLine, InputApplicationTarget
from .services import (
    ApplicationRequest,
    LineRequest,
    TargetRequest,
    application_state,
    cells_for_tray,
    create_application_draft,
    post_application,
    reverse_application,
    update_application_draft,
)

TargetType = InputApplicationTarget.TargetType

#: How each target type is reached, and the lookup that keeps it in workspace.
#: A tray cell is not workspace owned in its own right, so it is scoped through
#: the tray that holds it.
TARGET_SOURCES = {
    TargetType.BATCH: (ProductionBatch, 'workspace'),
    TargetType.SEED_TRAY_CELL: (SeedTrayCell, 'tray__workspace'),
    TargetType.SPECIFIC_PLANT: (SpecificPlant, 'workspace'),
    TargetType.INVENTORY_UNIT: (InventoryUnit, 'workspace'),
    TargetType.GARDEN_AREA: (GardenArea, 'workspace'),
    TargetType.GARDEN_BED: (GardenBed, 'workspace'),
    TargetType.GARDEN_ROW: (GardenRow, 'workspace'),
    TargetType.GARDEN_SQUARE: (GardenSquare, 'workspace'),
}


def _model_errors(error):
    """Translate a Django validation error into DRF's field-error shape."""
    return error.message_dict if hasattr(error, 'message_dict') else error.messages


def _run_domain_action(function, *args, **kwargs):
    """Run a domain service, surfacing its errors as DRF field errors."""
    try:
        return function(*args, **kwargs)
    except DjangoValidationError as exc:
        raise ValidationError(_model_errors(exc)) from exc


def _decimal(value):
    """Render a ledger decimal as the fixed-precision string clients expect."""
    return None if value is None else f'{quantize_quantity(value):.9f}'


class ActionSerializer(serializers.Serializer):
    """Base for payloads that drive a service rather than a model."""

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class ReasonSerializer(ActionSerializer):
    """A required explanation for an audited action."""

    reason = serializers.CharField(allow_blank=False, trim_whitespace=True)


class PostApplicationSerializer(ActionSerializer):
    """What a client echoes back to prove nothing moved since it looked."""

    revision = serializers.IntegerField(required=False)
    availability_digest = serializers.CharField(required=False)


class InputApplicationTargetSerializer(serializers.ModelSerializer):
    """One frozen target of a posted or draft line."""

    target = serializers.SerializerMethodField()

    class Meta:
        model = InputApplicationTarget
        fields = [
            'pk',
            'target_type',
            'target',
            'label',
            'weight',
            'seed_tray_generation',
            'cell_volume_ml',
            'area_m2',
        ]
        read_only_fields = fields

    def get_target(self, row):
        """Return the primary key of whichever thing this row points at."""
        return row.target_id


class InputApplicationLineSerializer(serializers.ModelSerializer):
    """One item drawn from one exact lot, with its calculation snapshot."""

    targets = InputApplicationTargetSerializer(many=True, read_only=True)

    class Meta:
        model = InputApplicationLine
        fields = [
            'pk',
            'item',
            'lot',
            'usage_basis',
            'base_unit',
            'configured_rate',
            'configured_rate_unit',
            'configured_fixed_quantity',
            'fill_factor',
            'formula_basis_quantity',
            'formula_basis_unit',
            'calculated_base_quantity',
            'applied_quantity',
            'unit_code',
            'unit_conversion',
            'applied_base_quantity',
            'waste_quantity',
            'waste_base_quantity',
            'waste_reason',
            'override_reason',
            'notes',
            'consumption_movement',
            'waste_movement',
            'targets',
        ]
        read_only_fields = fields


class InputApplicationSerializer(serializers.ModelSerializer):
    """The full readable record of one document."""

    lines = InputApplicationLineSerializer(many=True, read_only=True)

    class Meta:
        model = InputApplication
        fields = [
            'pk',
            'status',
            'batch',
            'applied_at',
            'source_location',
            'notes',
            'target_summary',
            'revision',
            'created_by',
            'posted_at',
            'reversed_at',
            'reverse_reason',
            'reversed_by',
            'created',
            'updated',
            'lines',
        ]
        read_only_fields = fields


class ApplicationTargetInputSerializer(ActionSerializer):
    """One target a caller selected, named by type and primary key."""

    target_type = serializers.ChoiceField(choices=TargetType.choices)
    target = serializers.IntegerField()
    weight = serializers.DecimalField(
        max_digits=12,
        decimal_places=6,
        required=False,
        default=None,
    )


class ApplicationLineInputSerializer(CurrentWorkspaceSerializerMixin, ActionSerializer):
    """One line a caller wants on the draft.

    This is not a model serializer. The service measures every target and
    freezes the item's configuration onto the line, so handing it raw
    selections keeps one place responsible for what a document records.
    """

    item = serializers.PrimaryKeyRelatedField(queryset=InventoryItem.objects.all())
    lot = serializers.PrimaryKeyRelatedField(queryset=StockLot.objects.all())
    applied_quantity = serializers.DecimalField(max_digits=24, decimal_places=9)
    unit_code = serializers.CharField(required=False, allow_null=True, default=None)
    unit_conversion = serializers.PrimaryKeyRelatedField(
        queryset=ItemUnitConversion.objects.all(),
        required=False,
        allow_null=True,
        default=None,
    )
    usage_basis = serializers.CharField(required=False, allow_blank=True, default='')
    fill_factor = serializers.DecimalField(
        max_digits=12,
        decimal_places=6,
        required=False,
        allow_null=True,
        default=None,
    )
    waste_quantity = serializers.DecimalField(
        max_digits=24,
        decimal_places=9,
        required=False,
        default=0,
    )
    waste_reason = serializers.CharField(required=False, allow_blank=True, default='')
    override_reason = serializers.CharField(required=False, allow_blank=True, default='')
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    targets = ApplicationTargetInputSerializer(many=True, required=False, default=list)
    tray = serializers.IntegerField(required=False, allow_null=True, default=None)

    workspace_field_lookups = {
        'item': 'workspace',
        'lot': 'workspace',
        'unit_conversion': 'workspace',
    }


class ApplicationDraftSerializer(CurrentWorkspaceSerializerMixin, ActionSerializer):
    """The payload that creates or replaces a draft."""

    applied_at = serializers.DateTimeField()
    source_location = serializers.PrimaryKeyRelatedField(
        queryset=InventoryLocation.objects.all(),
    )
    batch = serializers.PrimaryKeyRelatedField(
        queryset=ProductionBatch.objects.all(),
        required=False,
        allow_null=True,
        default=None,
    )
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    lines = ApplicationLineInputSerializer(many=True)

    workspace_field_lookups = {
        'source_location': 'workspace',
        'batch': 'workspace',
    }


def _resolve_target(workspace, values):
    """Turn one selected primary key into the object it names."""
    target_type = values['target_type']
    model, lookup = TARGET_SOURCES[target_type]
    try:
        target = model.objects.get(**{lookup: workspace}, pk=values['target'])
    except model.DoesNotExist as exc:
        raise ValidationError({
            'targets': f'No {target_type} with id {values["target"]} in this workspace.',
        }) from exc
    weight = values.get('weight')
    return TargetRequest(
        target_type=target_type,
        target=target,
        weight=1 if weight is None else weight,
    )


def _resolve_tray_cells(workspace, tray_pk):
    """Expand a whole-tray selection into one target per cell."""
    try:
        tray = SeedTray.objects.get(workspace=workspace, pk=tray_pk)
    except SeedTray.DoesNotExist as exc:
        raise ValidationError({
            'tray': f'No tray with id {tray_pk} in this workspace.',
        }) from exc
    cells = cells_for_tray(tray)
    if not cells:
        raise ValidationError({'tray': 'That tray has no cells recorded.'})
    return cells


def _build_lines(workspace, line_values):
    """Turn validated line payloads into the service's line requests.

    Every optional key is read with a fallback because a partial update skips
    serializer defaults, so an absent field means "leave it alone" rather than
    an error.
    """
    lines = []
    for line in line_values:
        targets = [
            _resolve_target(workspace, target)
            for target in line.get('targets') or []
        ]
        tray = line.get('tray')
        if tray is not None:
            targets.extend(_resolve_tray_cells(workspace, tray))
        lines.append(LineRequest(
            item=line['item'],
            lot=line['lot'],
            applied_quantity=line['applied_quantity'],
            unit_code=line.get('unit_code'),
            unit_conversion=line.get('unit_conversion'),
            usage_basis=line.get('usage_basis') or '',
            fill_factor=line.get('fill_factor'),
            waste_quantity=line.get('waste_quantity') or Decimal('0'),
            waste_reason=line.get('waste_reason') or '',
            override_reason=line.get('override_reason') or '',
            notes=line.get('notes') or '',
            targets=tuple(targets),
        ))
    return tuple(lines)


def _build_request(workspace, values):
    """Turn a validated create payload into the service's request object."""
    return ApplicationRequest(
        applied_at=values['applied_at'],
        source_location=values['source_location'],
        batch=values['batch'],
        notes=values['notes'],
        lines=_build_lines(workspace, values['lines']),
    )


def _state_response(application):
    """Render a document's calculations and stock as fixed-precision strings."""
    state = application_state(application)
    return {
        'revision': state['revision'],
        'availability_digest': state['availability_digest'],
        'target_summary': state['target_summary'],
        'lines': [
            {
                **line,
                'basis_quantity': _decimal(line['basis_quantity']),
                'calculated_base_quantity': _decimal(line['calculated_base_quantity']),
                'applied_base_quantity': _decimal(line['applied_base_quantity']),
                'waste_base_quantity': _decimal(line['waste_base_quantity']),
                'available_base_quantity': _decimal(line['available_base_quantity']),
                'available_after_base_quantity': _decimal(
                    line['available_after_base_quantity'],
                ),
            }
            for line in state['lines']
        ],
    }


class InputApplicationViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """Draft, preview, post, and reverse input applications."""

    queryset = InputApplication.objects.select_related(
        'batch',
        'source_location',
    ).prefetch_related('lines__targets', 'lines__item', 'lines__lot')
    serializer_class = InputApplicationSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']
    bind_workspace_on_create = False

    def get_queryset(self):
        """Filter documents by status, batch, item, and applied period."""
        queryset = super().get_queryset()
        query = self.request.query_params
        if 'status' in query:
            queryset = queryset.filter(status=query['status'])
        batch = parse_integer(query.get('batch'), 'batch')
        if batch is not None:
            queryset = queryset.filter(batch_id=batch)
        item = parse_integer(query.get('item'), 'item')
        if item is not None:
            queryset = queryset.filter(lines__item_id=item).distinct()
        applied_from = parse_datetime(query.get('applied_from'), 'applied_from')
        if applied_from is not None:
            queryset = queryset.filter(applied_at__gte=applied_from)
        applied_to = parse_datetime(query.get('applied_to'), 'applied_to')
        if applied_to is not None:
            queryset = queryset.filter(applied_at__lte=applied_to)
        return queryset

    def create(self, request, *args, **kwargs):
        """Assemble a draft, freezing what its calculation depends on."""
        values = ApplicationDraftSerializer(data=request.data, context={'request': request})
        values.is_valid(raise_exception=True)
        workspace = self.get_current_workspace()
        application = _run_domain_action(
            create_application_draft,
            workspace,
            request.user,
            _build_request(workspace, values.validated_data),
        )
        return Response(
            self.get_serializer(application).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        """Edit a draft, replacing its lines only when they are supplied.

        Any edit moves the revision, so a preview taken before it stops being
        something the client can post against.
        """
        application = self.get_object()
        values = ApplicationDraftSerializer(
            data=request.data,
            context={'request': request},
            partial=kwargs.pop('partial', False),
        )
        values.is_valid(raise_exception=True)
        data = values.validated_data
        workspace = self.get_current_workspace()
        replace_lines = 'lines' in data
        application = _run_domain_action(
            update_application_draft,
            application,
            ApplicationRequest(
                applied_at=data.get('applied_at', application.applied_at),
                source_location=data.get('source_location', application.source_location),
                batch=data.get('batch', application.batch),
                notes=data.get('notes', application.notes),
                lines=_build_lines(workspace, data['lines']) if replace_lines else (),
            ),
            replace_lines,
        )
        return Response(self.get_serializer(application).data)

    def perform_destroy(self, instance):
        """Refuse to delete anything that already moved stock."""
        try:
            instance.delete()
        except DjangoValidationError as exc:
            raise ValidationError(_model_errors(exc)) from exc

    @action(detail=True, methods=['get'])
    def preview(self, request, pk=None):  # pylint: disable=unused-argument
        """Report the calculations and stock without writing anything."""
        return Response(_run_domain_action(_state_response, self.get_object()))

    @action(detail=True, methods=['post'])
    def post(self, request, pk=None):  # pylint: disable=unused-argument
        """Confirm the draft and decrement the exact lots it names."""
        values = PostApplicationSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        application, _ = _run_domain_action(
            post_application,
            self.get_object(),
            request.user,
            values.validated_data.get('revision'),
            values.validated_data.get('availability_digest'),
        )
        return Response(self.get_serializer(application).data)

    @action(detail=True, methods=['post'])
    def reverse(self, request, pk=None):  # pylint: disable=unused-argument
        """Put back everything a posted application took."""
        values = ReasonSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        application = _run_domain_action(
            reverse_application,
            self.get_object(),
            request.user,
            values.validated_data['reason'],
        )
        return Response(self.get_serializer(application).data)


router = routers.DefaultRouter()
router.register(r'input-applications', InputApplicationViewSet)
