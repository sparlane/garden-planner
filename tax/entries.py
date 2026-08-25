"""Derive the GST entries a period report is built from, and reconciles to.

There is no GST ledger table. Every figure in a return is derived, at the
moment it is read, from the commerce records that produced it — and those are
already immutable: `Fulfillment`, `Payment`, `SalesReturn`, `Refund`, a posted
`StockReceipt` and task 118's `SupplyDocument` all refuse to be edited or
deleted. So "reconcile every period total to immutable source records" holds
without a second copy of them, and a change of basis rewrites nothing because
there is nothing to rewrite. Materialising the derivation would add a copy to
keep in step with immutable rows that cannot drift, so it stays derived.

The basis applied to a period is the basis in force during it. That is safe
because a period is clipped whenever an arrangement changes, so no period ever
spans two bases. Where an order straddles a change, the two periods can
legitimately disagree about how much of it has been brought to account — and
the size of that disagreement is the transition adjustment, which `transition`
computes rather than hides.

Anything falling outside a registration produces an entry with no period and an
exclusion reason. Nothing is silently dropped and nothing becomes a zero.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from django.db import models

from inventory.ledger import quantize_money
from inventory.models import InputTaxAdjustment, StockReceipt, StockReceiptLine

from .facts import workspace_order_facts
from .periods import (
    TaxablePeriod,
    enumerate_periods,
    registration_history,
    registration_in_force,
)
from .recognition import (
    HYBRID,
    INVOICE,
    PAYMENTS,
    SUPPLY,
    SUPPLY_CREDIT,
    order_recognition,
)


ZERO = Decimal('0.0000')
SUPPLY_KINDS = (SUPPLY, SUPPLY_CREDIT)
PURCHASE = 'purchase'
INPUT_TAX_ADJUSTMENT = 'input_tax_adjustment'

#: The bases that claim input tax when the supplier is paid rather than when
#: the goods were received. Hybrid follows the payments rule on the input side
#: and the invoice rule on the output side, which is the whole of what makes it
#: hybrid, so it belongs here and not in `OUTPUT_RULE`'s company.
PAYMENT_BASED = (PAYMENTS, HYBRID)

#: Where a purchase entry's date came from. A settled receipt is dated by the
#: payment that discharged it; everything else falls back to the receipt date,
#: which stands in for a supplier invoice date the application does not hold.
SUPPLIER_PAYMENT = 'supplier_payment'
RECEIPT_PROXY = 'receipt_date_proxy'
SUPPLIER_INVOICE = 'supplier_invoice'
CHANGE_OF_USE = 'change_of_use_adjustment'

#: Why an entry belongs to no taxable period. Each becomes a data-quality code
#: on the report, with the rows behind it reachable through the drill-down.
NO_REGISTRATION = 'no_registration'
DEREGISTERED_GAP = 'deregistered_gap'
AWAITING_PAYMENT = 'input_tax_awaiting_payment'


@dataclass(frozen=True)
class GstEntry:  # pylint: disable=too-many-instance-attributes
    """One line's worth of supply, credit, or purchase, placed in a period."""

    kind: str
    supply_date: date
    period: Optional[TaxablePeriod]
    basis: str
    source_type: str
    source_id: int
    document_id: Optional[int]
    line_id: Optional[int]
    tax_code: str
    tax_rate: Decimal
    taxable: Decimal
    tax: Decimal
    non_recoverable_tax: Decimal
    currency_code: str
    time_of_supply_source: str
    input_tax_source: str = ''
    adjustment_direction: str = ''
    proxy: bool = False
    exclusion: str = ''

    @property
    def gross(self):
        """Value including every kind of tax, claimable or not."""
        if self.kind == INPUT_TAX_ADJUSTMENT:
            return ZERO
        if self.input_tax_source == StockReceiptLine.InputTaxSource.CUSTOMS:
            return quantize_money(self.taxable + self.non_recoverable_tax)
        return quantize_money(self.taxable + self.tax + self.non_recoverable_tax)

    @property
    def period_label(self):
        """The period this entry is reported in, or None when it is in none."""
        return self.period.label if self.period else None


