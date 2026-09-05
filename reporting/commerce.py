"""Order, direct-cost profitability, and Nursery dashboard calculations."""

# Financial rows deliberately retain every dimension and source identifier.
# pylint: disable=too-many-locals,too-many-statements,too-many-branches

from calendar import monthrange
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from costing.models import CostAllocation
from plantings.lifecycle import lifecycle_summaries
from plantings.loss import CAUSE_OF_EVENT, LOSS_CAUSES, LOSS_EVENTS, empty_totals, loss_by_cause
from plantings.models import (
    CohortEvent,
    PlantLifecycleEvent,
    ProductionBatch,
    SpecificPlantLocation,
)
from sales.commerce import order_commerce_summary
from sales.models import (
    Fulfillment,
    FulfillmentLine,
    RefundLine,
    SalesOrder,
    SalesReturnLine,
)

from .common import Report, decimal_string
from .inventory import inventory_balances
from .production import production_batches


ZERO = Decimal('0')
RESTORES_COGS = {
    SalesReturnLine.Outcome.AVAILABLE,
    SalesReturnLine.Outcome.QUARANTINED,
}


def _local_today(workspace):
    return timezone.now().astimezone(ZoneInfo(workspace.timezone)).date()


def _month_filters(workspace, filters):
    """Default financial periods to the current workspace-local month."""
    result = dict(filters)
    today = _local_today(workspace)
    result.setdefault('date_from', today.replace(day=1).isoformat())
    result.setdefault(
        'date_to',
        today.replace(day=monthrange(today.year, today.month)[1]).isoformat(),
    )
    return result


def _date_bounds(workspace, filters):
    zone = ZoneInfo(workspace.timezone)
    start = datetime.combine(
        datetime.fromisoformat(filters['date_from']).date(), time.min, zone,
    )
    end = datetime.combine(
        datetime.fromisoformat(filters['date_to']).date() + timedelta(days=1),
        time.min,
        zone,
    )
    return start, end


