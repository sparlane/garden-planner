"""Decide what share of one source input belongs to each thing it reached.

These functions are pure in the same way and for the same reason as
`applications.usage`: they take already-measured facts and return shares,
without reading the database. A layer posted from them can therefore be
recalculated from its stored snapshot years later and come out the same.

The whole module rests on one invariant, which `inventory.ledger.distribute_exactly`
supplies: **the shares of a source always add back up to the source**. Nothing
is dropped to rounding and nothing is quietly absorbed into one arbitrary
share, so a batch's layers can always be reconciled against the stock movements
that created them.

Cost never rests in two places at once. A share whose plant is not yet known
sits on the cell, or in the batch pool; when a plant is observed it moves to the
plant, and when output is finalized without one it moves to production loss.
Each of those moves is a reversal plus a replacement, never a second layer on
top of the first, because two layers would double the cost of one input.
"""

from decimal import Decimal
from typing import NamedTuple

from django.core.exceptions import ValidationError

from inventory.ledger import QUANTITY_QUANTUM, distribute_exactly

from .models import CostAllocation


#: The key standing in for "nowhere individual yet". Shares carrying it become
#: batch-pool layers, and at output finalization production-loss layers.
POOL = None


class Share(NamedTuple):
    """One destination and the weight that earns it a part of the source."""

    key: object
    target_type: str
    weight: Decimal
    basis: str
    cell_id: object = None
    generation_id: object = None
    plant_id: object = None
    cohort_id: object = None


class Part(NamedTuple):
    """One share, valued: what quantity and what money it carries."""

    share: Share
    base_quantity: Decimal
    amount: object


def _require(condition, field, message):
    """Raise a field error unless a precondition holds."""
    if not condition:
        raise ValidationError({field: message})


def value_shares(shares, base_quantity, amount):
    """Split one source's quantity and cost across its shares exactly.

    Quantity and money are distributed separately because they are stored at
    different precisions, but both go through the same exact split, so a report
    can reconcile either one back to the source without a residue.
    """
    _require(shares, 'shares', 'A source input needs somewhere to go.')
    weights = [share.weight for share in shares]
    quantities = distribute_exactly(base_quantity, weights, QUANTITY_QUANTUM)
    amounts = distribute_exactly(amount, weights)
    return [
        Part(share=share, base_quantity=quantity, amount=part)
        for share, quantity, part in zip(shares, quantities, amounts)
    ]


def seed_shares(sowing_quantity, cell_quantities):
    """Split a sowing's seed cost by the seed actually placed in each cell.

    Seed drawn from the packet but never placed in a cell has not reached a
    seedling and may never, so it stays in the batch pool rather than inflating
    the cells that did get sown. That remainder is what
    `seedtrays.generations.unsown_seed` reports on the tray screen.
    """
    sowing_quantity = Decimal(sowing_quantity)
    shares = [
        Share(
            key=('cell', cell_id),
            target_type=CostAllocation.TargetType.SEED_TRAY_CELL,
            weight=Decimal(quantity),
            basis=CostAllocation.Basis.SEEDS_SOWN,
            cell_id=cell_id,
            generation_id=generation_id,
        )
        for cell_id, generation_id, quantity in cell_quantities
        if Decimal(quantity) > 0
    ]
    placed = sum((share.weight for share in shares), Decimal('0'))
    _require(
        placed <= sowing_quantity,
        'cell_quantities',
        'More seed is allocated to cells than the sowing drew.',
    )
    remainder = sowing_quantity - placed
    if remainder > 0:
        shares.append(_pool_share(remainder, CostAllocation.Basis.SEEDS_SOWN))
    return shares


def cell_volume_shares(cell_targets):
    """Split cell-targeted cost the way the application calculated it.

    The weights are `weight * cell_volume_ml`, which is the basis
    `applications.usage` used to arrive at the quantity in the first place, and
    which `seedtrays.generations.cell_shares` already applies when it reports a
    fill's media. Dividing any other way would attribute a number the
    application never used.
    """
    return [
        Share(
            key=('cell', target.seed_tray_cell_id),
            target_type=CostAllocation.TargetType.SEED_TRAY_CELL,
            weight=Decimal(target.weight) * Decimal(target.cell_volume_ml),
            basis=CostAllocation.Basis.CELL_VOLUME,
            cell_id=target.seed_tray_cell_id,
            generation_id=target.seed_tray_generation_id,
        )
        for target in cell_targets
        if target.cell_volume_ml
    ]


def plant_shares(plant_ids, weights=None):
    """Send per-plant cost straight to the plants it was applied to."""
    weights = weights or {}
    return [
        Share(
            key=('plant', plant_id),
            target_type=CostAllocation.TargetType.SPECIFIC_PLANT,
            weight=Decimal(weights.get(plant_id, 1)),
            basis=CostAllocation.Basis.PER_PLANT,
            plant_id=plant_id,
        )
        for plant_id in plant_ids
    ]


