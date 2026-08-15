"""Strict validated query schemas for Nursery reports."""

from rest_framework import serializers


class BaseReportFilters(serializers.Serializer):  # pylint: disable=abstract-method
    """Pagination is rendered centrally and never changes report calculations."""

    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1)

    def to_internal_value(self, data):
        """Reject misspelled filters instead of silently changing report scope."""
        unknown = sorted(set(data) - set(self.fields))
        if unknown:
            raise serializers.ValidationError({
                name: 'Unknown report filter.' for name in unknown
            })
        return super().to_internal_value(data)


class InventoryBalanceFilters(BaseReportFilters):  # pylint: disable=abstract-method
    """Filters for exact lot/location balances."""

    item = serializers.IntegerField(required=False, min_value=1)
    lot = serializers.IntegerField(required=False, min_value=1)
    location = serializers.IntegerField(required=False, min_value=1)
    expires_before = serializers.DateField(required=False)
    low_stock = serializers.BooleanField(required=False)


class SerializedTrayFilters(BaseReportFilters):  # pylint: disable=abstract-method
    """Filters for serialized tray state."""

    item = serializers.IntegerField(required=False, min_value=1)
    location = serializers.IntegerField(required=False, min_value=1)
    physical_state = serializers.ChoiceField(
        required=False,
        choices=('available', 'quarantined', 'lost', 'retired', 'dispatched', 'returned'),
    )
    in_use = serializers.BooleanField(required=False)


class MovementFilters(BaseReportFilters):  # pylint: disable=abstract-method
    """Filters for immutable movement history."""

    date_from = serializers.DateTimeField(required=False)
    date_to = serializers.DateTimeField(required=False)
    item = serializers.IntegerField(required=False, min_value=1)
    lot = serializers.IntegerField(required=False, min_value=1)
    location = serializers.IntegerField(required=False, min_value=1)
    movement_type = serializers.CharField(required=False)
    reference = serializers.CharField(required=False, allow_blank=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs.get('date_from') and attrs.get('date_to'):
            if attrs['date_to'] < attrs['date_from']:
                raise serializers.ValidationError({
                    'date_to': 'The end must not be before the start.',
                })
        return attrs


class StocktakeVarianceFilters(BaseReportFilters):  # pylint: disable=abstract-method
    """Filters for review and posted stocktake differences."""

    date_from = serializers.DateTimeField(required=False)
    date_to = serializers.DateTimeField(required=False)
    stocktake = serializers.IntegerField(required=False, min_value=1)
    location = serializers.IntegerField(required=False, min_value=1)
    kind = serializers.CharField(required=False)


class ProductionFilters(BaseReportFilters):  # pylint: disable=abstract-method
    """Filters for batch production outcomes and cost reconciliation."""

    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    variety = serializers.IntegerField(required=False, min_value=1)
    batch = serializers.IntegerField(required=False, min_value=1)
    location = serializers.IntegerField(required=False, min_value=1)
    garden_square = serializers.IntegerField(required=False, min_value=1)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs.get('date_from') and attrs.get('date_to'):
            if attrs['date_to'] < attrs['date_from']:
                raise serializers.ValidationError({
                    'date_to': 'The end must not be before the start.',
                })
        return attrs


class TraceFilters(BaseReportFilters):  # pylint: disable=abstract-method
    """Pagination-only schema for one exact traceability identity."""


class CommerceFilters(BaseReportFilters):  # pylint: disable=abstract-method
    """Shared exact dimensions for order and profitability reports."""

    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    variety = serializers.IntegerField(required=False, min_value=1)
    batch = serializers.IntegerField(required=False, min_value=1)
    customer = serializers.IntegerField(required=False, min_value=1)
    location = serializers.IntegerField(required=False, min_value=1)
    garden_square = serializers.IntegerField(required=False, min_value=1)
    fulfillment = serializers.CharField(required=False, allow_blank=False)
    order = serializers.CharField(required=False, allow_blank=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs.get('date_from') and attrs.get('date_to'):
            if attrs['date_to'] < attrs['date_from']:
                raise serializers.ValidationError({
                    'date_to': 'The end must not be before the start.',
                })
        return attrs


class OrderFilters(CommerceFilters):  # pylint: disable=abstract-method
    """Operational order-state filters."""

    status = serializers.CharField(required=False)
    overdue = serializers.BooleanField(required=False)


class DashboardFilters(BaseReportFilters):  # pylint: disable=abstract-method
    """Optional period override for the Nursery dashboard."""

    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
