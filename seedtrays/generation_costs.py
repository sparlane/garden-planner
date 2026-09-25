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
- Media bought in two currencies is reported in both and added in neither, the
  way `costing.services` reports a batch. The rule reaches all the way down:
  a cell fed from a dollar lot and a euro one carries no single cost, and
  neither does a seedling raised in it.
"""

# pylint: disable=duplicate-code

from decimal import Decimal

from costing.currency import stated_currency
from plantings.lifecycle import observed_only
from plantings.models import SpecificPlant
from inventory.models import COST_DECIMAL_PLACES

from .generations import applied_media, cell_shares, generation_cells
from .models import SeedTrayGeneration, SeedTrayGenerationResidual


COST_QUANTUM = Decimal(1).scaleb(-COST_DECIMAL_PLACES)


def quantize_cost(value):
    """Return a money amount at the precision unit costs are recorded in."""
    return None if value is None else Decimal(value).quantize(COST_QUANTUM)


def _one_currency(held, fallback):
    """Return the currency these amounts are all in, and their total in it.

    Both go null together where there is more than one, because half an answer
    — a figure without its code, or a code over a figure it does not describe —
    is the relabelling this grouping exists to stop. Nothing held at all still
    names the workspace's own currency: there is no amount in it to contradict
    that, and it keeps an empty cell reading as it always has.
    """
    code = stated_currency(sorted(held), fallback)
    return code, None if code is None else held.get(code, Decimal('0'))


def _add(totals, code, amount):
    """Add one amount into its own currency's running total, never another's."""
    totals[code] = totals.get(code, Decimal('0')) + amount


def _plants_by_cell(generation):
    """Return the plants observed in each cell of this fill.

    Observed is meant exactly: a seedling whose germination has been withdrawn
    never came up, so it takes no share of its cell and is not listed as having
    been supplied by anything. Its share goes back to the seedlings that did
    come up, and a cell whose only seedlings were withdrawn has raised nothing
    at all — provisional while the fill is open, production loss once it is
    closed, the same as a cell nothing was ever recorded in.
    """

    plants = observed_only(
        SpecificPlant.objects.filter(
            cell_planting__seed_tray_planting__generation=generation,
        )
    ).select_related('cell_planting').order_by('pk')
    grouped = {}
    for plant in plants:
        grouped.setdefault(plant.cell_planting.cell_id, []).append(plant)
    return grouped


def _residual_totals(generation):
    """Total what each disposition took back out of this fill, at cost.

    Per currency of the lot the residual came from: it copies that lot's unit
    cost, so it is a figure in that lot's currency, and tipping out a euro
    litre is a euro loss whatever the rest of the tray was bought in.
    """
    totals = {}
    unknown = False
    residuals = SeedTrayGenerationResidual.objects.filter(
        generation=generation,
        kind=SeedTrayGenerationResidual.Kind.MEDIA,
        movement__reversal__isnull=True,
    ).select_related('lot')
    for residual in residuals:
        if residual.unit_cost is None:
            unknown = True
            continue
        by_disposition = totals.setdefault(residual.lot.currency_code, {
            SeedTrayGenerationResidual.Disposition.WASTE: Decimal('0'),
            SeedTrayGenerationResidual.Disposition.RECLAIMED: Decimal('0'),
        })
        by_disposition[residual.disposition] += residual.base_quantity * residual.unit_cost
    return totals, unknown


def _media_and_cells(generation):
    """Total this fill's media by lot and the cost each of its cells carries.

    A cell's cost is kept per currency rather than as one running figure: two
    lines may reach the same cell from lots bought in different currencies,
    and adding them there would hide the mixture one level below where any
    reader could see it.
    """
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
                carried = cell_costs.setdefault(target.seed_tray_cell_id, {})
                _add(carried, line.lot.currency_code, quantity * unit_cost)
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
            'currency_code': line.lot.currency_code,
            'cost': None if unit_cost is None else mine * unit_cost,
        })
    return media, cell_costs, unknown


def _cell_row(cell, costs, plants, closed, fallback):
    """Report one cell's own media cost and what each plant in it took.

    A cell fed from two currencies states neither: 0.08 of one and 0.04 of the
    other is not 0.12 of anything, and a per-plant figure divided out of the
    mixture would be money in neither.
    """
    code, cost = _one_currency(costs, fallback)
    return {
        'cell': cell.pk,
        'x_position': cell.x_position,
        'y_position': cell.y_position,
        'cost': quantize_cost(cost),
        'currency_code': code,
        'mixed_currency': len(costs) > 1,
        'plants': [plant.pk for plant in plants],
        'per_plant_cost': None if not plants or cost is None else quantize_cost(cost / len(plants)),
        'provisional': not plants and not closed,
    }


def _share_among(plant_costs, plants, costs):
    """Give every plant in one cell its equal share of each currency in it."""
    for plant in plants:
        held = plant_costs.setdefault(plant.pk, {})
        for code, amount in costs.items():
            _add(held, code, amount / len(plants))


def _allocate_cells(generation, cell_costs, closed, fallback):
    """Share each cell's media cost among the plants that came up in it.

    A cell that produced three seedlings from one multigerm cluster spreads one
    cell's worth of media across three plants without changing any count. A cell
    that produced nothing is provisional while the fill is open, because a
    seedling may still come up in it, and production loss once it is closed.

    Each currency is shared out on its own, so a plant in a mixed cell holds a
    share of each and the cell states no single cost. `shares` keeps what
    reached a seedling apart from what did not, per currency, for the report to
    total.
    """
    plants_by_cell = _plants_by_cell(generation)
    cells = []
    plant_costs = {}
    shares = {'allocated': {}, 'unallocated': {}}
    for cell in generation_cells(generation):
        costs = cell_costs.get(cell.pk, {})
        plants = plants_by_cell.get(cell.pk, [])
        if not any(costs.values()) and not plants:
            continue
        _share_among(plant_costs, plants, costs)
        for code, amount in costs.items():
            _add(shares['allocated' if plants else 'unallocated'], code, amount)
        cells.append(_cell_row(cell, costs, plants, closed, fallback))
    return cells, plant_costs, shares


def _plant_row(plant_id, held, fallback):
    """Report what one seedling's media cost, in the currency it was bought in.

    A seedling raised in a cell fed from two currencies states neither: its
    shares are listed in `currencies` and the row carries no figure, the same
    answer `costing.services` gives a plant whose own inputs mix.
    """
    code, cost = _one_currency(held, fallback)
    return {
        'plant': plant_id,
        'cost': quantize_cost(cost),
        'currency_code': code,
        'mixed_currency': len(held) > 1,
        'currencies': [{'currency_code': key, 'amount': f'{quantize_cost(amount):f}'}
                       for key, amount in sorted(held.items())],
    }


def _dispositions(residuals, shares, code, closed):
    """Split one currency's media into where that currency ended up.

    The cost of a cell that never produced a plant is loss only once the fill
    is closed: while it is open a seedling may still come up in it.
    """
    waste = residuals.get(SeedTrayGenerationResidual.Disposition.WASTE, Decimal('0'))
    unallocated = shares['unallocated'].get(code, Decimal('0'))
    return {
        'recovered_cost': residuals.get(
            SeedTrayGenerationResidual.Disposition.RECLAIMED, Decimal('0'),
        ),
        'wasted_cost': waste,
        'allocated_cost': shares['allocated'].get(code, Decimal('0')),
        'unallocated_cost': unallocated,
        'production_loss': waste + (unallocated if closed else Decimal('0')),
    }


def _currency_payload(media, residuals, shares, closed, fallback):
    """Report every figure that depends on which currencies this fill drew on.

    A lot carries the currency of the receipt that brought it in, so a tray
    topped up from a lot bought abroad holds two totals and no single one:
    `currency_code` and every derived figure go null with `mixed_currency`
    saying why, and `currencies` lists each currency's own complete set for a
    reader to show side by side — the shape `costing.services` gives a batch
    fed the same way. A fill fed from one currency reads exactly as it did.

    A lot with no recorded unit cost still counts towards which currencies the
    fill drew on, because it was bought in one whether or not anyone wrote down
    what it cost: leaving it out would label a tray holding euro media with the
    workspace's own code, which is the relabelling this is here to stop. It
    contributes nothing to any amount, so the figures understate by its cost —
    which is exactly what `unknown_cost` has always said about them, and
    `seedtrays.pot_media` counts an unpriced lot's currency the same way.
    """
    applied = {}
    codes = set(residuals) | set(shares['allocated']) | set(shares['unallocated'])
    for row in media:
        codes.add(row['currency_code'])
        if row['cost'] is not None:
            _add(applied, row['currency_code'], row['cost'])
    codes = sorted(codes)
    amounts = {
        code: _dispositions(residuals.get(code, {}), shares, code, closed)
        for code in codes
    }
    currency = stated_currency(codes, fallback)
    figures = {
        'applied_cost': applied.get(currency, Decimal('0')),
        **amounts.get(currency, _dispositions({}, shares, None, closed)),
    }
    return {
        'currency_code': currency,
        'mixed_currency': len(codes) > 1,
        'currencies': [
            {
                'currency_code': code,
                'amount': f'{quantize_cost(applied.get(code, Decimal("0"))):f}',
                'totals': {bucket: f'{quantize_cost(value):f}'
                           for bucket, value in amounts[code].items()},
            }
            for code in codes
        ],
        **{key: None if currency is None else quantize_cost(value)
           for key, value in figures.items()},
    }


def _media_rows(media):
    """Render the lines that put media in, each with its lot's own currency."""
    return [
        {
            **row,
            'base_quantity': f'{row["base_quantity"]:.9f}',
            'unit_cost': quantize_cost(row['unit_cost']),
            'cost': quantize_cost(row['cost']),
        }
        for row in media
    ]


def generation_cost_breakdown(generation):
    """Report which media supplied each seedling of one fill, and what did not.

    Media the operator recorded as discarded when the tray was cleaned is
    production loss, together with the cost of any cell that never produced a
    plant once the fill is closed. `_currency_payload` owns everything that
    depends on which currencies the fill drew on, including whether any figure
    can be stated at all.
    """
    media, cell_costs, unknown = _media_and_cells(generation)
    residuals, residual_unknown = _residual_totals(generation)
    closed = generation.status == SeedTrayGeneration.Status.CLOSED
    fallback = generation.workspace.currency_code
    cells, plant_costs, shares = _allocate_cells(generation, cell_costs, closed, fallback)
    return {
        'generation': generation.pk,
        'code': generation.code,
        'status': generation.status,
        'unknown_cost': unknown or residual_unknown,
        'media': _media_rows(media),
        'cells': cells,
        'plants': [
            _plant_row(plant_id, held, fallback)
            for plant_id, held in sorted(plant_costs.items())
        ],
        **_currency_payload(media, residuals, shares, closed, fallback),
    }
