"""Media held by a pot fill or taken by its plants, separate from pot cost."""

from decimal import Decimal
from fractions import Fraction

from applications.models import InputApplication, InputApplicationLine
from costing.currency import stated_currency
from inventory.ledger import MONEY_QUANTUM, QUANTITY_QUANTUM, quantize_money, quantize_quantity

from .generation_costs import quantize_cost
from .pot_shares import counted_parts


#: The two figures a departure whose share was never recorded leaves
#: unstateable. `applied_cost` is not one of them: what went into the fill is
#: known whether or not anyone can say who took it away again.
UNALLOCATABLE_BUCKETS = ('departed_cost', 'held_cost')

#: A currency nothing was disposed of in, so that a fill fed from a lot it has
#: no residual for still reports that lot's held cost.
NO_RESIDUALS = {'waste': Decimal('0'), 'reclaimed': Decimal('0')}


def pot_fill_shares(fill):
    """Return exact fixed shares, distinguishing held plants from departures.

    Counted pots always use the original pot count, including unplanted pots.
    A missing numbered denominator means participation is still open or the
    fill has legacy departures; never infer it from the remaining plants.
    This reports allocation bases only; it does not post plant cost layers.
    """
    fill.refresh_from_db(fields=['plant_share_count', 'container_count', 'stock_lot', 'status'])
    count = fill.container_count if fill.stock_lot_id else fill.plant_share_count
    share = Fraction(1, count) if count else None
    return [
        {'placement': placement.pk, 'plant': placement.specific_plant_id,
         'share': share, 'departed_at': placement.ended}
        for placement in fill.plant_locations.order_by('pk')
    ]


def numbered_fill_shares(fill):
    """Retain the original numbered-fill report entry point for existing callers."""
    return pot_fill_shares(fill)


def pot_fill_contents(fill):
    """Total posted media per lot; a reversed application leaves nothing behind.

    Each pot-media line names exactly one whole fill. Application waste was
    never put into a pot, so only the applied quantity belongs to the fill.
    """
    rows = {}
    lines = InputApplicationLine.objects.filter(
        application__status=InputApplication.Status.POSTED,
        targets__container_fill=fill,
    ).select_related('application', 'lot__item').order_by('pk')
    for line in lines:
        row = rows.setdefault(line.lot_id, {
            'lot': line.lot, 'base_unit': line.base_unit,
            'base_quantity': Decimal('0'), 'unit_cost': line.lot.base_unit_cost,
            'latest_application': line.application.applied_at,
        })
        row['base_quantity'] += line.applied_base_quantity
        row['latest_application'] = max(row['latest_application'], line.application.applied_at)
    return [{**row, 'base_quantity': quantize_quantity(row['base_quantity'])} for row in rows.values()]


def counted_fill_balance(fill):
    """Subtract posted departure shares per line before combining media lots.

    Exact fractions describe participation, but stock movements use the same
    recorded quantity precision as plant cost layers. Reserve each line's
    rounding in the original participant order, including still-held plants.
    """
    participants = list(fill.plant_locations.order_by('pk'))
    departed = [index for index, row in enumerate(participants) if row.ended is not None]
    rows = {}
    for line in InputApplicationLine.objects.filter(
        application__status=InputApplication.Status.POSTED, targets__container_fill=fill,
    ).select_related('application', 'lot__item').order_by('pk'):
        quantity = line.applied_base_quantity
        cost = None if line.lot.base_unit_cost is None else quantize_money(quantity * line.lot.base_unit_cost)
        quantities = counted_parts(quantity, fill.container_count, len(participants), QUANTITY_QUANTUM)
        costs = counted_parts(cost, fill.container_count, len(participants), MONEY_QUANTUM)
        row = rows.setdefault(line.lot_id, {
            'lot': line.lot, 'base_unit': line.base_unit,
            'base_quantity': Decimal('0'), 'applied_cost': Decimal('0'), 'departed_cost': Decimal('0'),
            'unit_cost': line.lot.base_unit_cost, 'latest_application': line.application.applied_at,
        })
        row['base_quantity'] += quantity - sum(quantities[index] for index in departed)
        row['latest_application'] = max(row['latest_application'], line.application.applied_at)
        if cost is None:
            row['applied_cost'] = row['departed_cost'] = None
        else:
            row['applied_cost'] += cost
            row['departed_cost'] += sum(costs[index] for index in departed)
    return list(rows.values())


