"""Find every posted input one batch drew on, and where each one reached.

This is the reading half of the subledger. It answers one question per source:
of the quantity this input consumed, how much belongs to this batch, and which
cells, plants, or pools does that part land on? `costing.allocation` turns those
weights into exact amounts and `costing.services` posts them.

Only posted, unreversed facts are read. A reversed application put its stock
back, so it left nothing behind to cost, and a corrected sowing has a
replacement posting describing what was really drawn. Nothing here invents a
cost for history either: a lot with no recorded `base_unit_cost` yields a source
whose cost is None all the way through, and an unvalued batch stays unvalued.
"""

# pylint: disable=duplicate-code

from decimal import Decimal
from typing import NamedTuple

from applications.models import InputApplication, InputApplicationLine
from applications.usage import AREA_TARGETS, VOLUME_TARGETS
from garden.models import GardenSquare
from inventory.models import InventoryItem
from plantings.models import (
    GardenRowDirectSowPlanting,
    GardenSquareDirectSowPlanting,
    SeedTrayCellPlanting,
    SeedTrayPlanting,
    SpecificPlant,
    SpecificPlantLocation,
)
from plantings.sowing import current_sowing_consumption
from seedtrays.generations import cell_shares
from seedtrays.models import SeedTrayGenerationResidual

from .allocation import (
    area_plant_shares,
    cell_volume_shares,
    plant_shares,
    resolve_cells_to_plants,
    resolve_unidentified_to_cohorts,
    seed_shares,
    unattributable_share,
    whole_source_share,
)
from .models import CostAllocation


#: The sowing models a batch can own. Only a tray sowing can reach an individual
#: seedling; a direct sow produces a crop this subledger does not individualise.
SOWING_MODELS = (
    SeedTrayPlanting,
    GardenRowDirectSowPlanting,
    GardenSquareDirectSowPlanting,
)

Basis = CostAllocation.Basis
SourceType = CostAllocation.SourceType


class SourceInput(NamedTuple):
    """One posted input and the shares of it this batch is charged with.

    `base_quantity` is the part attributable to this batch, which is not always
    the whole movement: one lot of media can fill a tray whose cells are serving
    two crops, and each batch carries only its own cells.
    """

    source_type: str
    source: object
    movement: object
    base_quantity: Decimal
    base_unit: str
    unit_cost: object
    currency_code: str
    shares: tuple

    @property
    def amount(self):
        """Return this batch's part of the source cost, when it is known."""
        if self.unit_cost is None:
            return None
        return self.base_quantity * Decimal(self.unit_cost)


class Reach(NamedTuple):
    """How much of one line belongs to this batch, and where it lands."""

    fraction: Decimal
    shares: tuple


def batch_sowings(batch):
    """Return every sowing of any kind attached to one batch."""
    return [
        sowing
        for model in SOWING_MODELS
        for sowing in model.objects.filter(batch=batch).order_by('pk')
    ]


def batch_generations(batch):
    """Return the tray fills this batch was sown into."""
    return sorted(set(
        SeedTrayPlanting.objects
        .filter(batch=batch, generation__isnull=False)
        .values_list('generation_id', flat=True)
    ))


def plants_by_cell(batch):
    """Return this batch's observed plants, grouped by the cell they came up in.

    Every observed plant counts, including one that later failed. It held its
    share of the cell while it was alive; what became of that share is a question
    its lifecycle answers, not a reason to pretend it never grew.
    """
    grouped = {}
    rows = SpecificPlant.objects.filter(
        cell_planting__seed_tray_planting__batch=batch,
    ).values_list('pk', 'cell_planting__cell_id').order_by('pk')
    for plant_id, cell_id in rows:
        grouped.setdefault(cell_id, []).append(plant_id)
    return grouped


def cohort_outputs(batch):
    """Return anonymous quantities and promoted identities that share their cost."""
    from plantings.models import PlantCohort  # pylint: disable=import-outside-toplevel

    outputs = []
    cohorts = PlantCohort.objects.filter(batch=batch).prefetch_related('promoted_plants')
    for cohort in cohorts:
        if cohort.quantity:
            outputs.append(('cohort', cohort.pk, cohort.quantity))
        outputs.extend(
            ('plant', plant_id, 1)
            for plant_id in cohort.promoted_plants.values_list('pk', flat=True)
        )
    return outputs


