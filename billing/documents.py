"""Issue taxable supply documents and the corrections that amend them.

Every write in this module is atomic and idempotent under an operation key, in
the same shape as `sales.commerce`: a retried request returns the document the
first attempt issued rather than issuing a second one, and a key reused for
different work is refused rather than silently answering with the wrong record.

Three rules do most of the work here.

**A position is invoiced once.** A document line covers whole commercial
positions of one order line, and `invoiceable` is the single place that decides
which positions are still free. Everything change 3 asks for falls out of that
one rule: a partial invoice covers some positions, a backorder covers only the
dispatched ones, and an invoice ahead of dispatch covers positions no
fulfillment has reached yet.

**A correction never exceeds the supply it corrects.** Credits are capped at
the document line's own value and debits at the credits already made against
it, so the net value of an issued line can move anywhere between nothing and
what it was issued for, and nowhere else. A genuine undercharge is not a debit
note against an order that says otherwise — the order is the agreed price, and
this module will not invent a figure that disagrees with it.

**A fully credited line frees its positions again.** That is what makes the
credit-and-re-issue correction possible, and it is the reason there is no
database uniqueness on the covered position: the rule needs to know whether a
document is still live, which no single-table constraint can see.
"""

# Issuing one document coordinates the order, the registration history, the
# payment ledger and the threshold rules in one transaction, deliberately.
# pylint: disable=duplicate-code,too-many-locals,too-many-arguments

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from sales.calculations import line_position_amounts, money
from sales.commerce import request_fingerprint
from sales.models import (
    FulfillmentLine,
    Payment,
    Refund,
    SalesOrder,
    SalesReturnLine,
)
from tax.periods import local_date

from .identity import buyer_snapshot, seller_snapshot
from .models import (
    CREDIT_SERIES,
    DEBIT_SERIES,
    SERIES_PREFIXES,
    SUPPLY_SERIES,
    DocumentNumberSequence,
    SupplyCorrection,
    SupplyCorrectionLine,
    SupplyDocument,
    SupplyDocumentCoverage,
    SupplyDocumentLine,
)
from .thresholds import DocumentInformation, describe, missing_information, tier_for


ZERO = Decimal('0.0000')

#: Order states a document may be issued against. A quote is not a supply and a
#: draft is still being written; a cancelled order has nothing left to invoice,
#: and the documents already issued against it are corrected rather than
#: extended.
ISSUABLE_STATUSES = (
    SalesOrder.Status.CONFIRMED,
    SalesOrder.Status.PARTIALLY_FULFILLED,
    SalesOrder.Status.FULFILLED,
)


def _actor(user):
    """Return the user to attribute a document to, or None for anonymous work."""
    return user if user is not None and user.is_authenticated else None


def _effective(queryset):
    """Narrow a commerce queryset to records that are neither reversal nor reversed."""
    return queryset.filter(reversal_of__isnull=True, reversal__isnull=True)


def _existing(model, workspace, operation_key, fingerprint):
    """Return the record a retried operation key already produced."""
    row = model.objects.filter(
        workspace=workspace, operation_key=operation_key,
    ).first()
    if row and row.request_fingerprint != fingerprint:
        raise ValidationError({
            'operation_key': 'That operation key was already used for different work.',
        })
    return row


def next_document_number(workspace, series):
    """Return the next readable number in one series, under a row lock."""
    sequence, _created = DocumentNumberSequence.objects.select_for_update(
        of=('self',),
    ).get_or_create(workspace=workspace, series=series)
    number = sequence.next_number
    sequence.next_number += 1
    sequence.save(update_fields=['next_number'])
    return f'{SERIES_PREFIXES[series]}-{number:06d}'


def net_credited(document_line):
    """Return how much of one document line has been credited away, net of debits."""
    totals = SupplyCorrectionLine.objects.filter(
        document_line=document_line,
    ).values('correction__correction_type').annotate(total=Sum('total_incl_tax'))
    amounts = {row['correction__correction_type']: row['total'] for row in totals}
    credited = amounts.get(SupplyCorrection.CorrectionType.CREDIT) or ZERO
    debited = amounts.get(SupplyCorrection.CorrectionType.DEBIT) or ZERO
    return money(credited - debited)


def _released_line_ids(order):
    """Return the document lines whose value has been credited away in full.

    A released line's positions are invoiceable again, which is what makes the
    credit-and-re-issue correction work. A partial credit releases nothing: it
    reduces what was charged without saying which of the covered items the
    reduction was about.
    """
    lines = SupplyDocumentLine.objects.filter(document__order=order)
    released = set()
    for line in lines:
        credited = net_credited(line)
        if credited > 0 and credited >= line.total_incl_tax:
            released.add(line.pk)
    return released


