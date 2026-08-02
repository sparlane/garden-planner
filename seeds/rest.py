"""REST resources for seed catalogs, packet receipts, and stock metadata."""

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import routers, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.response import Response

from inventory.models import QuantityCertainty
from inventory.units import UnitCode
from workspaces.scoping import (
    CurrentWorkspaceSerializerMixin,
    CurrentWorkspaceViewSetMixin,
)

from .models import SeedPacket, SeedPacketReceiptDraft, Seeds
from .services import (
    create_packet_receipt_draft,
    create_seed_inventory_item,
    delete_packet_receipt_draft,
    packet_inventory_snapshot,
    post_packet_receipt,
    reconcile_packet_quantity,
    set_seed_inventory_unit,
    update_packet_receipt_draft,
)


def _model_errors(error):
    if hasattr(error, 'message_dict'):
        return error.message_dict
    return error.messages


class SeedsSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """Serialize one supplier/variety seed catalog and its semantic unit."""

    base_unit = serializers.ChoiceField(
        choices=(UnitCode.SEED, UnitCode.SEED_CLUSTER),
        required=False,
        default=UnitCode.SEED,
    )
    inventory_item = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Seeds
        fields = [
            'pk',
            'supplier',
            'plant_variety',
            'supplier_code',
            'url',
            'notes',
            'inventory_item',
            'base_unit',
        ]

    workspace_field_lookups = {
        'supplier': 'workspace',
        'plant_variety': 'workspace',
    }

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['base_unit'] = (
            instance.inventory_item.base_unit
            if instance.inventory_item_id
            else None
        )
        return data

    @transaction.atomic
    def create(self, validated_data):
        base_unit = validated_data.pop('base_unit', UnitCode.SEED)
        seeds = Seeds.objects.create(**validated_data)
        item = create_seed_inventory_item(seeds.workspace, seeds, base_unit)
        seeds.inventory_item = item
        seeds.save(update_fields=['inventory_item'])
        return seeds

    @transaction.atomic
    def update(self, instance, validated_data):
        base_unit = validated_data.pop('base_unit', None)
        instance = super().update(instance, validated_data)
        if base_unit and instance.inventory_item.base_unit != base_unit:
            try:
                set_seed_inventory_unit(instance, base_unit)
            except DjangoValidationError as exc:
                raise ValidationError(_model_errors(exc)) from exc
        return instance


class SeedPacketSerializer(serializers.ModelSerializer):
    """Expose the packet selector with truthful inventory metadata."""

    purchase_date = serializers.SerializerMethodField()
    sow_by = serializers.SerializerMethodField()
    empty = serializers.SerializerMethodField()
    inventory = serializers.SerializerMethodField()

    class Meta:
        model = SeedPacket
        fields = [
            'pk',
            'seeds',
            'purchase_date',
            'sow_by',
            'empty',
            'notes',
            'inventory',
        ]
        read_only_fields = [
            'pk',
            'seeds',
            'purchase_date',
            'sow_by',
            'empty',
            'inventory',
        ]

    @staticmethod
    def _snapshot(packet):
        if not packet.stock_lot_id or not packet.storage_location_id:
            return None
        return packet_inventory_snapshot(packet)

    def get_purchase_date(self, packet):
        """Read the authoritative receipt date from the linked lot."""
        if packet.stock_lot_id:
            return packet.stock_lot.received_on
        return packet.purchase_date

    def get_sow_by(self, packet):
        """Read the authoritative expiry/sow-by date from the linked lot."""
        if packet.stock_lot_id:
            return packet.stock_lot.expires_on
        return packet.sow_by

    def get_empty(self, packet):
        """Return a boolean only when an exact balance establishes it."""
        snapshot = self._snapshot(packet)
        return snapshot['empty'] if snapshot else None

    def get_inventory(self, packet):
        """Return nested truthful quantity and valuation metadata."""
        return self._snapshot(packet)