def order_report(workspace, filters):
    """Report operational commitments, fulfillment, cash, and amount due."""
    queryset = SalesOrder.objects.filter(workspace=workspace).select_related(
        'customer',
    ).prefetch_related('lines__allocations', 'fulfillments', 'returns', 'refunds', 'payments')
    if filters.get('date_from'):
        queryset = queryset.filter(order_date__gte=filters['date_from'])
    if filters.get('date_to'):
        queryset = queryset.filter(order_date__lte=filters['date_to'])
    if filters.get('customer'):
        queryset = queryset.filter(customer_id=filters['customer'])
    if filters.get('order'):
        queryset = queryset.filter(order_number__icontains=filters['order'])
    if filters.get('fulfillment'):
        queryset = queryset.filter(
            fulfillments__fulfillment_number__icontains=filters['fulfillment'],
        )
    if filters.get('variety'):
        queryset = queryset.filter(lines__variety_id=filters['variety'])
    if filters.get('batch'):
        batch = filters['batch']
        matches_batch = Q(lines__allocations__plant__batch_id=batch)
        matches_batch |= Q(lines__allocations__plant_cohort__batch_id=batch)
        queryset = queryset.filter(matches_batch)
    valid_statuses = {choice for choice, _label in SalesOrder.Status.choices}
    if filters.get('status'):
        if filters['status'] not in valid_statuses:
            raise ValidationError({'status': 'Select a valid order status.'})
        queryset = queryset.filter(status=filters['status'])
    today = _local_today(workspace)
    rows = []
    for order in queryset.distinct().order_by('-order_date', '-pk'):
        summary = order_commerce_summary(order)
        overdue = bool(
            order.requested_date and order.requested_date < today and (
                order.status not in {
                    SalesOrder.Status.FULFILLED, SalesOrder.Status.CANCELLED,
                }
            )
        )
        if 'overdue' in filters and overdue != filters['overdue']:
            continue
        effective_fulfillments = order.fulfillments.filter(
            reversal_of__isnull=True, reversal__isnull=True,
        )
        effective_returns = order.returns.filter(
            reversal_of__isnull=True, reversal__isnull=True,
        )
        effective_refunds = order.refunds.filter(
            reversal_of__isnull=True, reversal__isnull=True,
        )
        rows.append({
            'order_id': order.pk,
            'order_number': order.order_number,
            'order_date': order.order_date,
            'requested_date': order.requested_date,
            'customer_id': order.customer_id,
            'customer_name': order.customer.name if order.customer_id else None,
            'status': order.status,
            'overdue': overdue,
            'requested_quantity': summary['requested_quantity'],
            'reserved_quantity': summary['reserved_quantity'],
            'committed_forward_quantity': summary['committed_forward_quantity'],
            'short_quantity': summary['short_quantity'],
            'fulfilled_quantity': summary['fulfilled_quantity'],
            'returned_quantity': summary['returned_quantity'],
            'fulfillments': effective_fulfillments.count(),
            'returns': effective_returns.count(),
            'refunds': effective_refunds.count(),
            'order_total_incl_tax': decimal_string(order.total_incl_tax, 4),
            'fulfilled_total_incl_tax': summary['fulfilled_total_incl_tax'],
            'refunded_total_incl_tax': summary['refunded_total_incl_tax'],
            'paid_total': summary['paid_total'],
            'net_paid_total': summary['net_paid_total'],
            'outstanding_total': summary['outstanding_total'],
            'overpaid_total': summary['overpaid_total'],
            'payment_status': summary['payment_status'],
            'currency_code': order.currency_code,
        })
    totals = defaultdict(lambda: defaultdict(Decimal))
    for row in rows:
        values = totals[row['currency_code']]
        for key in (
                'order_total_incl_tax', 'fulfilled_total_incl_tax',
                'refunded_total_incl_tax', 'paid_total', 'net_paid_total',
                'outstanding_total', 'overpaid_total'):
            values[key] += Decimal(row[key])
    return Report(
        name='orders', filters=filters, rows=rows,
        columns=tuple(rows[0]) if rows else (
            'order_id', 'order_number', 'order_date', 'requested_date',
            'customer_id', 'customer_name', 'status', 'overdue',
            'requested_quantity', 'reserved_quantity',
            'committed_forward_quantity', 'short_quantity', 'fulfilled_quantity',
            'returned_quantity', 'fulfillments', 'returns', 'refunds',
            'order_total_incl_tax', 'fulfilled_total_incl_tax',
            'refunded_total_incl_tax', 'paid_total', 'net_paid_total',
            'outstanding_total', 'overpaid_total', 'payment_status', 'currency_code',
        ),
        totals={
            'orders': len(rows),
            'open_orders': sum(row['status'] in {
                SalesOrder.Status.CONFIRMED,
                SalesOrder.Status.PARTIALLY_FULFILLED,
            } for row in rows),
            'overdue_orders': sum(row['overdue'] for row in rows),
            'currencies': [{
                'currency_code': currency,
                **{key: decimal_string(value, 4) for key, value in values.items()},
            } for currency, values in sorted(totals.items())],
        },
        reconciliation={
            'cash_equation': 'net paid = effective payments - effective refunds',
            'due_equation': 'amount due = max(order total - refunds - net paid, 0)',
        },
    )


