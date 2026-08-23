"""When a supply is brought to account for GST, under each accounting basis.

This module is the whole of task 117's accounting judgement, and it holds no
database access at all. Facts come in as frozen dataclasses and recognition
events go out; the ORM read that gathers the facts lives in `services`. Keeping
the split means the basis-by-order-shape matrix — prepaid, part-paid,
invoiced-but-unpaid, fulfilled, returned, refunded, across three bases — is a
fast unit test rather than eighteen fixtures.

The three bases, and what each one waits for:

* **Payments** recognises on money received. An order invoiced and delivered
  but unpaid produces nothing at all, which is the point of the basis.
* **Invoice** recognises on the earlier of an invoice issued and a payment
  received. Task 118 built the invoice, so where one has been issued the event
  falls on its own date and says so. Where none has, a fulfillment still stands
  in for it and the event is marked `proxy` — an order delivered and never
  invoiced has still been supplied, and dropping it would understate a return.
  The flag is therefore not a leftover: it is the difference between a date
  somebody issued and a date this module chose.
* **Hybrid** is invoice for output tax and payments for input tax. Only output
  tax is decided here, so hybrid and invoice behave identically in this module;
  the difference shows up on the purchase side.

Credits work the same way round. Under the invoice and hybrid bases a credit
note is what reduces the consideration, so a correction document is the credit
event; a refund with no credit note against it still credits, marked `proxy`,
because money that went back is not nothing. Under the payments basis only the
refund matters, since no document moves cash.

Recognition is capped at the order's own value. Cash beyond it is not
consideration for a supply — it is an overpayment — and it is reported rather
than quietly inflating a return.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Mapping, Tuple

from inventory.ledger import distribute_exactly, quantize_money


PERCENT_DIVISOR = Decimal('100')
ZERO = Decimal('0.0000')

#: Accounting bases, matching ``GstRegistration.Basis``. Restated as plain
#: strings so this module stays free of the model layer; a test keeps them in
#: step, the way ``costing`` keeps its source tuples honest.
PAYMENTS = 'payments'
INVOICE = 'invoice'
HYBRID = 'hybrid'
BASES = (PAYMENTS, INVOICE, HYBRID)

#: Tax codes, matching ``SalesOrderLine.TaxTreatment``. Only a standard-rated
#: supply carries output tax; the other codes are the reason a zero-rated sale
#: can be told apart from an exempt one, which a rate of zero cannot do.
STANDARD = 'standard'
ZERO_RATED = 'zero_rated'
EXEMPT = 'exempt'
OUT_OF_SCOPE = 'out_of_scope'
UNCLASSIFIED = 'unclassified'

#: Codes that count towards taxable turnover for the registration threshold.
#: Exempt and out-of-scope supplies do not, and an unclassified supply is not
#: assumed either way — it is reported as a gap.
TURNOVER_CODES = (STANDARD, ZERO_RATED)

#: Triggers falling on one day are processed in this order, so a deposit, an
#: invoice and a same-day delivery split deterministically. The totals are
#: identical whatever the order; only the attribution moves, and an arbitrary
#: attribution that changed between runs would make a report impossible to
#: reconcile. An invoice ranks ahead of a fulfillment because where both exist
#: the invoice is the real time of supply and the fulfillment is the stand-in.
PAYMENT_RANK = 0
INVOICE_RANK = 1
FULFILLMENT_RANK = 2


@dataclass(frozen=True)
class LineFact:
    """One sales order line's commercial terms, as snapshotted."""

    line_id: int
    tax_rate: Decimal
    tax_code: str
    gross_incl_tax: Decimal


@dataclass(frozen=True)
class FulfillmentFact:
    """One effective fulfillment and the gross value it delivered per line."""

    fulfillment_id: int
    supply_date: date
    line_grosses: Mapping[int, Decimal]