class PacketReceiptDraftSerializer(
    CurrentWorkspaceSerializerMixin,
    serializers.Serializer,
):
    """Validate and render one seed-focused one-line receipt draft."""

    pk = serializers.IntegerField(read_only=True)
    seeds = serializers.PrimaryKeyRelatedField(queryset=Seeds.objects.all())
    status = serializers.CharField(read_only=True)
    quantity_certainty = serializers.ChoiceField(
        choices=QuantityCertainty.choices,
    )
    quantity = serializers.DecimalField(
        max_digits=24,
        decimal_places=9,
        allow_null=True,
        required=False,
    )
    base_unit = serializers.CharField(read_only=True)
    line_price = serializers.DecimalField(
        max_digits=18,
        decimal_places=4,
        min_value=Decimal('0'),
    )
    supplier_lot_reference = serializers.CharField(
        allow_blank=True,
        required=False,
    )
    received_date = serializers.DateField()
    sow_by = serializers.DateField(allow_null=True, required=False)
    supplier_reference = serializers.CharField(
        allow_blank=True,
        required=False,
    )
    tax_rate = serializers.DecimalField(
        max_digits=7,
        decimal_places=4,
        min_value=Decimal('0'),
        required=False,
    )
    tax_recoverable = serializers.BooleanField(required=False)
    notes = serializers.CharField(allow_blank=True, required=False)
    packet = serializers.PrimaryKeyRelatedField(read_only=True)

    workspace_field_lookups = {'seeds': 'workspace'}

    def validate(self, attrs):
        existing_line = self.instance.receipt.lines.get() if self.instance else None
        certainty = attrs.get(
            'quantity_certainty',
            existing_line.quantity_certainty if existing_line else None,
        )
        quantity = attrs.get(
            'quantity',
            existing_line.quantity if existing_line else None,
        )
        if certainty == QuantityCertainty.UNKNOWN and quantity is not None:
            raise ValidationError({
                'quantity': 'Leave quantity blank when it is unknown.',
            })
        if certainty != QuantityCertainty.UNKNOWN:
            if quantity is None or quantity <= 0:
                raise ValidationError({
                    'quantity': 'Exact and estimated packets require a positive quantity.',
                })
        if self.instance and 'seeds' in attrs:
            if attrs['seeds'].pk != self.instance.seeds_id:
                raise ValidationError({
                    'seeds': 'Create a new draft to change the seed catalog.',
                })
        return attrs

    def create(self, validated_data):
        request = self.context['request']
        try:
            return create_packet_receipt_draft(
                self.context['view'].get_current_workspace(),
                request.user,
                validated_data,
            )
        except DjangoValidationError as exc:
            raise ValidationError(_model_errors(exc)) from exc

    def update(self, instance, validated_data):
        try:
            return update_packet_receipt_draft(instance, validated_data)
        except DjangoValidationError as exc:
            raise ValidationError(_model_errors(exc)) from exc

    def to_representation(self, instance):
        """Flatten the linked receipt and its one line for the seed UI."""
        draft = instance
        line = draft.receipt.lines.get()
        return {
            'pk': draft.pk,
            'seeds': draft.seeds_id,
            'status': draft.receipt.status,
            'quantity_certainty': line.quantity_certainty,
            'quantity': (
                f'{line.quantity:.9f}' if line.quantity is not None else None
            ),
            'base_unit': line.item.base_unit,
            'line_price': f'{line.line_cost_ex_tax:.4f}',
            'supplier_lot_reference': line.supplier_lot_reference,
            'received_date': draft.receipt.received_date,
            'sow_by': line.expires_on,
            'supplier_reference': draft.receipt.supplier_reference,
            'tax_rate': f'{draft.receipt.tax_rate:.4f}',
            'tax_recoverable': draft.receipt.tax_recoverable,
            'notes': draft.notes,
            'packet': draft.packet_id,
        }


