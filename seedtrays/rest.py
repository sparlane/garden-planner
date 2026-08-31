"""
Rest related classes for seed trays
"""
# pylint: disable=duplicate-code
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework_nested import routers

from inventory.ledger import post_receipt, unit_is_in_use, unit_physical_state
from inventory.models import (
    InventoryItem,
    StockReceipt,
    StockReceiptLine,
)
from inventory.serialized_rest import InventoryUnitSerializer
from inventory.units import UnitCode
from locations.models import Location
from supplies.defaults import ensure_default_supplier
from supplies.models import Supplier
from workspaces.scoping import CurrentWorkspaceSerializerMixin, CurrentWorkspaceViewSetMixin

from .generations import open_generation_for
from .models import SeedTrayModel, SeedTray, SeedTrayCell, SeedTrayGeneration


class SeedTrayModelSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for a SeedTrayModel
    """
    class Meta:
        model = SeedTrayModel
        fields = ['pk', 'identifier', 'inventory_item', 'description', 'height', 'x_size', 'y_size', 'x_cells', 'y_cells', 'cell_size_ml']
        extra_kwargs = {'inventory_item': {'required': False}}

    workspace_field_lookups = {'inventory_item': 'workspace'}

    def validate(self, data):  # pylint: disable=arguments-renamed
        """Keep cell-grid dimensions stable after trays have been created."""
        errors = {}
        if self.instance is not None and self.instance.seedtray_set.exists():
            for field in ('x_cells', 'y_cells'):
                if field in data and data[field] != getattr(self.instance, field):
                    errors[field] = 'Cannot change cell dimensions after trays have been created.'
        item = data.get('inventory_item')
        if item:
            if item.category != InventoryItem.Category.TRAY:
                errors['inventory_item'] = 'Select a tray-category inventory item.'
            elif item.tracking_mode != InventoryItem.TrackingMode.SERIALIZED:
                errors['inventory_item'] = 'Select a serialized inventory item.'
            elif item.base_unit != UnitCode.EACH:
                errors['inventory_item'] = 'Tray inventory items must use each.'
            if self.instance and item.pk != self.instance.inventory_item_id:
                has_history = self.instance.seedtray_set.exists() or self.instance.inventory_item.stock_history_started_at
                if has_history:
                    errors['inventory_item'] = 'Cannot change the inventory item after tray or stock history exists.'
        if errors:
            raise serializers.ValidationError(errors)
        return data


class SeedTraySerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for a Seed Tray
    """
    inventory = InventoryUnitSerializer(source='inventory_unit', read_only=True)
    active_generation = serializers.SerializerMethodField()
    generation_review_required = serializers.SerializerMethodField()

    class Meta:
        model = SeedTray
        fields = [
            'pk', 'model', 'inventory_unit', 'inventory', 'created', 'notes',
            'active_generation', 'generation_review_required',
        ]
        read_only_fields = [
            'inventory_unit', 'inventory', 'created',
            'active_generation', 'generation_review_required',
        ]

    workspace_field_lookups = {'model': 'workspace'}

    def _open_generation(self, tray):
        """Return the fill this tray is currently using, or None if empty."""
        return open_generation_for(tray)

    def get_active_generation(self, tray):
        """Report which fill the tray is on, so a screen can say `empty`."""
        generation = self._open_generation(tray)
        return generation.pk if generation else None

    def get_generation_review_required(self, tray):
        """Flag a migrated fill an operator has not confirmed yet."""
        generation = self._open_generation(tray)
        if generation is None:
            return False
        return generation.review_state == SeedTrayGeneration.ReviewState.NEEDS_REVIEW

    def validate(self, data):  # pylint: disable=arguments-renamed
        """A created tray keeps the model that defined its generated cell grid."""
        if self.instance is not None and 'model' in data:
            if data['model'].pk != self.instance.model_id:
                raise serializers.ValidationError({
                    'model': 'Cannot change the model of an existing tray.'
                })
        return data


class SeedTrayReceiptSerializer(
    CurrentWorkspaceSerializerMixin,
    serializers.Serializer,
):
    """Validate an immediately posted receipt for one tray model."""

    supplier = serializers.PrimaryKeyRelatedField(
        queryset=Supplier.objects.all(),
        required=False,
    )
    received_date = serializers.DateField()
    supplier_reference = serializers.CharField(allow_blank=True, required=False, default='')
    quantity = serializers.IntegerField(min_value=1)
    line_cost_ex_tax = serializers.DecimalField(
        max_digits=18,
        decimal_places=4,
        min_value=Decimal('0'),
        required=False,
        default=Decimal('0'),
    )
    destination = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
    )
    notes = serializers.CharField(allow_blank=True, required=False, default='')
    workspace_field_lookups = {
        'supplier': 'workspace',
        'destination': 'workspace',
    }

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class SeedTrayCellSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for a Seed Tray Cell
    """
    class Meta:
        model = SeedTrayCell
        fields = ['pk', 'tray', 'x_position', 'y_position']

    workspace_field_lookups = {'tray': 'workspace'}

    def validate(self, data):  # pylint: disable=arguments-renamed
        """Keep a cell on its original tray and within that tray's grid."""
        parent_tray = self.context.get('parent_tray')
        tray = parent_tray or data.get('tray') or getattr(self.instance, 'tray', None)

        if self.instance is not None and 'tray' in data:
            if data['tray'].pk != self.instance.tray_id:
                raise serializers.ValidationError({
                    'tray': 'Cannot move an existing cell to another tray.'
                })

        if tray is None:
            return data

        x_position = data.get('x_position', getattr(self.instance, 'x_position', None))
        y_position = data.get('y_position', getattr(self.instance, 'y_position', None))
        errors = {}
        if x_position is not None and x_position >= tray.model.x_cells:
            errors['x_position'] = f'Must be less than {tray.model.x_cells}.'
        if y_position is not None and y_position >= tray.model.y_cells:
            errors['y_position'] = f'Must be less than {tray.model.y_cells}.'
        if errors:
            raise serializers.ValidationError(errors)
        return data