def cell_batch_weights(generation_ids):
    """Return how much seed each batch put into each cell of these fills.

    A cell is normally serving one crop, but a fill can carry sowings from two
    batches, so its cost divides between them by the seed each one placed. A cell
    nobody sowed into belongs to no batch, and its cost stays with the fill
    rather than being handed to whichever crop happens to be nearby.
    """
    weights = {}
    rows = SeedTrayCellPlanting.objects.filter(
        seed_tray_planting__generation_id__in=generation_ids,
    ).values_list('cell_id', 'seed_tray_planting__batch_id', 'quantity')
    for cell_id, batch_id, quantity in rows:
        by_batch = weights.setdefault(cell_id, {})
        by_batch[batch_id] = by_batch.get(batch_id, Decimal('0')) + Decimal(quantity)
    return weights


def _cell_portion(cell_weights, cell_id, batch_id):
    """Return the fraction of one cell that belongs to one batch."""
    by_batch = cell_weights.get(cell_id) or {}
    total = sum(by_batch.values(), Decimal('0'))
    if total <= 0:
        return Decimal('0')
    return by_batch.get(batch_id, Decimal('0')) / total


def fill_seed_shares(generation_ids):
    """Return each batch's share of the seed sown into each fill.

    Used to divide a fill-level fact — a discarded remainder — between the crops
    that were using the tray. A fill serving one crop, which is the ordinary
    case, gives that crop all of it. `seedtrays.generation_costs` remains the
    exact view of a fill shared by two.
    """
    totals = {}
    rows = SeedTrayCellPlanting.objects.filter(
        seed_tray_planting__generation_id__in=generation_ids,
    ).values_list(
        'seed_tray_planting__generation_id',
        'seed_tray_planting__batch_id',
        'quantity',
    )
    for generation_id, batch_id, quantity in rows:
        by_batch = totals.setdefault(generation_id, {})
        by_batch[batch_id] = by_batch.get(batch_id, Decimal('0')) + Decimal(quantity)
    return totals


# --------------------------------------------------------------------------
# Seed
# --------------------------------------------------------------------------


def _seed_source(sowing):
    """Describe what one sowing drew from its packet and where it went."""
    posting = current_sowing_consumption(sowing)
    if posting is None:
        return None
    lot = posting.movement.lot
    quantity = Decimal(posting.movement.quantity)
    if isinstance(sowing, SeedTrayPlanting):
        shares = seed_shares(quantity, [
            (row.cell_id, sowing.generation_id, row.quantity)
            for row in sowing.cell_plantings.order_by('cell_id')
        ])
    else:
        shares = unattributable_share(Basis.SEEDS_SOWN)
    return SourceInput(
        source_type=SourceType.SOWING_POSTING,
        source=posting,
        movement=posting.movement,
        base_quantity=quantity,
        base_unit=lot.item.base_unit,
        unit_cost=lot.base_unit_cost,
        currency_code=lot.currency_code,
        shares=tuple(shares),
    )


def seed_sources(batch):
    """Return one source per sowing that still has a posted consumption."""
    sources = (_seed_source(sowing) for sowing in batch_sowings(batch))
    return [source for source in sources if source is not None]


# --------------------------------------------------------------------------
# Input applications
# --------------------------------------------------------------------------


def _posted_lines():
    """Return the base query every reaching-line lookup narrows."""
    return InputApplicationLine.objects.filter(
        application__status=InputApplication.Status.POSTED,
    )


def batch_application_lines(batch, generation_ids):
    """Return every posted line whose input could have reached this batch.

    Four ways in: the document names the batch, a line names it, a line names a
    plant the batch raised, or a line names a cell of a fill the batch was sown
    into. A line that reaches the batch only through a serialized unit is
    excluded — a tray asset is not a seedling's input, and task 39 values it.
    """
    reaching = set()
    for lookup in (
        {'application__batch': batch},
        {'targets__batch': batch},
        {'targets__specific_plant__batch': batch},
        {'targets__seed_tray_generation_id__in': generation_ids},
    ):
        reaching.update(_posted_lines().filter(**lookup).values_list('pk', flat=True))
    return InputApplicationLine.objects.filter(pk__in=sorted(reaching)).select_related(
        'item',
        'lot__item',
        'application',
    ).prefetch_related('targets').order_by('pk')


