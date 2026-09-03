"""REST resources and explicit actions for the exact-lot stock ledger."""

# pylint: disable=too-many-lines

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from locations.models import Location
from supplies.defaults import ensure_default_supplier
from workspaces.models import get_current_workspace
from workspaces.scoping import (
    CurrentWorkspaceSerializerMixin,
    CurrentWorkspaceViewSetMixin,
)

from .ledger import (
    IndividualizationRequest,
    MovementRequest,
    OpeningBalanceRequest,
    individualize_lot_units,
    normalize_quantity,
    post_opening_balance,
    post_receipt,
    post_stock_movement,
    post_stocktake,
    reverse_movement,
    reverse_receipt,
    reverse_stocktake,
    settle_receipt,
)
from .models import (
    InputTaxAdjustment,
    InventoryItem,
    ItemUnitConversion,
    QuantityCertainty,
    StockLot,
    StockMovement,
    StockReceipt,
    StockReceiptLine,
    Stocktake,
    StocktakeLine,
)
from .input_tax import receipt_tax_warnings
from .rest_query import (
    parse_boolean as _parse_boolean,
    parse_date as _parse_date,
    parse_datetime as _parse_datetime,
    parse_integer as _parse_integer,
)
from .units import UnitCode


def _model_errors(error):
    """Translate model validation errors into DRF response details."""
    if hasattr(error, 'message_dict'):
        return error.message_dict
    return error.messages


def _validate_serialized_receipt_quantity(item, certainty, base_quantity):
    """Keep general serialized receiving limited to exact mapped trays."""
    if item.tracking_mode != InventoryItem.TrackingMode.SERIALIZED:
        return
    if not hasattr(item, 'seed_tray_model'):
        raise ValidationError({
            'item': 'Only mapped seed trays can use general receiving.',
        })
    if certainty != QuantityCertainty.EXACT:
        raise ValidationError({
            'quantity_certainty': 'Seed trays require an exact quantity.',
        })
    if base_quantity != base_quantity.to_integral_value():
        raise ValidationError({
            'quantity': 'Seed trays require a whole normalized quantity.',
        })


def _run_domain_action(function, *args):
    """Invoke a ledger service with field-friendly API errors."""
    try:
        return function(*args)
    except DjangoValidationError as exc:
        raise ValidationError(_model_errors(exc)) from exc


class StockReceiptLineSerializer(
    CurrentWorkspaceSerializerMixin,
    serializers.ModelSerializer,
):
    """Serialize one normalized line nested inside a stock receipt."""

    base_quantity = serializers.DecimalField(
        max_digits=24,
        decimal_places=9,
        read_only=True,
    )
    base_unit = serializers.CharField(source='item.base_unit', read_only=True)
    lot = serializers.SerializerMethodField()

    class Meta:
        model = StockReceiptLine
        fields = [
            'pk',
            'item',
            'supplier_lot_reference',
            'expires_on',
            'quantity',
            'quantity_certainty',
            'unit_code',
            'unit_conversion',
            'base_quantity',
            'base_unit',
            'line_cost_ex_tax',
            'supplier_cost_incl_tax',
            'tax_treatment',
            'tax_rate',
            'input_tax_source',
            'input_tax_amount',
            'claim_input_tax',
            'claimable_percentage',
            'apportionment_basis',
            'recoverable_input_tax',
            'non_recoverable_tax',
            'acquisition_amount',
            'legacy_tax_classification',
            'destination',
            'lot',
            'created',
            'updated',
        ]
        read_only_fields = [
            'recoverable_input_tax', 'non_recoverable_tax', 'acquisition_amount',
            'legacy_tax_classification', 'lot', 'created', 'updated',
        ]
        extra_kwargs = {
            # A Basic Garden gift or swap has no price; zero is a legitimate
            # cost, not a placeholder for one that was never entered.
            'line_cost_ex_tax': {'required': False},
            'supplier_cost_incl_tax': {'required': False},
        }

    workspace_field_lookups = {
        'item': 'workspace',
        'unit_conversion': 'workspace',
        'destination': 'workspace',
    }

    def get_lot(self, line):
        """Return the generated lot only after posting."""
        try:
            return line.stock_lot.pk
        except StockLot.DoesNotExist:
            return None

    def validate(self, attrs):
        """Normalize display quantity and reject inactive selectors."""
        item = attrs.get('item')
        conversion = attrs.get('unit_conversion')
        destination = attrs.get('destination')
        if not item.active:
            raise ValidationError({'item': 'The item is inactive.'})
        if item.category == InventoryItem.Category.SEED:
            raise ValidationError({
                'item': 'Receive seed packets through the seed receipt API.',
            })
        if conversion and not conversion.active:
            raise ValidationError(
                {'unit_conversion': 'The conversion is inactive.'},
            )
        if destination and not destination.active:
            raise ValidationError({'destination': 'The location is inactive.'})
        if 'supplier_cost_incl_tax' not in attrs and 'line_cost_ex_tax' in attrs:
            attrs['supplier_cost_incl_tax'] = attrs['line_cost_ex_tax']
            attrs['_legacy_receipt_tax'] = True
        attrs.setdefault('supplier_cost_incl_tax', Decimal('0'))
        attrs.setdefault('line_cost_ex_tax', Decimal('0'))
        certainty = attrs.get(
            'quantity_certainty',
            getattr(self.instance, 'quantity_certainty', QuantityCertainty.EXACT),
        )
        if certainty == QuantityCertainty.UNKNOWN:
            if attrs.get('quantity') is not None:
                raise ValidationError({
                    'quantity': 'Unknown quantities must not include a number.',
                })
            attrs['base_quantity'] = None
        else:
            try:
                attrs['base_quantity'] = normalize_quantity(
                    item,
                    attrs.get('quantity'),
                    attrs.get('unit_code'),
                    conversion,
                )
            except DjangoValidationError as exc:
                raise ValidationError(_model_errors(exc)) from exc
        _validate_serialized_receipt_quantity(
            item,
            certainty,
            attrs['base_quantity'],
        )
        return attrs


