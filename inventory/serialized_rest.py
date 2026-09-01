"""Read-only serialized inventory resources and explicit unit actions."""

# pylint: disable=duplicate-code

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from locations.models import Location
from workspaces.scoping import (
    CurrentWorkspaceSerializerMixin,
    CurrentWorkspaceViewSetMixin,
)

from .ledger import (
    UnitMovementRequest,
    UnitReconciliationRequest,
    discard_numbering,
    post_unit_movement,
    reconcile_unit_opening,
    unit_is_in_use,
    unit_physical_state,
)
from .models import InventoryUnit, StockLot, StockMovement
from .rest_query import parse_boolean, parse_integer


def _model_errors(error):
    """Translate model validation errors into REST response details."""
    if hasattr(error, 'message_dict'):
        return error.message_dict
    return error.messages


def _run_domain_action(function, *args):
    """Invoke a unit service with field-friendly API errors."""
    try:
        return function(*args)
    except DjangoValidationError as exc:
        raise ValidationError(_model_errors(exc)) from exc


class ActionSerializer(serializers.Serializer):
    """Validation-only serializer base for unit actions."""

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class UnitMovementSerializer(serializers.ModelSerializer):
    """Serialize the immutable result of one unit action."""

    reversed_by = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = [
            'pk',
            'lot',
            'unit',
            'movement_type',
            'quantity',
            'source',
            'destination',
            'occurred_at',
            'created_by',
            'reason',
            'reference',
            'reversal_of',
            'reversed_by',
            'created',
        ]
        read_only_fields = fields

    def get_reversed_by(self, movement):
        """Return the inverse row when one exists."""
        try:
            return movement.reversal.pk
        except StockMovement.DoesNotExist:
            return None


class InventoryUnitSerializer(serializers.ModelSerializer):
    """Serialize exact unit identity, provenance, and derived state."""

    item_name = serializers.CharField(source='item.name', read_only=True)
    receipt_line = serializers.IntegerField(
        source='source_lot.receipt_line_id',
        read_only=True,
    )
    physical_state = serializers.SerializerMethodField()
    in_use = serializers.SerializerMethodField()
    reconciliation_required = serializers.SerializerMethodField()
    movement_ids = serializers.SerializerMethodField()

    class Meta:
        model = InventoryUnit
        fields = [
            'pk',
            'item',
            'item_name',
            'source_lot',
            'receipt_line',
            'asset_code',
            'acquisition_cost',
            'currency_code',
            'current_location',
            'physical_state',
            'in_use',
            'reconciliation_required',
            'active',
            'movement_ids',
            'created',
            'updated',
        ]
        read_only_fields = fields

    def get_physical_state(self, unit):
        """Return the unit's movement-derived physical state."""
        return unit_physical_state(unit)

    def get_in_use(self, unit):
        """Return whether cultivation currently occupies this unit."""
        return unit_is_in_use(unit)

    def get_reconciliation_required(self, unit):
        """Flag migrated opening identities whose facts remain unknown."""
        is_opening = unit.source_lot.origin == StockLot.Origin.OPENING
        return is_opening and unit.acquisition_cost is None and not hasattr(unit, 'opening_reconciliation')

    def get_movement_ids(self, unit):
        """Return immutable movement history in posting order."""
        return list(unit.movements.order_by('occurred_at', 'pk').values_list('pk', flat=True))


class UnitMovementActionSerializer(
    CurrentWorkspaceSerializerMixin,
    ActionSerializer,
):  # pylint: disable=abstract-method
    """Validate a destination and audit metadata for one unit action."""

    destination = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        allow_null=True,
        required=False,
    )
    occurred_at = serializers.DateTimeField(required=False)
    reason = serializers.CharField(allow_blank=True, required=False, default='')
    reference = serializers.CharField(allow_blank=True, required=False, default='')
    workspace_field_lookups = {'destination': 'workspace'}


class UnitReconciliationActionSerializer(
    CurrentWorkspaceSerializerMixin,
    ActionSerializer,
):  # pylint: disable=abstract-method
    """Validate one legacy unit's opening cost and physical location."""

    acquisition_cost = serializers.DecimalField(
        max_digits=18,
        decimal_places=4,
        min_value=Decimal('0'),
    )
    destination = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
    )
    occurred_at = serializers.DateTimeField(required=False)
    reason = serializers.CharField(allow_blank=False, trim_whitespace=True)
    workspace_field_lookups = {'destination': 'workspace'}


class InventoryUnitViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ReadOnlyModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Read serialized stock and post exact-unit physical actions."""

    queryset = InventoryUnit.objects.select_related(
        'item',
        'source_lot__receipt_line',
        'current_location',
    ).prefetch_related('movements')
    serializer_class = InventoryUnitSerializer

    def get_queryset(self):
        """Apply identity, location, state, and cultivation filters."""
        queryset = super().get_queryset()
        item = parse_integer(self.request.query_params.get('item'), 'item')
        location = parse_integer(
            self.request.query_params.get('location'),
            'location',
        )
        active = parse_boolean(self.request.query_params.get('active'), 'active')
        if item is not None:
            queryset = queryset.filter(item_id=item)
        if location is not None:
            queryset = queryset.filter(current_location_id=location)
        if active is not None:
            queryset = queryset.filter(active=active)
        asset_code = self.request.query_params.get('asset_code', '').strip()
        if asset_code:
            queryset = queryset.filter(asset_code__icontains=asset_code)
        queryset = self._filter_state(queryset)
        return self._filter_in_use(queryset)

    def _filter_state(self, queryset):
        """Filter by the movement-derived state when requested."""
        state = self.request.query_params.get('physical_state')
        if not state:
            return queryset
        valid_states = {
            'available',
            'quarantined',
            'lost',
            'retired',
            'dispatched',
            'returned',
        }
        if state not in valid_states:
            raise ValidationError({
                'physical_state': 'Select a valid physical state.',
            })
        return queryset.filter(
            pk__in=[
                unit.pk
                for unit in queryset
                if unit_physical_state(unit) == state
            ],
        )

    def _filter_in_use(self, queryset):
        """Filter by cultivation occupancy when requested."""
        in_use = parse_boolean(self.request.query_params.get('in_use'), 'in_use')
        if in_use is None:
            return queryset
        return queryset.filter(
            pk__in=[
                unit.pk
                for unit in queryset
                if unit_is_in_use(unit) == in_use
            ],
        )

    def _post_action(self, request, movement_type, require_reason=False):
        """Validate and post one typed exact-unit action."""
        serializer = UnitMovementActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if require_reason and not values['reason'].strip():
            raise ValidationError({'reason': 'A reason is required.'})
        movement = _run_domain_action(
            post_unit_movement,
            self.get_current_workspace(),
            request.user,
            UnitMovementRequest(
                unit=self.get_object(),
                movement_type=movement_type,
                destination=values.get('destination'),
                occurred_at=values.get('occurred_at'),
                reason=values['reason'],
                reference=values['reference'],
            ),
        )
        return Response(
            UnitMovementSerializer(movement).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'])
    def transfer(self, request, pk=None):  # pylint: disable=unused-argument
        """Transfer one on-hand unit to a different location."""
        return self._post_action(request, StockMovement.MovementType.TRANSFER)

    @action(detail=True, methods=['post'])
    def loss(self, request, pk=None):  # pylint: disable=unused-argument
        """Record that one on-hand unit is lost."""
        return self._post_action(
            request,
            StockMovement.MovementType.ADJUSTMENT_LOSS,
            require_reason=True,
        )

    @action(detail=True, methods=['post'])
    def retire(self, request, pk=None):  # pylint: disable=unused-argument
        """Retire one on-hand unit from service."""
        return self._post_action(
            request,
            StockMovement.MovementType.WASTE,
            require_reason=True,
        )

    @action(detail=True, methods=['post'], url_path='return')
    def return_unit(self, request, pk=None):  # pylint: disable=unused-argument
        """Return one lost or retired unit to a physical location."""
        return self._post_action(
            request,
            StockMovement.MovementType.ADJUSTMENT_GAIN,
            require_reason=True,
        )

    @action(detail=True, methods=['delete'])
    def discard(self, request, pk=None):  # pylint: disable=unused-argument
        """Undo a numbering that was a typo, before the unit was used."""
        _run_domain_action(
            discard_numbering,
            self.get_current_workspace(),
            self.get_object(),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='reconcile-opening')
    def reconcile_opening(self, request, pk=None):  # pylint: disable=unused-argument
        """Audit the cost and real location of one legacy opening unit."""
        serializer = UnitReconciliationActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        reconciliation = _run_domain_action(
            reconcile_unit_opening,
            self.get_current_workspace(),
            request.user,
            UnitReconciliationRequest(
                unit=self.get_object(),
                acquisition_cost=values['acquisition_cost'],
                destination=values['destination'],
                occurred_at=values.get('occurred_at'),
                reason=values['reason'],
            ),
        )
        reconciliation.unit.refresh_from_db()
        return Response(self.get_serializer(reconciliation.unit).data)