def pot_fill_remaining_media(fill):
    """Return media to dispose of once no plants remain in the fill."""
    if fill.stock_lot_id:
        return [row for row in counted_fill_balance(fill) if row['base_quantity']]
    return [] if fill.plant_share_count else pot_fill_contents(fill)


def _applied_and_departed(fill, media, fraction):
    """Total applied and departed media per currency of the lot it came from.

    A counted fill reserves its per-pot rounding line by line in
    `counted_fill_balance`, and a line draws on exactly one lot, so each line's
    departures are already whole money in one currency. A numbered fill takes
    the departed share of what was applied, and takes it inside each currency:
    there is no rate that would let a euro share be worked out of a dollar
    total, and taking it of the mixture and then splitting the answer would
    invent one.
    """
    applied = {}
    departed = {}
    if fill.stock_lot_id:
        for row in counted_fill_balance(fill):
            code = row['lot'].currency_code
            applied[code] = applied.get(code, Decimal('0')) + (row['applied_cost'] or 0)
            departed[code] = departed.get(code, Decimal('0')) + (row['departed_cost'] or 0)
        return applied, departed
    for row in media:
        code = row['lot'].currency_code
        applied[code] = applied.get(code, Decimal('0')) + row['base_quantity'] * (row['unit_cost'] or 0)
    return applied, {
        code: quantize_cost(value * fraction.numerator / fraction.denominator)
        for code, value in applied.items()
    }


def _residuals_by_currency(fill):
    """Total what each disposition took back out, per currency of its lot.

    A residual copies the unit cost of the lot it came from, so it states the
    same currency the application that put the media in did, and discarding a
    euro litre is a euro loss however the rest of the fill was bought.
    """
    residuals = {}
    unknown = False
    rows = fill.residuals.filter(kind='media', pot_correction__isnull=True).select_related('lot')
    for residual in rows:
        unknown = unknown or residual.unit_cost is None
        totals = residuals.setdefault(residual.lot.currency_code, dict(NO_RESIDUALS))
        totals[residual.disposition] += residual.base_quantity * (residual.unit_cost or 0)
    if fill.stock_lot_id:
        residuals = {code: {key: quantize_cost(value) for key, value in totals.items()}
                     for code, totals in residuals.items()}
    return residuals, unknown


def _fill_dispositions(fill, applied, departed, residuals):
    """Split one currency's applied media into where that currency ended up.

    The four buckets and the rounding difference add back up to `applied_cost`
    within the currency, which is what lets a reader list the sides of a mixed
    fill without ever needing a rate between them.

    Exactly, as long as the applied cost itself fits the twelve places these
    figures are published at. A counted fill's is whole money already, because
    `counted_fill_balance` quantizes each line. A numbered fill's is a nine-place
    quantity times a twelve-place unit cost, so a lot priced to the last place
    can carry more than twelve and the published parts can then differ from the
    published whole by one unit in the last place — a property of rendering, not
    of the split, and the same one place a single-currency numbered fill has
    always been rounded at.
    """
    amounts = {
        'departed_cost': departed,
        'held_cost': applied - departed - sum(residuals.values()),
        'production_loss': residuals['waste'],
        'recovered_cost': residuals['reclaimed'],
        'rounding_difference': Decimal('0'),
    }
    # Stock recovery is valued at the lot rate; plant layers reserve monetary
    # rounding per pot. Show their difference explicitly after a counted clean,
    # never as imaginary held media or a change to an earlier plant's cost.
    if fill.stock_lot_id and fill.status == 'closed':
        amounts['rounding_difference'] = amounts['held_cost']
        amounts['held_cost'] = Decimal('0')
    return amounts