class StockReceiptSerializer(
    CurrentWorkspaceSerializerMixin,
    serializers.ModelSerializer,
):
    """Serialize a draft or immutable posted supplier receipt."""

    lines = StockReceiptLineSerializer(many=True, required=False)
    movement_ids = serializers.SerializerMethodField()
    is_seed_packet_draft = serializers.SerializerMethodField()
    tax_warnings = serializers.SerializerMethodField()
    settled_on = serializers.SerializerMethodField()

    class Meta:
        model = StockReceipt
        fields = [
            'pk',
            'supplier',
            'status',
            'received_date',
            'supplier_reference',
            'invoice_date',
            'source_document_type',
            'source_document_number',
            'evidence_reference',
            'evidence_url',
            'supplier_name_snapshot',
            'supplier_address_snapshot',
            'supplier_gst_status',
            'supplier_gst_number',
            'currency_code',
            'settled_on',
            'notes',
            'created_by',
            'posted_at',
            'reversed_at',
            'is_seed_packet_draft',
            'created',
            'updated',
            'lines',
            'movement_ids',
            'tax_warnings',
        ]
        read_only_fields = [
            'status',
            'created_by',
            'posted_at',
            'reversed_at',
            'settled_on',
            'is_seed_packet_draft',
            'created',
            'updated',
            'movement_ids',
            'tax_warnings',
        ]
        extra_kwargs = {
            'supplier': {'required': False},
            'currency_code': {'required': False},
        }

    workspace_field_lookups = {'supplier': 'workspace'}

    def get_movement_ids(self, receipt):
        """Expose posted receipt movements without making them writable."""
        return list(
            StockMovement.objects.filter(receipt_line__receipt=receipt)
            .order_by('pk')
            .values_list('pk', flat=True)
        )

    def get_is_seed_packet_draft(self, receipt):
        """Name the drafts the seed workflow owns its own editor for."""
        return hasattr(receipt, 'seed_packet_draft')

    def get_tax_warnings(self, receipt):
        """Expose unsupported claims without silently changing them."""
        return receipt_tax_warnings(receipt)

    def get_settled_on(self, receipt):
        """Expose invoice-allocation settlement, falling back for legacy receipts."""
        from purchasing.services import receipt_paid_on  # pylint: disable=import-outside-toplevel

        paid_on = receipt_paid_on(receipt)
        return paid_on.isoformat() if paid_on else None

    def validate(self, attrs):
        """Apply workspace financial defaults to new draft documents."""
        if self.instance and hasattr(self.instance, 'seed_packet_draft'):
            raise ValidationError({
                'status': 'Edit seed packet drafts through the seed receipt API.',
            })
        if self.instance and self.instance.status != StockReceipt.Status.DRAFT:
            raise ValidationError({'status': 'Posted receipts are immutable.'})
        if self.instance is None:
            workspace = get_current_workspace()
            attrs.setdefault('currency_code', workspace.currency_code)
            attrs.setdefault('supplier', ensure_default_supplier(workspace))
        supplier = attrs.get('supplier') or getattr(self.instance, 'supplier', None)
        if supplier is not None:
            attrs.setdefault('supplier_name_snapshot', supplier.name)
            attrs.setdefault('supplier_address_snapshot', supplier.address)
            attrs.setdefault('supplier_gst_status', supplier.gst_status)
            attrs.setdefault('supplier_gst_number', supplier.gst_number)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        """Create a draft and its normalized nested lines atomically."""
        lines = validated_data.pop('lines', [])
        lines = self._canonical_lines(lines)
        receipt = StockReceipt.objects.create(**validated_data)
        for line in lines:
            StockReceiptLine.objects.create(receipt=receipt, **line)
        return receipt

    @transaction.atomic
    def update(self, instance, validated_data):
        """Update a draft, replacing nested lines only when supplied."""
        lines = validated_data.pop('lines', None)
        if lines is not None:
            lines = self._canonical_lines(lines)
        instance = super().update(instance, validated_data)
        if lines is not None:
            instance.lines.all().delete()
            for line in lines:
                StockReceiptLine.objects.create(receipt=instance, **line)
        return instance

    @staticmethod
    def _canonical_lines(lines):
        """Discard the temporary marker accepted from legacy ex-tax clients."""
        for line in lines:
            line.pop('_legacy_receipt_tax', None)
        return lines