def _commerce_queryset(workspace, filters, start, end):
    queryset = FulfillmentLine.objects.filter(
        fulfillment__workspace=workspace,
        fulfillment__fulfilled_at__gte=start,
        fulfillment__fulfilled_at__lt=end,
        fulfillment__reversal_of__isnull=True,
        fulfillment__reversal__isnull=True,
    ).select_related(
        'fulfillment__order__customer', 'allocation__line',
        'allocation__plant__batch', 'allocation__inventory_unit',
        'allocation__plant_cohort__batch',
    )
    if filters.get('variety'):
        queryset = queryset.filter(allocation__line__variety_id=filters['variety'])
    if filters.get('batch'):
        batch = filters['batch']
        matches_batch = Q(allocation__plant__batch_id=batch)
        matches_batch |= Q(allocation__plant_cohort__batch_id=batch)
        queryset = queryset.filter(matches_batch)
    if filters.get('customer'):
        queryset = queryset.filter(fulfillment__order__customer_id=filters['customer'])
    if filters.get('fulfillment'):
        queryset = queryset.filter(
            fulfillment__fulfillment_number__icontains=filters['fulfillment'],
        )
    if filters.get('order'):
        queryset = queryset.filter(
            fulfillment__order__order_number__icontains=filters['order'],
        )
    return queryset.order_by('fulfillment__fulfilled_at', 'pk')


def _cogs_bucket(allocation):
    """Return which cost-of-sale column one dispatched promise belongs in.

    A count sold out of a cohort is nursery plants the business grew, so it
    belongs beside the identified seedlings rather than in the general bucket:
    the difference between the two is whether anybody wrote the plants down
    one by one, which is not a difference in what was sold. Anonymous stock
    out of an inventory lot really is neither a plant nor a lent tray, and
    stays where it was.
    """
    if allocation.plant_id or allocation.plant_cohort_id:
        return 'plant_cogs'
    if allocation.inventory_unit_id:
        return 'tray_cogs'
    return 'other_cogs'


def _allocation_batch_id(allocation):
    """Return the production batch a dispatched promise's plants came from.

    A cohort reaches its batch the same way a plant does, so a cohort sale is
    reportable by batch and lands in the same profitability row as the
    identified half of the very same crop.
    """
    if allocation.plant_id:
        return allocation.plant.batch_id
    if allocation.plant_cohort_id:
        return allocation.plant_cohort.batch_id
    return None


def _matches_place(line, filters, occurred_at):
    """Return whether a dispatched line was standing where the filter asks.

    A cohort stands in one place as a block rather than plant by plant, and it
    keeps no history of where it stood when: the location on the block is the
    only answer there is, so a garden-square filter — which is a question
    about one plant in one square — never matches one.
    """
    if not filters.get('location') and not filters.get('garden_square'):
        return True
    if line.allocation.plant_cohort_id:
        if filters.get('garden_square'):
            return False
        return line.allocation.plant_cohort.location_id == int(filters['location'])
    if not line.allocation.plant_id:
        return False
    locations = SpecificPlantLocation.objects.filter(
        specific_plant_id=line.allocation.plant_id,
        started__lte=occurred_at,
    ).filter(Q(ended__isnull=True) | Q(ended__gte=occurred_at))
    if filters.get('location'):
        locations = locations.filter(location_id=filters['location'])
    if filters.get('garden_square'):
        locations = locations.filter(garden_square_id=filters['garden_square'])
    return locations.exists()


def _base_financial_row(kind, occurred_at, currency, source_id):
    return {
        'kind': kind,
        'occurred_at': occurred_at,
        'source_id': source_id,
        'fulfillment_id': None,
        'fulfillment_number': None,
        'fulfillment_line_id': None,
        'order_id': None,
        'order_number': None,
        'customer_id': None,
        'variety_id': None,
        'batch_id': None,
        'plant_id': None,
        'inventory_unit_id': None,
        'lot_id': None,
        'cohort_id': None,
        'loss_cause': None,
        'gross_sales': decimal_string(ZERO, 4),
        'discounts': decimal_string(ZERO, 4),
        'refunds': decimal_string(ZERO, 4),
        'net_sales': decimal_string(ZERO, 4),
        'plant_cogs': decimal_string(ZERO, 4),
        'tray_cogs': decimal_string(ZERO, 4),
        'packaging_cogs': decimal_string(ZERO, 4),
        'other_cogs': decimal_string(ZERO, 4),
        'production_loss': decimal_string(ZERO, 4),
        'currency_code': currency,
        'provisional': False,
        'unvalued': False,
    }