def _hidden(key, unknown, unknown_allocation, mixed):
    """Say whether one figure cannot be stated, whichever of the three reasons.

    An unpriced lot means no figure at all, a departure whose share nobody
    recorded means neither of the two figures that depend on it, and two
    currencies mean no single figure anywhere — and the per-currency rows are
    held to the first two rules as well, so a fill with an unpriced lot lists
    its currencies without amounts rather than amounts that treat the unpriced
    lot as free.
    """
    if unknown or mixed:
        return True
    return unknown_allocation and key in UNALLOCATABLE_BUCKETS


def _stated_cost(value, hidden):
    """Render one currency's own figure, or nothing where one is not stateable.

    Rendered here rather than left a `Decimal` for the caller because
    `container_fill_rest` only formats the top level of this report, and a
    `Decimal` reaching the JSON encoder from inside the list would come out a
    float — the one rendering a money column of twelve decimal places exists
    to avoid.
    """
    return None if hidden else f'{quantize_cost(value):f}'


def pot_fill_cost_breakdown(fill):
    """Report departed, held, discarded and recovered media at fill level.

    Unknown acquisition costs remain unknown. The media ledger and these
    residuals are the source of the report; the pot's acquisition cost never
    changes when it is filled or cleaned.

    `held_cost` is all media still in the fill, including the share in any
    unplanted counted pots, and excludes both the pots and what the plants
    cost to raise. It is not what the planted pots would cost to dispatch;
    `costing.pot_pending.pot_fill_pending_cost` reports that.

    Two currencies are kept apart the way `costing.services` keeps a batch's
    apart. A lot carries the currency of the receipt that brought it in, so a
    fill topped up from a lot bought abroad holds two totals and no single
    one: `currency_code` and every figure go null with `mixed_currency` saying
    why, and `currencies` lists each currency's complete set of figures for a
    reader to show side by side. A fill fed from one currency — every fill in
    most workspaces — reads exactly as it always has.
    """
    media = pot_fill_contents(fill)
    departures = [row for row in pot_fill_shares(fill) if row['departed_at'] is not None]
    unknown_allocation = any(row['share'] is None for row in departures)
    fraction = sum((row['share'] for row in departures if row['share'] is not None), Fraction(0))
    applied, departed = _applied_and_departed(fill, media, fraction)
    residuals, residual_unknown = _residuals_by_currency(fill)
    unknown = any(row['unit_cost'] is None for row in media) or residual_unknown
    codes = sorted(set(applied) | set(residuals))
    amounts = {
        code: _fill_dispositions(
            fill, applied.get(code, Decimal('0')), departed.get(code, Decimal('0')),
            residuals.get(code, NO_RESIDUALS),
        )
        for code in codes
    }
    currency = stated_currency(codes, fill.workspace.currency_code)
    empty = _fill_dispositions(fill, Decimal('0'), Decimal('0'), NO_RESIDUALS)
    figures = {'applied_cost': applied.get(currency, Decimal('0')), **amounts.get(currency, empty)}
    return {
        'fill': fill.pk, 'container_count': fill.container_count,
        'currency_code': currency, 'unknown_cost': unknown,
        'mixed_currency': len(codes) > 1,
        'unknown_allocation': unknown_allocation,
        'currencies': [
            {
                'currency_code': code,
                'amount': _stated_cost(applied.get(code, Decimal('0')), unknown),
                'totals': {
                    bucket: _stated_cost(value, _hidden(bucket, unknown, unknown_allocation, False))
                    for bucket, value in amounts[code].items()
                },
            }
            for code in codes
        ],
        **{key: None if _hidden(key, unknown, unknown_allocation, currency is None)
           else quantize_cost(value) for key, value in figures.items()},
    }


def pot_fill_media_departures(fill):
    """Return exact quantities per departed placement and posted media lot.

    Fractions preserve thirds without rounding away media. A legacy departure
    without a frozen denominator remains explicitly unallocated. Active plants
    claim nothing yet, and this report does not create crop cost layers.
    """
    media = pot_fill_contents(fill)
    return [
        {**departure, 'lot': row['lot'], 'base_unit': row['base_unit'],
         'base_quantity': None if departure['share'] is None else Fraction(row['base_quantity']) * departure['share'],
         'unit_cost': row['unit_cost']}
        for departure in pot_fill_shares(fill) if departure['departed_at'] is not None
        for row in media
    ]
