"""Workspace-scoped customer, order, and reservation REST workflows."""

# pylint: disable=too-many-ancestors,missing-class-docstring
# pylint: disable=missing-function-docstring,abstract-method

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import QueryDict
from rest_framework import mixins, routers, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from health.models import HealthObservation, HealthObservationType
from inventory.models import StockLot
from locations.models import Location
from plantings.register import parse_register_filters, register_queryset
from workspaces.models import Workspace
from workspaces.scoping import (
    CurrentWorkspaceSerializerMixin,
    CurrentWorkspaceViewSetMixin,
    RequireWorkspaceModeMixin,
)

from .commerce import (
    order_commerce_summary,
    post_fulfillment,
    post_refund,
    post_return,
    record_payment,
    reverse_fulfillment,
    reverse_payment,
    reverse_refund,
    reverse_return,
)
from .models import (
    Customer,
    Fulfillment,
    FulfillmentLine,
    FulfillmentPackagingLine,
    Payment,
    Refund,
    RefundLine,
    ReservationEvent,
    SalesOrder,
    SalesOrderAllocation,
    SalesOrderLine,
    SalesReturn,
    SalesReturnLine,
)
from .services import (
    CohortRequest,
    LotRequest,
    allocate_targets,
    cancel_order,
    close_reservations,
    confirm_order,
    create_order,
    deallocate_pending,
    order_margin,
    preview_targets,
    quote_to_draft,
    update_pricing_mode,
)


def _model_errors(error):
    """Translate domain validation into field-oriented API errors."""
    return error.message_dict if hasattr(error, 'message_dict') else error.messages


def _run(function, *args, **kwargs):
    """Run one domain command with REST-native validation errors."""
    try:
        return function(*args, **kwargs)
    except DjangoValidationError as exc:
        raise ValidationError(_model_errors(exc)) from exc


def _lot_requests(data):
    """Return the validated counted draws as the service's own request type."""
    return [
        LotRequest(row['lot'], row['location'], row['quantity'])
        for row in data['lot_requests']
    ]


def _cohort_requests(data):
    """Return the validated cohort draws as the service's own request type."""
    return [
        CohortRequest(row['cohort'], row['quantity'], row['expected_revision'])
        for row in data['cohort_requests']
    ]


class CustomerSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """Editable customer details without exposing workspace ownership."""

    class Meta:
        model = Customer
        fields = ['pk', 'name', 'email', 'phone', 'billing_address', 'delivery_address', 'notes', 'active', 'created', 'updated']
        read_only_fields = ['created', 'updated']


class ReservationEventSerializer(serializers.ModelSerializer):
    """Read-only reservation history."""

    class Meta:
        model = ReservationEvent
        fields = ['pk', 'event_type', 'occurred_at', 'reason', 'created_by', 'created']
        read_only_fields = fields


class AllocationSerializer(serializers.ModelSerializer):
    """One pending or historical promise: an identity, or a count on a pool."""

    events = ReservationEventSerializer(many=True, read_only=True)
    asset_code = serializers.CharField(source='inventory_unit.asset_code', read_only=True, allow_null=True)
    competing_claims = serializers.SerializerMethodField()

    class Meta:
        model = SalesOrderAllocation
        fields = ['pk', 'plant', 'inventory_unit', 'asset_code', 'stock_lot', 'plant_cohort', 'source_location', 'quantity', 'status', 'expires_at', 'created_by', 'created', 'updated', 'events', 'competing_claims']
        read_only_fields = fields

    def get_competing_claims(self, allocation):
        """Name other open orders promising this allocation's exact target.

        A counted draw competes over a pool rather than over a thing, so the
        orders holding parts of the same lot or cohort are all legitimate;
        naming them is context for an operator, never a conflict.
        """
        identity = {f'{allocation.target_kind}_id': getattr(allocation, f'{allocation.target_kind}_id')}
        if allocation.stock_lot_id:
            identity['source_location_id'] = allocation.source_location_id
        claims = (
            SalesOrderAllocation.objects
            .filter(
                **identity,
                status__in=[
                    SalesOrderAllocation.Status.PENDING,
                    SalesOrderAllocation.Status.RESERVED,
                ],
            )
            .exclude(line__order_id=allocation.line.order_id)
            .select_related('line__order')
            .order_by('line__order__order_number', 'pk')
        )
        return [
            {
                'order': claim.line.order_id,
                'order_number': claim.line.order.order_number,
                'status': claim.status,
                'quantity': claim.quantity,
            }
            for claim in claims
        ]


class SalesOrderLineSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """Editable commercial terms and read-only concrete allocations."""

    allocations = AllocationSerializer(many=True, read_only=True)
    prices_include_tax = serializers.BooleanField(source='order.prices_include_tax', read_only=True)

    class Meta:
        model = SalesOrderLine
        fields = [
            'pk', 'order', 'line_type', 'variety', 'item', 'description',
            'quantity', 'unit_price', 'tax_rate', 'tax_treatment',
            'discount_type', 'discount_value',
            'prices_include_tax', 'gross_ex_tax', 'discount_ex_tax',
            'subtotal_ex_tax', 'tax_total', 'total_incl_tax', 'allocations',
            'created', 'updated',
        ]
        read_only_fields = [
            'gross_ex_tax', 'discount_ex_tax', 'subtotal_ex_tax', 'tax_total',
            'total_incl_tax', 'created', 'updated',
        ]

    workspace_field_lookups = {
        'order': 'workspace',
        'variety': 'workspace',
        'item': 'workspace',
    }

    def validate(self, attrs):
        """Fill a new line's tax rate from its immutable order context."""
        order = self.instance.order if self.instance else attrs['order']
        if order.status not in {SalesOrder.Status.QUOTE, SalesOrder.Status.DRAFT}:
            raise ValidationError({'order': 'Confirmed commercial terms are immutable.'})
        if self.instance is None and 'tax_rate' not in attrs:
            attrs['tax_rate'] = attrs['order'].workspace.default_tax_rate
        # A blank treatment is filled by the model from the rate: above zero is
        # a standard-rated supply by definition, and zero stays unclassified
        # rather than being guessed at.
        return attrs


class SalesOrderSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """Order header, snapshotted totals, exact allocations, and margin."""

    status = serializers.ChoiceField(choices=SalesOrder.Status.choices, required=False)
    lines = SalesOrderLineSerializer(many=True, read_only=True)
    margin = serializers.SerializerMethodField()
    commerce = serializers.SerializerMethodField()

    class Meta:
        model = SalesOrder
        fields = [
            'pk', 'order_number', 'customer', 'status', 'quote_date', 'order_date',
            'requested_date', 'currency_code', 'prices_include_tax', 'notes',
            'gross_ex_tax', 'discount_total_ex_tax', 'subtotal_ex_tax',
            'tax_total', 'total_incl_tax', 'created_by', 'created', 'updated',
            'lines', 'margin', 'commerce',
        ]
        read_only_fields = [
            'order_number', 'gross_ex_tax', 'discount_total_ex_tax',
            'subtotal_ex_tax', 'tax_total', 'total_incl_tax', 'created_by',
            'created', 'updated',
        ]
        extra_kwargs = {'currency_code': {'required': False}, 'prices_include_tax': {'required': False}}

    workspace_field_lookups = {'customer': 'workspace'}

    def get_margin(self, order):
        """Expose a clearly qualified ex-tax margin preview."""
        return order_margin(order)

    def get_commerce(self, order):
        """Expose physical, recognized, refunded, and cash totals separately."""
        return order_commerce_summary(order)

    def validate(self, attrs):
        """Reserve status changes for explicit transition actions."""
        if self.instance and 'status' in attrs:
            raise ValidationError({'status': 'Use an explicit order action.'})
        customer = attrs.get('customer')
        if customer is not None and not customer.active:
            raise ValidationError({'customer': 'Select an active customer.'})
        return attrs

    def create(self, validated_data):
        """Use the numbering service and snapshot workspace defaults."""
        request = self.context['request']
        return _run(create_order, self.context['workspace'], request.user, **validated_data)

    def update(self, instance, validated_data):
        """Recalculate all line snapshots when draft pricing mode changes."""
        validated_data.pop('status', None)
        pricing_mode = validated_data.pop('prices_include_tax', instance.prices_include_tax)
        if pricing_mode != instance.prices_include_tax:
            instance = _run(update_pricing_mode, instance, pricing_mode)
        return super().update(instance, validated_data)