def _fulfillment_rows(lines, filters):
    rows = []
    included_fulfillments = {}
    for line in lines:
        if not _matches_place(line, filters, line.fulfillment.fulfilled_at):
            continue
        row = _base_financial_row(
            'fulfillment', line.fulfillment.fulfilled_at,
            line.currency_code, line.pk,
        )
        row.update({
            'fulfillment_id': line.fulfillment_id,
            'fulfillment_number': line.fulfillment.fulfillment_number,
            'fulfillment_line_id': line.pk,
            'order_id': line.fulfillment.order_id,
            'order_number': line.fulfillment.order.order_number,
            'customer_id': line.fulfillment.order.customer_id,
            'variety_id': line.allocation.line.variety_id,
            'batch_id': _allocation_batch_id(line.allocation),
            'plant_id': line.allocation.plant_id,
            'inventory_unit_id': line.allocation.inventory_unit_id,
            'lot_id': line.allocation.stock_lot_id,
            'cohort_id': line.allocation.plant_cohort_id,
            'gross_sales': decimal_string(line.gross_ex_tax, 4),
            'discounts': decimal_string(line.discount_ex_tax, 4),
            'net_sales': decimal_string(line.subtotal_ex_tax, 4),
            'provisional': line.cogs_provisional,
            'unvalued': line.cogs_amount is None,
        })
        if line.cogs_amount is not None:
            row[_cogs_bucket(line.allocation)] = decimal_string(line.cogs_amount, 4)
        rows.append(row)
        included_fulfillments[line.fulfillment_id] = line.fulfillment
    return rows, included_fulfillments


def _packaging_rows(fulfillments, filters):
    rows = []
    dimensioned = any(filters.get(key) for key in (
        'variety', 'batch', 'location', 'garden_square',
    ))
    for fulfillment in fulfillments.values():
        for line in fulfillment.packaging_lines.select_related('lot').all():
            row = _base_financial_row(
                'packaging', fulfillment.fulfilled_at,
                line.currency_code, line.pk,
            )
            row.update({
                'fulfillment_id': fulfillment.pk,
                'fulfillment_number': fulfillment.fulfillment_number,
                'order_id': fulfillment.order_id,
                'order_number': fulfillment.order.order_number,
                'customer_id': fulfillment.order.customer_id,
                'lot_id': line.lot_id,
                'packaging_cogs': decimal_string(line.cogs_amount or ZERO, 4),
                'unvalued': line.cogs_amount is None,
                'dimension_unattributed': dimensioned,
            })
            if not dimensioned:
                rows.append(row)
            else:
                rows.append(row | {'packaging_cogs': decimal_string(ZERO, 4)})
    return rows


