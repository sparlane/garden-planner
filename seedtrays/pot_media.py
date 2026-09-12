"""Media held by a pot fill or taken by its plants, separate from pot cost."""

from decimal import Decimal
from fractions import Fraction

from applications.models import InputApplication, InputApplicationLine
from inventory.ledger import quantize_quantity

from .generation_costs import quantize_cost


def pot_fill_shares(fill):
    """Return exact fixed shares, distinguishing held plants from departures.

    Counted pots always use the original pot count, including unplanted pots.
    A missing numbered denominator means participation is still open or the
    fill has legacy departures; never infer it from the remaining plants.
    This reports allocation bases only; it does not post plant cost layers.
    """
    fill.refresh_from_db(fields=['plant_share_count', 'container_count', 'stock_lot'])
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
    residuals = {'waste': Decimal('0'), 'reclaimed': Decimal('0')}
    for residual in fill.residuals.filter(kind='media', pot_correction__isnull=True):
        unknown = unknown or residual.unit_cost is None
        residuals[residual.disposition] += residual.base_quantity * (residual.unit_cost or 0)
    amounts = {
        'applied_cost': applied,
        'departed_cost': departed,
        'held_cost': applied - departed - sum(residuals.values()),
        'production_loss': residuals['waste'],
        'recovered_cost': residuals['reclaimed'],
    }
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
