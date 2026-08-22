"""Derive the GST entries a period report is built from, and reconciles to.

There is no GST ledger table. Every figure in a return is derived, at the
moment it is read, from the commerce records that produced it — and those are
already immutable: `Fulfillment`, `Payment`, `SalesReturn`, `Refund` and a
posted `StockReceipt` all refuse to be edited or deleted. So "reconcile every
period total to immutable source records" holds without a second copy of them,
and a change of basis rewrites nothing because there is nothing to rewrite.
Task 118 owns materialising this once it has real invoice documents to key on.

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

from inventory.ledger import quantize_money
from inventory.models import StockReceipt, StockReceiptLine

from .facts import workspace_order_facts
from .periods import (
    TaxablePeriod,
    enumerate_periods,
    registration_history,
    registration_in_force,
)
from .recognition import INVOICE, SUPPLY, SUPPLY_CREDIT, order_recognition


ZERO = Decimal('0.0000')
PERCENT_DIVISOR = Decimal('100')

SUPPLY_KINDS = (SUPPLY, SUPPLY_CREDIT)
PURCHASE = 'purchase'

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
    proxy: bool = False
    exclusion: str = ''

    @property
    def gross(self):
        """Value including every kind of tax, claimable or not."""
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
    for period in periods:
        entries.extend(_period_supply_entries(order_facts, period))
        entries.extend(_period_purchase_entries(workspace, period))
    context = _Uncovered(workspace, history, (start, end), _covered_days(periods))
    entries.extend(_unregistered_supply_entries(order_facts, context))
    entries.extend(_unregistered_purchase_entries(context))
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


def _period_purchase_entries(workspace, period):
    """Return the input tax a period may claim.

    Only the invoice basis can claim on the receipt date. Under the payments
    and hybrid bases input tax is claimed when the supplier is paid, and this
    application records no supplier payment anywhere — task 80 owns purchasing
    and accounts payable. So those purchases are reported as awaiting payment
    evidence rather than being claimed on a date that is not the right one.
    """
    if period.basis != INVOICE:
        return _awaiting_payment_entries(workspace, period.start, period.end, period.basis)
    return [
        _purchase_entry(line, period, period.basis)
        for line in _posted_receipt_lines(workspace, period.start, period.end)
    ]


def _unregistered_purchase_entries(context):
    """Report purchases made while unregistered, with the reason they claim nothing."""
    start, end = context.window
    entries = []
    for line in _posted_receipt_lines(context.workspace, start, end):
        received = line.receipt.received_date
        if received in context.covered:
            continue
        entry = _purchase_entry(line, None, '')
        entries.append(_with(entry, exclusion=context.exclusion_for(received)))
    return entries


def _awaiting_payment_entries(workspace, start, end, basis):
    """Return purchases held back for want of a supplier payment date."""
    return [
        _with(_purchase_entry(line, None, basis), exclusion=AWAITING_PAYMENT)
        for line in _posted_receipt_lines(workspace, start, end)
    ]


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
    full_tax = quantize_money(taxable * Decimal(receipt.tax_rate) / PERCENT_DIVISOR)
    recoverable = receipt.tax_recoverable
    return GstEntry(
        kind=PURCHASE,
        supply_date=receipt.received_date,
        period=period,
        basis=basis,
        source_type='stock_receipt',
        source_id=receipt.pk,
        document_id=receipt.pk,
        line_id=line.pk,
        tax_code='standard' if full_tax > 0 else 'unclassified',
        tax_rate=Decimal(receipt.tax_rate),
        taxable=taxable,
        tax=full_tax if recoverable else ZERO,
        non_recoverable_tax=ZERO if recoverable else full_tax,
        currency_code=receipt.currency_code,
        time_of_supply_source='receipt_date_proxy',
        proxy=True,
    )


def _posted_receipt_lines(workspace, start, end):
    """Return the lines of every posted receipt received in a range.

    A reversed receipt leaves POSTED, so filtering on status is what excludes
    it; there is no reversal pair to filter here as there is on the sales side.
    """
    return StockReceiptLine.objects.filter(
        receipt__workspace=workspace,
        receipt__status=StockReceipt.Status.POSTED,
        receipt__received_date__gte=start,
        receipt__received_date__lte=end,
    ).select_related('receipt').order_by('receipt__received_date', 'pk')


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