def _refund_rows(workspace, filters, start, end):
    queryset = RefundLine.objects.filter(
        refund__workspace=workspace,
        refund__refunded_at__gte=start,
        refund__refunded_at__lt=end,
        refund__reversal_of__isnull=True,
        refund__reversal__isnull=True,
        fulfillment_line__fulfillment__reversal_of__isnull=True,
        fulfillment_line__fulfillment__reversal__isnull=True,
    ).select_related(
        'refund__order', 'fulfillment_line__allocation__line',
        'fulfillment_line__allocation__plant__batch',
        'fulfillment_line__allocation__plant_cohort__batch',
        'fulfillment_line__fulfillment',
    )
    if filters.get('variety'):
        queryset = queryset.filter(
            fulfillment_line__allocation__line__variety_id=filters['variety'],
        )
    if filters.get('batch'):
        batch = filters['batch']
        matches_batch = Q(fulfillment_line__allocation__plant__batch_id=batch)
        matches_batch |= Q(fulfillment_line__allocation__plant_cohort__batch_id=batch)
        queryset = queryset.filter(matches_batch)
    if filters.get('customer'):
        queryset = queryset.filter(refund__order__customer_id=filters['customer'])
    if filters.get('order'):
        queryset = queryset.filter(refund__order__order_number__icontains=filters['order'])
    if filters.get('fulfillment'):
        queryset = queryset.filter(
            fulfillment_line__fulfillment__fulfillment_number__icontains=filters['fulfillment'],
        )
    rows = []
    for refund_line in queryset.order_by('refund__refunded_at', 'pk'):
        line = refund_line.fulfillment_line
        if not _matches_place(line, filters, line.fulfillment.fulfilled_at):
            continue
        row = _base_financial_row(
            'refund', refund_line.refund.refunded_at,
            refund_line.refund.currency_code, refund_line.pk,
        )
        row.update({
            'fulfillment_id': line.fulfillment_id,
            'fulfillment_number': line.fulfillment.fulfillment_number,
            'fulfillment_line_id': line.pk,
            'order_id': refund_line.refund.order_id,
            'order_number': refund_line.refund.order.order_number,
            'customer_id': refund_line.refund.order.customer_id,
            'variety_id': line.allocation.line.variety_id,
            'batch_id': _allocation_batch_id(line.allocation),
            'plant_id': line.allocation.plant_id,
            'inventory_unit_id': line.allocation.inventory_unit_id,
            'lot_id': line.allocation.stock_lot_id,
            'cohort_id': line.allocation.plant_cohort_id,
            'refunds': decimal_string(refund_line.subtotal_ex_tax, 4),
            'net_sales': decimal_string(-refund_line.subtotal_ex_tax, 4),
        })
        rows.append(row)
    return rows


def _return_rows(workspace, filters, start, end):
    queryset = SalesReturnLine.objects.filter(
        sales_return__workspace=workspace,
        sales_return__returned_at__gte=start,
        sales_return__returned_at__lt=end,
        sales_return__reversal_of__isnull=True,
        sales_return__reversal__isnull=True,
        outcome__in=RESTORES_COGS,
        fulfillment_line__fulfillment__reversal_of__isnull=True,
        fulfillment_line__fulfillment__reversal__isnull=True,
    ).select_related(
        'sales_return__order', 'fulfillment_line__fulfillment',
        'fulfillment_line__allocation__line',
        'fulfillment_line__allocation__plant__batch',
        'fulfillment_line__allocation__plant_cohort__batch',
    )
    if filters.get('variety'):
        queryset = queryset.filter(
            fulfillment_line__allocation__line__variety_id=filters['variety'],
        )
    if filters.get('batch'):
        batch = filters['batch']
        matches_batch = Q(fulfillment_line__allocation__plant__batch_id=batch)
        matches_batch |= Q(fulfillment_line__allocation__plant_cohort__batch_id=batch)
        queryset = queryset.filter(matches_batch)
    if filters.get('customer'):
        queryset = queryset.filter(sales_return__order__customer_id=filters['customer'])
    if filters.get('order'):
        queryset = queryset.filter(
            sales_return__order__order_number__icontains=filters['order'],
        )
    if filters.get('fulfillment'):
        queryset = queryset.filter(
            fulfillment_line__fulfillment__fulfillment_number__icontains=filters['fulfillment'],
        )
    rows = []
    for return_line in queryset.order_by('sales_return__returned_at', 'pk'):
        line = return_line.fulfillment_line
        if not _matches_place(line, filters, line.fulfillment.fulfilled_at):
            continue
        row = _base_financial_row(
            'cogs_restoration', return_line.sales_return.returned_at,
            line.currency_code, return_line.pk,
        )
        row.update({
            'fulfillment_id': line.fulfillment_id,
            'fulfillment_number': line.fulfillment.fulfillment_number,
            'fulfillment_line_id': line.pk,
            'order_id': return_line.sales_return.order_id,
            'order_number': return_line.sales_return.order.order_number,
            'customer_id': return_line.sales_return.order.customer_id,
            'variety_id': line.allocation.line.variety_id,
            'batch_id': _allocation_batch_id(line.allocation),
            'plant_id': line.allocation.plant_id,
            'inventory_unit_id': line.allocation.inventory_unit_id,
            'lot_id': line.allocation.stock_lot_id,
            'cohort_id': line.allocation.plant_cohort_id,
            'provisional': line.cogs_provisional,
            'unvalued': line.cogs_amount is None,
        })
        if line.cogs_amount is not None:
            row[_cogs_bucket(line.allocation)] = decimal_string(-line.cogs_amount, 4)
        rows.append(row)
    return rows


