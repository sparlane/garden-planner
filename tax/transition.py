"""What a change of accounting basis leaves outstanding, and which way.

Changing basis does not restate anything already filed. It does leave a gap:
under the payments basis, GST on a supply is not brought to account until the
money arrives, so on the day a workspace moves to the invoice basis it is
carrying supplies that have been made and not yet accounted for. Those are its
debtors, and the GST on them has to be brought in with a one-off adjustment.
Moving the other way is the mirror — output tax already returned on unpaid
debtors comes back out.

The whole computation reuses `order_recognition` with a date cutoff rather than
implementing the rules a second time, which is why that function takes `basis`
and `as_at` as parameters at all. The difference between what the two bases had
recognised by the change date *is* the adjustment.

The creditors side is still not computed here. `StockReceipt.settled_on` now
says when a receipt was paid, which is enough for a period to claim input tax,
but not enough for this adjustment: it carries no partial payments and covers
only stock received through the ledger, so an unpaid-creditors balance built
from it would be a floor presented as a total. Task 80 owns purchasing and
accounts payable, task 120 the liabilities. That side stays reported as
unavailable rather than guessed, and the transition is marked incomplete so
nobody reads a half-answer as a whole one.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional, Tuple

from inventory.ledger import quantize_money

from .facts import workspace_order_facts
from .periods import registration_history
from .recognition import INVOICE, PAYMENTS, order_recognition


ZERO = Decimal('0.0000')

DEBIT = 'debit'
CREDIT = 'credit'
NONE = 'none'

#: How each basis brings output tax to account. Hybrid follows the invoice
#: rule, so a move between the two needs no output-side adjustment at all —
#: which falls out of this mapping rather than being special-cased.
OUTPUT_RULE = {PAYMENTS: PAYMENTS, INVOICE: INVOICE, 'hybrid': INVOICE}


@dataclass(frozen=True)
class DebtorPortion:
    """One order's supplies made but not yet accounted for at a date."""

    order_id: int
    currency_code: str
    gross: Decimal
    tax: Decimal


# A transition has to state both bases, the date, the direction, the working,
# and both sides of the adjustment; there is no grouping of those that is not
# arbitrary.
@dataclass(frozen=True)
class BasisTransition:  # pylint: disable=too-many-instance-attributes
    """The one-off adjustment a change of accounting basis requires."""

    change_date: date
    previous_basis: str
    new_basis: str
    direction: str
    debtors: Tuple[DebtorPortion, ...] = ()
    #: Keyed by currency, never totalled across them: there is no exchange rate.
    adjustment_tax: dict = field(default_factory=dict)
    adjustment_gross: dict = field(default_factory=dict)
    #: Always None. See the module docstring: no supplier payment date exists.
    creditors_gross: Optional[Decimal] = None
    creditors_tax: Optional[Decimal] = None

    @property
    def complete(self):
        """Whether both sides of the adjustment could be computed."""
        return self.creditors_tax is not None

    @property
    def required(self):
        """Whether this change of basis needs an adjustment at all."""
        return self.direction != NONE and any(self.adjustment_tax.values())


def basis_transitions(workspace, history=None):
    """Return the adjustment every recorded change of basis requires."""
    rows = registration_history(workspace) if history is None else history
    transitions = []
    previous = None
    for row in rows:
        if not row.registered:
            previous = None
            continue
        if previous is not None and previous.basis != row.basis:
            transitions.append(
                basis_transition(workspace, previous.basis, row.basis, row.effective_from),
            )
        previous = row
    return transitions


def basis_transition(workspace, previous_basis, new_basis, change_date):
    """Return the adjustment moving from one basis to another on a date.

    The direction follows from which rule each basis uses for output tax.
    Moving onto the invoice rule brings outstanding debtors in as a debit
    adjustment; moving off it takes them back out as a credit. A move between
    two bases that share the rule needs nothing, and says so.
    """
    previous_rule = OUTPUT_RULE[previous_basis]
    new_rule = OUTPUT_RULE[new_basis]
    if previous_rule == new_rule:
        return BasisTransition(
            change_date=change_date,
            previous_basis=previous_basis,
            new_basis=new_basis,
            direction=NONE,
        )
    debtors = outstanding_debtors(workspace, change_date)
    direction = DEBIT if new_rule == INVOICE else CREDIT
    return BasisTransition(
        change_date=change_date,
        previous_basis=previous_basis,
        new_basis=new_basis,
        direction=direction,
        debtors=debtors,
        adjustment_tax=_by_currency(debtors, 'tax'),
        adjustment_gross=_by_currency(debtors, 'gross'),
    )


def outstanding_debtors(workspace, as_at):
    """Return supplies made on or before a date that no payment has reached.

    Both sides are the same function with the same facts and the same cutoff,
    differing only in the basis. Their difference is exactly what the invoice
    rule would have accounted for and the payments rule would not, which is the
    definition of the debtors an adjustment is due on.
    """
    portions = []
    for facts in workspace_order_facts(workspace, end=as_at):
        invoiced = order_recognition(facts, INVOICE, as_at=as_at)
        received = order_recognition(facts, PAYMENTS, as_at=as_at)
        gross = quantize_money(invoiced.recognised_gross - received.recognised_gross)
        if gross <= 0:
            continue
        tax = quantize_money(_tax_of(invoiced, as_at) - _tax_of(received, as_at))
        portions.append(DebtorPortion(
            order_id=facts.order_id,
            currency_code=facts.currency_code,
            gross=gross,
            tax=max(tax, ZERO),
        ))
    return tuple(portions)


def _tax_of(recognition, as_at):
    """Total the GST one basis had brought to account by a date."""
    supplied = sum(
        (event.tax for event in recognition.supply_events if event.supply_date <= as_at),
        ZERO,
    )
    credited = sum(
        (event.tax for event in recognition.credit_events if event.supply_date <= as_at),
        ZERO,
    )
    return supplied - credited


def _by_currency(debtors, attribute):
    """Total one measure per currency, because there is no rate to combine them."""
    totals = {}
    for portion in debtors:
        current = totals.get(portion.currency_code, ZERO)
        totals[portion.currency_code] = current + getattr(portion, attribute)
    return {code: quantize_money(value) for code, value in totals.items()}
