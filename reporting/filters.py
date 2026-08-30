"""Strict validated query schemas for Nursery reports."""

from rest_framework import serializers
from rest_framework.fields import empty


class ReportBooleanField(serializers.BooleanField):
    """A three-state query filter: true, false, or not asked.

    `BooleanField` reads a missing key as False, because an HTML form omits an
    unticked checkbox and there is no other way to tell. A report filter is a
    query string rather than a form, and every one of these narrows the rows:
    reading "not asked" as "false" made the default view of a report the one
    that hides the rows the filter is about -- low stock, trays in use,
    overdue orders. Restoring the sentinel leaves the filter out of the
    validated data entirely when nobody asked for it.
    """

    default_empty_html = empty


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
    low_stock = ReportBooleanField(required=False)


class SerializedTrayFilters(BaseReportFilters):  # pylint: disable=abstract-method
    """Filters for serialized tray state."""

    item = serializers.IntegerField(required=False, min_value=1)
    location = serializers.IntegerField(required=False, min_value=1)
    physical_state = serializers.ChoiceField(
        required=False,
        choices=('available', 'quarantined', 'lost', 'retired', 'dispatched', 'returned'),
    )
    in_use = ReportBooleanField(required=False)


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


class GerminationFilters(BaseReportFilters):  # pylint: disable=abstract-method
    """Filters for observed germination rate per sowing."""

    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    variety = serializers.IntegerField(required=False, min_value=1)
    batch = serializers.IntegerField(required=False, min_value=1)
    seed_tray = serializers.IntegerField(required=False, min_value=1)
    provisional = ReportBooleanField(required=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        date_from, date_to = attrs.get('date_from'), attrs.get('date_to')
        if date_from and date_to and date_to < date_from:
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
    overdue = ReportBooleanField(required=False)


class DashboardFilters(BaseReportFilters):  # pylint: disable=abstract-method
    """Optional period override for the Nursery dashboard."""

    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)


class GstPeriodFilters(BaseReportFilters):  # pylint: disable=abstract-method
    """Filters for GST period totals.

    The range is optional: with none given the report answers the question the
    operator actually has, which is what the open period looks like. The
    date_to/date_from check is a fourth copy of the same block in this file —
    extracting a shared mixin would touch every existing filter class and
    belongs in its own change.
    """

    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)

    def validate(self, attrs):
        """Reject a range that ends before it starts."""
        date_from, date_to = attrs.get('date_from'), attrs.get('date_to')
        if date_from and date_to and date_to < date_from:
            raise serializers.ValidationError({
                'date_to': 'The range end must not be before its start.',
            })
        return attrs


class SupplyDocumentFilters(BaseReportFilters):  # pylint: disable=abstract-method
    """Filters for the register of issued documents and corrections.

    The date range is matched against each row's own date — a document by when
    it was issued, a correction by when it was made — because a credit note
    belongs to the period it was issued in rather than to the period of the
    supply it corrects.
    """

    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    order = serializers.IntegerField(required=False, min_value=1)
    customer = serializers.IntegerField(required=False, min_value=1)
    kind = serializers.ChoiceField(required=False, choices=('supply', 'credit', 'debit'))

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs.get('date_from') and attrs.get('date_to'):
            if attrs['date_to'] < attrs['date_from']:
                raise serializers.ValidationError({
                    'date_to': 'The end must not be before the start.',
                })
        return attrs


class GstEntryFilters(GstPeriodFilters):  # pylint: disable=abstract-method
    """Filters for the entries behind a period total, as the drill-down links use."""

    period = serializers.CharField(required=False)
    kind = serializers.ChoiceField(
        required=False,
        choices=('supply', 'supply_credit', 'purchase', 'input_tax_adjustment'),
    )
    tax_code = serializers.ChoiceField(
        required=False,
        choices=(
            'standard', 'zero_rated', 'exempt', 'out_of_scope',
            'unclassified', 'unknown',
        ),
    )
    exclusion = serializers.ChoiceField(
        required=False,
        choices=('no_registration', 'deregistered_gap', 'input_tax_awaiting_payment'),
    )
