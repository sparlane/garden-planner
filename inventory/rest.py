"""REST resources for inventory catalog configuration."""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework import routers, serializers, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView
from rest_framework.response import Response

from workspaces.models import get_current_workspace
from workspaces.scoping import CurrentWorkspaceSerializerMixin, CurrentWorkspaceViewSetMixin

from .models import InventoryItem, ItemUnitConversion
from .units import UNIT_DEFINITIONS, get_unit_definition


def _model_validation_errors(error):
    """Translate Django model validation into DRF field errors."""
    if hasattr(error, 'message_dict'):
        return error.message_dict
    return error.messages


class InventoryItemSerializer(serializers.ModelSerializer):
    """Serialize one workspace inventory catalog item."""

    base_unit_dimension = serializers.SerializerMethodField()
    usage_rate_unit_dimension = serializers.SerializerMethodField()

    class Meta:
        model = InventoryItem
        fields = [
            'pk',
            'name',
            'sku',
            'category',
            'description',
            'active',
            'base_unit',
            'base_unit_dimension',
            'tracking_mode',
            'default_usage_basis',
            'default_usage_rate',
            'usage_rate_unit',
            'usage_rate_unit_dimension',
            'default_fixed_quantity',
            'stock_history_started_at',
            'created',
            'updated',
        ]
        read_only_fields = [
            'base_unit_dimension',
            'usage_rate_unit_dimension',
            'stock_history_started_at',
            'created',
            'updated',
        ]

    def get_base_unit_dimension(self, item):
        """Expose the physical dimension needed to render compatible forms."""
        return get_unit_definition(item.base_unit).dimension

    def get_usage_rate_unit_dimension(self, item):
        """Expose the optional usage denominator dimension."""
        if not item.usage_rate_unit:
            return None
        return get_unit_definition(item.usage_rate_unit).dimension

    def validate(self, attrs):
        """Apply category defaults and model-wide configuration validation."""
        if self.instance and self.instance.stock_history_started_at:
            errors = {}
            for field in ('base_unit', 'tracking_mode'):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    errors[field] = (
                        'Create a new item instead of changing this after stock history exists.'
                    )
            if errors:
                raise ValidationError(errors)

        if self.instance is None and 'tracking_mode' not in attrs:
            attrs['tracking_mode'] = InventoryItem.default_tracking_mode(
                attrs.get('category'),
            )

        candidate = self.instance or InventoryItem(
            workspace=get_current_workspace(),
        )
        for field, value in attrs.items():
            setattr(candidate, field, value)
        try:
            candidate.full_clean()
        except DjangoValidationError as exc:
            raise ValidationError(_model_validation_errors(exc)) from exc
        return attrs


class ItemUnitConversionSerializer(
    CurrentWorkspaceSerializerMixin,
    serializers.ModelSerializer,
):
    """Serialize a package multiplier into an item's base unit."""

    base_unit = serializers.CharField(source='item.base_unit', read_only=True)
    base_unit_dimension = serializers.SerializerMethodField()

    class Meta:
        model = ItemUnitConversion
        fields = [
            'pk',
            'item',
            'label',
            'multiplier',
            'active',
            'base_unit',
            'base_unit_dimension',
            'created',
            'updated',
        ]
        read_only_fields = [
            'base_unit',
            'base_unit_dimension',
            'created',
            'updated',
        ]

    workspace_field_lookups = {'item': 'workspace'}

    def get_base_unit_dimension(self, conversion):
        """Return the item's physical dimension with its package multiplier."""
        return get_unit_definition(conversion.item.base_unit).dimension

    def validate(self, attrs):
        """Validate the complete conversion with current workspace ownership."""
        candidate = self.instance or ItemUnitConversion(
            workspace=get_current_workspace(),
        )
        for field, value in attrs.items():
            setattr(candidate, field, value)
        try:
            candidate.full_clean()
        except DjangoValidationError as exc:
            raise ValidationError(_model_validation_errors(exc)) from exc
        return attrs


class UnitRegistryView(APIView):
    """List immutable controlled-unit metadata."""

    http_method_names = ['get', 'head', 'options']

    def get(self, request):  # pylint: disable=unused-argument
        """Return stable codes and exact reference multipliers as strings."""
        units = [
            {
                'code': definition.code,
                'label': definition.label,
                'dimension': definition.dimension,
                'reference_unit': definition.reference_unit,
                'to_reference_multiplier': str(
                    definition.to_reference_multiplier,
                ),
            }
            for definition in UNIT_DEFINITIONS.values()
        ]
        return Response(units)


class InventoryItemViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """List and configure current-workspace inventory items."""

    queryset = InventoryItem.objects.all()
    serializer_class = InventoryItemSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        """Apply catalog filters without weakening workspace scope."""
        queryset = super().get_queryset()
        active = self.request.query_params.get('active')
        if active is not None:
            if active not in {'true', 'false'}:
                raise ValidationError({'active': 'Use true or false.'})
            queryset = queryset.filter(active=active == 'true')

        category = self.request.query_params.get('category')
        if category:
            if category not in InventoryItem.Category.values:
                raise ValidationError({'category': 'Select a valid category.'})
            queryset = queryset.filter(category=category)

        tracking_mode = self.request.query_params.get('tracking_mode')
        if tracking_mode:
            if tracking_mode not in InventoryItem.TrackingMode.values:
                raise ValidationError(
                    {'tracking_mode': 'Select a valid tracking mode.'},
                )
            queryset = queryset.filter(tracking_mode=tracking_mode)

        search = self.request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(sku__icontains=search) | Q(description__icontains=search))
        return queryset


class ItemUnitConversionViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """List and configure item-specific package units."""

    queryset = ItemUnitConversion.objects.select_related('item')
    serializer_class = ItemUnitConversionSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        """Filter package units by item and lifecycle state."""
        queryset = super().get_queryset()
        active = self.request.query_params.get('active')
        if active is not None:
            if active not in {'true', 'false'}:
                raise ValidationError({'active': 'Use true or false.'})
            queryset = queryset.filter(active=active == 'true')

        item = self.request.query_params.get('item')
        if item is not None:
            try:
                item_pk = int(item)
            except ValueError as exc:
                raise ValidationError({'item': 'Use an integer item ID.'}) from exc
            queryset = queryset.filter(item_id=item_pk)
        return queryset


router = routers.DefaultRouter()
router.register(r'items', InventoryItemViewSet)
router.register(r'conversions', ItemUnitConversionViewSet)
