"""Read the commerce records the GST rules run on, and nothing more.

`recognition` decides when a supply falls due and holds no database access;
this module is the other half — one ORM read per order, shaped into the frozen
facts those rules take. Splitting them is what keeps the accounting judgement
testable without fixtures and the query work reviewable on its own.

Only effective records are read. A reversal and the record it reverses both
drop out, using the same pair of filters the commerce services already use, so
a reversed fulfillment can never contribute to a return.
"""

from collections import defaultdict
from decimal import Decimal

from sales.models import (
    Fulfillment,
    FulfillmentLine,
    Payment,
    Refund,
    RefundLine,
    SalesOrder,
    SalesReturn,
)

from .periods import local_date
from .recognition import (
    FulfillmentFact,
    LineFact,
    OrderFacts,
    PaymentFact,
    RefundFact,
    RefundPortion,
)


ZERO = Decimal('0.0000')

#: The house filter for a record that still counts: neither a reversal nor
#: reversed. Restated here rather than imported out of `sales.commerce`, which
#: is a write path this module has no business touching.
EFFECTIVE = {'reversal_of__isnull': True, 'reversal__isnull': True}


def effective(queryset):
    """Narrow a commerce queryset to the records that still count."""
    return queryset.filter(**EFFECTIVE)


def orders_with_activity(workspace, start=None, end=None):
    """Return orders whose GST could fall due in a date range.

    An order contributes to a return only at one of its own triggers, so one
    with no fulfillment, payment or refund inside the range contributes
    nothing and does not need reading. The whole of a matching order is then
    read, not just the part inside the range: under the invoice basis what a
    fulfillment recognises depends on what earlier payments already brought to
    account, so a window that cut the facts short would overstate it.
    """
    orders = SalesOrder.objects.filter(workspace=workspace)
    if start is None and end is None:
        return orders.order_by('pk')
    bounds = {}
    if start is not None:
        bounds['gte'] = start
    if end is not None:
        bounds['lte'] = end
    matched = set()
    for model, field, path in (
        (Fulfillment, 'fulfilled_at__date', 'order_id'),
        (Payment, 'paid_on', 'order_id'),
        (Refund, 'refunded_at__date', 'order_id'),
    ):
        lookup = {f'{field}__{suffix}': value for suffix, value in bounds.items()}
        matched.update(
            effective(model.objects.filter(workspace=workspace, **lookup))
            .values_list(path, flat=True)
        )
    return orders.filter(pk__in=matched).order_by('pk')


def order_facts(order):
    """Return everything about one order that bears on when GST falls due."""
    workspace = order.workspace
    lines = tuple(
        LineFact(
            line_id=line.pk,
            tax_rate=line.tax_rate,
            tax_code=line.tax_treatment,
            gross_incl_tax=line.total_incl_tax,
        )
        for line in order.lines.all().order_by('pk')
    )
    return OrderFacts(
        order_id=order.pk,
        currency_code=order.currency_code,
        lines=lines,
        fulfillments=_fulfillment_facts(workspace, order),
        payments=_payment_facts(order),
        refunds=_refund_facts(workspace, order),
        unrefunded_return_ids=_unrefunded_return_ids(order),
    )


def workspace_order_facts(workspace, start=None, end=None):
    """Return the facts for every order that could fall due in a range."""
    orders = orders_with_activity(workspace, start, end).prefetch_related('lines')
    return [order_facts(order) for order in orders]


def _fulfillment_facts(workspace, order):
    """Group each effective fulfillment's value by the order line it delivered."""
    grouped = defaultdict(dict)
    dates = {}
    lines = FulfillmentLine.objects.filter(
        fulfillment__order=order,
        fulfillment__reversal_of__isnull=True,
        fulfillment__reversal__isnull=True,
    ).select_related('allocation', 'fulfillment').order_by('pk')
    for line in lines:
        fulfillment = line.fulfillment
        dates[fulfillment.pk] = local_date(workspace, fulfillment.fulfilled_at)
        totals = grouped[fulfillment.pk]
        line_id = line.allocation.line_id
        totals[line_id] = totals.get(line_id, ZERO) + line.total_incl_tax
    return tuple(
        FulfillmentFact(
            fulfillment_id=fulfillment_id,
            supply_date=dates[fulfillment_id],
            line_grosses=dict(totals),
        )
        for fulfillment_id, totals in sorted(grouped.items())
    )


def _payment_facts(order):
    """Return every effective payment, oldest first.

    `paid_on` is already a local business date, so unlike the timestamps on
    the other documents it needs no conversion.
    """
    payments = effective(order.payments.all()).order_by('paid_on', 'pk')
    return tuple(
        PaymentFact(payment_id=payment.pk, paid_on=payment.paid_on, gross=payment.amount)
        for payment in payments
    )


def _refund_facts(workspace, order):
    """Return every effective refund, already classified against its order lines."""
    grouped = defaultdict(list)
    dates = {}
    lines = RefundLine.objects.filter(
        refund__order=order,
        refund__reversal_of__isnull=True,
        refund__reversal__isnull=True,
    ).select_related('refund', 'fulfillment_line__allocation').order_by('pk')
    for line in lines:
        refund = line.refund
        dates[refund.pk] = local_date(workspace, refund.refunded_at)
        grouped[refund.pk].append(RefundPortion(
            line_id=line.fulfillment_line.allocation.line_id,
            gross=line.total_incl_tax,
            tax=line.tax_total,
        ))
    return tuple(
        RefundFact(
            refund_id=refund_id,
            refunded_on=dates[refund_id],
            portions=tuple(portions),
        )
        for refund_id, portions in sorted(grouped.items())
    )


def _unrefunded_return_ids(order):
    """Return effective returns carrying no effective refund.

    A return moves plants, not money, so it changes no consideration and owes
    no GST adjustment. It does owe a credit note, and reporting that as
    outstanding work is more useful than reporting nothing.
    """
    refunded = set(
        effective(Refund.objects.filter(order=order))
        .exclude(sales_return__isnull=True)
        .values_list('sales_return_id', flat=True)
    )
    returns = effective(SalesReturn.objects.filter(order=order)).order_by('pk')
    return tuple(
        sales_return.pk for sales_return in returns if sales_return.pk not in refunded
    )
