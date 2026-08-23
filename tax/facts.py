"""Read the commerce records the GST rules run on, and nothing more.

`recognition` decides when a supply falls due and holds no database access;
this module is the other half — one ORM read per order, shaped into the frozen
facts those rules take. Splitting them is what keeps the accounting judgement
testable without fixtures and the query work reviewable on its own.

Only effective records are read. A reversal and the record it reverses both
drop out, using the same pair of filters the commerce services already use, so
a reversed fulfillment can never contribute to a return.

Documents from `billing` are read here too. They are already immutable and have
no reversal pair — an issued document is corrected, never withdrawn — so the
effective filter does not apply to them; what a correction did to a document is
its own dated event rather than a reason to stop reading the original.
"""

from collections import defaultdict
from decimal import Decimal

from billing.models import SupplyCorrection, SupplyCorrectionLine, SupplyDocument, SupplyDocumentLine
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
    CorrectionFact,
    CreditPortion,
    FulfillmentFact,
    InvoiceFact,
    LineFact,
    OrderFacts,
    PaymentFact,
    RefundFact,
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
    matched.update(_document_order_ids(workspace, bounds))
    return orders.filter(pk__in=matched).order_by('pk')


def _document_order_ids(workspace, bounds):
    """Return the orders whose documents fall in a range.

    Documents carry no reversal pair, so `effective` has nothing to filter
    here. A correction reaches this the same way, through its own date: an
    invoice issued in March and credited in May makes both periods worth
    reading, and leaving the second out would hide the adjustment.
    """
    matched = set()
    document_lookup = {f'issued_on__{suffix}': value for suffix, value in bounds.items()}
    matched.update(
        SupplyDocument.objects.filter(workspace=workspace, **document_lookup)
        .values_list('order_id', flat=True)
    )
    correction_lookup = {f'corrected_on__{suffix}': value for suffix, value in bounds.items()}
    matched.update(
        SupplyCorrection.objects.filter(workspace=workspace, **correction_lookup)
        .values_list('document__order_id', flat=True)
    )
    return matched


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
    corrections = _correction_facts(order)
    return OrderFacts(
        order_id=order.pk,
        currency_code=order.currency_code,
        lines=lines,
        fulfillments=_fulfillment_facts(workspace, order),
        invoices=_invoice_facts(order),
        payments=_payment_facts(order),
        refunds=_refund_facts(workspace, order),
        corrections=corrections,
        uncredited_return_ids=_uncredited_return_ids(order),
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
        grouped[refund.pk].append(CreditPortion(
            line_id=line.fulfillment_line.allocation.line_id,
            gross=line.total_incl_tax,
            tax=line.tax_total,
        ))
    credited = _refunds_carrying_a_credit_note(order)
    return tuple(
        RefundFact(
            refund_id=refund_id,
            refunded_on=dates[refund_id],
            portions=tuple(portions),
            credited_by_document=refund_id in credited,
        )
        for refund_id, portions in sorted(grouped.items())
    )


def _refunds_carrying_a_credit_note(order):
    """Return the refunds a credit note already accounts for.

    Under the invoice and hybrid bases the note is what alters the agreed
    consideration, so the refund beside it is the same money reaching the
    customer rather than a second adjustment.
    """
    return set(
        SupplyCorrection.objects
        .filter(document__order=order, refund__isnull=False)
        .values_list('refund_id', flat=True)
    )


def _invoice_facts(order):
    """Group each issued document's value by the order line it invoiced."""
    grouped = defaultdict(dict)
    dates = {}
    lines = SupplyDocumentLine.objects.filter(
        document__order=order,
    ).select_related('document').order_by('pk')
    for line in lines:
        document = line.document
        dates[document.pk] = document.issued_on
        totals = grouped[document.pk]
        totals[line.order_line_id] = totals.get(line.order_line_id, ZERO) + line.total_incl_tax
    return tuple(
        InvoiceFact(
            document_id=document_id,
            issued_on=dates[document_id],
            line_grosses=dict(totals),
        )
        for document_id, totals in sorted(grouped.items())
    )


def _correction_facts(order):
    """Group each correction's value by the order line it moves."""
    grouped = defaultdict(list)
    headers = {}
    lines = SupplyCorrectionLine.objects.filter(
        correction__document__order=order,
    ).select_related('correction', 'document_line').order_by('pk')
    for line in lines:
        correction = line.correction
        headers[correction.pk] = correction
        grouped[correction.pk].append(CreditPortion(
            line_id=line.document_line.order_line_id,
            gross=line.total_incl_tax,
            tax=line.tax_total,
        ))
    return tuple(
        CorrectionFact(
            correction_id=correction_id,
            corrected_on=headers[correction_id].corrected_on,
            kind=headers[correction_id].correction_type,
            portions=tuple(portions),
        )
        for correction_id, portions in sorted(grouped.items())
    )


def _uncredited_return_ids(order):
    """Return effective returns carrying neither a refund nor a credit note.

    A return moves plants, not money, so on its own it changes no consideration
    and owes no GST adjustment. What it owes is a correction document, and now
    that one can be issued this reports only the returns where nobody has —
    reaching zero when the paperwork is done, rather than nagging forever.
    """
    refunded = set(
        effective(Refund.objects.filter(order=order))
        .exclude(sales_return__isnull=True)
        .values_list('sales_return_id', flat=True)
    )
    credited = set(
        SupplyCorrection.objects
        .filter(document__order=order, sales_return__isnull=False)
        .values_list('sales_return_id', flat=True)
    )
    returns = effective(SalesReturn.objects.filter(order=order)).order_by('pk')
    settled = refunded | credited
    return tuple(
        sales_return.pk for sales_return in returns if sales_return.pk not in settled
    )
