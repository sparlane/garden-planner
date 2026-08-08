"""REST surface for filling, cleaning, and correcting a tray generation.

Every write here drives a service in `generations`; nothing decides anything on
its own. That is deliberate — the clean has to be one transaction with one set
of rules whether it arrives from this screen, a management command, or a test.
"""

# Every serializer here is an action payload rather than a writable resource, so
# none of them implements DRF's `create` or `update`; `ActionSerializer` refuses
# both once on their behalf.
# pylint: disable=duplicate-code,abstract-method

from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework_nested import routers

from inventory.ledger import quantize_quantity
from inventory.models import InventoryLocation
from inventory.rest_query import parse_integer
from workspaces.scoping import CurrentWorkspaceSerializerMixin, CurrentWorkspaceViewSetMixin

from .generation_costs import generation_cost_breakdown
from .generations import (
    CloseRequest,
    MediaDisposition,
    PlantDisposition,
    SeedDisposition,
    close_generation,
    contents_digest,
    generation_contents,
    open_generation,
    reopen_generation,
    review_generation,
)
from .models import SeedTray, SeedTrayGeneration, SeedTrayGenerationEvent, SeedTrayGenerationResidual


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


class OpenGenerationSerializer(CurrentWorkspaceSerializerMixin, ActionSerializer):
    """What an operator says when they fill a tray."""

    tray = serializers.PrimaryKeyRelatedField(queryset=SeedTray.objects.all())
    opened_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    notes = serializers.CharField(required=False, allow_blank=True, default='')

    workspace_field_lookups = {'tray': 'workspace'}


class PlantDispositionSerializer(ActionSerializer):
    """What an operator decided about one plant still in the tray."""

    plant = serializers.IntegerField()
    outcome = serializers.CharField()
    reason = serializers.CharField(required=False, allow_blank=True, default='')


class SeedDispositionSerializer(CurrentWorkspaceSerializerMixin, ActionSerializer):
    """What became of the seed one sowing drew but never placed."""

    sowing = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=24, decimal_places=9)
    disposition = serializers.CharField()
    reason = serializers.CharField(required=False, allow_blank=True, default='')
    destination = serializers.PrimaryKeyRelatedField(
        queryset=InventoryLocation.objects.all(),
        required=False,
        allow_null=True,
        default=None,
    )

    workspace_field_lookups = {'destination': 'workspace'}


class MediaDispositionSerializer(CurrentWorkspaceSerializerMixin, ActionSerializer):
    """What became of the media one lot left in the tray."""

    lot = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=24, decimal_places=9)
    disposition = serializers.CharField()
    reason = serializers.CharField(required=False, allow_blank=True, default='')
    destination = serializers.PrimaryKeyRelatedField(
        queryset=InventoryLocation.objects.all(),
        required=False,
        allow_null=True,
        default=None,
    )

    workspace_field_lookups = {'destination': 'workspace'}


class CloseGenerationSerializer(ActionSerializer):
    """The confirmed disposition of everything the tray still holds."""

    reason = serializers.CharField(allow_blank=False, trim_whitespace=True)
    occurred_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    plants = PlantDispositionSerializer(many=True, required=False, default=list)
    seeds = SeedDispositionSerializer(many=True, required=False, default=list)
    media = MediaDispositionSerializer(many=True, required=False, default=list)
    digest = serializers.CharField(required=False, allow_null=True, default=None)
    open_next = serializers.BooleanField(required=False, default=False)


class SeedTrayGenerationEventSerializer(serializers.ModelSerializer):
    """One immutable record of a generation lifecycle change."""

    class Meta:
        model = SeedTrayGenerationEvent
        fields = ['pk', 'event_type', 'occurred_at', 'reason', 'created_by', 'created']
        read_only_fields = fields


class SeedTrayGenerationResidualSerializer(serializers.ModelSerializer):
    """One disposition an operator recorded while cleaning."""

    class Meta:
        model = SeedTrayGenerationResidual
        fields = [
            'pk',
            'kind',
            'disposition',
            'lot',
            'sowing',
            'base_quantity',
            'base_unit',
            'unit_cost',
            'movement',
            'reason',
            'created',
        ]
        read_only_fields = fields


class SeedTrayGenerationSerializer(serializers.ModelSerializer):
    """The full readable record of one fill of one tray."""

    events = SeedTrayGenerationEventSerializer(many=True, read_only=True)
    residuals = SeedTrayGenerationResidualSerializer(many=True, read_only=True)

    class Meta:
        model = SeedTrayGeneration
        fields = [
            'pk',
            'tray',
            'code',
            'sequence',
            'status',
            'origin',
            'review_state',
            'review_details',
            'opened_at',
            'closed_at',
            'close_reason',
            'notes',
            'created_by',
            'closed_by',
            'created',
            'updated',
            'events',
            'residuals',
        ]
        read_only_fields = fields


