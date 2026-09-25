"""Keep a cost of sale in the currency the stock was actually raised in.

A dispatched line has two halves recorded in two different units. The revenue
is the order's currency, because that is what the customer is billed in; the
cost is whatever the inputs behind the stock were bought in, which for a batch
raised wholly on a foreign lot is not the order's currency at all. Storing the
cost under the order's code labelled a euro figure as a dollar one, and the
profitability report then subtracted it from dollar revenue (task 157).

So a line carries both codes and nothing is relabelled. This is not a new
shape: `FulfillmentPackagingLine` has recorded its cost under its own lot's
currency since packaging was added, and `reporting.commerce._packaging_rows`
already reports it that way. What this module does is make the other four
dispatch paths say the same thing, and say it in one place.

Where a line's cost is assembled out of parts — a plant plus the passengers in
its pot, a plant plus the pot itself — the parts are added within one currency
and never across two: a line whose parts disagree records no cost of sale,
exactly as task 142 does for a plant raised in two currencies. No rate is
invented here and the dispatch is never refused; task 121 owns rates, and a
reader wanting one figure converts each part at the rate recorded against the
transaction it came from.
"""

from decimal import Decimal
from typing import NamedTuple, Optional

from costing.currency import held_by_currency, stated_currency
from costing.services import cohort_cost_breakdown, plant_cost_breakdown

from .calculations import money


class CostOfSale(NamedTuple):
    """What one dispatched thing cost, in the currency it cost it in.

    One absence, stated one way: `amount` is None exactly when the cost cannot
    be stated, and `currency_code` is blank exactly then. An amount nobody can
    state is not money in any currency, so naming one for it would be the
    relabelling this module exists to stop, and the two columns behind this
    carry a check constraint saying the same thing.

    A figure that is missing one of its inputs is such an absence. A plant fed
    one priced input and one unpriced one has no cost of sale — task 138's
    reason, which is also task 142's: a number with a part missing is not a
    smaller true cost, and recording it would have the profitability report
    subtract it from revenue as though it were final.
    """

    amount: Optional[Decimal]
    currency_code: str
    provisional: bool = False


#: What a thing costs when nothing about its cost can be stated. Naming it is
#: worth one line, because five readings return it and each returns it for a
#: different reason.
UNSTATEABLE = CostOfSale(None, '')


def combine_costs(parts, fallback):
    """Add the parts of one line's cost together, within one currency only.

    `fallback` is the currency a cost of nothing is stated in — the order's,
    because a line that drew on nothing priced contradicts nobody. A part with
    no amount, or two parts bought in different currencies, leave the whole
    line unstateable: a total missing one of its halves is not a smaller true
    cost, and there is no rate here to make the two halves one.
    """
    parts = list(parts)
    held, missing = held_by_currency(parts)
    currency = stated_currency(sorted(held), fallback)
    provisional = any(part.provisional for part in parts)
    if missing or currency is None:
        return CostOfSale(None, '', provisional)
    return CostOfSale(money(held.get(currency, Decimal('0'))), currency, provisional)


def plant_cost_of_sale(plant):
    """Return what this plant has cost, or nothing when that cannot be stated.

    A plant raised on inputs bought in two currencies has no committed value to
    read: no exchange rate exists to make one, and `costing.currency` says why
    inventing one here is not an option. A plant one of whose inputs has no
    recorded price has no whole figure either. Both record no cost of sale, for
    the reason task 138 gives — a number with a part missing is not a smaller
    true cost. A plant raised wholly in one foreign currency does have a value,
    and keeps it under that currency rather than the order's.
    """
    breakdown = plant_cost_breakdown(plant)
    value = breakdown['provisional_value'] or breakdown['final_value']
    if value is None or breakdown['unknown_cost']:
        return CostOfSale(None, '', breakdown['provisional'])
    return CostOfSale(Decimal(value), breakdown['currency_code'], breakdown['provisional'])


def cohort_draw_cost(cohort, quantity):
    """Return what a counted draw on one cohort costs, and how sure that is.

    An anonymous block divides its cost evenly per unit, so a draw on it is
    worth its share and nothing more exact exists to charge it with. A block
    whose inputs have no recorded price yields no cost rather than a zero,
    exactly as an unpriced lot does — a part-priced one included, because a
    share of an incomplete total is not what the plants cost. A block bought in
    two currencies has no unit value either, so it arrives here as nothing for
    the same reason and is charged out the same way.
    """
    breakdown = cohort_cost_breakdown(cohort)
    unit_value = breakdown['unit_value']
    if unit_value is None or breakdown['unknown_cost']:
        return CostOfSale(None, '', breakdown['provisional'])
    return CostOfSale(
        money(Decimal(unit_value) * quantity), breakdown['currency_code'],
        breakdown['provisional'],
    )


def lot_draw_cost(lot, quantity):
    """Return what a counted draw on one stock lot costs, in the lot's currency.

    A second delivery of the same item is a different lot and cost something
    else, possibly in a different currency: the price the pots in this box were
    bought at is the one on this lot.

    An unpriced lot is not provisional. Provisional means a production cost
    still waiting on its batch to be finalized, and a lot has no batch — it has
    a price nobody typed, which is what a null amount already says. The
    dispatch path marked such a line provisional as well; nothing read it as
    anything but a warning, and the order margin beside it never did.
    """
    if lot.base_unit_cost is None:
        return UNSTATEABLE
    return CostOfSale(money(Decimal(quantity) * lot.base_unit_cost), lot.currency_code)


def unit_cost_of_sale(unit):
    """Return what one serialized inventory unit cost, in its own currency."""
    if unit.acquisition_cost is None:
        return UNSTATEABLE
    return CostOfSale(unit.acquisition_cost, unit.currency_code)
