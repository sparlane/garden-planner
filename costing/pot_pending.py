"""Price each planted pot of a fill as it stands: its plants and one container.

This composes `plant_cost_breakdown` per held plant rather than re-deriving the
media and pot shares, so a pot can never disagree with its own plants. It is
not the fill's `held_cost`: that carries media in unplanted pots too, which no
dispatch takes with it and a clean will waste or reclaim instead.
"""

from decimal import Decimal

from health.availability import is_quarantined
from inventory.ledger import quantize_money
from plantings.lifecycle import SELLABLE_STATES, lifecycle_summaries

from .services import plant_cost_breakdown


def _add(totals, currency, amount):
    totals[currency] = totals.get(currency, Decimal('0')) + Decimal(amount)


def _summary(totals, unknown):
    """Report per currency, and a single total only when nothing is combined."""
    rows = [{'currency_code': currency, 'amount': None if unknown else f'{quantize_money(amount):f}'}
            for currency, amount in sorted(totals.items())]
    single = not unknown and len(rows) == 1
    return {'totals': rows, 'unknown_cost': unknown, 'mixed_currency': len(rows) > 1,
            'currency_code': rows[0]['currency_code'] if single else None,
            'total': rows[0]['amount'] if single else None}


def _plant(placement, states):
    """One occupant's committed layers per currency and its task-144 pending shares."""
    plant = placement.specific_plant
    breakdown = plant_cost_breakdown(plant)
    committed = {}
    for layer in breakdown['layers']:
        if layer['amount'] is not None:
            _add(committed, layer['currency_code'], layer['amount'])
    blocked = None
    if states[plant.pk].state not in SELLABLE_STATES:
        blocked = 'not_sellable'
    if is_quarantined(plant):
        blocked = 'quarantined'
    return {
        'plant': plant.pk, 'batch': plant.batch_id,
        'committed': _summary(committed, breakdown['unknown_cost']),
        'pending': breakdown['pending'],
        'dispatch_blocked': blocked,
    }, committed, breakdown


def _container(placement):
    if placement.container_unit_id:
        unit = placement.container_unit
        return unit.pk, unit.acquisition_cost, unit.currency_code
    lot = placement.container_fill.stock_lot
    return None, lot.base_unit_cost, lot.currency_code


def _pot(group, states):
    """Held plants plus the container once, split across them as dispatch splits it."""
    unit, cost, currency = _container(group[0])
    totals = {} if cost is None else {currency: quantize_money(cost)}
    unknown = cost is None
    plants = []
    for placement in group:
        row, committed, breakdown = _plant(placement, states)
        media = breakdown['pending'][0]
        unknown = unknown or breakdown['unknown_cost'] or media['amount'] is None
        for key, amount in committed.items():
            _add(totals, key, amount)
        if media['amount'] is not None:
            _add(totals, media['currency_code'], media['amount'])
        plants.append(row)
    return {
        'container_unit': unit, 'placement': None if unit else group[0].pk,
        'container_cost': {'amount': None if cost is None else f'{quantize_money(cost):f}',
                           'currency_code': currency, 'unknown_cost': cost is None},
        'plants': plants,
        'dispatchable': not any(row['dispatch_blocked'] for row in plants),
        **_summary(totals, unknown),
    }, totals, unknown


def pot_fill_pending_cost(fill):
    """Report every planted pot of a fill and their total, if dispatched today.

    Only held placements count, with one container per physical pot: a
    numbered fill is one pot however many plants share it, and a counted fill
    is a bench of one-plant pots. Grouping matches `dispatch_selected_pots`,
    so each pot's total equals the cost of sale its plants would record when
    dispatched together with it. Unknown anywhere leaves that total unknown,
    and currencies are reported side by side, never added together.
    """
    placements = list(fill.plant_locations.filter(ended__isnull=True).select_related(
        'specific_plant__workspace', 'container_unit', 'container_fill__stock_lot',
    ).order_by('specific_plant_id'))
    states = lifecycle_summaries([row.specific_plant_id for row in placements])
    groups = {}
    for row in placements:
        key = ('unit', row.container_unit_id) if row.container_unit_id else ('placement', row.pk)
        groups.setdefault(key, []).append(row)
    pots = []
    totals = {}
    unknown = False
    for group in groups.values():
        pot, pot_totals, pot_unknown = _pot(group, states)
        pots.append(pot)
        unknown = unknown or pot_unknown
        for currency, amount in pot_totals.items():
            _add(totals, currency, amount)
    return {'pots': pots, 'pot_count': len(pots), **_summary(totals, unknown)}