def _covered_positions(order):
    """Return the positions of each order line held by a live document."""
    released = _released_line_ids(order)
    covered = defaultdict(dict)
    rows = SupplyDocumentCoverage.objects.filter(
        document_line__document__order=order,
    ).select_related('document_line')
    for row in rows:
        if row.document_line_id in released:
            continue
        covered[row.document_line.order_line_id][row.commercial_position] = row.document_line.document_id
    return covered


def _dispatch_state(order):
    """Return which positions have shipped, and which have come back.

    Both are needed, and they are not complements: a position may have shipped
    and stayed out, shipped and returned, or never shipped at all. Only the
    middle case stops being invoiceable, because giving something back before
    anybody billed for it means no supply was made.
    """
    returned_lines = set(SalesReturnLine.objects.filter(
        sales_return__order=order,
        sales_return__reversal_of__isnull=True,
        sales_return__reversal__isnull=True,
    ).values_list('fulfillment_line_id', flat=True))
    rows = FulfillmentLine.objects.filter(
        fulfillment__order=order,
        fulfillment__reversal_of__isnull=True,
        fulfillment__reversal__isnull=True,
    ).select_related('allocation')
    dispatched = defaultdict(dict)
    returned = defaultdict(set)
    for row in rows:
        if row.pk in returned_lines:
            returned[row.allocation.line_id].add(row.commercial_position)
            continue
        dispatched[row.allocation.line_id][row.commercial_position] = row
    return dispatched, returned


def invoiceable(order):
    """Return, per order line, which commercial positions may still be invoiced.

    A position is offered when no live document covers it and it has not been
    dispatched and given back. `fulfillment_line` says whether it has shipped,
    which is the only difference between invoicing after delivery and invoicing
    ahead of it — both are allowed, and the caller decides which it is doing by
    what it selects.
    """
    covered = _covered_positions(order)
    dispatched, returned = _dispatch_state(order)
    rows = []
    for line in order.lines.all().order_by('pk'):
        amounts = line_position_amounts(line)
        held = covered.get(line.pk, {})
        shipped = dispatched.get(line.pk, {})
        given_back = returned.get(line.pk, set())
        unavailable = set(held) | given_back
        positions = [
            {
                'position': position,
                'fulfillment_line': shipped.get(position),
                'total_incl_tax': amounts[position]['total_incl_tax'],
            }
            for position in range(1, line.quantity + 1)
            if position not in unavailable
        ]
        rows.append({
            'order_line': line,
            'description': line.description,
            'invoiced_positions': sorted(held),
            'returned_positions': sorted(given_back),
            'positions': positions,
        })
    return rows


def _selected_positions(order, requested):
    """Validate a selection against what is actually invoiceable.

    Returns the order lines paired with the positions chosen for each, in
    order-line order, so the document lines come out in the order the customer
    reads their order in rather than in whatever order the request arrived.
    """
    available = {row['order_line'].pk: row for row in invoiceable(order)}
    chosen = {}
    for item in requested:
        line = item['order_line']
        positions = sorted(set(item['positions']))
        if line.order_id != order.pk:
            raise ValidationError({'lines': 'Choose lines from this order.'})
        if not positions:
            raise ValidationError({'lines': f'Line {line.pk} selects no items to invoice.'})
        if line.pk in chosen:
            raise ValidationError({'lines': f'Line {line.pk} appears twice in one document.'})
        offered = {row['position']: row for row in available[line.pk]['positions']}
        unavailable = [position for position in positions if position not in offered]
        if unavailable:
            raise ValidationError({
                'lines': (
                    f'Line {line.pk} items {unavailable} are already invoiced, '
                    'or were returned before they were.'
                ),
            })
        chosen[line.pk] = (line, [offered[position] for position in positions])
    if not chosen:
        raise ValidationError({'lines': 'Select at least one item to invoice.'})
    return [chosen[key] for key in sorted(chosen)]


def _document_line_values(line, positions):
    """Sum the exact position amounts a document line covers.

    The amounts come from `line_position_amounts`, which is the same split
    `sales.commerce` fulfils against, so a document and a dispatch of the same
    item state the same money down to the last hundredth of a cent.
    """
    amounts = line_position_amounts(line)
    fields = ('gross_ex_tax', 'discount_ex_tax', 'subtotal_ex_tax', 'tax_total', 'total_incl_tax')
    totals = {
        field: money(sum((amounts[row['position']][field] for row in positions), ZERO))
        for field in fields
    }
    return {
        'order_line': line,
        'description': line.description,
        'quantity': len(positions),
        'unit_price': line.unit_price,
        'tax_rate': line.tax_rate,
        'tax_treatment': line.tax_treatment,
        **totals,
    }