def _loss_rows(workspace, filters, start, end):
    if any(filters.get(key) for key in ('customer', 'order', 'fulfillment')):
        return []
    layers = CostAllocation.objects.filter(
        workspace=workspace, reversal_of__isnull=True, reversal__isnull=True,
    ).select_related('batch__variety', 'specific_plant')
    if filters.get('variety'):
        layers = layers.filter(batch__variety_id=filters['variety'])
    if filters.get('batch'):
        layers = layers.filter(batch_id=filters['batch'])
    plant_ids = list(layers.filter(
        target_type=CostAllocation.TargetType.SPECIFIC_PLANT,
    ).values_list('specific_plant_id', flat=True).distinct())
    summaries = lifecycle_summaries(plant_ids)
    rows = []
    for layer in layers.order_by('pk'):
        occurred_at = None
        cause = None
        if layer.target_type == CostAllocation.TargetType.PRODUCTION_LOSS:
            occurred_at = layer.created
        elif layer.specific_plant_id:
            summary = summaries[layer.specific_plant_id]
            if summary.final_outcome in LOSS_EVENTS:
                occurred_at = summary.final_outcome_at
                cause = CAUSE_OF_EVENT[summary.final_outcome].value
        if occurred_at is None or not start <= occurred_at < end:
            continue
        if filters.get('location') or filters.get('garden_square'):
            locations = SpecificPlantLocation.objects.filter(
                specific_plant_id=layer.specific_plant_id,
                started__lte=occurred_at,
            ).filter(Q(ended__isnull=True) | Q(ended__gte=occurred_at))
            if filters.get('location'):
                locations = locations.filter(location_id=filters['location'])
            if filters.get('garden_square'):
                locations = locations.filter(garden_square_id=filters['garden_square'])
            if not locations.exists():
                continue
        row = _base_financial_row(
            'production_loss', occurred_at, layer.currency_code, layer.pk,
        )
        row.update({
            'variety_id': layer.batch.variety_id,
            'batch_id': layer.batch_id,
            'plant_id': layer.specific_plant_id,
            'loss_cause': cause,
            'production_loss': decimal_string(layer.amount or ZERO, 4),
            'provisional': layer.batch.output_finalized_at is None,
            'unvalued': layer.amount is None,
        })
        rows.append(row)
    return rows


def _placed_plant_events(events, filters):
    """Keep only losses whose plant stood in the filtered place when it happened."""
    if not filters.get('location') and not filters.get('garden_square'):
        return events
    locations = SpecificPlantLocation.objects.filter(
        specific_plant_id=OuterRef('plant_id'),
        started__lte=OuterRef('occurred_at'),
    ).filter(Q(ended__isnull=True) | Q(ended__gte=OuterRef('occurred_at')))
    if filters.get('location'):
        locations = locations.filter(location_id=filters['location'])
    if filters.get('garden_square'):
        locations = locations.filter(garden_square_id=filters['garden_square'])
    return events.filter(Exists(locations))


