"""What confirmed orders are already committed to, as production demand.

Task 55's plans are driven by demand, and its most certain kind is a confirmed
order: somebody has agreed to buy the crop. Until an order could name stock
that was still growing, a forward commitment had nowhere to live in the
application at all, so the demand it represents had to be typed in again by
hand as a forecast — a second figure, entered from the first, free to disagree
with it the moment the order changed.

This reads the commitments back out. It lives in `sales` rather than in
`plantings.planning` because the nursery is built without knowledge of who is
buying from it: `plantings.cohort_availability` defers its one reach into sales
for the same reason, and a module here can import the plan models directly.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from plantings.models import NurseryPlanDemand, NurseryProductionPlan

from .commerce import dispatched_quantity
from .models import (
    SalesOrder,
    SalesOrderAllocation,
    SalesOrderLine,
    SalesOrderShortfall,
    VARIETY_LINE_TYPES,
)


#: The order statuses whose promises the nursery still has to grow. A quote is
#: not an agreement and a draft is still being written, so neither is demand;
#: a cancelled order released its stock, and a fulfilled one already shipped.
COMMITTED_ORDER_STATUSES = (
    SalesOrder.Status.CONFIRMED,
    SalesOrder.Status.PARTIALLY_FULFILLED,
)

#: How a demand line points back at the order line it was read from. It goes in
#: `source_line_reference`, whose uniqueness per plan and source is what makes
#: importing twice a refresh rather than a second copy of the same commitment.
LINE_REFERENCE = 'sales-line:{pk}'


def _dispatched(line):
    """Return how many of a line's units have actually left, net of returns."""
    total = 0
    for allocation in line.allocations.filter(
            status=SalesOrderAllocation.Status.FULFILLED):
        total += dispatched_quantity(allocation)
    return total


def _short(line):
    """Return how many of a line's units were given up as never supplied."""
    return SalesOrderShortfall.objects.filter(line=line).aggregate(
        total=Sum('quantity'),
    )['total'] or 0


def outstanding_commitment(line):
    """Return how many of one line's plants the nursery still owes.

    What shipped is grown and gone, and what was written off short will never
    be grown at all, so a plan that counted either would sow a crop nobody is
    waiting for.
    """
    return line.quantity - _dispatched(line) - _short(line)


def committed_demand(workspace, ready_from, ready_until):
    """Return one row per confirmed commitment falling due in a date window.

    Only lines selling nursery plants are read. A line selling a numbered pot
    or a counted crate is a promise about the store rather than about the
    benches, and no amount of sowing changes what it needs.

    The order's requested date is the ready date, because that is the date the
    customer was promised, and a plan whose window did not contain it would be
    planning for a delivery somebody else agreed to.
    """
    lines = (
        SalesOrderLine.objects
        .filter(
            order__workspace=workspace,
            order__status__in=COMMITTED_ORDER_STATUSES,
            line_type__in=VARIETY_LINE_TYPES,
            order__requested_date__gte=ready_from,
            order__requested_date__lte=ready_until,
        )
        .select_related('order__customer', 'variety')
        .prefetch_related('allocations')
        .order_by('order__requested_date', 'order__order_number', 'pk')
    )
    rows = []
    for line in lines:
        outstanding = outstanding_commitment(line)
        if outstanding <= 0:
            continue
        order = line.order
        rows.append({
            'variety': line.variety,
            'target_quantity': outstanding,
            'ready_from': order.requested_date,
            'ready_until': order.requested_date,
            'product_reference': line.description,
            'customer_reference': order.customer.name if order.customer_id else '',
            'order_reference': order.order_number,
            'source_line_reference': LINE_REFERENCE.format(pk=line.pk),
        })
    return rows


@transaction.atomic
def import_committed_demand(plan, ready_from, ready_until):
    """Replace a draft plan's confirmed-order demand with what is committed now.

    A refresh rather than an append. Every line the import owns is rewritten
    from the orders as they stand and any it no longer finds is removed, so a
    commitment that was cancelled, shipped or written off short stops driving a
    sowing the moment somebody re-imports — which is the whole reason for
    reading the orders instead of retyping them.

    Demand lines an operator entered by hand are left alone. Somebody wrote
    them for a reason the orders cannot see, and an import is not entitled to
    an opinion about a forecast.
    """
    if plan.status != NurseryProductionPlan.Status.DRAFT:
        raise ValidationError({'plan': 'Only a draft plan can import demand.'})
    if ready_until < ready_from:
        raise ValidationError({
            'ready_until': 'The ready window cannot end before it starts.',
        })
    rows = committed_demand(plan.workspace, ready_from, ready_until)
    owned = NurseryPlanDemand.objects.filter(
        plan=plan, source=NurseryPlanDemand.Source.CONFIRMED_ORDER,
    )
    keep = {row['source_line_reference'] for row in rows}
    owned.exclude(source_line_reference__in=keep).delete()
    imported = []
    for row in rows:
        demand, _created = NurseryPlanDemand.objects.update_or_create(
            plan=plan,
            source=NurseryPlanDemand.Source.CONFIRMED_ORDER,
            source_line_reference=row['source_line_reference'],
            # A confirmed order outranks a forecast by construction: somebody
            # has agreed to buy this and has not agreed to buy the rest.
            defaults={**row, 'priority': NurseryPlanDemand.Priority.HIGH},
        )
        imported.append(demand)
    return imported