class ActionSerializer(serializers.Serializer):
    """Validation-only serializer base for explicit commands."""

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class LotRequestSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One counted draw on a lot standing at one place."""

    lot = serializers.IntegerField(min_value=1)
    location = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1)


class CohortRequestSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One counted draw on a cohort, against the revision it was read at."""

    cohort = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1)
    expected_revision = serializers.IntegerField(min_value=1)


#: The four ways a selection can name stock. Listed once so the preview and
#: the allocation request cannot drift on which sources they accept.
SELECTION_SOURCES = ('plant_ids', 'unit_ids', 'lot_requests', 'cohort_requests')


def _selection_fields():
    """Return the selection sources as serializer fields."""
    return {
        'plant_ids': serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, default=list),
        'unit_ids': serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, default=list),
        'lot_requests': LotRequestSerializer(many=True, required=False, default=list),
        'cohort_requests': CohortRequestSerializer(many=True, required=False, default=list),
    }


class TargetSelectionSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """Exact IDs, counted draws, or register filters for a selection preview."""

    line = serializers.IntegerField(min_value=1)
    plant_ids, unit_ids, lot_requests, cohort_requests = _selection_fields().values()
    filters = serializers.DictField(required=False, default=dict)

    def validate(self, attrs):
        """Require one unambiguous selection source."""
        sources = sum(bool(attrs[name]) for name in SELECTION_SOURCES + ('filters',))
        if sources != 1:
            raise ValidationError('Select exactly one of plant_ids, unit_ids, lot_requests, cohort_requests, or filters.')
        return attrs


class AllocationRequestSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """Concrete targets to attach to one order line."""

    line = serializers.IntegerField(min_value=1)
    plant_ids, unit_ids, lot_requests, cohort_requests = _selection_fields().values()
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate(self, attrs):
        """Require exactly one kind of target, never a mixture."""
        if sum(bool(attrs[name]) for name in SELECTION_SOURCES) != 1:
            raise ValidationError('Select plants, serialized units, lot quantities, or cohort quantities.')
        return attrs


class AllocationIdsSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """A non-empty set of allocation identities."""

    allocations = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False)
    reason = serializers.CharField(required=False, allow_blank=True, default='')


class ReasonSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """Optional audit reason for a document transition."""

    reason = serializers.CharField(required=False, allow_blank=True, default='')


class CommerceRecordSerializer(serializers.ModelSerializer):
    """Shared immutable action state derived from reversal links."""

    status = serializers.SerializerMethodField()

    def get_status(self, record):
        if record.reversal_of_id:
            return 'reversal'
        if hasattr(record, 'reversal'):
            return 'reversed'
        return 'posted'


class FulfillmentLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = FulfillmentLine
        fields = [
            'pk', 'allocation', 'commercial_position', 'gross_ex_tax',
            'discount_ex_tax', 'subtotal_ex_tax', 'tax_total',
            'total_incl_tax', 'tax_treatment', 'cogs_amount', 'cogs_provisional',
            'currency_code', 'lifecycle_event', 'stock_movement',
        ]


class FulfillmentPackagingSerializer(serializers.ModelSerializer):
    class Meta:
        model = FulfillmentPackagingLine
        fields = [
            'pk', 'lot', 'source', 'quantity', 'base_unit', 'unit_cost',
            'cogs_amount', 'currency_code', 'stock_movement',
        ]