def _paid_to_date(order, on_date):
    """Return cash received net of refunds, up to and including a business date.

    Refund dates are filtered in Python rather than in SQL because
    `refunded_at` is a UTC instant and the comparison has to happen in the
    workspace's own day — the same rule `tax.periods.local_date` exists for.
    """
    workspace = order.workspace
    received = _effective(
        Payment.objects.filter(order=order, paid_on__lte=on_date),
    ).aggregate(total=Sum('amount'))['total'] or ZERO
    refunded = sum(
        (
            refund.amount for refund in _effective(Refund.objects.filter(order=order))
            if local_date(workspace, refund.refunded_at) <= on_date
        ),
        ZERO,
    )
    return money(received - refunded)


def _previously_invoiced(order):
    """Return the net value of every document already issued against an order."""
    total = ZERO
    for document in SupplyDocument.objects.filter(order=order).prefetch_related('lines'):
        for line in document.lines.all():
            total += line.total_incl_tax - net_credited(line)
    return money(total)


def _refuse_incomplete(document_values, line_values):
    """Refuse to issue a document its own value band would make defective."""
    information = DocumentInformation(
        total_incl_tax=document_values['total_incl_tax'],
        taxable_supply=document_values['taxable_supply'],
        seller_name=document_values['seller_legal_name'],
        seller_gst_number=document_values['seller_gst_number'],
        document_date=document_values['issued_on'],
        gst_stated=document_values['taxable_supply'],
        line_descriptions=tuple(row['description'] for row in line_values),
        supply_quantities=tuple(row['quantity'] for row in line_values),
        buyer_name=document_values['buyer_name'],
        buyer_identification=(
            document_values['buyer_address'].strip() or document_values['buyer_identifier'].strip()
        ),
    )
    missing = missing_information(information)
    if missing:
        raise ValidationError({
            code: f'A supply of this value must state {describe((code,))}.'
            for code in missing
        })


@transaction.atomic
def issue_supply_document(order, user, *, operation_key, lines, issued_on=None, buyer=None, notes=''):
    """Issue one taxable supply document covering the items selected.

    `lines` is a sequence of ``{'order_line': line, 'positions': [1, 2]}``.
    `invoiceable` says what may go in it; nothing else is accepted, so an item
    already on a live document cannot reach a second one.
    """
    workspace = order.workspace
    requested_on = issued_on
    issued_on = issued_on or local_date(workspace, timezone.now())
    payload = {
        'order': order.pk,
        'lines': sorted(
            (item['order_line'].pk, sorted(set(item['positions']))) for item in lines
        ),
        'issued_on': requested_on,
        'buyer': buyer,
        'notes': notes,
    }
    fingerprint = request_fingerprint(payload)
    existing = _existing(SupplyDocument, workspace, operation_key, fingerprint)
    if existing:
        return existing
    order = SalesOrder.objects.select_for_update(of=('self',)).prefetch_related('lines').get(pk=order.pk)
    existing = _existing(SupplyDocument, workspace, operation_key, fingerprint)
    if existing:
        return existing
    _check_issuable(order, issued_on)
    selected = _selected_positions(order, lines)
    line_values = [_document_line_values(line, positions) for line, positions in selected]
    document_values = _document_values(order, issued_on, buyer, line_values, notes)
    _refuse_incomplete(document_values, line_values)
    document = SupplyDocument.objects.create(
        workspace=workspace,
        order=order,
        document_number=next_document_number(workspace, SUPPLY_SERIES),
        operation_key=operation_key,
        request_fingerprint=fingerprint,
        created_by=_actor(user),
        **document_values,
    )
    for values, (_line, positions) in zip(line_values, selected):
        document_line = SupplyDocumentLine.objects.create(document=document, **values)
        SupplyDocumentCoverage.objects.bulk_create([
            SupplyDocumentCoverage(
                document_line=document_line,
                commercial_position=row['position'],
                fulfillment_line=row['fulfillment_line'],
            )
            for row in positions
        ])
    return document


def _check_issuable(order, issued_on):
    """Refuse an order or a date a document cannot honestly be issued against."""
    if order.status not in ISSUABLE_STATUSES:
        raise ValidationError({
            'order': 'Only a confirmed order that has not been cancelled can be invoiced.',
        })
    order_date = order.order_date
    if order_date is not None and issued_on < order_date:
        raise ValidationError({
            'issued_on': f'A document cannot be dated before its order, {order_date.isoformat()}.',
        })


