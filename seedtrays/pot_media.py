"""Media held by a pot fill or taken by its plants, separate from pot cost."""

from decimal import Decimal
from fractions import Fraction

from applications.models import InputApplication, InputApplicationLine
from inventory.ledger import MONEY_QUANTUM, QUANTITY_QUANTUM, quantize_money, quantize_quantity

from .generation_costs import quantize_cost
from .pot_shares import counted_parts


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


def pot_fill_cost_breakdown(fill):
    """Report departed, held, discarded and recovered media at fill level.

    Unknown acquisition costs remain unknown. The media ledger and these
    residuals are the source of the report; the pot's acquisition cost never
    changes when it is filled or cleaned.
    """
    media = pot_fill_contents(fill)
    departures = [row for row in pot_fill_shares(fill) if row['departed_at'] is not None]
    unknown_allocation = any(row['share'] is None for row in departures)
    fraction = sum((row['share'] for row in departures if row['share'] is not None), Fraction(0))
    unknown = any(row['unit_cost'] is None for row in media)
    applied = sum((row['base_quantity'] * (row['unit_cost'] or 0) for row in media), Decimal('0'))
    departed = quantize_cost(applied * fraction.numerator / fraction.denominator)
    if fill.stock_lot_id:
        balance = counted_fill_balance(fill)
        applied = sum((row['applied_cost'] or 0 for row in balance), Decimal('0'))
        departed = sum((row['departed_cost'] or 0 for row in balance), Decimal('0'))
    residuals = {'waste': Decimal('0'), 'reclaimed': Decimal('0')}
    for residual in fill.residuals.filter(kind='media', pot_correction__isnull=True):
        unknown = unknown or residual.unit_cost is None
        residuals[residual.disposition] += residual.base_quantity * (residual.unit_cost or 0)
    if fill.stock_lot_id:
        residuals = {key: quantize_cost(value) for key, value in residuals.items()}
    amounts = {
        'applied_cost': applied,
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
    return {
        'fill': fill.pk, 'container_count': fill.container_count,
        'currency_code': fill.workspace.currency_code, 'unknown_cost': unknown,
        'unknown_allocation': unknown_allocation,
        **{key: None if unknown or (unknown_allocation and key in ('departed_cost', 'held_cost'))
           else quantize_cost(value) for key, value in amounts.items()},
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