class FulfillmentSerializer(CommerceRecordSerializer):
    lines = FulfillmentLineSerializer(many=True, read_only=True)
    packaging_lines = FulfillmentPackagingSerializer(many=True, read_only=True)

    class Meta:
        model = Fulfillment
        fields = [
            'pk', 'fulfillment_number', 'fulfilled_at', 'status', 'notes',
            'operation_key', 'created_by', 'created', 'reversal_of', 'lines',
            'packaging_lines',
        ]


class PaymentSerializer(CommerceRecordSerializer):
    class Meta:
        model = Payment
        fields = [
            'pk', 'paid_on', 'amount', 'currency_code', 'method',
            'external_reference', 'account_reference', 'notes', 'status', 'operation_key',
            'created_by', 'created', 'reversal_of',
        ]


class SalesReturnLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesReturnLine
        fields = [
            'pk', 'fulfillment_line', 'outcome', 'destination',
            'lifecycle_event', 'return_movement', 'discard_movement',
        ]


class SalesReturnSerializer(CommerceRecordSerializer):
    lines = SalesReturnLineSerializer(many=True, read_only=True)

    class Meta:
        model = SalesReturn
        fields = [
            'pk', 'returned_at', 'reason', 'notes', 'status',
            'health_observation', 'quarantine_case', 'operation_key',
            'created_by', 'created', 'reversal_of', 'lines',
        ]


class RefundLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = RefundLine
        fields = [
            'pk', 'fulfillment_line', 'gross_ex_tax', 'discount_ex_tax',
            'subtotal_ex_tax', 'tax_total', 'total_incl_tax',
        ]


class RefundSerializer(CommerceRecordSerializer):
    lines = RefundLineSerializer(many=True, read_only=True)

    class Meta:
        model = Refund
        fields = [
            'pk', 'payment', 'sales_return', 'refunded_at', 'amount',
            'currency_code', 'reason', 'notes', 'status', 'operation_key',
            'account_reference',
            'created_by', 'created', 'reversal_of', 'lines',
        ]


class PackagingWriteSerializer(serializers.Serializer):
    lot = serializers.PrimaryKeyRelatedField(queryset=StockLot.objects.all())
    source = serializers.PrimaryKeyRelatedField(queryset=Location.objects.all())
    quantity = serializers.DecimalField(max_digits=18, decimal_places=9, min_value=0)


class FulfillmentWriteSerializer(ActionSerializer):
    operation_key = serializers.UUIDField()
    allocation_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), allow_empty=False,
    )
    packaging = PackagingWriteSerializer(many=True, required=False, default=list)
    fulfilled_at = serializers.DateTimeField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True, default='')


class PaymentWriteSerializer(ActionSerializer):
    operation_key = serializers.UUIDField()
    paid_on = serializers.DateField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=4, min_value=0)
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    external_reference = serializers.CharField(required=False, allow_blank=True, default='')
    account_reference = serializers.CharField(required=False, allow_blank=True, default='')
    notes = serializers.CharField(required=False, allow_blank=True, default='')


class ReturnItemWriteSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One dispatched item coming back, with what physically happens to it.

    `quantity` is optional and exists to be checked rather than obeyed: a
    counted dispatch returns whole or not at all, so naming a smaller figure
    earns a refusal that says how many actually shipped instead of a silent
    return of the lot.
    """

    fulfillment_line = serializers.PrimaryKeyRelatedField(
        queryset=FulfillmentLine.objects.all(),
    )
    outcome = serializers.ChoiceField(choices=SalesReturnLine.Outcome.choices)
    destination = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(), required=False, allow_null=True,
    )
    quantity = serializers.IntegerField(min_value=1, required=False, allow_null=True)


class ReturnWriteSerializer(ActionSerializer):
    operation_key = serializers.UUIDField()
    items = ReturnItemWriteSerializer(many=True, allow_empty=False)
    returned_at = serializers.DateTimeField(required=False)
    reason = serializers.CharField(allow_blank=False)
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    observation_type = serializers.PrimaryKeyRelatedField(
        queryset=HealthObservationType.objects.all(), required=False,
        allow_null=True,
    )
    severity = serializers.ChoiceField(
        choices=HealthObservation.Severity.choices, required=False, allow_null=True,
    )
    follow_up_due_at = serializers.DateTimeField(required=False, allow_null=True)


class RefundWriteSerializer(ActionSerializer):
    operation_key = serializers.UUIDField()
    payment = serializers.PrimaryKeyRelatedField(queryset=Payment.objects.all())
    sales_return = serializers.PrimaryKeyRelatedField(
        queryset=SalesReturn.objects.all(), required=False, allow_null=True,
    )
    fulfillment_lines = serializers.PrimaryKeyRelatedField(
        queryset=FulfillmentLine.objects.all(), many=True, allow_empty=False,
    )
    amount = serializers.DecimalField(max_digits=14, decimal_places=4, min_value=0)
    refunded_at = serializers.DateTimeField(required=False)
    reason = serializers.CharField(allow_blank=False)
    account_reference = serializers.CharField(required=False, allow_blank=True, default='')
    notes = serializers.CharField(required=False, allow_blank=True, default='')


class ReverseWriteSerializer(ActionSerializer):
    operation_key = serializers.UUIDField()
    occurred_at = serializers.DateTimeField(required=False)
    reason = serializers.CharField(allow_blank=False)


class CustomerViewSet(
    RequireWorkspaceModeMixin,
    CurrentWorkspaceViewSetMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Manage active and historical customers without deletion."""

    required_workspace_modes = (Workspace.Mode.NURSERY,)
    queryset = Customer.objects.order_by('name', 'pk')
    serializer_class = CustomerSerializer

    def get_queryset(self):
        """Optionally narrow the register by active state or name."""
        queryset = super().get_queryset()
        active = self.request.query_params.get('active')
        if active in {'true', 'false'}:
            queryset = queryset.filter(active=active == 'true')
        search = self.request.query_params.get('search', '').strip()
        return queryset.filter(name__icontains=search) if search else queryset


