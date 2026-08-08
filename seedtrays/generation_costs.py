"""Trace one generation's media cost back to the seedlings it raised.

This is a derivation, not a subledger. Task 43 owns per-plant costing; what a
generation supplies is the identity that makes it possible — which fill of which
tray a cell was serving when the media went in — and this module reports the
allocation that identity implies so the boundary can be checked before the
subledger is built on top of it.

Three rules keep the numbers honest:

- Media is split across a line's cells by ``weight * cell_volume_ml``, which is
  the basis ``applications.usage`` already used to calculate the quantity.
  Dividing any other way would report a number the application never used.
- A cell's cost is shared equally among the plants observed in it, so a cell
  that produced three seedlings from one multigerm cluster spreads one cell's
  worth of media across three plants without changing any count.
- A lot with no recorded unit cost reports an unknown cost rather than zero. A
  zero would quietly understate every total built on it.
"""

# pylint: disable=duplicate-code

from decimal import Decimal

from inventory.models import COST_DECIMAL_PLACES

from .generations import applied_media, cell_shares, generation_cells
from .models import SeedTrayGeneration, SeedTrayGenerationResidual


COST_QUANTUM = Decimal(1).scaleb(-COST_DECIMAL_PLACES)


def quantize_cost(value):
    """Return a money amount at the precision unit costs are recorded in."""
    return None if value is None else Decimal(value).quantize(COST_QUANTUM)


def _plants_by_cell(generation):
    """Return the plants observed in each cell of this fill."""
    from plantings.models import SpecificPlant  # pylint: disable=import-outside-toplevel

    plants = SpecificPlant.objects.filter(
        cell_planting__seed_tray_planting__generation=generation,
    ).select_related('cell_planting').order_by('pk')
    grouped = {}
    for plant in plants:
        grouped.setdefault(plant.cell_planting.cell_id, []).append(plant)
    return grouped


def _residual_totals(generation):
    """Total what each disposition took back out of this fill, at cost."""
    totals = {disposition: Decimal('0') for disposition in (
        SeedTrayGenerationResidual.Disposition.WASTE,
        SeedTrayGenerationResidual.Disposition.RECLAIMED,
    )}
    unknown = False
    residuals = SeedTrayGenerationResidual.objects.filter(
        generation=generation,
        kind=SeedTrayGenerationResidual.Kind.MEDIA,
        movement__reversal__isnull=True,
    )
    for residual in residuals:
        if residual.unit_cost is None:
            unknown = True
            continue
        totals[residual.disposition] += residual.base_quantity * residual.unit_cost
    return totals, unknown


def _media_and_cells(generation):
    """Total this fill's media by lot and the cost each of its cells carries."""
    media = []
    cell_costs = {}
    unknown = False
    for line in applied_media(generation):
        unit_cost = line.lot.base_unit_cost
        mine = Decimal('0')
        for target, share in cell_shares(line):
            if target.seed_tray_generation_id != generation.pk:
                continue
            quantity = Decimal(line.applied_base_quantity) * share
            mine += quantity
            if unit_cost is not None:
                carried = cell_costs.get(target.seed_tray_cell_id, Decimal('0'))
                cell_costs[target.seed_tray_cell_id] = carried + quantity * unit_cost
        if not mine:
            continue
        if unit_cost is None:
            unknown = True
        media.append({
            'line': line.pk,
            'application': line.application_id,
            'lot': line.lot_id,
            'item': line.item_id,
            'base_quantity': mine,
            'base_unit': line.base_unit,
            'unit_cost': unit_cost,
            'cost': None if unit_cost is None else mine * unit_cost,
        })
    return media, cell_costs, unknown


def _allocate_cells(generation, cell_costs, closed):
    """Share each cell's media cost among the plants that came up in it.

    A cell that produced three seedlings from one multigerm cluster spreads one
    cell's worth of media across three plants without changing any count. A cell
    that produced nothing is provisional while the fill is open, because a
    seedling may still come up in it, and production loss once it is closed.
    """
    plants_by_cell = _plants_by_cell(generation)
    cells = []
    plant_costs = {}
    allocated = Decimal('0')
    unallocated = Decimal('0')
    for cell in generation_cells(generation):
        cost = cell_costs.get(cell.pk, Decimal('0'))
        plants = plants_by_cell.get(cell.pk, [])
        if not cost and not plants:
            continue
        per_plant = (cost / len(plants)) if plants else None
        for plant in plants:
            plant_costs[plant.pk] = plant_costs.get(plant.pk, Decimal('0')) + per_plant
        if plants:
            allocated += cost
        else:
            unallocated += cost
        cells.append({
            'cell': cell.pk,
            'x_position': cell.x_position,
            'y_position': cell.y_position,
            'cost': quantize_cost(cost),
            'plants': [plant.pk for plant in plants],
            'per_plant_cost': quantize_cost(per_plant),
            'provisional': not plants and not closed,
        })
    return cells, plant_costs, allocated, unallocated


def generation_cost_breakdown(generation):
    """Report which media supplied each seedling of one fill, and what did not.

    Media the operator recorded as discarded when the tray was cleaned is
    production loss, together with the cost of any cell that never produced a
    plant once the fill is closed.
    """
    media, cell_costs, unknown = _media_and_cells(generation)
    residuals, residual_unknown = _residual_totals(generation)
    closed = generation.status == SeedTrayGeneration.Status.CLOSED
    cells, plant_costs, allocated, unallocated = _allocate_cells(
        generation,
        cell_costs,
        closed,
    )
    waste = residuals[SeedTrayGenerationResidual.Disposition.WASTE]
    reclaimed = residuals[SeedTrayGenerationResidual.Disposition.RECLAIMED]
    applied = sum(
        (row['cost'] for row in media if row['cost'] is not None),
        Decimal('0'),
    )
    return {
        'generation': generation.pk,
        'code': generation.code,
        'status': generation.status,
        'currency_code': generation.workspace.currency_code,
        'unknown_cost': unknown or residual_unknown,
        'media': [
            {
                **row,
                'base_quantity': f'{row["base_quantity"]:.9f}',
                'unit_cost': quantize_cost(row['unit_cost']),
                'cost': quantize_cost(row['cost']),
            }
            for row in media
        ],
        'applied_cost': quantize_cost(applied),
        'recovered_cost': quantize_cost(reclaimed),
        'wasted_cost': quantize_cost(waste),
        'cells': cells,
        'plants': [
            {'plant': plant_id, 'cost': quantize_cost(cost)}
            for plant_id, cost in sorted(plant_costs.items())
        ],
        'allocated_cost': quantize_cost(allocated),
        'unallocated_cost': quantize_cost(unallocated),
        'production_loss': quantize_cost(waste + (unallocated if closed else Decimal('0'))),
    }
