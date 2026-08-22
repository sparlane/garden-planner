"""Read and issue taxable supply documents and their corrections.

Documents are immutable, so this surface offers list, retrieve and create and
nothing else — the same shape `tax` uses for arrangements, and for the same
reason: correcting one is a different act from editing one and reads as one in
the audit trail.

There is deliberately no action on `SalesOrderViewSet` for this. `billing`
already depends on `sales`, and hanging the endpoint off the order would make
the dependency run both ways for the sake of a URL. The order is a field on the
request instead.
"""

# pylint: disable=duplicate-code,too-many-ancestors

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import mixins, routers, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from sales.models import Refund, SalesOrder, SalesOrderLine, SalesReturn
from workspaces.models import Workspace
from workspaces.scoping import (
    CurrentWorkspaceSerializerMixin,
    CurrentWorkspaceViewSetMixin,
    RequireWorkspaceModeMixin,
)

from .documents import document_state, full_credit, invoiceable, issue_correction, issue_supply_document
from .models import SupplyCorrection, SupplyDocument, SupplyDocumentLine
from .printing import printable_document


def _model_errors(error):
    """Translate a Django validation error into DRF's field-error shape."""
    return error.message_dict if hasattr(error, 'message_dict') else error.messages


def _run(function, *args, **kwargs):
    """Run one domain command with REST-native validation errors."""
    try:
        return function(*args, **kwargs)
    except DjangoValidationError as exc:
        raise ValidationError(_model_errors(exc)) from exc


class ActionSerializer(serializers.Serializer):
    """Validation-only serializer base for explicit commands."""

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class SupplyDocumentCoverageSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Which item of an order line a document line covers, and whether it shipped."""

    commercial_position = serializers.IntegerField(read_only=True)
    fulfillment_line = serializers.IntegerField(source='fulfillment_line_id', read_only=True)


class SupplyDocumentLineSerializer(serializers.ModelSerializer):
    """One order line's worth of supply, with the items behind it."""

    coverage = SupplyDocumentCoverageSerializer(many=True, read_only=True)

    class Meta:
        model = SupplyDocumentLine
        fields = [
            'pk', 'order_line', 'description', 'quantity', 'unit_price',
            'tax_rate', 'tax_treatment', 'gross_ex_tax', 'discount_ex_tax',
            'subtotal_ex_tax', 'tax_total', 'total_incl_tax', 'coverage',
        ]
        read_only_fields = fields


class SupplyCorrectionLineSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """How much of one document line a correction moves."""

    pk = serializers.IntegerField(read_only=True)
    document_line = serializers.IntegerField(source='document_line_id', read_only=True)
    quantity = serializers.IntegerField(read_only=True, allow_null=True)
    tax_rate = serializers.DecimalField(max_digits=7, decimal_places=4, read_only=True)
    tax_treatment = serializers.CharField(read_only=True)
    subtotal_ex_tax = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    tax_total = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    total_incl_tax = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)


class SupplyCorrectionSerializer(serializers.ModelSerializer):
    """One credit or debit note, exactly as it was issued."""

    lines = SupplyCorrectionLineSerializer(many=True, read_only=True)

    class Meta:
        model = SupplyCorrection
        fields = [
            'pk', 'document', 'document_number', 'correction_type',
            'reason_code', 'reason', 'corrected_on', 'sales_return', 'refund',
            'currency_code', 'subtotal_ex_tax', 'tax_total', 'total_incl_tax',
            'notes', 'created_by', 'created', 'lines',
        ]
        read_only_fields = fields