def derive_entries(workspace, start, end):
    """Return every GST entry falling in a date range, in supply-date order."""
    history = registration_history(workspace)
    periods = enumerate_periods(workspace, start, end, history=history)
    order_facts = workspace_order_facts(workspace, start, end)
    entries = []
    purchases = []
    # Periods arrive in order, so a line claimed by an earlier one is a line a
    # later one must not claim again. That is not hypothetical: a receipt
    # received under the invoice basis is claimed on its receipt date, and if
    # the workspace then moves to the payments basis the period holding its
    # settlement date would claim the very same line a second time. First claim
    # wins, which is also the right answer — the tax was already recovered.
    claimed = set()
    for period in periods:
        entries.extend(_period_supply_entries(order_facts, period))
        purchases.extend(_period_purchase_entries(workspace, period, claimed))
        entries.extend(_period_adjustment_entries(workspace, period))
    entries.extend(purchases)
    context = _Uncovered(workspace, history, (start, end), _covered_days(periods))
    entries.extend(_unregistered_supply_entries(order_facts, context))
    entries.extend(_unregistered_purchase_entries(
        context,
        {entry.line_id for entry in purchases},
    ))
    entries.extend(_unregistered_adjustment_entries(context))
    entries.sort(key=lambda entry: (entry.supply_date, entry.kind, entry.source_id, entry.line_id or 0))
    return entries


def _period_supply_entries(order_facts, period):
    """Bring every order to account under the basis this period was filed on."""
    entries = []
    for facts in order_facts:
        recognition = order_recognition(facts, period.basis)
        for event in recognition.events:
            if not period.contains(event.supply_date):
                continue
            entries.append(_supply_entry(facts, event, period))
    return entries


def _supply_entry(facts, event, period):
    """Place one recognition event in the period that reports it.

    A period of None means the supply fell outside every registration, so
    there is no basis it was accounted on either.
    """
    return GstEntry(
        kind=event.kind,
        supply_date=event.supply_date,
        period=period,
        basis=period.basis if period else '',
        source_type=event.source_type,
        source_id=event.source_id,
        document_id=facts.order_id,
        line_id=event.line_id,
        tax_code=event.tax_code,
        tax_rate=event.tax_rate,
        taxable=event.taxable,
        tax=event.tax,
        non_recoverable_tax=ZERO,
        currency_code=facts.currency_code,
        time_of_supply_source=event.time_of_supply_source,
        proxy=event.proxy,
    )


def _unregistered_supply_entries(order_facts, context):
    """Report supplies made while unregistered instead of dropping them.

    Commerce before a registration, or inside a gap after a cessation, carried
    no GST obligation. Leaving it out entirely would make a report look
    complete when it had quietly skipped a year of trading, so it appears with
    no period and the reason it has none.
    """
    start, end = context.window
    entries = []
    for facts in order_facts:
        recognition = order_recognition(facts, INVOICE)
        for event in recognition.events:
            if not start <= event.supply_date <= end or event.supply_date in context.covered:
                continue
            entry = _supply_entry(facts, event, None)
            entries.append(_with(entry, exclusion=context.exclusion_for(event.supply_date)))
    return entries


def _period_purchase_entries(workspace, period, claimed):
    """Return the input tax a period may claim, on the date its basis claims it.

    Only the invoice basis claims on the receipt date. Under the payments and
    hybrid bases input tax is claimed when the supplier is paid, which is
    `StockReceipt.settled_on`, so a period claims what it settled rather than
    what it received. A receipt with no settlement date recorded has no
    claimable date yet and is held back rather than claimed on a date that is
    not the right one.

    The two queries cannot be one. A receipt is claimed by the period holding
    its payment and held back by the period holding its receipt, and those are
    routinely different periods — which is exactly the case a single date range
    over one column would get wrong.

    `claimed` carries the lines earlier periods already took, and is added to
    here. See `derive_entries` for why a period has to be told.
    """
    if period.basis == INVOICE:
        lines = _invoiced_receipt_lines(workspace, period.start, period.end)
        return [
            _purchase_entry(line, period, period.basis)
            for line in _take(lines, claimed)
        ]
    settled = _settled_receipt_lines(workspace, period.start, period.end)
    entries = [
        _purchase_entry(line, period, period.basis)
        for line in _take(settled, claimed)
    ]
    unsettled = _unsettled_receipt_lines(workspace, period.start, period.end)
    entries.extend(
        _with(
            _purchase_entry(line, None, period.basis),
            exclusion=AWAITING_PAYMENT,
        )
        for line in _take(unsettled, claimed)
    )
    return entries


def _take(lines, claimed):
    """Yield the lines no earlier period has taken, marking each as taken."""
    for line in lines:
        if line.pk in claimed:
            continue
        claimed.add(line.pk)
        yield line