def _contents_response(generation):
    """Render what a clean has to find a disposition for, plus its digest."""
    contents = generation_contents(generation)
    return {
        'generation': generation.pk,
        'code': generation.code,
        'status': generation.status,
        'review_state': generation.review_state,
        'cell_count': contents['cell_count'],
        'digest': contents_digest(contents),
        'sowings': [
            {
                'pk': sowing.pk,
                'planted': sowing.planted,
                'batch': sowing.batch_id,
                'quantity': sowing.quantity,
                'seeds_used': sowing.seeds_used_id,
            }
            for sowing in contents['sowings']
        ],
        'plants': [
            {
                'pk': plant.pk,
                'cell_planting': plant.cell_planting_id,
                'cell': plant.cell_planting.cell_id,
                'germinated': plant.germinated,
            }
            for plant in contents['plants']
        ],
        'seeds': [
            {
                'sowing': row['sowing'].pk,
                'seeds_used': row['sowing'].seeds_used_id,
                'quantity': row['quantity'],
            }
            for row in contents['seeds']
        ],
        'media': [
            {
                'lot': row['lot'].pk,
                'item': row['item'].pk,
                'base_quantity': _decimal(row['base_quantity']),
                'base_unit': row['base_unit'],
                'unit_cost': row['unit_cost'],
            }
            for row in contents['media']
        ],
    }


def _close_request(values):
    """Turn a validated confirmation payload into the service's request."""
    return CloseRequest(
        reason=values['reason'],
        occurred_at=values['occurred_at'],
        plants=tuple(
            PlantDisposition(row['plant'], row['outcome'], row['reason'])
            for row in values['plants']
        ),
        seeds=tuple(
            SeedDisposition(
                row['sowing'],
                row['quantity'],
                row['disposition'],
                row['reason'],
                row['destination'],
            )
            for row in values['seeds']
        ),
        media=tuple(
            MediaDisposition(
                row['lot'],
                row['quantity'],
                row['disposition'],
                row['reason'],
                row['destination'],
            )
            for row in values['media']
        ),
        digest=values['digest'],
        open_next=values['open_next'],
    )


class SeedTrayGenerationViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """Fill a tray, clean it, and correct a clean that should not have happened."""

    queryset = SeedTrayGeneration.objects.select_related('tray').prefetch_related(
        'events',
        'residuals',
    )
    serializer_class = SeedTrayGenerationSerializer
    http_method_names = ['get', 'post', 'head', 'options']
    bind_workspace_on_create = False

    def get_queryset(self):
        """Filter fills by tray and by whether they are still in use."""
        queryset = super().get_queryset()
        query = self.request.query_params
        tray = parse_integer(query.get('tray'), 'tray')
        if tray is not None:
            queryset = queryset.filter(tray_id=tray)
        if 'status' in query:
            queryset = queryset.filter(status=query['status'])
        if 'review_state' in query:
            queryset = queryset.filter(review_state=query['review_state'])
        return queryset

    def create(self, request, *args, **kwargs):
        """Record that a tray has been filled and is ready to sow into."""
        values = OpenGenerationSerializer(data=request.data, context={'request': request})
        values.is_valid(raise_exception=True)
        data = values.validated_data
        generation = _run_domain_action(
            open_generation,
            data['tray'],
            request.user,
            data['opened_at'],
            data['notes'],
        )
        return Response(
            self.get_serializer(generation).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['get'])
    def contents(self, request, pk=None):  # pylint: disable=unused-argument
        """Report everything still in the tray, and the digest that pins it."""
        return Response(_run_domain_action(_contents_response, self.get_object()))

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):  # pylint: disable=unused-argument
        """Empty the tray, resolving everything the operator confirmed."""
        values = CloseGenerationSerializer(data=request.data, context={'request': request})
        values.is_valid(raise_exception=True)
        generation, following = _run_domain_action(
            close_generation,
            self.get_object(),
            request.user,
            _close_request(values.validated_data),
        )
        return Response({
            'generation': self.get_serializer(generation).data,
            'next_generation': (
                self.get_serializer(following).data if following else None
            ),
        })

    @action(detail=True, methods=['post'])
    def reopen(self, request, pk=None):  # pylint: disable=unused-argument
        """Correct a clean that should not have happened, without erasing it."""
        values = ReasonSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        generation = _run_domain_action(
            reopen_generation,
            self.get_object(),
            request.user,
            values.validated_data['reason'],
        )
        return Response(self.get_serializer(generation).data)

    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):  # pylint: disable=unused-argument
        """Confirm a migrated fill really is one fill."""
        values = ReasonSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        generation = _run_domain_action(
            review_generation,
            self.get_object(),
            request.user,
            values.validated_data['reason'],
        )
        return Response(self.get_serializer(generation).data)

    @action(detail=True, methods=['get'], url_path='cost-breakdown')
    def cost_breakdown(self, request, pk=None):  # pylint: disable=unused-argument
        """Trace this fill's media cost to the seedlings it raised."""
        return Response(
            _run_domain_action(generation_cost_breakdown, self.get_object()),
        )


generation_router = routers.SimpleRouter()
generation_router.register(
    r'seedtraygenerations',
    SeedTrayGenerationViewSet,
    basename='seedtraygeneration',
)