class SupplyDocumentSerializer(serializers.ModelSerializer):
    """One issued document, its lines, and what corrections have left of it."""

    lines = SupplyDocumentLineSerializer(many=True, read_only=True)
    corrections = SupplyCorrectionSerializer(many=True, read_only=True)
    order_number = serializers.CharField(source='order.order_number', read_only=True)
    state = serializers.SerializerMethodField()

    class Meta:
        model = SupplyDocument
        fields = [
            'pk', 'document_number', 'order', 'order_number', 'issued_on',
            'taxable_supply', 'tier', 'currency_code',
            'seller_legal_name', 'seller_trading_name', 'seller_address',
            'seller_gst_number', 'seller_registration',
            'customer', 'buyer_name', 'buyer_address', 'buyer_identifier',
            'subtotal_ex_tax', 'tax_total', 'total_incl_tax',
            'previously_invoiced', 'paid_to_date', 'balance_due',
            'overpaid_at_issue', 'notes', 'created_by', 'created',
            'lines', 'corrections', 'state',
        ]
        read_only_fields = fields

    def get_state(self, document):
        """Expose what the corrections have left of the document."""
        state = document_state(document)
        return {
            'status': state['status'],
            'credited_total': f"{state['credited_total']:f}",
            'net_total_incl_tax': f"{state['net_total_incl_tax']:f}",
        }


class BuyerSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """A one-off recipient named on a document without a customer record."""

    buyer_name = serializers.CharField(required=False, allow_blank=True)
    buyer_address = serializers.CharField(required=False, allow_blank=True)
    buyer_identifier = serializers.CharField(required=False, allow_blank=True)


class DocumentLineWriteSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """One order line and the items of it being invoiced."""

    order_line = serializers.PrimaryKeyRelatedField(queryset=SalesOrderLine.objects.all())
    positions = serializers.ListField(
        child=serializers.IntegerField(min_value=1), allow_empty=False,
    )


class SupplyDocumentWriteSerializer(CurrentWorkspaceSerializerMixin, ActionSerializer):  # pylint: disable=abstract-method
    """Everything needed to issue one document."""

    workspace_field_lookups = {'order': 'workspace'}

    operation_key = serializers.UUIDField()
    order = serializers.PrimaryKeyRelatedField(queryset=SalesOrder.objects.all())
    lines = DocumentLineWriteSerializer(many=True, allow_empty=False)
    issued_on = serializers.DateField(required=False, allow_null=True)
    buyer = BuyerSerializer(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default='')


class CorrectionLineWriteSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """One document line and how much of it a correction moves."""

    document_line = serializers.PrimaryKeyRelatedField(queryset=SupplyDocumentLine.objects.all())
    amount = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=0)
    quantity = serializers.IntegerField(required=False, allow_null=True, min_value=1)


class SupplyCorrectionWriteSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """Everything needed to issue one correction.

    `full` is the cancellation and wrong-treatment path: it credits every line
    that has anything left, which is what frees the items to be invoiced again
    on a corrected document.
    """

    operation_key = serializers.UUIDField()
    correction_type = serializers.ChoiceField(choices=SupplyCorrection.CorrectionType.choices)
    reason_code = serializers.ChoiceField(choices=SupplyCorrection.Reason.choices)
    reason = serializers.CharField(allow_blank=False)
    full = serializers.BooleanField(required=False, default=False)
    lines = CorrectionLineWriteSerializer(many=True, required=False, default=list)
    corrected_on = serializers.DateField(required=False, allow_null=True)
    sales_return = serializers.PrimaryKeyRelatedField(
        queryset=SalesReturn.objects.all(), required=False, allow_null=True,
    )
    refund = serializers.PrimaryKeyRelatedField(
        queryset=Refund.objects.all(), required=False, allow_null=True,
    )
    notes = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, attrs):
        """Require either a full credit or an explicit set of lines, never both."""
        if attrs['full'] == bool(attrs['lines']):
            raise ValidationError('Credit the whole document or name the lines to correct.')
        if attrs['full'] and attrs['correction_type'] != SupplyCorrection.CorrectionType.CREDIT:
            raise ValidationError({'full': 'Only a credit can cover a whole document.'})
        return attrs