def _document_values(order, issued_on, buyer, line_values, notes):
    """Assemble everything a document states except its number and its lines."""
    workspace = order.workspace
    seller = seller_snapshot(workspace, issued_on)
    if not seller['seller_legal_name']:
        raise ValidationError({
            'seller_legal_name': (
                'Record the legal name of the entity making supplies in the '
                'workspace settings before issuing a document under it.'
            ),
        })
    taxable_supply = seller.pop('taxable_supply')
    subtotal = money(sum((row['subtotal_ex_tax'] for row in line_values), ZERO))
    tax_total = money(sum((row['tax_total'] for row in line_values), ZERO))
    total = money(sum((row['total_incl_tax'] for row in line_values), ZERO))
    if not taxable_supply and tax_total != ZERO:
        raise ValidationError({
            'taxable_supply': (
                'The workspace was not GST registered on that date, so a '
                'document issued then cannot charge GST.'
            ),
        })
    previously_invoiced = _previously_invoiced(order)
    paid = _paid_to_date(order, issued_on)
    invoiced = money(previously_invoiced + total)
    return {
        'issued_on': issued_on,
        'taxable_supply': taxable_supply,
        'tier': tier_for(total),
        'currency_code': order.currency_code,
        'subtotal_ex_tax': subtotal,
        'tax_total': tax_total,
        'total_incl_tax': total,
        'previously_invoiced': previously_invoiced,
        'paid_to_date': paid,
        'balance_due': money(max(invoiced - paid, ZERO)),
        'overpaid_at_issue': money(max(paid - invoiced, ZERO)),
        'notes': notes.strip(),
        'customer': order.customer,
        **seller,
        **buyer_snapshot(order.customer, buyer),
    }


def _correction_line_values(document_line, quantity, amount):
    """Split one credited amount the way the line it credits was split.

    The ratio is taken from the document line itself rather than recalculated
    from the rate, so a credit lands in the same box of a GST return as the
    supply it reverses even where rounding put a hundredth of a cent somewhere
    unexpected.
    """
    amount = money(amount)
    if amount <= ZERO:
        raise ValidationError({'lines': 'A correction moves an amount above zero.'})
    source_total = document_line.total_incl_tax
    if source_total > ZERO:
        fraction = amount / source_total
        subtotal = money(document_line.subtotal_ex_tax * fraction)
    else:
        subtotal = amount
    return {
        'document_line': document_line,
        'quantity': quantity,
        'tax_rate': document_line.tax_rate,
        'tax_treatment': document_line.tax_treatment,
        'subtotal_ex_tax': subtotal,
        'tax_total': money(amount - subtotal),
        'total_incl_tax': amount,
    }


def _check_correction_capacity(document, correction_type, line_values):
    """Refuse a correction that would take a line outside its own issued value.

    A credit cannot exceed what was charged, and a debit cannot exceed what has
    been credited back. Together those bound the net value of an issued line to
    somewhere between nothing and the figure on the document, which is the only
    range a correction to *this* supply can honestly move it through.
    """
    for values in line_values:
        line = values['document_line']
        if line.document_id != document.pk:
            raise ValidationError({'lines': 'Choose lines from the document being corrected.'})
        credited = net_credited(line)
        amount = values['total_incl_tax']
        if correction_type == SupplyCorrection.CorrectionType.CREDIT:
            headroom = money(line.total_incl_tax - credited)
            if amount > headroom:
                raise ValidationError({
                    'lines': (
                        f'Line {line.pk} has {headroom} left to credit; the '
                        'rest of it has already been credited away.'
                    ),
                })
        elif amount > credited:
            raise ValidationError({
                'lines': (
                    f'Line {line.pk} has {credited} of credit to reverse. A '
                    'debit note corrects a credit, and an undercharge needs an '
                    'order at the right price rather than a figure invented here.'
                ),
            })