class SalesOrderLineViewSet(RequireWorkspaceModeMixin, CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):
    """Edit line terms only while their parent order remains editable."""

    required_workspace_modes = (Workspace.Mode.NURSERY,)
    workspace_lookup = 'order__workspace'
    bind_workspace_on_create = False
    queryset = SalesOrderLine.objects.select_related('order', 'variety', 'item').prefetch_related('allocations__events')
    serializer_class = SalesOrderLineSerializer

    def destroy(self, request, *args, **kwargs):
        """Translate the model's confirmed-term deletion guard."""
        _run(self.get_object().delete)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SalesOrderViewSet(RequireWorkspaceModeMixin, CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):
    """Read orders and perform explicit commercial/reservation transitions."""

    required_workspace_modes = (Workspace.Mode.NURSERY,)
    bind_workspace_on_create = False
    queryset = SalesOrder.objects.select_related('customer', 'workspace').prefetch_related('lines__allocations__events')
    serializer_class = SalesOrderSerializer

    def get_serializer_context(self):
        """Supply the workspace used for server-owned creation defaults."""
        context = super().get_serializer_context()
        context['workspace'] = self.get_current_workspace()
        return context

    def get_queryset(self):
        """Filter the order register by status and customer."""
        queryset = super().get_queryset()
        order_status = self.request.query_params.get('status')
        customer = self.request.query_params.get('customer')
        if order_status:
            queryset = queryset.filter(status=order_status)
        if customer:
            queryset = queryset.filter(customer_id=customer)
        return queryset

    def destroy(self, request, *args, **kwargs):
        """Translate the model's historical-order deletion guard."""
        _run(self.get_object().delete)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _line(self, order, line_id):
        """Resolve a line only within the action's already-scoped order."""
        try:
            return order.lines.select_related('order', 'variety', 'item').get(pk=line_id)
        except SalesOrderLine.DoesNotExist as exc:
            raise ValidationError({'line': 'Select a line from this order.'}) from exc

    @action(detail=True, methods=['post'], url_path='to-draft')
    def to_draft(self, request, pk=None):  # pylint: disable=unused-argument
        """Accept a quote into the draft order workflow."""
        order = _run(quote_to_draft, self.get_object())
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=['post'], url_path='allocation-preview')
    def allocation_preview(self, request, pk=None):  # pylint: disable=unused-argument
        """Resolve explicit IDs or plant-register filters without writing."""
        values = TargetSelectionSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        data = values.validated_data
        order = self.get_object()
        line = self._line(order, data['line'])
        if data['filters']:
            if line.line_type != SalesOrderLine.LineType.SEEDLING:
                raise ValidationError({'filters': 'Register filters select seedlings only.'})
            query = QueryDict('', mutable=True)
            for name, value in data['filters'].items():
                query.setlist(name, value if isinstance(value, list) else [str(value)])
            query['variety'] = str(line.variety_id)
            plant_ids = list(register_queryset(order.workspace, parse_register_filters(query)).values_list('pk', flat=True)[:5001])
            if len(plant_ids) > 5000:
                raise ValidationError({'filters': 'Narrow the selection to 5000 plants or fewer.'})
            data['plant_ids'] = plant_ids
        result = _run(
            preview_targets, line, data['plant_ids'], data['unit_ids'],
            _lot_requests(data), _cohort_requests(data),
        )
        return Response(result)

    @action(detail=True, methods=['post'])
    def allocate(self, request, pk=None):  # pylint: disable=unused-argument
        """Attach exact targets, reserving replacements when already confirmed."""
        values = AllocationRequestSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        data = values.validated_data
        line = self._line(self.get_object(), data['line'])
        allocations = _run(
            allocate_targets,
            line,
            request.user,
            data['plant_ids'],
            data['unit_ids'],
            _lot_requests(data),
            _cohort_requests(data),
            data.get('expires_at'),
        )
        return Response(AllocationSerializer(allocations, many=True).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def deallocate(self, request, pk=None):  # pylint: disable=unused-argument
        """Remove tentative selections before confirmation."""
        values = AllocationIdsSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        _run(deallocate_pending, self.get_object(), values.validated_data['allocations'])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):  # pylint: disable=unused-argument
        """Reserve every line's exact stock in one transaction."""
        order = _run(confirm_order, self.get_object(), request.user)
        return Response(self.get_serializer(order).data)

    def _close(self, request, action_name):
        values = AllocationIdsSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        data = values.validated_data
        allocations = _run(close_reservations, self.get_object(), request.user, data['allocations'], action_name, data['reason'])
        return Response(AllocationSerializer(allocations, many=True).data)

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):  # pylint: disable=unused-argument
        """Release selected unfulfilled reservations."""
        return self._close(request, 'release')

    @action(detail=True, methods=['post'])
    def expire(self, request, pk=None):  # pylint: disable=unused-argument
        """Explicitly expire selected overdue or unwanted reservations."""
        return self._close(request, 'expire')

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):  # pylint: disable=unused-argument
        """Cancel an incomplete order and release unfulfilled stock."""
        values = ReasonSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        order = _run(cancel_order, self.get_object(), request.user, values.validated_data['reason'])
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=['get', 'post'], url_path='fulfillments')
    def fulfillments(self, request, pk=None):  # pylint: disable=unused-argument
        """List fulfillment history or atomically post one dispatch."""
        order = self.get_object()
        if request.method == 'GET':
            rows = order.fulfillments.prefetch_related('lines', 'packaging_lines')
            return Response(FulfillmentSerializer(rows, many=True).data)
        values = FulfillmentWriteSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        row = _run(post_fulfillment, order, request.user, **values.validated_data)
        return Response(FulfillmentSerializer(row).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True, methods=['post'],
        url_path=r'fulfillments/(?P<document_id>[^/.]+)/reverse',
    )
    def reverse_fulfillment_record(self, request, document_id=None, pk=None):  # pylint: disable=unused-argument
        """Append a reversal for one fulfillment in this order."""
        order = self.get_object()
        original = order.fulfillments.filter(pk=document_id).first()
        if original is None:
            raise ValidationError({'fulfillment': 'Choose a fulfillment from this order.'})
        values = ReverseWriteSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        row = _run(reverse_fulfillment, original, request.user, **values.validated_data)
        return Response(FulfillmentSerializer(row).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get', 'post'], url_path='payments')
    def payments(self, request, pk=None):  # pylint: disable=unused-argument
        """List payment history or record operational cash."""
        order = self.get_object()
        if request.method == 'GET':
            return Response(PaymentSerializer(order.payments.all(), many=True).data)
        values = PaymentWriteSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        row = _run(record_payment, order, request.user, **values.validated_data)
        return Response(PaymentSerializer(row).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True, methods=['post'],
        url_path=r'payments/(?P<document_id>[^/.]+)/reverse',
    )
    def reverse_payment_record(self, request, document_id=None, pk=None):  # pylint: disable=unused-argument
        """Append a reversal for one payment in this order."""
        order = self.get_object()
        original = order.payments.filter(pk=document_id).first()
        if original is None:
            raise ValidationError({'payment': 'Choose a payment from this order.'})
        values = ReverseWriteSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        row = _run(reverse_payment, original, request.user, **values.validated_data)
        return Response(PaymentSerializer(row).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get', 'post'], url_path='returns')
    def returns(self, request, pk=None):  # pylint: disable=unused-argument
        """List physical returns or post explicit item outcomes."""
        order = self.get_object()
        if request.method == 'GET':
            return Response(SalesReturnSerializer(
                order.returns.prefetch_related('lines'), many=True,
            ).data)
        values = ReturnWriteSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        row = _run(post_return, order, request.user, **values.validated_data)
        return Response(SalesReturnSerializer(row).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True, methods=['post'],
        url_path=r'returns/(?P<document_id>[^/.]+)/reverse',
    )
    def reverse_return_record(self, request, document_id=None, pk=None):  # pylint: disable=unused-argument
        """Append a reversal for one physical return in this order."""
        order = self.get_object()
        original = order.returns.filter(pk=document_id).first()
        if original is None:
            raise ValidationError({'sales_return': 'Choose a return from this order.'})
        values = ReverseWriteSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        row = _run(reverse_return, original, request.user, **values.validated_data)
        return Response(SalesReturnSerializer(row).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get', 'post'], url_path='refunds')
    def refunds(self, request, pk=None):  # pylint: disable=unused-argument
        """List classified refunds or post a paid-value correction."""
        order = self.get_object()
        if request.method == 'GET':
            return Response(RefundSerializer(
                order.refunds.prefetch_related('lines'), many=True,
            ).data)
        values = RefundWriteSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        row = _run(post_refund, order, request.user, **values.validated_data)
        return Response(RefundSerializer(row).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True, methods=['post'],
        url_path=r'refunds/(?P<document_id>[^/.]+)/reverse',
    )
    def reverse_refund_record(self, request, document_id=None, pk=None):  # pylint: disable=unused-argument
        """Append a reversal for one refund in this order."""
        order = self.get_object()
        original = order.refunds.filter(pk=document_id).first()
        if original is None:
            raise ValidationError({'refund': 'Choose a refund from this order.'})
        values = ReverseWriteSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        row = _run(reverse_refund, original, request.user, **values.validated_data)
        return Response(RefundSerializer(row).data, status=status.HTTP_201_CREATED)


router = routers.SimpleRouter()
router.register(r'customers', CustomerViewSet)
router.register(r'order-lines', SalesOrderLineViewSet)
router.register(r'orders', SalesOrderViewSet)