def _unregistered_purchase_entries(context, accounted_line_ids):
    """Report purchases no taxable period accounted for, with the reason.

    Which lines are left over cannot be read off the receipt date alone, because
    a period accounts for a line under whichever date its basis claims on: a
    payments-basis receipt received before a registration and paid inside one is
    claimed by the period, not stranded here. So the period pass says what it
    took and this reports the remainder.

    Which day a leftover is explained by is `_unaccounted_day`'s business, and
    a leftover with no such day is not unaccounted for at all.
    """
    start, end = context.window
    entries = []
    for line in _invoiced_receipt_lines(context.workspace, start, end):
        if line.pk in accounted_line_ids:
            continue
        day = _unaccounted_day(line, context)
        if day is None:
            continue
        reason = context.exclusion_for(day)
        if not reason:
            continue
        entries.append(_with(_purchase_entry(line, None, ''), exclusion=reason))
    return entries


def _unaccounted_day(line, context):
    """Return the day a leftover purchase has to be explained by, or None.

    Only a payment-based basis can leave a line whose receipt date a period
    covers: it claimed on the settlement date instead. So where the receipt date
    is covered, the settlement date is the one that fell outside a registration
    and the one the reason has to be read on — a receipt bought while registered
    and paid after a cessation claims nothing, and says which of the two dates
    is why. The invoice basis takes every line received inside a period, so it
    never reaches here with a covered receipt date, and its leftovers are always
    explained by the receipt date they were going to be claimed on.

    A settlement outside the reported range belongs to a period the range does
    not cover. That line is accounted for elsewhere, and saying nothing about it
    here is what makes a narrow range honest rather than lossy.
    """
    invoice_day = line.receipt.invoice_date or line.receipt.received_date
    if invoice_day not in context.covered:
        return invoice_day
    start, end = context.window
    settled = line.receipt.settled_on
    if settled is not None and start <= settled <= end:
        return settled
    return None


def _purchase_entry(line, period, basis):
    """Split one receipt line into its recoverable and non-recoverable tax.

    The claimable amount is recomputed rather than read: `_receipt_acquisition_cost`
    calculates it to decide what to capitalise into stock and then discards it,
    so it exists nowhere in the database. Where the tax is not recoverable it is
    already inside the lot's cost, and it is reported as a memo here so a period
    states the whole of what was paid rather than only the claimable part.

    Tax treatment is a property of the whole receipt, not of its lines; task 119
    owns moving it down to the line, and the report says so.
    """
    receipt = line.receipt
    taxable = quantize_money(line.line_cost_ex_tax)
    recoverable = quantize_money(line.recoverable_input_tax)
    non_recoverable = quantize_money(line.non_recoverable_tax)
    # A settlement date is a date somebody paid a supplier on, so under a basis
    # that claims on payment it is the real time of supply and not a stand-in.
    # Every other purchase is still dated by the receipt standing in for the
    # supplier invoice date this application does not hold, and stays a proxy.
    paid = basis in PAYMENT_BASED and receipt.settled_on is not None
    invoice_day = receipt.invoice_date or receipt.received_date
    return GstEntry(
        kind=PURCHASE,
        supply_date=receipt.settled_on if paid else invoice_day,
        period=period,
        basis=basis,
        source_type='stock_receipt',
        source_id=receipt.pk,
        document_id=receipt.pk,
        line_id=line.pk,
        tax_code=line.tax_treatment,
        tax_rate=Decimal(line.tax_rate),
        taxable=taxable,
        tax=recoverable,
        non_recoverable_tax=non_recoverable,
        currency_code=receipt.currency_code,
        time_of_supply_source=(
            SUPPLIER_PAYMENT if paid
            else SUPPLIER_INVOICE if receipt.invoice_date
            else RECEIPT_PROXY
        ),
        input_tax_source=line.input_tax_source,
        proxy=not paid and receipt.invoice_date is None,
    )


def _period_adjustment_entries(workspace, period):
    """Return change-of-use adjustments made inside one taxable period."""
    adjustments = InputTaxAdjustment.objects.filter(
        workspace=workspace,
        adjustment_date__gte=period.start,
        adjustment_date__lte=period.end,
    ).select_related('receipt_line__receipt').order_by('adjustment_date', 'pk')
    return [_adjustment_entry(adjustment, period) for adjustment in adjustments]


