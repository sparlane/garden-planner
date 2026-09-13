"""Workspace-scoped actions for numbered and counted pot fills."""
# pylint: disable=duplicate-code,abstract-method

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework_nested import routers

from common.rest_query import parse_integer
from inventory.models import InventoryUnit, StockLot
from locations.models import Location
from plantings.counted_fills import plant_counted_fill
from plantings.fill_numbering import number_counted_pot
from plantings.rest import SpecificPlantLocationSerializer
from workspaces.models import get_current_workspace
from workspaces.scoping import CurrentWorkspaceSerializerMixin, CurrentWorkspaceViewSetMixin

from .container_fills import clean_pot_fill, lock_pot_fills, open_counted_fill, open_numbered_fill, open_numbered_fills, reopen_pot_fill
from .generation_rest import ActionSerializer, MediaDispositionSerializer, ReasonSerializer, SeedTrayGenerationEventSerializer, SeedTrayGenerationResidualSerializer
from .generations import CloseRequest, MediaDisposition, contents_digest
from .models import SeedTrayGeneration
from .pot_media import pot_fill_cost_breakdown, pot_fill_remaining_media


# One request fills one bench. Every pot costs a handful of queries under a row
# lock held until the whole claim commits, so an unbounded selection would hold
# the pot ledger against every other writer for as long as it took to walk it.
MAX_BENCH_POTS = 500


def _run(function, *args, **kwargs):
    """Keep model validation errors on the API's normal field-error contract."""
    try:
        return function(*args, **kwargs)
    except DjangoValidationError as error:
        raise ValidationError(error.message_dict if hasattr(error, 'message_dict') else error.messages) from error


class OpenPotFillSerializer(CurrentWorkspaceSerializerMixin, ActionSerializer):
    """One numbered pot, or a whole count of anonymous pots at a location."""

    inventory_unit = serializers.PrimaryKeyRelatedField(queryset=InventoryUnit.objects.all(), required=False, allow_null=True, default=None)
    stock_lot = serializers.PrimaryKeyRelatedField(queryset=StockLot.objects.all(), required=False, allow_null=True, default=None)
    source_location = serializers.PrimaryKeyRelatedField(queryset=Location.objects.all(), required=False, allow_null=True, default=None)
    container_count = serializers.IntegerField(min_value=1, required=False, allow_null=True, default=None)
    opened_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    notes = serializers.CharField(required=False, allow_blank=True, default='')

    workspace_field_lookups = {'inventory_unit': 'workspace', 'stock_lot': 'workspace', 'source_location': 'workspace'}

    def validate(self, data):  # pylint: disable=arguments-renamed
        if bool(data['inventory_unit']) == bool(data['stock_lot']):
            raise ValidationError('Choose exactly one numbered container or pot lot.')
        if data['stock_lot']:
            if data['source_location'] is None or data['container_count'] is None:
                raise ValidationError('Counted fills need a source location and container count.')
        elif data['source_location'] is not None or data['container_count'] not in (None, 1):
            raise ValidationError('A numbered fill uses its container location and a count of one.')
        return data


class OpenNumberedFillsSerializer(ActionSerializer):
    """A bench of numbered pots, claimed by the numbers the operator can read.

    A pot's number is its inventory identity, which is unique across the whole
    nursery rather than within one catalog item, so a selection names pots and
    nothing else: which item each belongs to is already settled by which pots
    they are. Refusals name the pot, because the answer to one bad number in a
    range of forty is to fix that number.
    """

    inventory_units = serializers.PrimaryKeyRelatedField(
        queryset=InventoryUnit.objects.all(), many=True, allow_empty=False,
    )
    opened_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    notes = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_inventory_units(self, value):
        """Refuse a selection this workspace cannot fill, naming every pot in it.

        The workspace filter the single-target serializer gets from its field
        lookups does not reach a `many` relation — the queryset lives on the
        child field — so the boundary is checked here as well as in the unit
        locks the service takes, which are what make it race-proof.
        """
        numbers = [unit.pk for unit in value]
        if len(numbers) != len(set(numbers)):
            raise ValidationError('Select each pot once.')
        if len(numbers) > MAX_BENCH_POTS:
            raise ValidationError(f'Fill at most {MAX_BENCH_POTS} pots in one request.')
        workspace = get_current_workspace()
        foreign = [unit.pk for unit in value if unit.workspace_id != workspace.pk]
        if foreign:
            raise ValidationError([f'Pot #{number} is not in this nursery.' for number in foreign])
        return value