def _lost_units(workspace, filters, start, end):
    """Count the stock lost in the period by cause, anonymous and identified.

    The money beside it only ever covers identified plants: a cohort's cost
    redistributes across the units the batch has left rather than becoming its
    own layer, so `production_loss` would report a batch whose whole loss was
    anonymous as costing nothing. The unit totals are what say how much was
    lost and why, in the vocabulary `plantings.loss` holds for both.
    """
    if any(filters.get(key) for key in ('customer', 'order', 'fulfillment')):
        return empty_totals()
    plant_events = PlantLifecycleEvent.objects.filter(
        workspace=workspace, occurred_at__gte=start, occurred_at__lt=end,
    )
    cohort_events = CohortEvent.objects.filter(
        workspace=workspace,
        operation__occurred_at__gte=start,
        operation__occurred_at__lt=end,
    )
    if filters.get('variety'):
        plant_events = plant_events.filter(batch__variety_id=filters['variety'])
        cohort_events = cohort_events.filter(cohort__batch__variety_id=filters['variety'])
    if filters.get('batch'):
        plant_events = plant_events.filter(batch_id=filters['batch'])
        cohort_events = cohort_events.filter(cohort__batch_id=filters['batch'])
    if filters.get('location'):
        cohort_events = cohort_events.filter(location_before_id=filters['location'])
    if filters.get('garden_square'):
        # A cohort is placed at a location and never in a garden square, so a
        # square filter selects no anonymous stock rather than all of it.
        cohort_events = cohort_events.none()
    return loss_by_cause(
        plant_events=_placed_plant_events(plant_events, filters),
        cohort_events=cohort_events,
    )


def profitability_report(workspace, filters):
    """Calculate direct-cost P&L while keeping incomplete values outside margin."""
    filters = _month_filters(workspace, filters)
    start, end = _date_bounds(workspace, filters)
    lines = list(_commerce_queryset(workspace, filters, start, end))
    rows, fulfillments = _fulfillment_rows(lines, filters)
    rows.extend(_packaging_rows(fulfillments, filters))
    rows.extend(_refund_rows(workspace, filters, start, end))
    rows.extend(_return_rows(workspace, filters, start, end))
    rows.extend(_loss_rows(workspace, filters, start, end))
    rows.sort(key=lambda row: (row['occurred_at'], row['kind'], row['source_id']))
    lost_units = _lost_units(workspace, filters, start, end)
    money_fields = (
        'gross_sales', 'discounts', 'refunds', 'net_sales', 'plant_cogs',
        'tray_cogs', 'packaging_cogs', 'other_cogs', 'production_loss',
    )
    by_currency = defaultdict(lambda: defaultdict(Decimal))
    loss_by_currency = defaultdict(lambda: defaultdict(Decimal))
    for row in rows:
        for field in money_fields:
            if not row['provisional'] and not row['unvalued']:
                by_currency[row['currency_code']][field] += Decimal(row[field])
            elif field in {'gross_sales', 'discounts', 'refunds', 'net_sales'}:
                by_currency[row['currency_code']][field] += Decimal(row[field])
        if row['loss_cause'] and not row['provisional'] and not row['unvalued']:
            loss_by_currency[row['currency_code']][row['loss_cause']] += (
                Decimal(row['production_loss'])
            )
    provisional = [row for row in rows if row['provisional']]
    unvalued = [row for row in rows if row['unvalued']]
    unattributed = [row for row in rows if row.get('dimension_unattributed')]
    currencies = set(by_currency)
    incomplete = bool(provisional or unvalued or unattributed or len(currencies) != 1)
    summaries = []
    for currency, values in sorted(by_currency.items()):
        direct_cogs = sum(
            (values[field] for field in (
                'plant_cogs', 'tray_cogs', 'packaging_cogs', 'other_cogs',
            )), ZERO,
        )
        gross_profit = None
        gross_margin = None
        if not incomplete:
            gross_profit = values['net_sales'] - direct_cogs - values['production_loss']
            if values['net_sales'] != 0:
                gross_margin = gross_profit / values['net_sales']
        summaries.append({
            'currency_code': currency,
            **{field: decimal_string(values[field], 4) for field in money_fields},
            'direct_cogs': decimal_string(direct_cogs, 4),
            'loss_by_cause': {
                cause.value: decimal_string(
                    loss_by_currency[currency][cause.value], 4,
                )
                for cause in LOSS_CAUSES
            },
            'gross_profit': decimal_string(gross_profit, 4),
            'gross_margin': decimal_string(gross_margin, 6),
        })
    quality = []
    for code, selected, message in (
        ('provisional_cost', provisional, 'Provisional cost is excluded from finalized margin.'),
        ('unvalued_cost', unvalued, 'Unknown cost is not treated as zero.'),
        ('dimension_unattributed_cost', unattributed, 'Packaging cannot be exactly assigned to this production dimension.'),
    ):
        if selected:
            quality.append({
                'code': code, 'count': len(selected), 'message': message,
                'drill_down': f'/reports/profitability/?{code}=true',
            })
    if len(currencies) > 1:
        quality.append({
            'code': 'mixed_currency', 'count': len(currencies),
            'message': 'No exchange rate exists, so currencies are not consolidated.',
            'drill_down': '/reports/profitability/',
        })
    return Report(
        name='profitability', filters=filters, rows=rows,
        columns=tuple(rows[0]) if rows else tuple(_base_financial_row(
            'kind', timezone.now(), workspace.currency_code, 0,
        )),
        totals={
            'currencies': summaries,
            'lost_units_by_cause': lost_units,
            'lost_units': sum(lost_units.values()),
            'provisional_rows': len(provisional),
            'unvalued_rows': len(unvalued),
            'dimension_unattributed_rows': len(unattributed),
            'finalized_margin_available': not incomplete,
        },
        reconciliation={
            'sales_equation': 'gross sales - discounts - refunds = net sales',
            'cost_equation': 'direct COGS = plant + tray + packaging + other COGS',
            'profit_equation': 'gross profit = net sales - direct COGS - production loss',
            'loss_equation': (
                'lost units = failed + lost + culled + donated + unspecified, '
                'counting anonymous cohort units and identified plants in the '
                'same vocabulary; production loss values the identified half, '
                'because a cohort loss redistributes its cost over the units '
                'the batch has left instead of booking its own layer'
            ),
        },
        data_quality=quality,
    )