class InputTaxAdjustmentSerializer(
    CurrentWorkspaceSerializerMixin,
    serializers.ModelSerializer,
):
    """Create and read append-only changes in taxable use."""

    class Meta:
        model = InputTaxAdjustment
        fields = [
            'pk', 'receipt_line', 'adjustment_date',
            'previous_claimable_percentage', 'revised_claimable_percentage',
            'tax_adjustment', 'apportionment_basis', 'reason',
            'evidence_reference', 'evidence_url', 'created_by', 'created',
        ]
        read_only_fields = [
            'previous_claimable_percentage', 'tax_adjustment',
            'created_by', 'created',
        ]

    workspace_field_lookups = {'receipt_line': 'receipt__workspace'}

    def create(self, validated_data):
        """Calculate the delta from the latest recorded claim percentage."""
        line = validated_data['receipt_line']
        latest = line.input_tax_adjustments.order_by(
            '-adjustment_date', '-pk',
        ).first()
        previous = (
            latest.revised_claimable_percentage
            if latest else line.claimable_percentage
        )
        revised = validated_data['revised_claimable_percentage']
        tax_delta = Decimal(line.input_tax_amount) * (revised - previous)
        return InputTaxAdjustment.objects.create(
            previous_claimable_percentage=previous,
            tax_adjustment=(tax_delta / Decimal('100')).quantize(Decimal('0.0001')),
            **validated_data,
        )


class InputTaxAdjustmentViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """List and append input-tax adjustments; never edit or delete them."""

    queryset = InputTaxAdjustment.objects.select_related('receipt_line__receipt')
    serializer_class = InputTaxAdjustmentSerializer
    http_method_names = ['get', 'post', 'head', 'options']

    def perform_create(self, serializer):
        serializer.save(
            workspace=self.get_current_workspace(),
            created_by=self.request.user,
        )

    def get_queryset(self):
        queryset = super().get_queryset()
        receipt_line = _parse_integer(
            self.request.query_params.get('receipt_line'),
            'receipt_line',
        )
        if receipt_line is not None:
            queryset = queryset.filter(receipt_line_id=receipt_line)
        receipt = _parse_integer(
            self.request.query_params.get('receipt'),
            'receipt',
        )
        if receipt is not None:
            queryset = queryset.filter(receipt_line__receipt_id=receipt)
        return queryset