class NestedSeedTrayCellSerializer(SeedTrayCellSerializer):
    """Cell serializer whose tray is supplied by the nested URL."""
    class Meta(SeedTrayCellSerializer.Meta):
        extra_kwargs = {'tray': {'read_only': True}}


class SeedTrayModelsViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SeedTrayModels
    """
    queryset = SeedTrayModel.objects.order_by('pk')
    serializer_class = SeedTrayModelSerializer

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def receive(self, request, pk=None):  # pylint: disable=unused-argument
        """Receive and serialize exact physical trays for this model."""
        tray_model = self.get_object()
        serializer = SeedTrayReceiptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        workspace = self.get_current_workspace()
        receipt = StockReceipt.objects.create(
            workspace=workspace,
            supplier=values.get('supplier') or ensure_default_supplier(workspace),
            received_date=values['received_date'],
            supplier_reference=values['supplier_reference'],
            currency_code=workspace.currency_code,
            notes=values['notes'],
            created_by=request.user,
        )
        line = StockReceiptLine.objects.create(
            receipt=receipt,
            item=tray_model.inventory_item,
            quantity=Decimal(values['quantity']),
            unit_code=UnitCode.EACH,
            base_quantity=Decimal(values['quantity']),
            line_cost_ex_tax=values['line_cost_ex_tax'],
            supplier_cost_incl_tax=values['line_cost_ex_tax'],
            destination=values['destination'],
        )
        try:
            receipt, lots = post_receipt(receipt, request.user)
        except DjangoValidationError as exc:
            details = exc.message_dict if hasattr(exc, 'message_dict') else exc.messages
            raise ValidationError(details) from exc
        trays = SeedTray.objects.filter(
            inventory_unit__source_lot__in=lots,
        ).select_related(
            'model',
            'inventory_unit__item',
            'inventory_unit__source_lot',
            'inventory_unit__current_location',
        ).order_by('pk')
        return Response(
            {
                'receipt': receipt.pk,
                'receipt_line': line.pk,
                'trays': SeedTraySerializer(trays, many=True).data,
            },
            status=status.HTTP_201_CREATED,
        )


class SeedTrayAllViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of all SeedTrays
    """
    queryset = SeedTray.objects.select_related(
        'model',
        'inventory_unit__item',
        'inventory_unit__source_lot__receipt_line',
        'inventory_unit__current_location',
    ).prefetch_related('inventory_unit__movements').order_by('pk')
    serializer_class = SeedTraySerializer
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_queryset(self):
        """Filter trays by model and serialized inventory state."""
        queryset = super().get_queryset()
        model = self.request.query_params.get('model')
        location = self.request.query_params.get('location')
        if model:
            try:
                queryset = queryset.filter(model_id=int(model))
            except ValueError as exc:
                raise ValidationError({'model': 'Use an integer model ID.'}) from exc
        if location:
            try:
                queryset = queryset.filter(inventory_unit__current_location_id=int(location))
            except ValueError as exc:
                raise ValidationError({'location': 'Use an integer location ID.'}) from exc
        state = self.request.query_params.get('physical_state')
        if state:
            if state not in {
                'available',
                'quarantined',
                'lost',
                'retired',
                'dispatched',
                'returned',
            }:
                raise ValidationError({'physical_state': 'Select a valid physical state.'})
            queryset = queryset.filter(
                pk__in=[
                    tray.pk
                    for tray in queryset
                    if unit_physical_state(tray.inventory_unit) == state
                ],
            )
        in_use = self.request.query_params.get('in_use')
        if in_use is not None:
            if in_use not in {'true', 'false'}:
                raise ValidationError({'in_use': 'Use true or false.'})
            expected = in_use == 'true'
            queryset = queryset.filter(
                pk__in=[
                    tray.pk
                    for tray in queryset
                    if unit_is_in_use(tray.inventory_unit) == expected
                ],
            )
        return queryset


class SeedTrayCellViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of all SeedTrayCells
    """
    queryset = SeedTrayCell.objects.order_by('pk')
    serializer_class = SeedTrayCellSerializer
    workspace_lookup = 'tray__workspace'
    bind_workspace_on_create = False


class SeedTrayCellFilteredViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SeedTrayCells filtered by tray
    """
    queryset = SeedTrayCell.objects.all()
    serializer_class = NestedSeedTrayCellSerializer
    workspace_lookup = 'tray__workspace'
    bind_workspace_on_create = False
    pagination_class = None
    _parent_tray = None

    def get_parent_tray(self):
        """Resolve and cache the tray identified by the nested URL."""
        if self._parent_tray is None:
            self._parent_tray = get_object_or_404(
                SeedTray.objects.select_related('model'),
                pk=self.kwargs['seedtray_pk'],
                workspace=self.get_current_workspace(),
            )
        return self._parent_tray

    def get_queryset(self):
        return super().get_queryset().filter(tray=self.get_parent_tray())

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['parent_tray'] = self.get_parent_tray()
        return context

    def perform_create(self, serializer):
        serializer.save(tray=self.get_parent_tray())


router = routers.SimpleRouter()
router.register(r'seedtraymodels', SeedTrayModelsViewSet)
router.register(r'seedtrays', SeedTrayAllViewSet)
router.register(r'seedtraycells', SeedTrayCellViewSet)

filtered_router = routers.NestedSimpleRouter(router, r'seedtrays', lookup='seedtray')
filtered_router.register(r'cells', SeedTrayCellFilteredViewSet, basename='seedtray-cells')
