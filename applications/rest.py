"""REST surface for drafting, previewing, posting, and reversing applications."""

# Most serializers here are action payloads rather than writable resources, so
# they implement neither of DRF's `create` and `update`; `ActionSerializer`
# refuses both once on their behalf.
# pylint: disable=duplicate-code,abstract-method

from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import routers, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from inventory.ledger import quantize_quantity
from inventory.models import (
    InventoryItem,
    ItemUnitConversion,
    StockLot,
)
from common.rest_query import parse_datetime, parse_integer
from locations.models import Location
from plantings.models import ProductionBatch
from workspaces.scoping import CurrentWorkspaceSerializerMixin, CurrentWorkspaceViewSetMixin

from .requests import build_lines, build_request
from .models import InputApplication, InputApplicationLine, InputApplicationTarget
from .services import (
    ApplicationRequest,
    application_state,
    create_application_draft,
    post_application,
    reverse_application,
    update_application_draft,
)

TargetType = InputApplicationTarget.TargetType

SEED_TRAY_INPUT_CATEGORIES = {
    InventoryItem.Category.GROWING_MEDIA,
    InventoryItem.Category.FERTILIZER_TREATMENT,
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

    def validate_item(self, item):
        """Keep seed consumption in the sowing workflow that owns it."""
        if item.category == InventoryItem.Category.SEED:
            raise serializers.ValidationError(
                'Seed stock must be consumed by recording a sowing.'
            )
        return item

    def validate(self, attrs):
        """Only accept categories that can physically be applied to tray cells."""
        attrs = super().validate(attrs)
        targets_tray = attrs.get('tray') is not None or any(
            target['target_type'] == TargetType.SEED_TRAY_CELL
            for target in attrs.get('targets', [])
        )
        item = attrs['item']
        invalid_tray_item = targets_tray and item.category not in SEED_TRAY_INPUT_CATEGORIES
        if invalid_tray_item:
            raise serializers.ValidationError({
                'item': (
                    'Only growing media or fertilizer/treatment can be applied '
                    'to seed trays.'
                ),
            })
        return attrs


class ApplicationDraftSerializer(CurrentWorkspaceSerializerMixin, ActionSerializer):
    """The payload that creates or replaces a draft."""

    applied_at = serializers.DateTimeField()
    source_location = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
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
            build_request(workspace, values.validated_data),
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
                lines=build_lines(workspace, data['lines']) if replace_lines else (),
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