@dataclass(frozen=True)
class PaymentFact:
    """One effective payment. It carries no line linkage and no tax split."""

    payment_id: int
    paid_on: date
    gross: Decimal


@dataclass(frozen=True)
class InvoiceFact:
    """One issued taxable supply document and the value it invoiced per line.

    This is what supersedes the fulfillment stand-in. `issued_on` is already a
    workspace-local business date, because a document is dated rather than
    timestamped — an invoice is issued on a day, not at an instant.
    """

    document_id: int
    issued_on: date
    line_grosses: Mapping[int, Decimal]


@dataclass(frozen=True)
class CreditPortion:
    """One credited line's share, read straight off the row that credits it.

    Used by both a refund line and a correction line. The two are different
    documents but the same three facts, and deriving the tax again here rather
    than reading it would let a credit drift by a rounding step from the supply
    it reverses.
    """

    line_id: int
    gross: Decimal
    tax: Decimal


@dataclass(frozen=True)
class RefundFact:
    """One effective refund, already classified against the lines it credits.

    `credited_by_document` says a credit note covers this refund. Under the
    invoice and hybrid bases the note is the credit event, so counting the
    refund as well would credit the same money twice.
    """

    refund_id: int
    refunded_on: date
    portions: Tuple[CreditPortion, ...]
    credited_by_document: bool = False


@dataclass(frozen=True)
class CorrectionFact:
    """One issued supply correction, classified against the lines it moves.

    A credit note reduces the consideration for a supply and a debit note puts
    part of a credit back. Both fall due on the correction's own date, which is
    the whole point of the document: the adjustment belongs in the period it
    was issued in, not retroactively in the period of the supply.
    """

    correction_id: int
    corrected_on: date
    kind: str
    portions: Tuple[CreditPortion, ...]


# An order's facts are one attribute per kind of record that can bear on its
# GST, and there is no grouping of them that is not arbitrary — the same reason
# `RecognitionEvent` below carries the disable.
@dataclass(frozen=True)
class OrderFacts:  # pylint: disable=too-many-instance-attributes
    """Everything about one order that bears on when GST falls due."""

    order_id: int
    currency_code: str
    lines: Tuple[LineFact, ...]
    fulfillments: Tuple[FulfillmentFact, ...] = ()
    invoices: Tuple[InvoiceFact, ...] = ()
    payments: Tuple[PaymentFact, ...] = ()
    refunds: Tuple[RefundFact, ...] = ()
    corrections: Tuple[CorrectionFact, ...] = ()
    #: Returns with neither a refund nor a credit note against them. A return
    #: moves plants, not money, so no GST adjustment is due on its own — but
    #: the correction document is outstanding work somebody has to do, and it
    #: is reported rather than assumed away.
    uncredited_return_ids: Tuple[int, ...] = ()


# A recognition event has to carry every fact a return column and its
# drill-down need, and there is no grouping of them that is not arbitrary.
@dataclass(frozen=True)
class RecognitionEvent:  # pylint: disable=too-many-instance-attributes
    """One line's worth of supply or credit, at the date it falls due."""

    kind: str
    supply_date: date
    source_type: str
    source_id: int
    line_id: int
    tax_code: str
    tax_rate: Decimal
    gross: Decimal
    tax: Decimal
    time_of_supply_source: str
    proxy: bool = False

    @property
    def taxable(self):
        """The ex-GST value, derived as the residual so the row always balances."""
        return self.gross - self.tax


SUPPLY = 'supply'
SUPPLY_CREDIT = 'supply_credit'

#: Correction kinds, matching ``billing.SupplyCorrection.CorrectionType``.
#: Restated as plain strings so this module stays free of the model layer, the
#: way the bases and tax codes above are; a test keeps them in step.
CREDIT_NOTE = 'credit'
DEBIT_NOTE = 'debit'