def _fill_removals(generation_ids):
    """Total what each clean took back out of each fill, by lot.

    Media applied to a tray and then discarded never reached a seedling, and
    media reclaimed into stock physically left the tray. Both reduce what the
    fill really consumed, so both come off the cell allocation. Only the
    discarded part becomes a loss: the reclaimed part is back on the shelf, and
    its `adjustment_gain` movement took its cost with it.
    """
    removals = {}
    residuals = SeedTrayGenerationResidual.objects.filter(
        generation_id__in=generation_ids,
        kind=SeedTrayGenerationResidual.Kind.MEDIA,
    ).select_related('lot__item').order_by('pk')
    for residual in residuals:
        if residual.movement_id is not None and hasattr(residual.movement, 'reversal'):
            continue
        row = removals.setdefault((residual.generation_id, residual.lot_id), {
            'removed': Decimal('0'),
            'discarded': [],
        })
        row['removed'] += Decimal(residual.base_quantity)
        if residual.disposition == SeedTrayGenerationResidual.Disposition.WASTE:
            row['discarded'].append(residual)
    return removals


def _applied_per_fill(lines, generation_ids):
    """Total what every line put into each fill, per lot."""
    totals = {}
    for line in lines:
        for target, portion in cell_shares(line):
            if target.seed_tray_generation_id not in generation_ids:
                continue
            key = (target.seed_tray_generation_id, line.lot_id)
            quantity = Decimal(line.applied_base_quantity) * portion
            totals[key] = totals.get(key, Decimal('0')) + quantity
    return totals


def _keep_factor(applied, removed):
    """Return the fraction of a fill's media that stayed in its cells."""
    if applied <= 0:
        return Decimal('1')
    if removed >= applied:
        return Decimal('0')
    return (applied - removed) / applied


def _cell_reach(batch, line, context):
    """Weight cell targets by volume, batch ownership, and what stayed in."""
    targets = [
        target for target in line.targets.all()
        if target.target_type in VOLUME_TARGETS and target.cell_volume_ml
    ]
    if not targets:
        return None
    basis = sum(
        (Decimal(target.weight) * Decimal(target.cell_volume_ml) for target in targets),
        Decimal('0'),
    )
    if basis <= 0:
        return None
    shares = []
    for share, target in zip(cell_volume_shares(targets), targets):
        if target.seed_tray_generation_id not in context['generations']:
            continue
        key = (target.seed_tray_generation_id, line.lot_id)
        removal = context['removals'].get(key) or {'removed': Decimal('0')}
        kept = _keep_factor(context['applied'].get(key, Decimal('0')), removal['removed'])
        mine = _cell_portion(context['cells'], target.seed_tray_cell_id, batch.pk)
        weight = share.weight * kept * mine
        if weight > 0:
            shares.append(share._replace(weight=weight))
    if not shares:
        return None
    mine = sum((share.weight for share in shares), Decimal('0'))
    return Reach(fraction=mine / basis, shares=tuple(shares))


def _plant_reach(batch, line):
    """Send per-unit cost to the named plants this batch actually raised."""
    named = [
        target.specific_plant_id
        for target in line.targets.all()
        if target.specific_plant_id
    ]
    if not named:
        return None
    mine = set(
        SpecificPlant.objects.filter(
            pk__in=named,
            batch=batch,
        ).values_list('pk', flat=True)
    )
    ours = sorted(plant_id for plant_id in named if plant_id in mine)
    if not ours:
        return None
    return Reach(
        fraction=Decimal(len(ours)) / Decimal(len(named)),
        shares=tuple(plant_shares(ours)),
    )


def _squares_under(target):
    """Return the garden squares a target covers, since only they hold plants.

    A row sits beside squares rather than above them, so a row-targeted
    application reaches no individually tracked plant.
    """
    if target.garden_square_id:
        return [target.garden_square_id]
    if target.garden_bed_id:
        lookup = {'bed_id': target.garden_bed_id}
    elif target.garden_area_id:
        lookup = {'bed__area_id': target.garden_area_id}
    else:
        return []
    return list(GardenSquare.objects.filter(**lookup).values_list('pk', flat=True))


def _plants_standing_in(batch, target):
    """Return this batch's plants whose open location is under one target."""
    squares = _squares_under(target)
    if not squares:
        return []
    return list(
        SpecificPlantLocation.objects.filter(
            specific_plant__batch=batch,
            ended__isnull=True,
            garden_square_id__in=squares,
        ).values_list('specific_plant_id', flat=True).order_by('specific_plant_id')
    )


def _area_reach(batch, line):
    """Split ground-applied cost across the plants standing on that ground."""
    targets = [
        target for target in line.targets.all()
        if target.target_type in AREA_TARGETS and target.area_m2
    ]
    if not targets:
        return None
    basis = Decimal('0')
    areas = []
    for target in targets:
        weight = Decimal(target.weight) * Decimal(target.area_m2)
        basis += weight
        plant_ids = _plants_standing_in(batch, target)
        if plant_ids:
            areas.append((weight, plant_ids, None))
    if not areas or basis <= 0:
        return None
    mine = sum((weight for weight, _plants, _weights in areas), Decimal('0'))
    return Reach(fraction=mine / basis, shares=tuple(area_plant_shares(areas)))