def dashboard_report(workspace, filters):
    """Summarize the current operational month without storing dashboard state."""
    filters = _month_filters(workspace, filters)
    today = _local_today(workspace)
    expiring = inventory_balances(workspace, {
        'expires_before': (today + timedelta(days=30)).isoformat(),
    })
    low = inventory_balances(workspace, {'low_stock': True})
    production = production_batches(workspace, {})
    orders = order_report(workspace, {})
    profit = profitability_report(workspace, filters)
    recent = list(Fulfillment.objects.filter(
        workspace=workspace,
        reversal_of__isnull=True,
        reversal__isnull=True,
    ).select_related('order').order_by('-fulfilled_at', '-pk')[:10])
    row = {
        'expiring_lot_locations': len(expiring.rows),
        'low_stock_lot_locations': len(low.rows),
        'active_batches': ProductionBatch.objects.filter(
            workspace=workspace, status=ProductionBatch.Status.ACTIVE,
        ).count(),
        'available_seedlings': production.totals['current_seedlings'],
        'open_orders': orders.totals['open_orders'],
        'provisional_batches': production.totals['provisional_batches'],
        'period_profitability': profit.totals,
        'recent_fulfillments': [{
            'fulfillment_id': fulfillment.pk,
            'fulfillment_number': fulfillment.fulfillment_number,
            'fulfilled_at': fulfillment.fulfilled_at,
            'order_id': fulfillment.order_id,
            'order_number': fulfillment.order.order_number,
        } for fulfillment in recent],
    }
    return Report(
        name='dashboard', filters=filters, rows=[row], columns=tuple(row),
        totals=row,
        reconciliation=profit.reconciliation,
        data_quality=expiring.data_quality + production.data_quality + profit.data_quality,
    )