def _unregistered_adjustment_entries(context):
    """Keep adjustments outside registration visible and unclaimed."""
    start, end = context.window
    adjustments = InputTaxAdjustment.objects.filter(
        workspace=context.workspace,
        adjustment_date__gte=start,
        adjustment_date__lte=end,
    ).select_related('receipt_line__receipt').order_by('adjustment_date', 'pk')
    entries = []
    for adjustment in adjustments:
        if adjustment.adjustment_date in context.covered:
            continue
        entry = _adjustment_entry(adjustment, None)
        entries.append(_with(
            entry,
            exclusion=context.exclusion_for(adjustment.adjustment_date),
        ))
    return entries


def _adjustment_entry(adjustment, period):
    amount = quantize_money(abs(adjustment.tax_adjustment))
    return GstEntry(
        kind=INPUT_TAX_ADJUSTMENT,
        supply_date=adjustment.adjustment_date,
        period=period,
        basis=period.basis if period else '',
        source_type='input_tax_adjustment',
        source_id=adjustment.pk,
        document_id=adjustment.receipt_line.receipt_id,
        line_id=adjustment.receipt_line_id,
        tax_code=adjustment.receipt_line.tax_treatment,
        tax_rate=adjustment.receipt_line.tax_rate,
        taxable=ZERO,
        tax=amount,
        non_recoverable_tax=ZERO,
        currency_code=adjustment.receipt_line.receipt.currency_code,
        time_of_supply_source=CHANGE_OF_USE,
        input_tax_source=CHANGE_OF_USE,
        adjustment_direction='credit' if adjustment.tax_adjustment > 0 else 'debit',
    )


def _receipt_lines(workspace):
    """Return every posted receipt line in a workspace.

    A reversed receipt leaves POSTED, so filtering on status is what excludes
    it; there is no reversal pair to filter here as there is on the sales side.
    A settlement date recorded before a reversal goes with it, because the
    receipt it belongs to stops being claimable at all.
    """
    return StockReceiptLine.objects.filter(
        receipt__workspace=workspace,
        receipt__status=StockReceipt.Status.POSTED,
    ).select_related('receipt')


def _invoiced_receipt_lines(workspace, start, end):
    """Return lines whose invoice date, or receipt proxy, is in a range."""
    return _receipt_lines(workspace).filter(
        models.Q(
            receipt__invoice_date__gte=start,
            receipt__invoice_date__lte=end,
        ) | models.Q(
            receipt__invoice_date__isnull=True,
            receipt__received_date__gte=start,
            receipt__received_date__lte=end,
        ),
    ).order_by('receipt__received_date', 'pk')


def _settled_receipt_lines(workspace, start, end):
    """Return the lines of every posted receipt paid for in a range."""
    return _receipt_lines(workspace).filter(
        receipt__settled_on__gte=start,
        receipt__settled_on__lte=end,
    ).order_by('receipt__settled_on', 'pk')


def _unsettled_receipt_lines(workspace, start, end):
    """Return the lines of posted receipts received in a range and not yet paid."""
    return _invoiced_receipt_lines(workspace, start, end).filter(
        receipt__settled_on__isnull=True,
    )


class _Uncovered:  # pylint: disable=too-few-public-methods
    """The days in a report range that no taxable period accounts for.

    Bundled rather than passed around loose because every caller needs the
    same four things, and a six-argument helper reads worse than the thing it
    is helping.
    """

    def __init__(self, workspace, history, window, covered):
        self.workspace = workspace
        self.history = history
        self.window = window
        self.covered = covered

    def exclusion_for(self, day):
        """Say why a day belongs to no period: never registered, or no longer.

        The two are told apart by whether anything was recorded on or before
        the day itself, not by whether the workspace has any history at all —
        a workspace that registers in July has no registration in March, and
        calling that a cessation gap would misdescribe it.
        """
        if registration_in_force(self.workspace, day, history=self.history) is not None:
            return ''
        applying = [row for row in self.history if row.effective_from <= day]
        return DEREGISTERED_GAP if applying else NO_REGISTRATION


def _covered_days(periods):
    """Return every day a taxable period accounts for."""
    covered = set()
    for period in periods:
        for ordinal in range(period.start.toordinal(), period.end.toordinal() + 1):
            covered.add(date.fromordinal(ordinal))
    return covered


def _with(entry, **changes):
    """Return a copy of an entry with fields replaced."""
    values = dict(entry.__dict__)
    values.update(changes)
    return GstEntry(**values)