@transaction.atomic
def issue_correction(document, user, *, operation_key, correction_type, reason_code, reason, lines, corrected_on=None, sales_return=None, refund=None, notes=''):
    """Issue one supply correction against an already-issued document.

    `lines` is a sequence of ``{'document_line': line, 'amount': Decimal,
    'quantity': int or None}``. The document itself is never touched: what it
    said when it was handed over is what it goes on saying, and the correction
    is the record of the change.
    """
    workspace = document.workspace
    requested_on = corrected_on
    corrected_on = corrected_on or local_date(workspace, timezone.now())
    payload = {
        'document': document.pk,
        'correction_type': correction_type,
        'reason_code': reason_code,
        'reason': reason,
        'lines': sorted(
            (item['document_line'].pk, str(money(item['amount']))) for item in lines
        ),
        'corrected_on': requested_on,
        'sales_return': getattr(sales_return, 'pk', None),
        'refund': getattr(refund, 'pk', None),
        'notes': notes,
    }
    fingerprint = request_fingerprint(payload)
    existing = _existing(SupplyCorrection, workspace, operation_key, fingerprint)
    if existing:
        return existing
    document = SupplyDocument.objects.select_for_update(of=('self',)).get(pk=document.pk)
    existing = _existing(SupplyCorrection, workspace, operation_key, fingerprint)
    if existing:
        return existing
    if not reason.strip():
        raise ValidationError({'reason': 'Say why the document needed correcting.'})
    if corrected_on < document.issued_on:
        raise ValidationError({
            'corrected_on': (
                'A correction cannot be dated before the document it corrects, '
                f'{document.issued_on.isoformat()}.'
            ),
        })
    if not lines:
        raise ValidationError({'lines': 'Select at least one line to correct.'})
    line_values = [
        _correction_line_values(item['document_line'], item.get('quantity'), item['amount'])
        for item in lines
    ]
    _check_correction_capacity(document, correction_type, line_values)
    correction = SupplyCorrection.objects.create(
        workspace=workspace,
        document=document,
        document_number=next_document_number(
            workspace,
            CREDIT_SERIES if correction_type == SupplyCorrection.CorrectionType.CREDIT else DEBIT_SERIES,
        ),
        correction_type=correction_type,
        reason_code=reason_code,
        reason=reason.strip(),
        corrected_on=corrected_on,
        sales_return=sales_return,
        refund=refund,
        currency_code=document.currency_code,
        subtotal_ex_tax=money(sum((row['subtotal_ex_tax'] for row in line_values), ZERO)),
        tax_total=money(sum((row['tax_total'] for row in line_values), ZERO)),
        total_incl_tax=money(sum((row['total_incl_tax'] for row in line_values), ZERO)),
        notes=notes.strip(),
        operation_key=operation_key,
        request_fingerprint=fingerprint,
        created_by=_actor(user),
        **_copied_parties(document),
    )
    SupplyCorrectionLine.objects.bulk_create([
        SupplyCorrectionLine(correction=correction, **values) for values in line_values
    ])
    return correction


def _copied_parties(document):
    """Copy the parties from the document being corrected, never re-derive them.

    A correction identifies the same supply between the same two parties. Re-
    reading the registration history would put this year's GST number on a
    correction to last year's invoice, which describes a supply that never
    happened.
    """
    return {
        'seller_legal_name': document.seller_legal_name,
        'seller_trading_name': document.seller_trading_name,
        'seller_address': document.seller_address,
        'seller_gst_number': document.seller_gst_number,
        'seller_registration': document.seller_registration,
        'customer': document.customer,
        'buyer_name': document.buyer_name,
        'buyer_address': document.buyer_address,
        'buyer_identifier': document.buyer_identifier,
    }


def full_credit(document, user, *, operation_key, reason_code, reason, corrected_on=None, sales_return=None, refund=None, notes=''):
    """Credit every line of one document in full.

    This is the cancellation and the wrong-treatment path. Because each line
    ends fully credited, its positions become invoiceable again and a corrected
    document can be issued in its place — which is why correcting a document is
    never editing one.
    """
    lines = [
        {
            'document_line': line,
            'amount': money(line.total_incl_tax - net_credited(line)),
            'quantity': line.quantity,
        }
        for line in document.lines.all()
    ]
    outstanding = [item for item in lines if item['amount'] > ZERO]
    if not outstanding:
        raise ValidationError({'document': 'Every line of that document has already been credited.'})
    return issue_correction(
        document, user,
        operation_key=operation_key,
        correction_type=SupplyCorrection.CorrectionType.CREDIT,
        reason_code=reason_code,
        reason=reason,
        lines=outstanding,
        corrected_on=corrected_on,
        sales_return=sales_return,
        refund=refund,
        notes=notes,
    )


def document_state(document):
    """Return what is left of one document after every correction against it.

    A document is never edited, so "what does it come to now" is a question
    only this function can answer — and it answers it without touching the row
    that says what was handed over.
    """
    credited = ZERO
    for line in document.lines.all():
        credited += net_credited(line)
    net = money(document.total_incl_tax - credited)
    if credited <= ZERO:
        status = 'issued'
    elif net <= ZERO:
        status = 'credited'
    else:
        status = 'part_credited'
    return {
        'status': status,
        'credited_total': money(credited),
        'net_total_incl_tax': net,
    }