class PacketReconciliationSerializer(serializers.Serializer):
    """Validate an explicit physical packet count."""

    counted_quantity = serializers.DecimalField(
        max_digits=24,
        decimal_places=9,
        min_value=Decimal('0'),
    )
    quantity_certainty = serializers.ChoiceField(
        choices=(QuantityCertainty.EXACT, QuantityCertainty.ESTIMATED),
    )
    reason = serializers.CharField(allow_blank=False, trim_whitespace=True)

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class SeedsViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Configure seed catalogs and their paired inventory units."""

    queryset = Seeds.objects.select_related('inventory_item')
    serializer_class = SeedsSerializer


class SeedPacketCurrentViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Expose usable packet selectors and explicit physical counts."""

    queryset = SeedPacket.objects.select_related(
        'stock_lot__item',
        'storage_location',
    )
    serializer_class = SeedPacketSerializer
    http_method_names = ['get', 'patch', 'head', 'options', 'post']

    def create(self, request, *args, **kwargs):
        """Require packet creation to go through an auditable receipt."""
        del args, kwargs
        raise MethodNotAllowed(
            request.method,
            detail='Receive seed packets through /seeds/packet-receipts/.',
        )

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action != 'list':
            return queryset
        usable_ids = []
        for packet in queryset:
            if not packet.stock_lot_id or not packet.storage_location_id:
                if not packet.empty:
                    usable_ids.append(packet.pk)
                continue
            snapshot = packet_inventory_snapshot(packet)
            remaining = snapshot['remaining_quantity']
            if remaining is None or remaining > 0:
                usable_ids.append(packet.pk)
        return queryset.filter(pk__in=usable_ids)

    @action(detail=True, methods=['post'])
    def reconcile(self, request, pk=None):  # pylint: disable=unused-argument
        """Record a physical count and return the refreshed packet balance."""
        serializer = PacketReconciliationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        try:
            reconciliation = reconcile_packet_quantity(
                self.get_object(),
                request.user,
                values['counted_quantity'],
                values['quantity_certainty'],
                values['reason'],
            )
        except DjangoValidationError as exc:
            raise ValidationError(_model_errors(exc)) from exc
        packet = self.get_queryset().get(pk=reconciliation.packet_id)
        return Response(self.get_serializer(packet).data)


class SeedPacketAllViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Expose all packet states while allowing notes-only updates."""

    queryset = SeedPacket.objects.select_related(
        'stock_lot__item',
        'storage_location',
    )
    serializer_class = SeedPacketSerializer
    http_method_names = ['get', 'patch', 'head', 'options']


class PacketReceiptDraftViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Edit, confirm, or cancel seed-focused receipt drafts."""

    queryset = SeedPacketReceiptDraft.objects.select_related(
        'seeds',
        'receipt',
        'storage_location',
        'packet',
    ).prefetch_related('receipt__lines__item')
    serializer_class = PacketReceiptDraftSerializer
    bind_workspace_on_create = False
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def perform_create(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        try:
            delete_packet_receipt_draft(instance)
        except DjangoValidationError as exc:
            raise ValidationError(_model_errors(exc)) from exc

    @action(detail=True, methods=['post'])
    def post(self, request, pk=None):  # pylint: disable=unused-argument
        """Confirm a draft and return its newly usable packet."""
        try:
            _receipt, packet = post_packet_receipt(
                self.get_object(),
                request.user,
            )
        except DjangoValidationError as exc:
            raise ValidationError(_model_errors(exc)) from exc
        return Response(
            SeedPacketSerializer(packet).data,
            status=status.HTTP_201_CREATED,
        )


router = routers.DefaultRouter()
router.register(r'seeds', SeedsViewSet)
router.register(r'packet-receipts', PacketReceiptDraftViewSet)
router.register(r'packets/all', SeedPacketAllViewSet, 'AllSeedPackets')
router.register(r'packets', SeedPacketCurrentViewSet)