def _pooled_reach(batch, line):
    """Take whole-document cost that named nothing individual to the pool.

    Only the batch the document itself names carries this. A line reaching a
    batch solely through one of its cells has already been divided by cell; the
    rest of that line belongs to whoever else the tray was serving.
    """
    names_batch = any(target.batch_id == batch.pk for target in line.targets.all())
    if not names_batch and line.application.batch_id != batch.pk:
        return None
    return Reach(fraction=Decimal('1'), shares=tuple(whole_source_share()))


def _line_reach(batch, line, context):
    """Return how much of one line belongs to this batch, and where it lands.

    The order follows how specific each attribution is. A named cell or a named
    plant says exactly where the input went; ground says which plants were
    standing there; a document-level batch says only which crop paid for it.
    """
    for resolve in (
        lambda: _cell_reach(batch, line, context),
        lambda: _plant_reach(batch, line),
        lambda: _area_reach(batch, line),
        lambda: _pooled_reach(batch, line),
    ):
        reach = resolve()
        if reach is not None:
            return reach
    return None


def application_sources(batch, generation_ids, cell_weights):
    """Return one source per posted line, carrying this batch's share of it."""
    lines = list(batch_application_lines(batch, generation_ids))
    generations = set(generation_ids)
    context = {
        'generations': generations,
        'cells': cell_weights,
        'applied': _applied_per_fill(lines, generations),
        'removals': _fill_removals(generation_ids),
    }
    sources = []
    for line in lines:
        if line.item.tracking_mode == InventoryItem.TrackingMode.SERIALIZED:
            continue
        reach = _line_reach(batch, line, context)
        if reach is None or reach.fraction <= 0:
            continue
        sources.append(SourceInput(
            source_type=SourceType.APPLICATION_LINE,
            source=line,
            movement=line.consumption_movement,
            base_quantity=Decimal(line.applied_base_quantity) * reach.fraction,
            base_unit=line.base_unit,
            unit_cost=line.lot.base_unit_cost,
            currency_code=line.lot.currency_code,
            shares=reach.shares,
        ))
    return sources


# --------------------------------------------------------------------------
# Discarded remainders
# --------------------------------------------------------------------------


def residual_sources(batch, generation_ids):
    """Return one source per discarded remainder, all of it production loss.

    Media an operator threw away when cleaning the tray was applied and paid for
    but reached no seedling. It is already off the cells, because `_keep_factor`
    took it out of the allocation, so this is where its cost comes back in — as
    loss, pointing at the residual that recorded the decision.
    """
    seed_shares_by_fill = fill_seed_shares(generation_ids)
    sources = []
    for (generation_id, _lot_id), row in _fill_removals(generation_ids).items():
        by_batch = seed_shares_by_fill.get(generation_id) or {}
        total = sum(by_batch.values(), Decimal('0'))
        if total <= 0:
            continue
        fraction = by_batch.get(batch.pk, Decimal('0')) / total
        if fraction <= 0:
            continue
        for residual in row['discarded']:
            sources.append(SourceInput(
                source_type=SourceType.GENERATION_RESIDUAL,
                source=residual,
                movement=residual.movement,
                base_quantity=Decimal(residual.base_quantity) * fraction,
                base_unit=residual.base_unit,
                unit_cost=residual.unit_cost,
                currency_code=residual.lot.currency_code,
                shares=tuple(whole_source_share()),
            ))
    return sources


def batch_sources(batch):
    """Return every posted input this batch drew on, resolved to its targets.

    Cell shares are resolved to plants last and in one place, so seed and media
    reaching the same cell both follow the same seedlings out of it.
    """
    generation_ids = batch_generations(batch)
    cell_weights = cell_batch_weights(generation_ids)
    sources = seed_sources(batch)
    sources += application_sources(batch, generation_ids, cell_weights)
    sources += residual_sources(batch, generation_ids)
    observed = plants_by_cell(batch)
    outputs = cohort_outputs(batch)
    resolved = []
    for source in sources:
        shares = resolve_cells_to_plants(list(source.shares), observed)
        shares = resolve_unidentified_to_cohorts(shares, outputs)
        resolved.append(source._replace(shares=tuple(shares)))
    return resolved
