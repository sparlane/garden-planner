"""Read costs held by a plant without posting them before departure."""

from decimal import Decimal

from applications.models import InputApplication, InputApplicationLine
from inventory.ledger import distribute_exactly, quantize_money
from plantings.models import SpecificPlantLocation

from .sources import pot_media_line_parts


def _entry(kind, amount, currency, reason, *, unknown=False, unallocatable=False):  # pylint: disable=too-many-arguments
    return {
        'kind': kind, 'amount': None if unknown or unallocatable else f'{quantize_money(amount):f}',
        'currency_code': currency, 'unknown_cost': unknown,
        'not_yet_allocatable': unallocatable, 'reason': reason,
    }


def _media(placement, currency):
    fill = placement.container_fill
    participants = list(fill.plant_locations.order_by('pk'))
    count = fill.plant_share_count
    # The first departure freezes today's complete participation, without a write here.
    if count is None and not any(row.ended is not None for row in participants):
        count = len(participants)
    incomplete = (len(participants) > fill.container_count if fill.stock_lot_id
                  else count is None or count != len(participants))
    index = next(index for index, row in enumerate(participants) if row.pk == placement.pk)
    amount = Decimal('0')
    unknown = False
    for line in InputApplicationLine.objects.filter(
        application__status=InputApplication.Status.POSTED, targets__container_fill=fill,
    ).select_related('lot').order_by('pk'):
        unknown = unknown or line.lot.base_unit_cost is None or line.lot.currency_code != currency
        if not incomplete:
            _, parts = pot_media_line_parts(line, fill, len(participants), count)
            amount += parts[index] or Decimal('0')
    reason = 'Media posts when this plant leaves the fill.'
    if incomplete:
        reason = 'Fill participation is incomplete; its media share cannot yet be allocated.'
    elif unknown:
        reason = 'Media has an unknown cost or a different currency.'
    return _entry('held_media', amount, currency, reason, unknown=unknown, unallocatable=incomplete)


def plant_pending_cost(plant):
    """Project media and the physical pot share for a departure right now.

    Shared-pot amounts assume every current occupant leaves together, exactly
    as the explicit with-pot dispatch requires. Historical participants still
    reserve their original media slots, but no longer share the physical pot.
    """
    currency = plant.workspace.currency_code
    placement = SpecificPlantLocation.objects.filter(specific_plant=plant, ended__isnull=True).select_related(
        'container_fill__stock_lot', 'container_unit',
    ).first()
    reason = 'No current pot fill is recorded.'
    if placement and placement.location_type == 'seed_tray_cell':
        reason = 'Tray media is already committed; the tray is lent, not consumed.'
    entries = [_entry(kind, Decimal('0'), currency, reason) for kind in ('held_media', 'pot')]
    occupants = []
    if placement and placement.container_fill_id and not placement.container_fill.tray_id:
        entries[0] = _media(placement, currency)
        if placement.container_unit_id:
            occupants = list(SpecificPlantLocation.objects.filter(
                container_unit_id=placement.container_unit_id, ended__isnull=True,
            ).order_by('specific_plant_id').values_list('specific_plant_id', flat=True))
            cost = placement.container_unit.acquisition_cost
            pot_currency = placement.container_unit.currency_code
        else:
            occupants = [plant.pk]
            cost = placement.container_fill.stock_lot.base_unit_cost
            pot_currency = placement.container_fill.stock_lot.currency_code
        unknown = cost is None or pot_currency != currency
        amount = Decimal('0') if unknown else distribute_exactly(
            quantize_money(cost), [Decimal('1')] * len(occupants),
        )[occupants.index(plant.pk)]
        entries[1] = _entry('pot', amount, pot_currency,
                            'The pot posts only if dispatched with the plant.' if not unknown
                            else 'The pot has an unknown cost or a different currency.', unknown=unknown)
    return {'pending': entries, 'pot_requires_plants': occupants,
            'with_pot_available': bool(occupants) and not any(row['not_yet_allocatable'] for row in entries)}


def plant_sale_totals(value, unknown, projection):
    """Add only known pending costs to the committed value supplied by the reader."""
    media, pot = projection['pending']
    without = None if unknown or media['amount'] is None else value + Decimal(media['amount'])
    with_pot = None if without is None or pot['amount'] is None or not projection['with_pot_available'] else without + Decimal(pot['amount'])
    return {'sale_without_pot': None if without is None else f'{quantize_money(without):f}',
            'sale_with_pot': None if with_pot is None else f'{quantize_money(with_pot):f}'}
