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
  received. This repository has no invoice document yet — task 118 owns it — so
  a fulfillment stands in for the invoice date. Every event says so through
  `time_of_supply_source` and `proxy`, so task 118 can find exactly the events
  it supersedes rather than re-deriving all of them.
* **Hybrid** is invoice for output tax and payments for input tax. Only output
  tax is decided here, so hybrid and invoice behave identically in this module;
  the difference shows up on the purchase side.

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

#: Payments are ranked before fulfillments on the same day, so a deposit and a
#: same-day delivery split deterministically. The totals are identical either
#: way; only the attribution moves, and an arbitrary attribution that changed
#: between runs would make a report impossible to reconcile.
PAYMENT_RANK = 0
FULFILLMENT_RANK = 1


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
class RefundPortion:
    """One refunded line's share, read straight off its RefundLine."""

    line_id: int
    gross: Decimal
    tax: Decimal


@dataclass(frozen=True)
class RefundFact:
    """One effective refund, already classified against the lines it credits."""

    refund_id: int
    refunded_on: date
    portions: Tuple[RefundPortion, ...]


@dataclass(frozen=True)
class OrderFacts:
    """Everything about one order that bears on when GST falls due."""

    order_id: int
    currency_code: str
    lines: Tuple[LineFact, ...]
    fulfillments: Tuple[FulfillmentFact, ...] = ()
    payments: Tuple[PaymentFact, ...] = ()
    refunds: Tuple[RefundFact, ...] = ()
    #: Returns with no refund against them. A return moves plants, not money,
    #: so no GST adjustment is due — but the credit note is outstanding work
    #: somebody has to do, and it is reported rather than assumed away.
    unrefunded_return_ids: Tuple[int, ...] = ()


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
    unrefunded_return_ids: Tuple[int, ...] = field(default_factory=tuple)

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
        else:
            recognised = _recognise_fulfillment(trigger, lines, remaining, basis)
        events.extend(recognised)

    credit_events, over_credited = _recognise_refunds(facts, events, as_at)
    events.extend(credit_events)
    events.sort(key=lambda event: (event.supply_date, event.source_type, event.source_id, event.line_id))
    return Recognition(
        events=tuple(events),
        unmatched_overpayment=quantize_money(overpaid),
        unrecognised_gross=quantize_money(sum(remaining.values(), ZERO)),
        over_credited=quantize_money(over_credited),
        unrefunded_return_ids=tuple(facts.unrefunded_return_ids),
    )


def _ordered_triggers(facts, basis, as_at):
    """Return every event that can bring a supply to account, in a stable order."""
    triggers = [
        (payment.paid_on, PAYMENT_RANK, payment.payment_id, payment)
        for payment in facts.payments
    ]
    if basis in (INVOICE, HYBRID):
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


def _recognise_fulfillment(fulfillment, lines, remaining, basis):
    """Bring the value one fulfillment delivered to account, less anything prepaid.

    Under the invoice and hybrid bases a fulfillment stands in for the invoice
    task 118 will issue. It recognises what it delivered, capped by what that
    line still owes: a deposit taken earlier has already brought part of it to
    account, and recognising the delivery in full would double-count it.
    """
    events = []
    for line_id, delivered in fulfillment.line_grosses.items():
        outstanding = remaining.get(line_id, ZERO)
        share = min(quantize_money(delivered), outstanding)
        if share <= 0:
            continue
        remaining[line_id] = quantize_money(outstanding - share)
        events.append(_supply_event(
            lines[line_id],
            share,
            fulfillment.supply_date,
            'fulfillment',
            fulfillment.fulfillment_id,
            proxy=basis in (INVOICE, HYBRID),
        ))
    return events


def _recognise_refunds(facts, supply_events, as_at):
    """Credit refunded value at the refund date, capped by what was supplied.

    The amounts are read straight off the refund's own lines rather than
    re-derived. `proportional_refund` already split the refund preserving each
    source line's tax ratio, so reading it back reconciles to the refund row
    exactly and cannot drift by a rounding step.
    """
    supplied = {}
    for event in supply_events:
        supplied[event.line_id] = supplied.get(event.line_id, ZERO) + event.gross
    events = []
    over_credited = ZERO
    for refund in facts.refunds:
        if as_at is not None and refund.refunded_on > as_at:
            continue
        for portion in refund.portions:
            available = supplied.get(portion.line_id, ZERO)
            gross = quantize_money(portion.gross)
            credited = min(gross, available)
            over_credited += gross - credited
            if credited <= 0:
                continue
            supplied[portion.line_id] = quantize_money(available - credited)
            tax = quantize_money(portion.tax)
            if credited < gross:
                tax = quantize_money(tax * credited / gross)
            events.append(RecognitionEvent(
                kind=SUPPLY_CREDIT,
                supply_date=refund.refunded_on,
                source_type='refund',
                source_id=refund.refund_id,
                line_id=portion.line_id,
                tax_code='',
                tax_rate=ZERO,
                gross=credited,
                tax=tax,
                time_of_supply_source='refund',
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
