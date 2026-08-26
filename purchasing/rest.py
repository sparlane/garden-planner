"""REST resources and explicit commands for the purchasing subledger."""

# pylint: disable=abstract-method,missing-function-docstring,too-many-ancestors,too-many-lines,unused-argument

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from inventory.models import StockReceiptLine
from supplies.models import Supplier
from workspaces.models import get_current_workspace
from workspaces.scoping import CurrentWorkspaceSerializerMixin, CurrentWorkspaceViewSetMixin

from .models import (
    BusinessExpense,
    ExpenseCategory,
    PurchaseOrder,
    PurchaseOrderCancellation,
    PurchaseOrderLine,
    PurchaseRequisition,
    ReceiptMatch,
    SupplierInvoice,
    SupplierInvoiceCorrection,
    SupplierInvoiceLine,
    SupplierPayment,
    SupplierPaymentAllocation,
)
from .reports import purchasing_summary
from .services import (
    cancel_order,
    cancel_order_quantity,
    cancel_requisition,
    close_order,
    confirm_expense,
    confirm_invoice,
    confirm_order,
    create_invoice,
    create_order,
    invoice_state,
    issue_invoice_correction,
    match_receipt,
    order_line_state,
    record_supplier_payment,
    replace_invoice_draft,
    replace_order_draft,
    review_requisition,
    reverse_supplier_payment,
)


def _errors(error):
    return error.message_dict if hasattr(error, 'message_dict') else error.messages


def _run(command, *args, **kwargs):
    try:
        return command(*args, **kwargs)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(_errors(exc)) from exc


class ActionSerializer(serializers.Serializer):
    """A validation-only serializer for domain commands."""

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class PurchaseRequisitionSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """Read and write an editable purchase need."""

    workspace_field_lookups = {
        'item': 'workspace',
        'preferred_supplier': 'workspace',
        'source_issue': 'plan__workspace',
    }

    class Meta:
        model = PurchaseRequisition
        fields = [
            'pk', 'item', 'source_issue', 'required_on', 'quantity',
            'unit_code', 'preferred_supplier', 'estimated_total_incl_tax',
            'status', 'notes', 'created_by', 'reviewed_at', 'cancelled_at',
            'created', 'updated',
        ]
        read_only_fields = [
            'status', 'created_by', 'reviewed_at', 'cancelled_at', 'created', 'updated',
        ]


class RequisitionOrderSerializer(ActionSerializer):
    """Commercial terms used to convert one reviewed need into an order."""

    order_number = serializers.CharField()
    supplier = serializers.PrimaryKeyRelatedField(queryset=Supplier.objects.all())
    ordered_on = serializers.DateField()
    expected_on = serializers.DateField(required=False, allow_null=True)
    currency_code = serializers.CharField(max_length=3)
    unit_price_ex_tax = serializers.DecimalField(max_digits=18, decimal_places=4)
    tax_rate = serializers.DecimalField(max_digits=7, decimal_places=4)
    freight_ex_tax = serializers.DecimalField(max_digits=18, decimal_places=4, required=False, default=0)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_supplier(self, supplier):
        if supplier.workspace_id != get_current_workspace().pk:
            raise serializers.ValidationError('The supplier belongs to another workspace.')
        return supplier


class PurchaseOrderLineSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """Entered order terms plus derived delivery state."""

    state = serializers.SerializerMethodField()
    workspace_field_lookups = {
        'item': 'workspace',
        'requisition': 'workspace',
    }

    class Meta:
        model = PurchaseOrderLine
        fields = [
            'pk', 'item', 'requisition', 'description', 'quantity',
            'unit_code', 'base_quantity', 'unit_price_ex_tax', 'tax_rate',
            'freight_ex_tax', 'subtotal_ex_tax', 'tax_total',
            'total_incl_tax', 'cancelled_quantity', 'state',
        ]
        read_only_fields = [
            'pk', 'base_quantity', 'subtotal_ex_tax', 'tax_total',
            'total_incl_tax', 'cancelled_quantity', 'state',
        ]

    def get_state(self, line):
        return order_line_state(line)