class StockLotSerializer(serializers.ModelSerializer):
    """Serialize immutable lot provenance and cost."""

    item_name = serializers.CharField(source='item.name', read_only=True)
    base_unit = serializers.CharField(source='item.base_unit', read_only=True)

    class Meta:
        model = StockLot
        fields = [
            'pk',
            'item',
            'item_name',
            'base_unit',
            'identifier',
            'origin',
            'receipt_line',
            'supplier_lot_reference',
            'received_on',
            'expires_on',
            'initial_base_quantity',
            'quantity_certainty',
            'acquisition_total',
            'base_unit_cost',
            'currency_code',
            'created',
        ]
        read_only_fields = fields


class StockMovementSerializer(serializers.ModelSerializer):
    """Serialize one immutable ledger entry and reversal linkage."""

    base_unit = serializers.CharField(source='lot.item.base_unit', read_only=True)
    reversed_by = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = [
            'pk',
            'lot',
            'unit',
            'movement_type',
            'quantity',
            'base_unit',
            'source',
            'destination',
            'occurred_at',
            'created_by',
            'reason',
            'reference',
            'reversal_of',
            'reversed_by',
            'receipt_line',
            'stocktake_line',
            'created',
        ]
        read_only_fields = fields

    def get_reversed_by(self, movement):
        """Return the inverse row when the movement was reversed."""
        try:
            return movement.reversal.pk
        except StockMovement.DoesNotExist:
            return None


class StocktakeLineSerializer(
    CurrentWorkspaceSerializerMixin,
    serializers.ModelSerializer,
):
    """Serialize a display count and its posted variance snapshot."""

    counted_base_quantity = serializers.DecimalField(
        max_digits=24,
        decimal_places=9,
        read_only=True,
    )
    base_unit = serializers.CharField(source='lot.item.base_unit', read_only=True)
    movement_ids = serializers.SerializerMethodField()

    class Meta:
        model = StocktakeLine
        fields = [
            'pk',
            'lot',
            'location',
            'counted_quantity',
            'unit_code',
            'unit_conversion',
            'counted_base_quantity',
            'base_unit',
            'expected_base_quantity',
            'variance_base_quantity',
            'reason',
            'movement_ids',
            'created',
            'updated',
        ]
        read_only_fields = [
            'expected_base_quantity',
            'variance_base_quantity',
            'movement_ids',
            'created',
            'updated',
        ]

    workspace_field_lookups = {
        'lot': 'workspace',
        'location': 'workspace',
        'unit_conversion': 'workspace',
    }

    def get_movement_ids(self, line):
        """Return adjustment movements posted for this count."""
        return list(line.movements.order_by('pk').values_list('pk', flat=True))

    def validate(self, attrs):
        """Normalize the display count and enforce active selectors."""
        lot = attrs.get('lot')
        location = attrs.get('location')
        conversion = attrs.get('unit_conversion')
        if not lot.item.active:
            raise ValidationError({'lot': 'The lot item is inactive.'})
        if not location.active:
            raise ValidationError({'location': 'The location is inactive.'})
        if conversion and not conversion.active:
            raise ValidationError(
                {'unit_conversion': 'The conversion is inactive.'},
            )
        try:
            attrs['counted_base_quantity'] = normalize_quantity(
                lot.item,
                attrs.get('counted_quantity'),
                attrs.get('unit_code'),
                conversion,
                allow_zero=True,
            )
        except DjangoValidationError as exc:
            raise ValidationError(_model_errors(exc)) from exc
        return attrs


class StocktakeSerializer(serializers.ModelSerializer):
    """Serialize a draft or posted physical count document."""

    lines = StocktakeLineSerializer(many=True, required=False)

    class Meta:
        model = Stocktake
        fields = [
            'pk',
            'status',
            'counted_at',
            'notes',
            'created_by',
            'posted_at',
            'reversed_at',
            'created',
            'updated',
            'lines',
        ]
        read_only_fields = [
            'status',
            'created_by',
            'posted_at',
            'reversed_at',
            'created',
            'updated',
        ]

    def validate(self, attrs):
        """Reject generic edits after a stocktake posts."""
        if self.instance and self.instance.status != Stocktake.Status.DRAFT:
            raise ValidationError({'status': 'Posted stocktakes are immutable.'})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        """Create a draft count and its nested lines atomically."""
        lines = validated_data.pop('lines', [])
        stocktake = Stocktake.objects.create(**validated_data)
        for line in lines:
            StocktakeLine.objects.create(stocktake=stocktake, **line)
        return stocktake

    @transaction.atomic
    def update(self, instance, validated_data):
        """Update a draft, replacing count lines only when supplied."""
        lines = validated_data.pop('lines', None)
        instance = super().update(instance, validated_data)
        if lines is not None:
            instance.lines.all().delete()
            for line in lines:
                StocktakeLine.objects.create(stocktake=instance, **line)
        return instance