@dataclass(frozen=True)
class Recognition:
    """What one order contributes to GST returns, and what it cannot answer."""

    events: Tuple[RecognitionEvent, ...] = ()
    #: Cash received beyond the order's own value. Not consideration for any
    #: supply, so it carries no GST until it is matched or refunded.
    unmatched_overpayment: Decimal = ZERO
    #: Value that has not yet reached a time of supply. Under the payments
    #: basis this is the unpaid balance; under invoice it is the undelivered,
    #: unpaid remainder.
    unrecognised_gross: Decimal = ZERO
    #: Credits that exceeded the supply they were credited against. Should be
    #: zero — `post_refund` caps refunds — so a non-zero value is a defect
    #: worth reporting rather than absorbing.
    over_credited: Decimal = ZERO
    uncredited_return_ids: Tuple[int, ...] = field(default_factory=tuple)
    #: Supplies brought to account on a fulfillment because no document was
    #: issued for them. Reported so a period can say how much of it rests on a
    #: date nobody chose.
    proxy_gross: Decimal = ZERO

    @property
    def supply_events(self):
        """Every event that brings a supply to account."""
        return tuple(event for event in self.events if event.kind == SUPPLY)

    @property
    def credit_events(self):
        """Every event that credits a supply already brought to account."""
        return tuple(event for event in self.events if event.kind == SUPPLY_CREDIT)

    @property
    def recognised_gross(self):
        """Gross value brought to account, net of credits."""
        supplied = sum((event.gross for event in self.supply_events), ZERO)
        credited = sum((event.gross for event in self.credit_events), ZERO)
        return quantize_money(supplied - credited)


def line_tax(gross, tax_rate, tax_code):
    """Return the GST inside a tax-inclusive amount.

    Only a standard-rated supply carries tax. Branching on the code rather than
    on the rate means an exempt line that somehow carries a rate cannot produce
    output tax, and it keeps a zero-rated line at exactly zero rather than at a
    rounded zero.
    """
    if tax_code != STANDARD:
        return ZERO
    rate = Decimal(tax_rate)
    return quantize_money(Decimal(gross) * rate / (PERCENT_DIVISOR + rate))


def order_recognition(facts, basis, *, as_at=None):
    """Return when each part of one order falls due under an accounting basis.

    ``as_at`` stops the clock: only triggers on or before that date are
    processed, which is what makes a basis-change adjustment computable from
    the same rules rather than from a second implementation of them.
    """
    if basis not in BASES:
        raise ValueError(f'Unknown accounting basis: {basis!r}')
    lines = {line.line_id: line for line in facts.lines}
    remaining = {line.line_id: quantize_money(line.gross_incl_tax) for line in facts.lines}
    events = []
    overpaid = ZERO

    for _, _, _, trigger in _ordered_triggers(facts, basis, as_at):
        if isinstance(trigger, PaymentFact):
            recognised, unmatched = _recognise_payment(trigger, lines, remaining)
            overpaid += unmatched
        elif isinstance(trigger, InvoiceFact):
            recognised = _recognise_invoice(trigger, lines, remaining)
        else:
            recognised = _recognise_fulfillment(trigger, lines, remaining, basis)
        events.extend(recognised)

    credit_events, over_credited = _recognise_credits(facts, basis, events, as_at)
    events.extend(credit_events)
    events.sort(key=lambda event: (event.supply_date, event.source_type, event.source_id, event.line_id))
    return Recognition(
        events=tuple(events),
        unmatched_overpayment=quantize_money(overpaid),
        unrecognised_gross=quantize_money(sum(remaining.values(), ZERO)),
        over_credited=quantize_money(over_credited),
        uncredited_return_ids=tuple(facts.uncredited_return_ids),
        proxy_gross=quantize_money(sum(
            (event.gross for event in events if event.proxy), ZERO,
        )),
    )