class PurchaseOrderSerializer(serializers.ModelSerializer):
    """A purchase order with commercial and delivery reconciliation."""

    lines = PurchaseOrderLineSerializer(many=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            'pk', 'order_number', 'supplier', 'supplier_name', 'status',
            'ordered_on', 'expected_on', 'currency_code', 'subtotal_ex_tax',
            'freight_ex_tax', 'tax_total', 'total_incl_tax', 'notes',
            'created_by', 'confirmed_at', 'closed_at', 'cancelled_at',
            'created', 'updated', 'lines',
        ]
        read_only_fields = [
            'status', 'subtotal_ex_tax', 'freight_ex_tax', 'tax_total',
            'total_incl_tax', 'created_by', 'confirmed_at', 'closed_at',
            'cancelled_at', 'created', 'updated',
        ]


class PurchaseOrderWriteSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """Nested draft purchase-order input."""

    lines = PurchaseOrderLineSerializer(many=True, allow_empty=False)
    workspace_field_lookups = {'supplier': 'workspace'}

    class Meta:
        model = PurchaseOrder
        fields = [
            'pk', 'status', 'order_number', 'supplier', 'ordered_on', 'expected_on',
            'currency_code', 'notes', 'lines',
        ]
        read_only_fields = ['pk', 'status']

    def create(self, validated_data):
        lines = validated_data.pop('lines')
        request = self.context['request']
        return _run(
            create_order, get_current_workspace(), request.user,
            validated_data, lines,
        )

    def update(self, instance, validated_data):
        lines = validated_data.pop('lines')
        return _run(replace_order_draft, instance, validated_data, lines)


class CancelQuantitySerializer(ActionSerializer):
    """A partial or complete order cancellation."""

    line = serializers.PrimaryKeyRelatedField(queryset=PurchaseOrderLine.objects.all())
    base_quantity = serializers.DecimalField(max_digits=24, decimal_places=9)
    reason = serializers.CharField()

    def validate_line(self, line):
        if line.order.workspace_id != get_current_workspace().pk:
            raise serializers.ValidationError('The order line belongs to another workspace.')
        return line


class MatchReceiptSerializer(ActionSerializer):
    """A posted receipt quantity matched to an ordered item."""

    order_line = serializers.PrimaryKeyRelatedField(queryset=PurchaseOrderLine.objects.all())
    receipt_line = serializers.PrimaryKeyRelatedField(queryset=StockReceiptLine.objects.all())
    base_quantity = serializers.DecimalField(max_digits=24, decimal_places=9)

    def validate(self, attrs):
        workspace_id = get_current_workspace().pk
        if attrs['order_line'].order.workspace_id != workspace_id:
            raise serializers.ValidationError({'order_line': 'The order line belongs to another workspace.'})
        if attrs['receipt_line'].receipt.workspace_id != workspace_id:
            raise serializers.ValidationError({'receipt_line': 'The receipt line belongs to another workspace.'})
        return attrs


class ReceiptMatchSerializer(serializers.ModelSerializer):
    """Read-only delivery reconciliation record."""

    class Meta:
        model = ReceiptMatch
        fields = ['pk', 'order_line', 'receipt_line', 'base_quantity', 'created_by', 'created']
        read_only_fields = fields


class PurchaseOrderCancellationSerializer(serializers.ModelSerializer):
    """Read-only quantity-cancellation history."""

    class Meta:
        model = PurchaseOrderCancellation
        fields = ['pk', 'line', 'base_quantity', 'reason', 'created_by', 'created']
        read_only_fields = fields


class ExpenseCategorySerializer(serializers.ModelSerializer):
    """A workspace expense category."""

    class Meta:
        model = ExpenseCategory
        fields = ['pk', 'name', 'active', 'notes']


class SupplierInvoiceLineSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """Invoice money reconciled to stock, freight, or an expense category."""

    workspace_field_lookups = {
        'purchase_order_line': 'order__workspace',
        'receipt_line': 'receipt__workspace',
        'expense_category': 'workspace',
    }

    class Meta:
        model = SupplierInvoiceLine
        fields = [
            'pk', 'description', 'purchase_order_line', 'receipt_line',
            'expense_category', 'is_freight', 'subtotal_ex_tax', 'tax_rate',
            'tax_total', 'total_incl_tax',
        ]
        read_only_fields = ['pk']