class ActionSerializer(serializers.Serializer):
    """Validation-only serializer base for domain action payloads."""

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class ReasonSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """Validate an explicit correction or reversal reason."""

    reason = serializers.CharField(allow_blank=False, trim_whitespace=True)


class SettlementSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """Validate the date a receipt's supplier was paid, or its removal.

    Null is a real value here rather than a missing one: clearing the date is
    how a settlement recorded against the wrong receipt is taken back.
    """

    settled_on = serializers.DateField(allow_null=True)


class IndividualizationSerializer(
    CurrentWorkspaceSerializerMixin,
    ActionSerializer,
):  # pylint: disable=abstract-method
    """Validate how many of a mixed lot's pots are being given identities."""

    location = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
    )
    count = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(
        allow_blank=True, required=False, default='', trim_whitespace=True,
    )
    workspace_field_lookups = {'location': 'workspace'}


class MovementActionSerializer(
    CurrentWorkspaceSerializerMixin,
    ActionSerializer,
):  # pylint: disable=abstract-method
    """Validate normalized fields shared by typed lot-movement actions."""

    lot = serializers.PrimaryKeyRelatedField(queryset=StockLot.objects.all())
    quantity = serializers.DecimalField(max_digits=24, decimal_places=9)
    unit_code = serializers.ChoiceField(
        choices=UnitCode.choices,
        allow_null=True,
        required=False,
    )
    unit_conversion = serializers.PrimaryKeyRelatedField(
        queryset=ItemUnitConversion.objects.all(),
        allow_null=True,
        required=False,
    )
    source = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        allow_null=True,
        required=False,
    )
    destination = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        allow_null=True,
        required=False,
    )
    occurred_at = serializers.DateTimeField(required=False)
    reason = serializers.CharField(allow_blank=True, required=False, default='')
    reference = serializers.CharField(allow_blank=True, required=False, default='')

    workspace_field_lookups = {
        'lot': 'workspace',
        'unit_conversion': 'workspace',
        'source': 'workspace',
        'destination': 'workspace',
    }

    def validate(self, attrs):
        """Add a canonical base quantity for the service layer."""
        conversion = attrs.get('unit_conversion')
        if conversion and not conversion.active:
            raise ValidationError(
                {'unit_conversion': 'The conversion is inactive.'},
            )
        try:
            attrs['base_quantity'] = normalize_quantity(
                attrs['lot'].item,
                attrs['quantity'],
                attrs.get('unit_code'),
                conversion,
            )
        except DjangoValidationError as exc:
            raise ValidationError(_model_errors(exc)) from exc
        return attrs


class AdjustmentActionSerializer(MovementActionSerializer):  # pylint: disable=abstract-method
    """Validate a gain/loss correction at one location."""

    direction = serializers.ChoiceField(choices=['gain', 'loss'])
    location = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
    )
    reason = serializers.CharField(allow_blank=False, trim_whitespace=True)
    workspace_field_lookups = {
        **MovementActionSerializer.workspace_field_lookups,
        'location': 'workspace',
    }


class OpeningBalanceActionSerializer(
    CurrentWorkspaceSerializerMixin,
    ActionSerializer,
):  # pylint: disable=abstract-method
    """Validate a costed opening lot request."""

    item = serializers.PrimaryKeyRelatedField(queryset=InventoryItem.objects.all())
    quantity = serializers.DecimalField(max_digits=24, decimal_places=9)
    unit_code = serializers.ChoiceField(
        choices=UnitCode.choices,
        allow_null=True,
        required=False,
    )
    unit_conversion = serializers.PrimaryKeyRelatedField(
        queryset=ItemUnitConversion.objects.all(),
        allow_null=True,
        required=False,
    )
    destination = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
    )
    acquisition_total = serializers.DecimalField(
        max_digits=18,
        decimal_places=4,
        min_value=Decimal('0'),
    )
    received_on = serializers.DateField()
    supplier_lot_reference = serializers.CharField(
        allow_blank=True,
        required=False,
        default='',
    )
    expires_on = serializers.DateField(allow_null=True, required=False)
    occurred_at = serializers.DateTimeField(required=False)
    reason = serializers.CharField(allow_blank=True, required=False, default='')

    workspace_field_lookups = {
        'item': 'workspace',
        'unit_conversion': 'workspace',
        'destination': 'workspace',
    }

    def validate(self, attrs):
        """Normalize the opening display quantity."""
        conversion = attrs.get('unit_conversion')
        if conversion and not conversion.active:
            raise ValidationError(
                {'unit_conversion': 'The conversion is inactive.'},
            )
        try:
            attrs['base_quantity'] = normalize_quantity(
                attrs['item'],
                attrs['quantity'],
                attrs.get('unit_code'),
                conversion,
            )
        except DjangoValidationError as exc:
            raise ValidationError(_model_errors(exc)) from exc
        return attrs


class StockReceiptViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Edit draft receipts and explicitly post or reverse them."""

    queryset = StockReceipt.objects.select_related(
        'supplier',
        'created_by',
        'seed_packet_draft',
    ).prefetch_related(
        'lines__item',
        'lines__unit_conversion',
        'lines__destination',
    )
    serializer_class = StockReceiptSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def perform_create(self, serializer):
        serializer.save(
            workspace=self.get_current_workspace(),
            created_by=self.request.user,
        )

    def perform_destroy(self, instance):
        if hasattr(instance, 'seed_packet_draft'):
            raise ValidationError({
                'status': 'Cancel seed packet drafts through the seed receipt API.',
            })
        try:
            instance.delete()
        except DjangoValidationError as exc:
            raise ValidationError(_model_errors(exc)) from exc

    def get_queryset(self):
        queryset = super().get_queryset()
        receipt_status = self.request.query_params.get('status')
        if receipt_status:
            if receipt_status not in StockReceipt.Status.values:
                raise ValidationError({'status': 'Select a valid status.'})
            queryset = queryset.filter(status=receipt_status)
        supplier = _parse_integer(
            self.request.query_params.get('supplier'),
            'supplier',
        )
        if supplier is not None:
            queryset = queryset.filter(supplier_id=supplier)
        received_after = _parse_date(
            self.request.query_params.get('received_after'),
            'received_after',
        )
        received_before = _parse_date(
            self.request.query_params.get('received_before'),
            'received_before',
        )
        if received_after:
            queryset = queryset.filter(received_date__gte=received_after)
        if received_before:
            queryset = queryset.filter(received_date__lte=received_before)
        seed_packet = _parse_boolean(
            self.request.query_params.get('seed_packet'),
            'seed_packet',
        )
        if seed_packet is not None:
            queryset = queryset.filter(seed_packet_draft__isnull=not seed_packet)
        return queryset

    @action(detail=True, methods=['post'])
    def post(self, request, pk=None):  # pylint: disable=unused-argument
        """Post every draft line as one exact lot and receipt movement."""
        receipt = self.get_object()
        if hasattr(receipt, 'seed_packet_draft'):
            raise ValidationError({
                'status': 'Post seed packet drafts through the seed receipt API.',
            })
        receipt, _lots = _run_domain_action(
            post_receipt,
            receipt,
            request.user,
        )
        return Response(self.get_serializer(receipt).data)

    @action(detail=True, methods=['post'])
    def reverse(self, request, pk=None):  # pylint: disable=unused-argument
        """Reverse every receipt movement while preserving provenance."""
        reason = ReasonSerializer(data=request.data)
        reason.is_valid(raise_exception=True)
        receipt, _movements = _run_domain_action(
            reverse_receipt,
            self.get_object(),
            request.user,
            reason.validated_data['reason'],
        )
        return Response(self.get_serializer(receipt).data)

    @action(detail=True, methods=['post'])
    def settle(self, request, pk=None):  # pylint: disable=unused-argument
        """Record or clear the date this receipt's supplier was paid."""
        settlement = SettlementSerializer(data=request.data)
        settlement.is_valid(raise_exception=True)
        receipt = _run_domain_action(
            settle_receipt,
            self.get_object(),
            settlement.validated_data['settled_on'],
        )
        return Response(self.get_serializer(receipt).data)


class StockLotViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ReadOnlyModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Read immutable exact-lot provenance."""

    queryset = StockLot.objects.select_related('item', 'receipt_line')
    serializer_class = StockLotSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        item = _parse_integer(self.request.query_params.get('item'), 'item')
        if item is not None:
            queryset = queryset.filter(item_id=item)
        identifier = self.request.query_params.get('identifier', '').strip()
        if identifier:
            queryset = queryset.filter(identifier__icontains=identifier)
        expires_before = _parse_date(
            self.request.query_params.get('expires_before'),
            'expires_before',
        )
        if expires_before:
            queryset = queryset.filter(expires_on__lte=expires_before)
        return queryset

    @action(detail=True, methods=['post'])
    def individualize(self, request, pk=None):  # pylint: disable=unused-argument
        """Give individual identities to part of this lot's bulk stock."""
        serializer = IndividualizationSerializer(
            data=request.data, context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        units = _run_domain_action(
            individualize_lot_units,
            self.get_current_workspace(),
            request.user,
            IndividualizationRequest(
                lot=self.get_object(),
                location=values['location'],
                count=values['count'],
                reason=values['reason'],
            ),
        )
        from .serialized_rest import InventoryUnitSerializer  # pylint: disable=import-outside-toplevel

        return Response(
            InventoryUnitSerializer(units, many=True).data,
            status=status.HTTP_201_CREATED,
        )


class StockMovementViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ReadOnlyModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Read history and expose typed append-only movement actions."""

    queryset = StockMovement.objects.select_related(
        'lot__item',
        'unit',
        'source',
        'destination',
        'created_by',
        'reversal_of',
    )
    serializer_class = StockMovementSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        lot = _parse_integer(self.request.query_params.get('lot'), 'lot')
        item = _parse_integer(self.request.query_params.get('item'), 'item')
        unit = _parse_integer(self.request.query_params.get('unit'), 'unit')
        location = _parse_integer(
            self.request.query_params.get('location'),
            'location',
        )
        if lot is not None:
            queryset = queryset.filter(lot_id=lot)
        if item is not None:
            queryset = queryset.filter(lot__item_id=item)
        if unit is not None:
            queryset = queryset.filter(unit_id=unit)
        if location is not None:
            queryset = queryset.filter(Q(source_id=location) | Q(destination_id=location))
        movement_type = self.request.query_params.get('movement_type')
        if movement_type:
            if movement_type not in StockMovement.MovementType.values:
                raise ValidationError(
                    {'movement_type': 'Select a valid movement type.'},
                )
            queryset = queryset.filter(movement_type=movement_type)
        occurred_after = _parse_datetime(
            self.request.query_params.get('occurred_after'),
            'occurred_after',
        )
        occurred_before = _parse_datetime(
            self.request.query_params.get('occurred_before'),
            'occurred_before',
        )
        if occurred_after:
            queryset = queryset.filter(occurred_at__gte=occurred_after)
        if occurred_before:
            queryset = queryset.filter(occurred_at__lte=occurred_before)
        return queryset

    def _post_typed(self, request, movement_type, serializer_class=None):
        """Validate and post one typed movement request."""
        serializer = (serializer_class or MovementActionSerializer)(
            data=request.data,
        )
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        source = values.get('source')
        destination = values.get('destination')
        if movement_type == 'adjust':
            movement_type = StockMovement.MovementType.ADJUSTMENT_GAIN
            destination = values['location']
            source = None
            if values['direction'] == 'loss':
                movement_type = StockMovement.MovementType.ADJUSTMENT_LOSS
                source = values['location']
                destination = None
        movement = _run_domain_action(
            post_stock_movement,
            self.get_current_workspace(),
            request.user,
            MovementRequest(
                lot=values['lot'],
                movement_type=movement_type,
                quantity=values['base_quantity'],
                source=source,
                destination=destination,
                occurred_at=values.get('occurred_at'),
                reason=values.get('reason', ''),
                reference=values.get('reference', ''),
            ),
        )
        return Response(
            StockMovementSerializer(movement).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=['post'])
    def opening(self, request):
        """Create one generated, costed opening lot and movement."""
        serializer = OpeningBalanceActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        lot, movement = _run_domain_action(
            post_opening_balance,
            self.get_current_workspace(),
            request.user,
            OpeningBalanceRequest(
                item=values['item'],
                quantity=values['base_quantity'],
                destination=values['destination'],
                acquisition_total=values['acquisition_total'],
                received_on=values['received_on'],
                supplier_lot_reference=values.get('supplier_lot_reference', ''),
                expires_on=values.get('expires_on'),
                occurred_at=values.get('occurred_at'),
                reason=values.get('reason', ''),
            ),
        )
        return Response(
            {
                'lot': StockLotSerializer(lot).data,
                'movement': StockMovementSerializer(movement).data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=['post'])
    def transfer(self, request):
        """Move an exact lot quantity between two physical locations."""
        return self._post_typed(
            request,
            StockMovement.MovementType.TRANSFER,
        )

    @action(detail=False, methods=['post'])
    def consume(self, request):
        """Consume an exact lot quantity from one physical location."""
        return self._post_typed(
            request,
            StockMovement.MovementType.CONSUMPTION,
        )

    @action(detail=False, methods=['post'])
    def adjust(self, request):
        """Post a reasoned stock gain or loss at one location."""
        return self._post_typed(request, 'adjust', AdjustmentActionSerializer)

    @action(detail=False, methods=['post'])
    def waste(self, request):
        """Post reasoned waste from one exact lot and location."""
        return self._post_typed(request, StockMovement.MovementType.WASTE)

    @action(detail=False, methods=['post'])
    def sale(self, request):
        """Dispatch an exact lot quantity through a sale movement."""
        return self._post_typed(request, StockMovement.MovementType.SALE)

    @action(detail=False, methods=['post'], url_path='customer-return')
    def customer_return(self, request):
        """Return an exact lot quantity to a physical location."""
        return self._post_typed(
            request,
            StockMovement.MovementType.CUSTOMER_RETURN,
        )

    @action(detail=True, methods=['post'])
    def reverse(self, request, pk=None):  # pylint: disable=unused-argument
        """Append a linked inverse row for a standalone movement."""
        reason = ReasonSerializer(data=request.data)
        reason.is_valid(raise_exception=True)
        movement = _run_domain_action(
            reverse_movement,
            self.get_object(),
            request.user,
            reason.validated_data['reason'],
        )
        return Response(
            self.get_serializer(movement).data,
            status=status.HTTP_201_CREATED,
        )


class StocktakeViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Edit draft counts and explicitly post or reverse variances."""

    queryset = Stocktake.objects.select_related('created_by').prefetch_related(
        'lines__lot__item',
        'lines__location',
        'lines__unit_conversion',
    )
    serializer_class = StocktakeSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def perform_create(self, serializer):
        serializer.save(
            workspace=self.get_current_workspace(),
            created_by=self.request.user,
        )

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except DjangoValidationError as exc:
            raise ValidationError(_model_errors(exc)) from exc

    def get_queryset(self):
        queryset = super().get_queryset()
        stocktake_status = self.request.query_params.get('status')
        if stocktake_status:
            if stocktake_status not in Stocktake.Status.values:
                raise ValidationError({'status': 'Select a valid status.'})
            queryset = queryset.filter(status=stocktake_status)
        return queryset

    @action(detail=True, methods=['post'])
    def post(self, request, pk=None):  # pylint: disable=unused-argument
        """Post counted variances as linked gain/loss movements."""
        stocktake, _movements = _run_domain_action(
            post_stocktake,
            self.get_object(),
            request.user,
        )
        return Response(self.get_serializer(stocktake).data)

    @action(detail=True, methods=['post'])
    def reverse(self, request, pk=None):  # pylint: disable=unused-argument
        """Reverse every non-zero variance from a posted stocktake."""
        reason = ReasonSerializer(data=request.data)
        reason.is_valid(raise_exception=True)
        stocktake, _movements = _run_domain_action(
            reverse_stocktake,
            self.get_object(),
            request.user,
            reason.validated_data['reason'],
        )
        return Response(self.get_serializer(stocktake).data)


def register_ledger_routes(router):
    """Attach ledger viewsets to the inventory API router."""
    from .serialized_rest import InventoryUnitViewSet  # pylint: disable=import-outside-toplevel
    from .stocktake_rest import NurseryStocktakeViewSet  # pylint: disable=import-outside-toplevel

    router.register(r'receipts', StockReceiptViewSet)
    router.register(r'input-tax-adjustments', InputTaxAdjustmentViewSet)
    router.register(r'lots', StockLotViewSet)
    router.register(r'serialized-units', InventoryUnitViewSet)
    router.register(r'movements', StockMovementViewSet)
    router.register(r'stocktakes', NurseryStocktakeViewSet, basename='stocktake')