class CleanPotFillSerializer(ReasonSerializer):
    """Confirm the media dispositions shown by the fill contents preview."""

    occurred_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    media = MediaDispositionSerializer(many=True, required=False, default=list)
    digest = serializers.CharField(allow_blank=False)


class PlantCountedFillSerializer(ActionSerializer):
    """Select plants without asking the operator to invent pot identities."""

    plants = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False)
    started = serializers.DateTimeField(required=False, allow_null=True, default=None)
    override_reason = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_plants(self, value):
        """A selected plant may occur only once in a potting request."""
        if len(value) != len(set(value)):
            raise ValidationError('Select each plant once.')
        return value


class NumberCountedPotSerializer(ActionSerializer):
    """Name the plant whose existing anonymous pot needs a label identity."""

    plant = serializers.IntegerField(min_value=1)


class PotResidualSerializer(SeedTrayGenerationResidualSerializer):
    """Keep original dispositions visible beside the corrections retiring them."""

    correction_event = serializers.ReadOnlyField(source='pot_correction.event_id', default=None)

    class Meta(SeedTrayGenerationResidualSerializer.Meta):
        fields = SeedTrayGenerationResidualSerializer.Meta.fields + ['correction_event']
        read_only_fields = fields


class PotFillSerializer(serializers.ModelSerializer):
    """Read the fill identity and its immutable clean/correction history."""

    events = SeedTrayGenerationEventSerializer(many=True, read_only=True)
    residuals = PotResidualSerializer(many=True, read_only=True)

    class Meta:
        model = SeedTrayGeneration
        fields = ['pk', 'code', 'sequence', 'inventory_unit', 'stock_lot', 'source_location',
                  'container_count', 'plant_share_count', 'status', 'review_state', 'opened_at',
                  'closed_at', 'close_reason', 'notes', 'created_by', 'closed_by', 'events', 'residuals']
        read_only_fields = fields


@transaction.atomic
def _contents(workspace, fill):
    """Give the preview one coherent view under the same stock locks as cleaning."""
    fill = lock_pot_fills(workspace, [fill.pk])[0]
    media = pot_fill_remaining_media(fill) if fill.status == SeedTrayGeneration.Status.OPEN else []
    return {
        'fill': fill.pk, 'status': fill.status,
        'plants': list(fill.plant_locations.filter(ended__isnull=True).values_list('specific_plant_id', flat=True)),
        'numbered_plants': list(fill.plant_locations.filter(ended__isnull=True, numbered_at__isnull=False).values_list('specific_plant_id', flat=True)),
        'digest': contents_digest({'plants': [], 'seeds': [], 'media': media}),
        'media': [{'lot': row['lot'].pk, 'item': row['lot'].item_id,
                   'base_quantity': f'{row["base_quantity"]:.9f}', 'base_unit': row['base_unit'],
                   'unit_cost': None if row['unit_cost'] is None else format(row['unit_cost'], 'f')}
                  for row in media],
        'costs': {key: format(value, 'f') if isinstance(value, Decimal) else value
                  for key, value in pot_fill_cost_breakdown(fill).items()},
    }


class PotFillViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """Open, plant, clean and correct pot fills through the existing domain services."""

    queryset = SeedTrayGeneration.objects.filter(tray__isnull=True).select_related(
        'inventory_unit', 'stock_lot', 'source_location',
    ).prefetch_related('events', 'residuals__pot_correction').order_by('-opened_at', '-pk')
    serializer_class = PotFillSerializer
    http_method_names = ['get', 'post', 'head', 'options']
    bind_workspace_on_create = False

    def get_queryset(self):
        """Find fills for a container or lot, with an optional lifecycle status."""
        queryset = super().get_queryset()
        for field in ('inventory_unit', 'stock_lot', 'source_location'):
            value = parse_integer(self.request.query_params.get(field), field)
            if value is not None:
                queryset = queryset.filter(**{f'{field}_id': value})
        state = self.request.query_params.get('status')
        if state is not None:
            if state not in SeedTrayGeneration.Status.values:
                raise ValidationError({'status': 'Choose open or closed.'})
            queryset = queryset.filter(status=state)
        return queryset

    def create(self, request, *args, **kwargs):
        """Claim the pots without consuming them or inventing numbered identities."""
        payload = OpenPotFillSerializer(data=request.data, context={'request': request})
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        common = {'opened_at': data['opened_at'], 'notes': data['notes']}
        if data['inventory_unit']:
            fill = _run(open_numbered_fill, self.get_current_workspace(), request.user, data['inventory_unit'], **common)
        else:
            fill = _run(open_counted_fill, self.get_current_workspace(), request.user,
                        data['stock_lot'], data['source_location'], data['container_count'], **common)
        return Response(self.get_serializer(fill).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='open-numbered')
    def open_numbered(self, request):
        """Fill a bench of numbered pots in one claim, without consuming them."""
        payload = OpenNumberedFillsSerializer(data=request.data, context={'request': request})
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        fills = _run(open_numbered_fills, self.get_current_workspace(), request.user,
                     data['inventory_units'], opened_at=data['opened_at'], notes=data['notes'])
        return Response(self.get_serializer(fills, many=True).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'])
    def contents(self, request, pk=None):  # pylint: disable=unused-argument
        """Preview held plants, remaining media, costs and the clean confirmation digest."""
        return Response(_run(_contents, self.get_current_workspace(), self.get_object()))

    @action(detail=True, methods=['post'])
    def clean(self, request, pk=None):  # pylint: disable=unused-argument
        """Dispose only of the media the operator confirmed was still held."""
        payload = CleanPotFillSerializer(data=request.data, context={'request': request})
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        media = tuple(MediaDisposition(row['lot'], row['quantity'], row['disposition'], row['reason'], row['destination']) for row in data['media'])
        fill = _run(clean_pot_fill, self.get_current_workspace(), request.user, self.get_object(),
                    CloseRequest(reason=data['reason'], occurred_at=data['occurred_at'], digest=data['digest'], media=media))
        return Response(self.get_serializer(fill).data)

    @action(detail=True, methods=['post'])
    def reopen(self, request, pk=None):  # pylint: disable=unused-argument
        """Correct a mistaken clean, retaining its original event and residual rows."""
        payload = ReasonSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        fill = _run(reopen_pot_fill, self.get_current_workspace(), request.user, self.get_object(), payload.validated_data['reason'])
        return Response(self.get_serializer(fill).data)

    @action(detail=True, methods=['post'], url_path='number-pot')
    def number_pot(self, request, pk=None):  # pylint: disable=unused-argument
        """Number one occupied counted pot without allocating its media."""
        payload = NumberCountedPotSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        placement = _run(number_counted_pot, self.get_current_workspace(), request.user, self.get_object(), payload.validated_data['plant'])
        return Response(SpecificPlantLocationSerializer(placement).data)

    @action(detail=True, methods=['post'])
    def plant(self, request, pk=None):  # pylint: disable=unused-argument
        """Place the selected plants in unused counted pots as one transaction."""
        payload = PlantCountedFillSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        placements = _run(plant_counted_fill, self.get_current_workspace(), request.user, self.get_object(),
                          data['plants'], started=data['started'], override_reason=data['override_reason'])
        return Response(SpecificPlantLocationSerializer(placements, many=True).data, status=status.HTTP_201_CREATED)


pot_fill_router = routers.SimpleRouter()
pot_fill_router.register(r'container-fills', PotFillViewSet, basename='container-fill')