class SupplierInvoiceCorrectionSerializer(serializers.ModelSerializer):
    """Read-only append-only invoice correction."""

    class Meta:
        model = SupplierInvoiceCorrection
        fields = [
            'pk', 'invoice', 'kind', 'external_reference', 'corrected_on',
            'subtotal_ex_tax', 'tax_total', 'total_incl_tax', 'reason',
            'attachment_url', 'operation_key', 'created_by', 'created',
        ]
        read_only_fields = fields


class SupplierInvoiceSerializer(serializers.ModelSerializer):
    """A payable document with live correction and settlement state."""

    lines = SupplierInvoiceLineSerializer(many=True)
    corrections = SupplierInvoiceCorrectionSerializer(many=True, read_only=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    state = serializers.SerializerMethodField()

    class Meta:
        model = SupplierInvoice
        fields = [
            'pk', 'supplier', 'supplier_name', 'purchase_order',
            'external_reference', 'invoice_date', 'due_date', 'currency_code',
            'status', 'supplier_name_snapshot', 'supplier_address_snapshot',
            'supplier_gst_number_snapshot', 'subtotal_ex_tax', 'tax_total',
            'total_incl_tax', 'attachment_url', 'notes', 'created_by',
            'confirmed_at', 'cancelled_at', 'created', 'updated', 'lines',
            'corrections', 'state',
        ]
        read_only_fields = [
            'status', 'supplier_name_snapshot', 'supplier_address_snapshot',
            'supplier_gst_number_snapshot', 'subtotal_ex_tax', 'tax_total',
            'total_incl_tax', 'created_by', 'confirmed_at', 'cancelled_at',
            'created', 'updated', 'corrections', 'state',
        ]

    def get_state(self, invoice):
        return invoice_state(invoice)


class SupplierInvoiceWriteSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """Nested draft supplier-invoice input."""

    lines = SupplierInvoiceLineSerializer(many=True, allow_empty=False)
    workspace_field_lookups = {
        'supplier': 'workspace',
        'purchase_order': 'workspace',
    }

    class Meta:
        model = SupplierInvoice
        fields = [
            'pk', 'status', 'supplier', 'purchase_order', 'external_reference', 'invoice_date',
            'due_date', 'currency_code', 'attachment_url', 'notes', 'lines',
        ]
        read_only_fields = ['pk', 'status']

    def create(self, validated_data):
        lines = validated_data.pop('lines')
        request = self.context['request']
        return _run(
            create_invoice, get_current_workspace(), request.user,
            validated_data, lines,
        )

    def update(self, instance, validated_data):
        lines = validated_data.pop('lines')
        return _run(replace_invoice_draft, instance, validated_data, lines)


class InvoiceCorrectionWriteSerializer(ActionSerializer):
    """Input for a credit or debit against an invoice."""

    kind = serializers.ChoiceField(choices=SupplierInvoiceCorrection.Kind.choices)
    external_reference = serializers.CharField()
    corrected_on = serializers.DateField()
    subtotal_ex_tax = serializers.DecimalField(max_digits=18, decimal_places=4)
    tax_total = serializers.DecimalField(max_digits=18, decimal_places=4)
    total_incl_tax = serializers.DecimalField(max_digits=18, decimal_places=4)
    reason = serializers.CharField()
    attachment_url = serializers.URLField(required=False, allow_blank=True)


class SupplierPaymentAllocationSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """Amount of a payment applied to one supplier invoice."""

    workspace_field_lookups = {'invoice': 'workspace'}

    class Meta:
        model = SupplierPaymentAllocation
        fields = ['pk', 'invoice', 'amount']
        read_only_fields = ['pk']


class SupplierPaymentSerializer(serializers.ModelSerializer):
    """An immutable supplier payment or reversal."""

    allocations = SupplierPaymentAllocationSerializer(many=True, read_only=True)

    class Meta:
        model = SupplierPayment
        fields = [
            'pk', 'supplier', 'paid_on', 'amount', 'currency_code', 'method',
            'external_reference', 'notes', 'reversal_of', 'operation_key',
            'created_by', 'created', 'allocations',
        ]
        read_only_fields = fields


class SupplierPaymentWriteSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """One payment with zero or more explicit invoice allocations."""

    allocations = SupplierPaymentAllocationSerializer(many=True, required=False)
    workspace_field_lookups = {'supplier': 'workspace'}

    class Meta:
        model = SupplierPayment
        fields = [
            'pk', 'supplier', 'paid_on', 'amount', 'currency_code', 'method',
            'external_reference', 'notes', 'allocations',
        ]
        read_only_fields = ['pk']

    def create(self, validated_data):
        allocations = validated_data.pop('allocations', [])
        request = self.context['request']
        return _run(
            record_supplier_payment, get_current_workspace(), request.user,
            validated_data, allocations,
        )


class ReversePaymentSerializer(ActionSerializer):
    """Reason for an equal payment reversal."""

    reason = serializers.CharField()


class ReasonSerializer(ActionSerializer):
    """Required explanation for an auditable cancellation."""

    reason = serializers.CharField()


class BusinessExpenseSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """A non-stock business cost with optional operational allocation."""

    workspace_field_lookups = {
        'category': 'workspace',
        'supplier': 'workspace',
        'supplier_invoice': 'workspace',
        'garden_area': 'workspace',
        'crop_plan': 'workspace',
        'production_batch': 'workspace',
    }

    class Meta:
        model = BusinessExpense
        fields = [
            'pk', 'category', 'supplier', 'payee', 'incurred_on',
            'currency_code', 'subtotal_ex_tax', 'tax_total', 'total_incl_tax',
            'supplier_invoice', 'garden_area', 'crop_plan', 'production_batch',
            'allocation_type', 'allocation_reference', 'status',
            'attachment_url', 'notes', 'created_by', 'confirmed_at',
            'cancelled_at', 'created', 'updated',
        ]
        read_only_fields = [
            'status', 'created_by', 'confirmed_at', 'cancelled_at', 'created', 'updated',
        ]


class PurchaseRequisitionViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):
    """Manage purchase needs and their review state."""

    queryset = PurchaseRequisition.objects.select_related('item', 'preferred_supplier', 'source_issue__plan')
    serializer_class = PurchaseRequisitionSerializer

    def perform_create(self, serializer):
        serializer.save(workspace=self.get_current_workspace(), created_by=self.request.user)

    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        requisition = _run(review_requisition, self.get_object(), request.user)
        return Response(self.get_serializer(requisition).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        requisition = _run(cancel_requisition, self.get_object(), request.user)
        return Response(self.get_serializer(requisition).data)

    @action(detail=True, methods=['post'])
    def order(self, request, pk=None):
        requisition = self.get_object()
        serializer = RequisitionOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data.copy()
        line = {
            'item': requisition.item,
            'requisition': requisition,
            'description': requisition.item.name,
            'quantity': requisition.quantity,
            'unit_code': requisition.unit_code,
            'unit_price_ex_tax': values.pop('unit_price_ex_tax'),
            'tax_rate': values.pop('tax_rate'),
            'freight_ex_tax': values.pop('freight_ex_tax'),
        }
        order = _run(
            create_order, self.get_current_workspace(), request.user, values, [line],
        )
        return Response(PurchaseOrderSerializer(order, context={'request': request}).data, status=201)


class PurchaseOrderViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):
    """Manage orders and explicit delivery reconciliation."""

    queryset = PurchaseOrder.objects.select_related('supplier').prefetch_related(
        'lines__receipt_matches__receipt_line__receipt',
    )
    bind_workspace_on_create = False
    http_method_names = ['get', 'post', 'put', 'delete', 'head', 'options']

    def get_serializer_class(self):
        if self.action in {'create', 'update'}:
            return PurchaseOrderWriteSerializer
        return PurchaseOrderSerializer

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        order = _run(confirm_order, self.get_object(), request.user)
        return Response(PurchaseOrderSerializer(order, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        order = _run(close_order, self.get_object(), request.user)
        return Response(PurchaseOrderSerializer(order, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = _run(
            cancel_order, self.get_object(), serializer.validated_data['reason'],
            request.user,
        )
        return Response(PurchaseOrderSerializer(order, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='cancel-quantity')
    def cancel_quantity(self, request, pk=None):
        serializer = CancelQuantitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        line = serializer.validated_data['line']
        if line.order_id != self.get_object().pk:
            raise serializers.ValidationError({'line': 'The line is not on this order.'})
        cancellation = _run(
            cancel_order_quantity, line,
            serializer.validated_data['base_quantity'],
            serializer.validated_data['reason'], request.user,
        )
        return Response(PurchaseOrderCancellationSerializer(cancellation).data, status=201)

    @action(detail=True, methods=['post'], url_path='match-receipt')
    def match_receipt_line(self, request, pk=None):
        serializer = MatchReceiptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        line = serializer.validated_data['order_line']
        if line.order_id != self.get_object().pk:
            raise serializers.ValidationError({'order_line': 'The line is not on this order.'})
        matched = _run(
            match_receipt, line, serializer.validated_data['receipt_line'],
            serializer.validated_data['base_quantity'], request.user,
        )
        return Response(ReceiptMatchSerializer(matched).data, status=201)


class ExpenseCategoryViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):
    """Manage reusable non-stock expense categories."""

    queryset = ExpenseCategory.objects.all()
    serializer_class = ExpenseCategorySerializer


class SupplierInvoiceViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):
    """Manage supplier invoices and append-only corrections."""

    queryset = SupplierInvoice.objects.select_related('supplier', 'purchase_order').prefetch_related(
        'lines', 'corrections', 'payment_allocations__payment',
    )
    bind_workspace_on_create = False
    http_method_names = ['get', 'post', 'put', 'delete', 'head', 'options']

    def get_serializer_class(self):
        if self.action in {'create', 'update'}:
            return SupplierInvoiceWriteSerializer
        return SupplierInvoiceSerializer

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        invoice = _run(confirm_invoice, self.get_object(), request.user)
        return Response(SupplierInvoiceSerializer(invoice, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def correct(self, request, pk=None):
        serializer = InvoiceCorrectionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        correction = _run(
            issue_invoice_correction, self.get_object(),
            serializer.validated_data, request.user,
        )
        return Response(SupplierInvoiceCorrectionSerializer(correction).data, status=201)


class SupplierPaymentViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):
    """Create and read append-only supplier payments."""

    queryset = SupplierPayment.objects.select_related('supplier', 'reversal_of').prefetch_related('allocations')
    bind_workspace_on_create = False
    http_method_names = ['get', 'post', 'head', 'options']

    def get_serializer_class(self):
        return SupplierPaymentWriteSerializer if self.action == 'create' else SupplierPaymentSerializer

    @action(detail=True, methods=['post'])
    def reverse(self, request, pk=None):
        serializer = ReversePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reversal = _run(
            reverse_supplier_payment, self.get_object(), request.user,
            serializer.validated_data['reason'],
        )
        return Response(SupplierPaymentSerializer(reversal).data, status=201)


class BusinessExpenseViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):
    """Manage and confirm non-stock business expenses."""

    queryset = BusinessExpense.objects.select_related('category', 'supplier')
    serializer_class = BusinessExpenseSerializer

    def perform_create(self, serializer):
        serializer.save(workspace=self.get_current_workspace(), created_by=self.request.user)

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        expense = _run(confirm_expense, self.get_object(), request.user)
        return Response(self.get_serializer(expense).data)


class PurchasingSummaryView(APIView):
    """Return reconciled purchasing and expense reporting as of one date."""

    def get(self, request):
        as_of = serializers.DateField().to_internal_value(
            request.query_params.get('as_of', timezone.localdate().isoformat()),
        )
        return Response(purchasing_summary(get_current_workspace(), as_of))