class SupplyDocumentViewSet(
    RequireWorkspaceModeMixin,
    CurrentWorkspaceViewSetMixin,
    mixins.CreateModelMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """Issue and read the documents this workspace has handed to customers."""

    required_workspace_modes = (Workspace.Mode.NURSERY,)
    queryset = SupplyDocument.objects.select_related('order', 'customer').prefetch_related(
        'lines__coverage', 'corrections__lines',
    ).order_by('-issued_on', '-pk')
    serializer_class = SupplyDocumentSerializer
    bind_workspace_on_create = False
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        """Narrow the register by order, customer, or document date."""
        queryset = super().get_queryset()
        for parameter, lookup in (
                ('order', 'order_id'),
                ('customer', 'customer_id'),
                ('issued_from', 'issued_on__gte'),
                ('issued_to', 'issued_on__lte'),
        ):
            value = self.request.query_params.get(parameter)
            if value:
                queryset = queryset.filter(**{lookup: value})
        return queryset

    def create(self, request, *args, **kwargs):
        """Issue one document against the order and items named."""
        values = SupplyDocumentWriteSerializer(data=request.data, context=self.get_serializer_context())
        values.is_valid(raise_exception=True)
        data = values.validated_data
        buyer = data.get('buyer')
        document = _run(
            issue_supply_document,
            data['order'],
            request.user,
            operation_key=data['operation_key'],
            lines=[
                {'order_line': item['order_line'], 'positions': item['positions']}
                for item in data['lines']
            ],
            issued_on=data.get('issued_on'),
            buyer=dict(buyer) if buyer else None,
            notes=data['notes'],
        )
        return Response(self.get_serializer(document).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'])
    def print(self, request, pk=None):  # pylint: disable=unused-argument
        """Return everything the printed form of this document shows."""
        return Response(printable_document(self.get_object()))

    @action(detail=True, methods=['get', 'post'])
    def corrections(self, request, pk=None):  # pylint: disable=unused-argument
        """List the corrections against one document, or issue another."""
        document = self.get_object()
        if request.method == 'GET':
            return Response(SupplyCorrectionSerializer(
                document.corrections.prefetch_related('lines'), many=True,
            ).data)
        values = SupplyCorrectionWriteSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        data = values.validated_data
        shared = {
            'operation_key': data['operation_key'],
            'reason_code': data['reason_code'],
            'reason': data['reason'],
            'corrected_on': data.get('corrected_on'),
            'sales_return': data.get('sales_return'),
            'refund': data.get('refund'),
            'notes': data['notes'],
        }
        if data['full']:
            correction = _run(full_credit, document, request.user, **shared)
        else:
            correction = _run(
                issue_correction, document, request.user,
                correction_type=data['correction_type'],
                lines=[
                    {
                        'document_line': item['document_line'],
                        'amount': item['amount'],
                        'quantity': item.get('quantity'),
                    }
                    for item in data['lines']
                ],
                **shared,
            )
        return Response(
            SupplyCorrectionSerializer(correction).data, status=status.HTTP_201_CREATED,
        )


class InvoiceableView(RequireWorkspaceModeMixin, CurrentWorkspaceViewSetMixin, viewsets.GenericViewSet):
    """What of one order has not been invoiced yet."""

    required_workspace_modes = (Workspace.Mode.NURSERY,)
    queryset = SalesOrder.objects.prefetch_related('lines')
    serializer_class = SupplyDocumentSerializer

    def retrieve(self, request, pk=None):  # pylint: disable=unused-argument
        """Return each order line's remaining items, priced as they will invoice."""
        order = self.get_object()
        return Response({
            'order': order.pk,
            'order_number': order.order_number,
            'currency_code': order.currency_code,
            'lines': [
                {
                    'order_line': row['order_line'].pk,
                    'description': row['description'],
                    'quantity': row['order_line'].quantity,
                    'invoiced_positions': row['invoiced_positions'],
                    'returned_positions': row['returned_positions'],
                    'positions': [
                        {
                            'position': item['position'],
                            'dispatched': item['fulfillment_line'] is not None,
                            'total_incl_tax': f"{item['total_incl_tax']:f}",
                        }
                        for item in row['positions']
                    ],
                }
                for row in invoiceable(order)
            ],
        })


router = routers.SimpleRouter()
router.register(r'supply-documents', SupplyDocumentViewSet, basename='supplydocument')
router.register(r'invoiceable', InvoiceableView, basename='invoiceable')
