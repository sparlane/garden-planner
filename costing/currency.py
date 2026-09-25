"""Keep cost in the currency it was recorded in until a rate says otherwise.

A cost layer snapshots the currency of the lot it drew from, a lot takes it
from the receipt that brought it in, and a supplier may invoice in anything, so
a batch fed from a USD lot and a EUR lot holds two totals and no single one.
Nothing here converts one into the other: task 121 owns the source, the
direction and the effective date a reproducible conversion needs, and a rate
guessed at in a reader would produce a figure nobody can reproduce.

These four helpers are how every cost reader says the same thing about it: add
within a currency, never across two; state one figure only when there is one
currency to state it in; say which absence a blank figure is; and list the
sides otherwise, which is also what a converted total would later be summed
from.
"""

from decimal import Decimal

from inventory.ledger import quantize_money


def held_by_currency(rows):
    """Add layers up within each currency, and never across them.

    Returns the amount held per currency code and whether any layer had no
    amount at all, which stays a separate question: an unknown cost is a
    missing price, not a missing rate.
    """
    held = {}
    unknown = False
    for row in rows:
        if row.amount is None:
            unknown = True
            continue
        held[row.currency_code] = held.get(row.currency_code, Decimal('0')) + row.amount
    return held, unknown


def stated_currency(codes, fallback):
    """Return the one currency these amounts are in, or None when they mix.

    A total of nothing is stated in the workspace's own currency: there is no
    amount in it to contradict that, and saying so keeps a batch that has not
    drawn on anything yet reading exactly as it did before.
    """
    if len(codes) > 1:
        return None
    return codes[0] if codes else fallback


def cost_blocked(codes, currency, unknown, target):
    """Name what stops these amounts reaching one figure stated in `target`.

    Three different absences read as the same blank on a screen, so each says
    which it is. A missing rate is not a missing price: `mixed_currency` is
    cost recorded in two currencies with nothing to combine them, and
    `foreign_currency` is a stateable cost in one currency that still cannot be
    set against a figure in another — a plant's pending pot and media shares,
    or an order's revenue, both of which are in the currency asked for here.
    `unknown_cost` is the ordinary unpriced input. None of them is a smaller
    true cost, which is why the figure goes null rather than dropping the part
    it cannot state.
    """
    if len(codes) > 1:
        return 'mixed_currency'
    if codes and currency != target:
        return 'foreign_currency'
    return 'unknown_cost' if unknown else None


def currency_amounts(held):
    """Render each currency's own total, in code order, for a reader to list."""
    return [
        {'currency_code': code, 'amount': f'{quantize_money(held[code]):f}'}
        for code in sorted(held)
    ]
