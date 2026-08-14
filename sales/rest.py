"""Workspace-scoped customer, order, and reservation REST workflows."""

# pylint: disable=too-many-ancestors

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import QueryDict
from rest_framework import mixins, routers, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from plantings.register import parse_register_filters, register_queryset
from workspaces.models import Workspace
from workspaces.scoping import (
    CurrentWorkspaceSerializerMixin,
    CurrentWorkspaceViewSetMixin,
    RequireWorkspaceModeMixin,
)

from .models import Customer, ReservationEvent, SalesOrder, SalesOrderAllocation, SalesOrderLine
from .services import (
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
    """One exact pending or historical allocation."""

    events = ReservationEventSerializer(many=True, read_only=True)
    asset_code = serializers.CharField(source='inventory_unit.asset_code', read_only=True, allow_null=True)

    class Meta:
        model = SalesOrderAllocation
        fields = ['pk', 'plant', 'inventory_unit', 'asset_code', 'status', 'expires_at', 'created_by', 'created', 'updated', 'events']
        read_only_fields = fields


class SalesOrderLineSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """Editable commercial terms and read-only concrete allocations."""

    allocations = AllocationSerializer(many=True, read_only=True)
    prices_include_tax = serializers.BooleanField(source='order.prices_include_tax', read_only=True)

    class Meta:
        model = SalesOrderLine
        fields = [
            'pk', 'order', 'line_type', 'variety', 'tray_item', 'description',
            'quantity', 'unit_price', 'tax_rate', 'discount_type', 'discount_value',
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
        'tray_item': 'workspace',
    }

    def validate(self, attrs):
        """Fill a new line's tax rate from its immutable order context."""
        order = self.instance.order if self.instance else attrs['order']
        if order.status not in {SalesOrder.Status.QUOTE, SalesOrder.Status.DRAFT}:
            raise ValidationError({'order': 'Confirmed commercial terms are immutable.'})
        if self.instance is None and 'tax_rate' not in attrs:
            attrs['tax_rate'] = attrs['order'].workspace.default_tax_rate
        return attrs


class SalesOrderSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """Order header, snapshotted totals, exact allocations, and margin."""

    status = serializers.ChoiceField(choices=SalesOrder.Status.choices, required=False)
    lines = SalesOrderLineSerializer(many=True, read_only=True)
    margin = serializers.SerializerMethodField()

    class Meta:
        model = SalesOrder
        fields = [
            'pk', 'order_number', 'customer', 'status', 'quote_date', 'order_date',
            'requested_date', 'currency_code', 'prices_include_tax', 'notes',
            'gross_ex_tax', 'discount_total_ex_tax', 'subtotal_ex_tax',
            'tax_total', 'total_incl_tax', 'created_by', 'created', 'updated',
            'lines', 'margin',
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


class TargetSelectionSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """Exact IDs or register filters for a selection preview."""

    line = serializers.IntegerField(min_value=1)
    plant_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, default=list)
    unit_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, default=list)
    filters = serializers.DictField(required=False, default=dict)

    def validate(self, attrs):
        """Require one unambiguous selection source."""
        sources = sum(bool(attrs[name]) for name in ('plant_ids', 'unit_ids', 'filters'))
        if sources != 1:
            raise ValidationError('Select exactly one of plant_ids, unit_ids, or filters.')
        return attrs


class AllocationRequestSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """Concrete targets to attach to one order line."""

    line = serializers.IntegerField(min_value=1)
    plant_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, default=list)
    unit_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, default=list)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate(self, attrs):
        """Require plants or units, never a mixture."""
        if bool(attrs['plant_ids']) == bool(attrs['unit_ids']):
            raise ValidationError('Select plants or serialized units.')
        return attrs


class AllocationIdsSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """A non-empty set of allocation identities."""

    allocations = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False)
    reason = serializers.CharField(required=False, allow_blank=True, default='')


class ReasonSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """Optional audit reason for a document transition."""

    reason = serializers.CharField(required=False, allow_blank=True, default='')


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
    queryset = SalesOrderLine.objects.select_related('order', 'variety', 'tray_item').prefetch_related('allocations__events')
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
            return order.lines.select_related('order', 'variety', 'tray_item').get(pk=line_id)
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
        result = _run(preview_targets, line, data['plant_ids'], data['unit_ids'])
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


router = routers.SimpleRouter()
router.register(r'customers', CustomerViewSet)
router.register(r'order-lines', SalesOrderLineViewSet)
router.register(r'orders', SalesOrderViewSet)