def _ordered_triggers(facts, basis, as_at):
    """Return every event that can bring a supply to account, in a stable order."""
    triggers = [
        (payment.paid_on, PAYMENT_RANK, payment.payment_id, payment)
        for payment in facts.payments
    ]
    if basis in (INVOICE, HYBRID):
        triggers.extend(
            (item.issued_on, INVOICE_RANK, item.document_id, item)
            for item in facts.invoices
        )
        triggers.extend(
            (item.supply_date, FULFILLMENT_RANK, item.fulfillment_id, item)
            for item in facts.fulfillments
        )
    if as_at is not None:
        triggers = [trigger for trigger in triggers if trigger[0] <= as_at]
    return sorted(triggers, key=lambda trigger: trigger[:3])


def _recognise_payment(payment, lines, remaining):
    """Apportion one payment across the lines that still owe a time of supply.

    A payment has no line linkage and no tax split, so the split has to be
    derived. Weighting by each line's remaining tax-inclusive value and using
    the ledger's own largest-remainder split means the shares sum back to
    exactly the payment and do not churn when recomputed.
    """
    outstanding = quantize_money(sum(remaining.values(), ZERO))
    gross = quantize_money(payment.gross)
    if outstanding <= 0:
        return [], gross
    applied = min(gross, outstanding)
    ordered = [line_id for line_id in remaining if remaining[line_id] > 0]
    shares = distribute_exactly(applied, [remaining[line_id] for line_id in ordered])
    events = []
    for line_id, share in zip(ordered, shares):
        if share <= 0:
            continue
        remaining[line_id] = quantize_money(remaining[line_id] - share)
        events.append(_supply_event(
            lines[line_id], share, payment.paid_on, 'payment', payment.payment_id,
        ))
    return events, quantize_money(gross - applied)


def _recognise_document(line_grosses, lines, remaining, event):
    """Bring the value one document or dispatch names to account, once.

    Every share is capped by what its line still owes. That single cap is what
    makes a deposit, an invoice and a delivery of the same goods add up to the
    order rather than to three times it: whichever comes first consumes the
    value, and the rest find nothing left to recognise.

    ``event`` carries the date, the source and whether the date was chosen by
    this module rather than by somebody issuing a document.
    """
    supply_date, source_type, source_id, proxy = event
    events = []
    for line_id, named in line_grosses.items():
        outstanding = remaining.get(line_id, ZERO)
        share = min(quantize_money(named), outstanding)
        if share <= 0:
            continue
        remaining[line_id] = quantize_money(outstanding - share)
        events.append(_supply_event(
            lines[line_id], share, supply_date, source_type, source_id, proxy=proxy,
        ))
    return events


def _recognise_invoice(invoice, lines, remaining):
    """Bring an issued document to account on the date it was issued.

    This is the invoice basis doing what it actually says, rather than through
    the fulfillment stand-in. Nothing is marked `proxy`, because the date came
    off a document somebody handed a customer.
    """
    return _recognise_document(
        invoice.line_grosses, lines, remaining,
        (invoice.issued_on, 'supply_document', invoice.document_id, False),
    )


def _recognise_fulfillment(fulfillment, lines, remaining, basis):
    """Bring the value one fulfillment delivered to account, less anything prepaid.

    Under the invoice and hybrid bases this is the stand-in for a document that
    was never issued. Where one was, the invoice ran first and consumed the
    line's value, so this finds nothing left and produces no event at all —
    which is exactly how the proxy gets superseded rather than switched off.
    """
    return _recognise_document(
        fulfillment.line_grosses, lines, remaining,
        (
            fulfillment.supply_date,
            'fulfillment',
            fulfillment.fulfillment_id,
            basis in (INVOICE, HYBRID),
        ),
    )


@dataclass(frozen=True)
class _CreditSource:
    """One dated adjustment to consideration already brought to account."""

    adjusted_on: date
    source_type: str
    source_id: int
    kind: str
    portions: Tuple[CreditPortion, ...]
    proxy: bool

    @property
    def order_key(self):
        """Sort adjustments deterministically, so a report reconciles run to run."""
        return (self.adjusted_on, self.source_type, self.source_id)