def area_plant_shares(areas):
    """Split area-targeted cost by area, then among the plants standing in it.

    Each area's weight is divided by its own plant count before the single
    exact split runs, rather than splitting twice. The proportions are identical
    either way, but one split means one set of rounding remainders to reconcile
    instead of a remainder per area on top of a remainder overall.

    An area with no eligible plants keeps its share in the batch pool. Spreading
    it over the other areas' plants would charge a seedling for ground it never
    occupied.
    """
    shares = []
    orphaned = Decimal('0')
    for area_weight, plant_ids, plant_weights in areas:
        area_weight = Decimal(area_weight)
        if not plant_ids:
            orphaned += area_weight
            continue
        supplied = plant_weights or {}
        basis = sum(
            (Decimal(supplied.get(plant_id, 1)) for plant_id in plant_ids),
            Decimal('0'),
        )
        _require(
            basis > 0,
            'targets',
            'The per-plant weights for an area total zero.',
        )
        shares.extend(
            Share(
                key=('plant', plant_id),
                target_type=CostAllocation.TargetType.SPECIFIC_PLANT,
                weight=area_weight * Decimal(supplied.get(plant_id, 1)) / basis,
                basis=CostAllocation.Basis.AREA,
                plant_id=plant_id,
            )
            for plant_id in plant_ids
        )
    if orphaned > 0:
        shares.append(_pool_share(orphaned, CostAllocation.Basis.AREA))
    return shares


def resolve_cells_to_plants(shares, plants_by_cell):
    """Move each cell's share onto the seedlings that actually came up in it.

    A cell that produced three plants from one multigerm cluster spreads one
    cell's worth of cost across three seedlings without changing any count —
    the quantity sown stays exactly what was sown. A cell that produced nothing
    keeps its share, which stays provisional while a seedling might still
    appear and becomes production loss when output is finalized without one.

    Shares that never named a cell pass through untouched.
    """
    resolved = []
    for share in shares:
        plants = plants_by_cell.get(share.cell_id) if share.cell_id else None
        if not plants:
            resolved.append(share)
            continue
        portion = share.weight / Decimal(len(plants))
        resolved.extend(
            Share(
                key=('plant', plant_id),
                target_type=CostAllocation.TargetType.SPECIFIC_PLANT,
                weight=portion,
                basis=CostAllocation.Basis.EQUAL_SHARE,
                plant_id=plant_id,
            )
            for plant_id in plants
        )
    return combine(resolved)


def resolve_unidentified_to_cohorts(shares, outputs):
    """Move unresolved nursery cost to cohort stock and promoted plant IDs.

    `outputs` contains one unit weight for every currently anonymous plant and
    every concrete plant promoted from the batch's cohorts. Recalculation then
    transfers value instead of layering a second cost on promotion.
    """
    if not outputs:
        return shares
    resolved = []
    total = sum((Decimal(weight) for _kind, _target_id, weight in outputs), Decimal('0'))
    for share in shares:
        if share.target_type not in {
                CostAllocation.TargetType.SEED_TRAY_CELL,
                CostAllocation.TargetType.BATCH_POOL}:
            resolved.append(share)
            continue
        for kind, target_id, weight in outputs:
            portion = share.weight * Decimal(weight) / total
            if kind == 'cohort':
                resolved.append(Share(
                    key=('cohort', target_id),
                    target_type=CostAllocation.TargetType.PLANT_COHORT,
                    weight=portion,
                    basis=CostAllocation.Basis.PER_PLANT,
                    cohort_id=target_id,
                ))
            else:
                resolved.append(Share(
                    key=('plant', target_id),
                    target_type=CostAllocation.TargetType.SPECIFIC_PLANT,
                    weight=portion,
                    basis=CostAllocation.Basis.PER_PLANT,
                    plant_id=target_id,
                ))
    return combine(resolved)


def combine(shares):
    """Total the weights of shares that ended up in the same place.

    Two sources of shared cost can reach one plant — seed through its cell and
    media through the same cell — and one plant can be reached twice by one
    source when it is named directly as well. One layer per destination per
    source keeps the ledger readable and the diff stable.
    """
    merged = {}
    for share in shares:
        existing = merged.get(share.key)
        if existing is None:
            merged[share.key] = share
        else:
            merged[share.key] = existing._replace(weight=existing.weight + share.weight)
    return list(merged.values())


def whole_source_share(basis=CostAllocation.Basis.DIRECT):
    """Return the single share for cost that reached nothing individual."""
    return [_pool_share(Decimal('1'), basis)]


def unattributable_share(basis=CostAllocation.Basis.DIRECT):
    """Return the share for cost that could never reach an individual plant.

    A direct-sown row produces a crop rather than a set of seedlings, so its
    seed cost has no plant to land on and never will. That is not the same as
    an unresolved pool waiting for a germination, and it is emphatically not a
    loss — reporting a Garden workspace's whole harvest as waste would be the
    opposite of true. It stays its own figure, for task 46 to reconcile against
    the yields those sowings actually produced.
    """
    return [
        Share(
            key=('unattributed', POOL),
            target_type=CostAllocation.TargetType.UNATTRIBUTED,
            weight=Decimal('1'),
            basis=basis,
        ),
    ]


def loss_shares(shares):
    """Turn unresolved shares into production loss, keeping their weights.

    Called once, at output finalization: a cell with no seedling and a pool
    nothing ever claimed are both cost the batch incurred and never recovered.
    Dropping them would understate what the crop cost; leaving them on the cell
    would imply a seedling that does not exist.

    Every retired share of one source becomes one loss layer rather than one per
    cell. Which cells were retired stays readable in the reversals that replaced
    them, and a single figure is what a report of the batch's loss actually
    wants.
    """
    return combine([
        share._replace(
            key=('production_loss', POOL),
            target_type=CostAllocation.TargetType.PRODUCTION_LOSS,
            cell_id=None,
            generation_id=None,
            plant_id=None,
            cohort_id=None,
        )
        for share in shares
    ])


def _pool_share(weight, basis):
    """Return one share of cost that has not reached anything individual."""
    return Share(
        key=('pool', POOL),
        target_type=CostAllocation.TargetType.BATCH_POOL,
        weight=Decimal(weight),
        basis=basis,
    )