def _credit_sources(facts, basis, as_at):
    """Return what reduces or restores consideration, in a stable order.

    Under the invoice and hybrid bases a credit note is what alters the agreed
    consideration, so it is the event; a refund carrying a credit note is that
    same money and is skipped, while a refund carrying none still credits as a
    stand-in and says so. Under the payments basis only cash matters, so the
    correction documents are not events at all — they change no money.
    """
    documented = basis in (INVOICE, HYBRID)
    sources = [
        _CreditSource(
            adjusted_on=refund.refunded_on,
            source_type='refund',
            source_id=refund.refund_id,
            kind=SUPPLY_CREDIT,
            portions=refund.portions,
            proxy=documented,
        )
        for refund in facts.refunds
        if not (documented and refund.credited_by_document)
    ]
    if documented:
        sources.extend(
            _CreditSource(
                adjusted_on=correction.corrected_on,
                source_type='supply_correction',
                source_id=correction.correction_id,
                kind=SUPPLY_CREDIT if correction.kind == CREDIT_NOTE else SUPPLY,
                portions=correction.portions,
                proxy=False,
            )
            for correction in facts.corrections
        )
    if as_at is not None:
        sources = [source for source in sources if source.adjusted_on <= as_at]
    return sorted(sources, key=lambda source: source.order_key)


def _recognise_credits(facts, basis, supply_events, as_at):
    """Adjust already-recognised supply at the date the adjustment was made.

    The amounts are read straight off the row that credits them rather than
    re-derived. `proportional_refund` and `billing.documents` each split their
    lines preserving the source line's tax ratio, so reading them back
    reconciles exactly and cannot drift by a rounding step.

    A credit is capped by what has actually been brought to account for that
    line; anything beyond it is reported as `over_credited` rather than
    absorbed, because a credit larger than its supply is a defect somewhere
    else. A debit note is not capped here — `billing` already bounds it by the
    credit it reverses — and it puts supply back.
    """
    supplied = {}
    for event in supply_events:
        supplied[event.line_id] = supplied.get(event.line_id, ZERO) + event.gross
    events = []
    over_credited = ZERO
    for source in _credit_sources(facts, basis, as_at):
        for portion in source.portions:
            gross = quantize_money(portion.gross)
            tax = quantize_money(portion.tax)
            if source.kind == SUPPLY:
                supplied[portion.line_id] = quantize_money(supplied.get(portion.line_id, ZERO) + gross)
            else:
                available = supplied.get(portion.line_id, ZERO)
                credited = min(gross, available)
                over_credited += gross - credited
                if credited <= 0:
                    continue
                supplied[portion.line_id] = quantize_money(available - credited)
                if credited < gross:
                    tax = quantize_money(tax * credited / gross)
                gross = credited
            events.append(RecognitionEvent(
                kind=source.kind,
                supply_date=source.adjusted_on,
                source_type=source.source_type,
                source_id=source.source_id,
                line_id=portion.line_id,
                tax_code='',
                tax_rate=ZERO,
                gross=gross,
                tax=tax,
                time_of_supply_source=source.source_type,
                proxy=source.proxy,
            ))
    return events, over_credited


def _supply_event(line, gross, supply_date, source_type, source_id, *, proxy=False):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Build one supply event, deriving its tax from the line's own code."""
    gross = quantize_money(gross)
    return RecognitionEvent(
        kind=SUPPLY,
        supply_date=supply_date,
        source_type=source_type,
        source_id=source_id,
        line_id=line.line_id,
        tax_code=line.tax_code,
        tax_rate=Decimal(line.tax_rate),
        gross=gross,
        tax=line_tax(gross, line.tax_rate, line.tax_code),
        time_of_supply_source=source_type,
        proxy=proxy,
    )
