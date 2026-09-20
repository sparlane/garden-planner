"""Post, correct, and read the per-plant production-cost subledger.

One entry point does the writing. `reallocate_batch` recomputes what a batch's
allocations ought to be from the facts currently on file, compares that against
what is stored, and appends a reversal plus a replacement wherever the two
disagree. It is idempotent: run twice with nothing changed in between and the
second run writes nothing at all, which is what lets it be called from ordinary
events rather than from a separate maintenance job.

**Locks are taken as plants, then the batch.** That order is not arbitrary and
not new. `applications.services.post_application` and `plantings.harvests`
already document it, and `plantings.test_harvest_concurrency` proves it: writing
a plant fact takes a key-share lock on the batch row through the foreign key, so
a transaction holding the batch exclusively while reaching for a plant deadlocks
against one holding the plant while reaching for the batch. Posting allocations
does exactly that reaching — every plant-targeted layer takes a key-share lock on
its plant — so this extends the existing chain instead of starting a new one.

**Freezing is a rule, not a column.** A batch that has reached
`output_finalized_at` no longer has its stored layers touched; what a
recalculation may still do is append layers for inputs that arrived afterwards,
and those go straight to the plants they name. Anything that would otherwise
rest on a cell or in the pool becomes production loss instead, because output
being final is precisely the statement that no further seedling is coming. The
way to redo a frozen allocation is `plantings.batches.reopen_batch`, which is
already an audited transition with a required reason.
"""

# pylint: disable=duplicate-code

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from applications.models import FACTOR_DECIMAL_PLACES
from inventory.ledger import QUANTITY_QUANTUM, distribute_exactly, quantize_money, quantize_quantity
from plantings.batches import lock_batch_with_plants
from plantings.lifecycle import LifecycleState, lifecycle_summaries
from plantings.models import PlantCohort, ProductionBatch, SpecificPlant, SpecificPlantLocation

from .allocation import combine, loss_shares, value_shares
from .currency import currency_amounts, held_by_currency, stated_currency
from .pending import plant_pending_cost, plant_sale_totals
from .models import CostAllocation, CostAllocationRun, FillDepartureRecalculation
from .cohort_weights import cohort_weights
from .sources import batch_sources, sold_cohort_quantities


FACTOR_QUANTUM = Decimal(1).scaleb(-FACTOR_DECIMAL_PLACES)

TargetType = CostAllocation.TargetType

#: Target types holding cost that has not reached a seedling and still might.
#: Finalizing output is the statement that it will not, which is what turns
#: these into production loss.
UNRESOLVED_TARGETS = (TargetType.SEED_TRAY_CELL, TargetType.BATCH_POOL)

#: The targets holding what one anonymous block is worth: the units still
#: standing there and the units already sold out of it. A block's unit value is
#: read from these two together, over the units they count.
COHORT_TARGETS = (TargetType.PLANT_COHORT, TargetType.COHORT_SALE)

#: The targets whose share of a batch is a count of anonymous units rather than
#: a fixed identity: the block's own halves and the units lost out of it. Their
#: division changes whenever a block is sold, lost, promoted, returned or
#: recounted, which is why a frozen batch still has to re-divide them where a
#: plant's frozen share is never touched again.
REDIVIDED_TARGETS = COHORT_TARGETS + (TargetType.COHORT_LOSS,)

#: Where a plant's production value goes once its lifecycle resolves. Derived
#: from the recorded facts every time it is asked for rather than stored, for
#: the reason `plantings.lifecycle` derives the state itself: a stored copy is a
#: second source of truth that can drift, and deriving is also what makes the
#: value transfer exactly once by construction.
DISPOSITION_OF_STATE = {
    LifecycleState.GROWING: 'plant_inventory',
    LifecycleState.AVAILABLE: 'plant_inventory',
    LifecycleState.RETAINED: 'plant_inventory',
    LifecycleState.DONATED: 'production_loss',
    LifecycleState.FAILED: 'production_loss',
    LifecycleState.LOST: 'production_loss',
    LifecycleState.CULLED: 'production_loss',
    LifecycleState.HARVESTED: 'harvested_output',
    LifecycleState.SOLD: 'cogs',
    LifecycleState.QUARANTINED: 'plant_inventory',
    LifecycleState.DISCARDED: 'production_loss',
    # A withdrawn plant never came up, so it holds no production value: the
    # reallocation that follows the withdrawal reverses every layer naming it
    # and sends the cost back to its cell. This entry is what a layer would
    # fall into if one somehow outlived that, and `unattributed` is the honest
    # bucket for it — cost against a plant that was never there is exactly what
    # cannot be attributed to a plant. `production_loss` would be wrong twice
    # over: nothing was lost, and the seed itself may yet be counted as an
    # ungerminated remainder once the sowing closes.
    LifecycleState.WITHDRAWN: 'unattributed',
}

#: Where the value still standing in an anonymous block goes, by the block's
#: state. The block's sold and lost units are already their own targets, so
#: this only ever sorts the units it still holds, and every state that holds
#: units is stock on hand, exactly as the same plants are once promoted. A
#: quarantined block is not a state here: quarantine is a health overlay a
#: block carries in any state, and it is still stock the workspace holds, as
#: `quarantined` is on the plant side. A depleted block holds no units, so it
#: never has a layer left standing against it; one that somehow outlived the
#: reallocation is a fault upstream, and `unresolved` is where it shows as one
#: rather than being absorbed as stock or loss.
DISPOSITION_OF_COHORT_STATE = {
    PlantCohort.LifecycleState.GROWING: 'plant_inventory',
    PlantCohort.LifecycleState.AVAILABLE: 'plant_inventory',
    PlantCohort.LifecycleState.RETAINED: 'plant_inventory',
    PlantCohort.LifecycleState.DEPLETED: 'unresolved',
}

#: Every bucket a batch's value can sit in, listed in full so a report never
#: has to guess whether a missing key means zero or means unsupported.
VALUE_BUCKETS = (
    'plant_inventory',
    'cogs',
    'harvested_output',
    'production_loss',
    'unresolved',
    'unattributed',
)

#: The fields that decide whether a stored layer still says the right thing.
COMPARED_FIELDS = (
    'basis',
    'basis_weight',
    'base_quantity',
    'base_unit',
    'unit_cost',
    'amount',
    'currency_code',
    'seed_tray_generation_id',
)


def is_frozen(batch):
    """Return whether this batch's pre-output allocations are final."""
    return batch.output_finalized_at is not None


def _basis_weights(shares):
    """Return each share's fraction of its source, at factor precision."""
    total = sum((share.weight for share in shares), Decimal('0'))
    if total <= 0:
        return [Decimal('0')] * len(shares)
    return [
        (share.weight / total).quantize(FACTOR_QUANTUM)
        for share in shares
    ]


def _layer_key(spec):
    """Identify one layer by the source it draws on and where it lands."""
    return (
        spec['source_type'],
        spec['source'].pk,
        spec['target_type'],
        spec.get('seed_tray_cell_id'),
        spec.get('specific_plant_id'),
        spec.get('plant_cohort_id'),
    )


def _stored_key(row):
    """Identify a stored layer the same way an intended one is identified."""
    return (
        row.source_type,
        row.source_id,
        row.target_type,
        row.seed_tray_cell_id,
        row.specific_plant_id,
        row.plant_cohort_id,
    )


def _resolve_for_freeze(shares, frozen):
    """Turn cost with nowhere left to go into production loss.

    Applied once output is final, and only then. Before that a cell with no
    seedling is a cell that might still produce one, and a pool is cost waiting
    to be claimed; after it, both are cost the batch incurred and never
    recovered. Unattributed cost is deliberately left alone — a direct-sown row
    produced a crop, and calling that a loss would be the opposite of true.
    """
    if not frozen:
        return shares
    unresolved = [share for share in shares if share.target_type in UNRESOLVED_TARGETS]
    if not unresolved:
        return shares
    keep = [share for share in shares if share.target_type not in UNRESOLVED_TARGETS]
    return combine(keep + loss_shares(unresolved))


def intended_layers(batch):
    """Return the layers this batch's facts currently imply, keyed for diffing."""
    frozen = is_frozen(batch)
    layers = {}
    for source in batch_sources(batch):
        shares = _resolve_for_freeze(list(source.shares), frozen)
        if not shares:
            continue
        weights = _basis_weights(shares)
        parts = value_shares(
            shares,
            quantize_quantity(source.base_quantity),
            quantize_money(source.amount),
        )
        for part, weight in zip(parts, weights):
            spec = {
                'source_type': source.source_type,
                'source': source.source,
                'movement': source.movement,
                'target_type': part.share.target_type,
                'seed_tray_cell_id': part.share.cell_id,
                'seed_tray_generation_id': part.share.generation_id,
                'specific_plant_id': part.share.plant_id,
                'plant_cohort_id': part.share.cohort_id,
                'basis': part.share.basis,
                'basis_weight': weight,
                'base_quantity': part.base_quantity,
                'base_unit': source.base_unit,
                'unit_cost': source.unit_cost,
                'amount': part.amount,
                'currency_code': source.currency_code,
                # Not stored: what a frozen batch re-divides its cohort layers
                # by, once the frozen plant shares are taken off the source.
                'weight': part.share.weight,
            }
            layers[_layer_key(spec)] = spec
    return layers


def effective_allocations(batch):
    """Return the layers that still count: not reversals, and not reversed."""
    return list(
        CostAllocation.objects
        .filter(batch=batch, reversal_of__isnull=True, reversal__isnull=True)
        .select_related('run')
        .order_by('pk')
    )


def _same(stored, wanted):
    """Compare one field of a layer, keeping unknown distinct from zero."""
    if stored is None or wanted is None:
        return stored is None and wanted is None
    if isinstance(stored, Decimal) or isinstance(wanted, Decimal):
        return Decimal(stored) == Decimal(wanted)
    return stored == wanted


def _matches(row, spec):
    """Return whether a stored layer already says what the facts imply."""
    return all(
        _same(getattr(row, field), spec.get(field))
        for field in COMPARED_FIELDS
    )


def _write_layer(run, spec, reversal_of=None):
    """Append one immutable layer, or the reversal that cancels one."""
    fields = {
        'workspace': run.workspace,
        'run': run,
        'batch': run.batch,
        'source_type': spec['source_type'],
        spec['source_type']: spec['source'],
        'movement': spec['movement'],
        'target_type': spec['target_type'],
        'seed_tray_cell_id': spec.get('seed_tray_cell_id'),
        'seed_tray_generation_id': spec.get('seed_tray_generation_id'),
        'specific_plant_id': spec.get('specific_plant_id'),
        'plant_cohort_id': spec.get('plant_cohort_id'),
        'basis': spec['basis'],
        'basis_weight': spec['basis_weight'],
        'base_quantity': spec['base_quantity'],
        'base_unit': spec['base_unit'],
        'unit_cost': spec['unit_cost'],
        'amount': spec['amount'],
        'currency_code': spec['currency_code'],
        'reversal_of': reversal_of,
    }
    return CostAllocation.objects.create(**fields)


def _spec_of(row):
    """Describe a stored layer the way an intended one is described."""
    return {
        'source_type': row.source_type,
        'source': row.source,
        'movement': row.movement,
        'target_type': row.target_type,
        'seed_tray_cell_id': row.seed_tray_cell_id,
        'seed_tray_generation_id': row.seed_tray_generation_id,
        'specific_plant_id': row.specific_plant_id,
        'plant_cohort_id': row.plant_cohort_id,
        'basis': row.basis,
        'basis_weight': row.basis_weight,
        'base_quantity': row.base_quantity,
        'base_unit': row.base_unit,
        'unit_cost': row.unit_cost,
        'amount': row.amount,
        'currency_code': row.currency_code,
    }


def _left_over(source_layers, fixed_layers):
    """Return the amount and quantity of one source its fixed layers leave.

    None when that cannot be known — an unpriced source, or frozen shares that
    already hold more than the source now does — and the caller then leaves
    the full split alone rather than inventing a remainder.
    """
    if any(spec['amount'] is None for spec in source_layers + fixed_layers):
        return None
    amount = sum((spec['amount'] for spec in source_layers), Decimal('0'))
    amount -= sum((spec['amount'] for spec in fixed_layers), Decimal('0'))
    quantity = sum((spec['base_quantity'] for spec in source_layers), Decimal('0'))
    quantity -= sum((spec['base_quantity'] for spec in fixed_layers), Decimal('0'))
    if amount < 0 or quantity < 0:
        return None
    return amount, quantity


def _reclaimed_losses(intended, stored, standing_at_freeze):
    """Return stored pool losses a finalized batch owes back to its cohorts.

    Before task 136 a block lost whole stopped being an output, so a batch
    finalized after that had nowhere to put its cost and `_resolve_for_freeze`
    retired all of it into the pool `PRODUCTION_LOSS`, undated and uncaused.
    Now the lost units are outputs again, but the pool row is not a cohort
    layer and its source is live, so the freeze would keep it and hand the
    cohort side only what it leaves — nothing.

    Such a row is recognised by its source: it has no stored cohort-side layer
    at all, yet today it resolves to a block that already existed when output
    was finalized. That block's units were outputs then, and the pool row is
    only what stood in for them. Its reversal is reposted from today's split,
    so whatever part of it really is pool loss (seed that never germinated)
    stays pool loss. A block first observed after finalization claims nothing
    this way; whether a late arrival may is task 147's question.
    """
    divided = {
        (row.source_type, row.source_id)
        for row in stored.values() if row.target_type in REDIVIDED_TARGETS
    }
    owed = {
        (spec['source_type'], spec['source'].pk)
        for spec in intended.values()
        if spec['target_type'] in REDIVIDED_TARGETS and spec['plant_cohort_id'] in standing_at_freeze
    }
    stranded = owed - divided
    return {
        key for key, row in stored.items()
        if row.target_type == TargetType.PRODUCTION_LOSS and (row.source_type, row.source_id) in stranded
    }


def _redivide_around_frozen(intended, stored, reclaimed=frozenset()):
    """Fit a frozen batch's cohort layers to what its frozen shares left over.

    `intended_layers` splits every source afresh over all of today's outputs,
    but a frozen batch keeps the plant shares it already posted. Posting the
    cohort side of today's split beside yesterday's plant shares only adds
    back up to the source when the two splits agree to the cent, and they do
    not always: `distribute_exactly` hands leftover cents out by position, so a
    split among more outputs can give the cent a promoted plant was given at
    promotion to another output as well. Rounding cents are not the only gap —
    a recount that changes the number of units moves every unit's share, and
    the frozen ones cannot follow.

    So each source's cohort side is divided out of the remainder instead: the
    source, less every layer of it that stays or is newly posted outside the
    cohort side. The frozen shares keep exactly what they hold, and the cohort
    side always completes the source.
    """
    by_source = {}
    for key, spec in intended.items():
        by_source.setdefault((spec['source_type'], spec['source'].pk), []).append(key)
    kept = {}
    for key, row in stored.items():
        if row.target_type in REDIVIDED_TARGETS or row.target_type in UNRESOLVED_TARGETS or key in reclaimed:
            continue
        kept.setdefault((row.source_type, row.source_id), []).append({
            'amount': row.amount, 'base_quantity': row.base_quantity,
        })
    fitted = dict(intended)
    for source_key, keys in by_source.items():
        posted = [
            intended[key] for key in keys
            if intended[key]['target_type'] not in REDIVIDED_TARGETS and (key not in stored or key in reclaimed)
        ]
        fitted.update(_fit_source(intended, keys, posted + kept.get(source_key, [])))
    return fitted


def _fit_source(intended, keys, fixed_layers):
    """Divide what one source's fixed layers leave over its cohort side."""
    redivided = [key for key in keys if intended[key]['target_type'] in REDIVIDED_TARGETS]
    left = _left_over([intended[key] for key in keys], fixed_layers)
    if not redivided or left is None:
        return {}
    weights = [intended[key]['weight'] for key in redivided]
    parts = zip(
        redivided,
        distribute_exactly(left[0], weights),
        distribute_exactly(left[1], weights, QUANTITY_QUANTUM),
    )
    return {
        key: {**intended[key], 'amount': amount, 'base_quantity': quantity}
        for key, amount, quantity in parts
    }


def _frozen_plan(intended, stored, standing_at_freeze=frozenset()):
    """Return the only two changes a finalized batch still admits.

    Retire what never reached a seedling, and cancel what an input reversal took
    back. A plant's frozen share is never re-divided: that is what finalizing
    output means, and reopening the batch is the audited way to undo it.

    A layer whose source has vanished from the intended set had its input
    reversed, so its cost has to come back out even though the batch is frozen —
    stock that returned to the shelf cannot still be sitting in a seedling.

    Cohort layers are the exception to never re-dividing: a sale, loss,
    promotion or return changes how many anonymous units share the cohort's
    cost, so the superseded layer is reversed and its replacement is posted in
    the same run. They divide what the frozen shares left of each source, not
    the whole of it; `_redivide_around_frozen` says why. The posting list is
    read off the reversal decision rather than repeating the match test,
    because the two have to agree about which stored rows are going away — a
    layer reversed without its replacement takes its cost off the batch, and a
    finalized total is the one that must not move.

    The one pool loss a frozen batch gives back is the one that stood in for a
    block's lost units before they were outputs; `_reclaimed_losses` says how
    it is told apart from loss that belongs in the pool.
    """
    reclaimed = _reclaimed_losses(intended, stored, standing_at_freeze)
    intended = _redivide_around_frozen(intended, stored, reclaimed)
    live_sources = {
        (spec['source_type'], spec['source'].pk)
        for spec in intended.values()
    }

    def retired(row):
        """Return whether a frozen batch still has to cancel this layer."""
        if row.target_type in UNRESOLVED_TARGETS or _stored_key(row) in reclaimed:
            return True
        if row.target_type in REDIVIDED_TARGETS:
            key = _stored_key(row)
            return key not in intended or not _matches(row, intended[key])
        return (row.source_type, row.source_id) not in live_sources

    reverse = [row for row in stored.values() if retired(row)]
    reversed_keys = {_stored_key(row) for row in reverse}
    post = [
        spec for key, spec in intended.items()
        if key not in stored or key in reversed_keys
    ]
    return reverse, post


def _plan(batch):
    """Return the layers to reverse and the layers to post, without writing."""
    intended = intended_layers(batch)
    stored = {_stored_key(row): row for row in effective_allocations(batch)}
    if is_frozen(batch):
        standing_at_freeze = frozenset(
            PlantCohort.objects
            .filter(batch=batch, created__lt=batch.output_finalized_at)
            .values_list('pk', flat=True)
        )
        return _frozen_plan(intended, stored, standing_at_freeze)
    reverse = [
        row for key, row in stored.items()
        if key not in intended or not _matches(row, intended[key])
    ]
    post = [
        spec for key, spec in intended.items()
        if key not in stored or not _matches(stored[key], spec)
    ]
    return reverse, post


@transaction.atomic
def reallocate_batch(batch, user, trigger, reason=''):
    """Bring one batch's stored allocations back in step with its facts.

    Returns the run that wrote them, or None when nothing needed changing. A run
    row exists only where there was something to record: this is called from
    ordinary events, most of which change no allocation, and storing a row for
    every check would bury the runs that did something.
    """
    try:
        trigger = CostAllocationRun.Trigger(trigger)
    except ValueError as exc:
        raise ValidationError({
            'trigger': f'Value {trigger!r} is not a valid choice.',
        }) from exc
    batch = lock_batch_with_plants(batch)
    reverse, post = _plan(batch)
    if not reverse and not post:
        return None
    run = CostAllocationRun.objects.create(
        workspace=batch.workspace,
        batch=batch,
        trigger=trigger,
        reason=reason,
        posted_count=len(post),
        reversed_count=len(reverse),
        froze_output=trigger == CostAllocationRun.Trigger.OUTPUT_FINALIZED,
        created_by=user if user is not None and user.is_authenticated else None,
    )
    for row in reverse:
        _write_layer(run, _spec_of(row), reversal_of=row)
    for spec in post:
        _write_layer(run, spec)
    return run


def schedule_fill_departure(placement):
    """Persist retry work in the departure transaction before trying it at commit."""
    FillDepartureRecalculation.objects.get_or_create(placement=placement)
    placement_id = placement.pk
    transaction.on_commit(lambda: reallocate_fill_departure(placement_id), robust=True)


def reallocate_fill_departure(placement_id):
    """Bring a committed pot-fill departure into its crop's cost ledger.

    Read persisted facts rather than a caller's potentially stale plant or
    fill. Legacy departures with no fixed shares have no new cost to post.
    A failure leaves the durable request for retry_fill_departure_costs. A crash
    after costing but before clearing the request is safe: costing is idempotent.
    """
    placement = SpecificPlantLocation.objects.select_related('specific_plant__batch').filter(
        pk=placement_id, ended__isnull=False,
        container_fill__tray__isnull=True,
    ).filter(Q(container_fill__plant_share_count__isnull=False) | Q(container_fill__stock_lot__isnull=False)).first()
    run = None
    if placement is not None and placement.specific_plant.batch_id is not None:
        run = reallocate_batch(
            placement.specific_plant.batch, None, CostAllocationRun.Trigger.FILL_DEPARTURE,
        )
    FillDepartureRecalculation.objects.filter(placement_id=placement_id).delete()
    return run


def reallocate_batches(batches, user, trigger, reason=''):
    """Reallocate several batches in key order, so locks are never crossed."""
    runs = []
    for batch in sorted(set(batches), key=lambda item: item.pk):
        run = reallocate_batch(batch, user, trigger, reason)
        if run is not None:
            runs.append(run)
    return runs


def finalize_batch_costs(batch, user, reason=''):
    """Freeze one batch's allocations and retire what never reached a plant.

    Called from inside `plantings.batches.finalize_batch_output` after the batch
    has been stamped, so the recomputation sees a finalized batch and applies the
    freeze rule to it.
    """
    return reallocate_batch(
        batch,
        user,
        CostAllocationRun.Trigger.OUTPUT_FINALIZED,
        reason,
    )


def recalculate_batch_costs(batch, user, reason):
    """Repost one batch's allocations from corrected source facts.

    This is the correction operation. It never edits an amount: a layer that no
    longer matches the facts is reversed and its replacement posted beside it. On
    a finalized batch it is append-only, because the frozen layers are the point
    of finalizing; `plantings.batches.reopen_batch` is what unfreezes them, and
    it already demands a reason and records a transition.
    """
    return reallocate_batch(
        batch,
        user,
        CostAllocationRun.Trigger.MANUAL_RECALCULATE,
        reason,
    )


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def plant_dispositions(batch):
    """Return each plant's derived state and the bucket its value belongs in."""
    plant_ids = list(
        SpecificPlant.objects
        .filter(batch=batch)
        .order_by('pk').values_list('pk', flat=True)
    )
    return {
        plant_id: (summary.state, DISPOSITION_OF_STATE[summary.state])
        for plant_id, summary in lifecycle_summaries(plant_ids).items()
    }


def cohort_dispositions(batch):
    """Return the bucket the stock standing in each of a batch's blocks belongs in."""
    return {
        cohort_id: DISPOSITION_OF_COHORT_STATE[state]
        for cohort_id, state in
        PlantCohort.objects.filter(batch=batch).values_list('pk', 'lifecycle_state')
    }


def _bucket_of(row, dispositions, cohorts):
    """Return which value bucket one layer belongs in."""
    if row.target_type == TargetType.SPECIFIC_PLANT:
        return dispositions.get(row.specific_plant_id, (None, 'plant_inventory'))[1]
    if row.target_type == TargetType.PLANT_COHORT:
        return cohorts.get(row.plant_cohort_id, 'plant_inventory')
    if row.target_type == TargetType.COHORT_SALE:
        return 'cogs'
    if row.target_type in (TargetType.PRODUCTION_LOSS, TargetType.COHORT_LOSS):
        return 'production_loss'
    if row.target_type == TargetType.UNATTRIBUTED:
        return 'unattributed'
    return 'unresolved'


def _source_lot(row):
    """Return the stock lot one layer's input came out of, when there is one.

    A sold container reaches its lot through the unit rather than through a
    consumption line, and a manually entered garden purchase has no lot at all.
    """
    if row.application_line is not None:
        return row.application_line.lot
    if row.sowing_posting is not None:
        return row.sowing_posting.movement.lot
    if row.generation_residual is not None:
        return row.generation_residual.lot
    if row.container_dispatch_id is not None:
        return row.container_dispatch.stock_movement.lot
    if row.container_unit is not None:
        return row.container_unit.source_lot
    return None


def _source_reference(row):
    """Return the identifiers that tie one layer back to its origin."""
    line = row.application_line
    posting = row.sowing_posting
    residual = row.generation_residual
    lot = _source_lot(row)
    return {
        'source_type': row.source_type,
        'source': row.source_id,
        'application': line.application_id if line is not None else None,
        'application_line': line.pk if line is not None else None,
        'sowing_posting': posting.pk if posting is not None else None,
        'generation_residual': residual.pk if residual is not None else None,
        'container_unit': row.container_unit_id,
        'container_dispatch': row.container_dispatch_id,
        'movement': row.movement_id,
        'lot': lot.pk if lot is not None else None,
        'item': lot.item_id if lot is not None else None,
        'receipt_line': lot.receipt_line_id if lot is not None else None,
    }


def _layer_row(row):
    """Render one layer with its money as decimal strings."""
    return {
        'allocation': row.pk,
        'run': row.run_id,
        **_source_reference(row),
        'target_type': row.target_type,
        'seed_tray_cell': row.seed_tray_cell_id,
        'seed_tray_generation': row.seed_tray_generation_id,
        'specific_plant': row.specific_plant_id,
        'plant_cohort': row.plant_cohort_id,
        'basis': row.basis,
        'basis_weight': f'{row.basis_weight:f}',
        'base_quantity': f'{row.base_quantity:f}',
        'base_unit': row.base_unit,
        'unit_cost': None if row.unit_cost is None else f'{row.unit_cost:f}',
        'amount': None if row.amount is None else f'{row.amount:f}',
        'currency_code': row.currency_code,
    }


def _loaded_allocations(batch):
    """Return this batch's effective layers with their sources in hand."""
    return list(
        CostAllocation.objects
        .filter(batch=batch, reversal_of__isnull=True, reversal__isnull=True)
        .select_related(
            'application_line__lot__item',
            'sowing_posting__movement__lot__item',
            'generation_residual__lot__item',
            'container_unit__source_lot__item',
            'container_dispatch__stock_movement__lot__item',
        )
        .order_by('pk')
    )


def _totals(rows, dispositions, cohorts):
    """Total each value bucket per currency, reporting unknown cost not zero.

    Each currency gets its own complete set of buckets rather than the buckets
    holding a sum of two of them; `costing.currency` says why nothing here may
    add them together, and a converted total would later be summed from these.
    """
    totals = {}
    unknown = False
    for row in rows:
        if row.amount is None:
            unknown = True
            continue
        buckets = totals.setdefault(
            row.currency_code,
            {bucket: Decimal('0') for bucket in VALUE_BUCKETS},
        )
        buckets[_bucket_of(row, dispositions, cohorts)] += row.amount
    return totals, unknown


def batch_cost_breakdown(batch):
    """Report where one batch's input cost went, and what it has not reached.

    Provisional and final figures are never added together here. A batch is
    wholly one or the other — finality is a property of its output finalization —
    so exactly one of `provisional_total` and `final_total` carries a number and
    the other is null. A caller cannot combine them by accident because there is
    never anything in both.

    Two currencies are kept apart the same way. `currencies` carries one set of
    buckets per currency the batch actually drew on, and where there is more
    than one there is no single figure to state: `currency_code`, the two
    totals and every bucket go null with `mixed_currency` saying why, exactly
    as `unknown_cost` says why an amount is missing. A batch bought in one
    currency — every batch in most workspaces — reads as it always has.
    """
    rows = _loaded_allocations(batch)
    dispositions = plant_dispositions(batch)
    by_currency, unknown = _totals(rows, dispositions, cohort_dispositions(batch))
    frozen = is_frozen(batch)
    codes = sorted(by_currency)
    currency = stated_currency(codes, batch.workspace.currency_code)
    totals = None if currency is None else by_currency.get(
        currency, {bucket: Decimal('0') for bucket in VALUE_BUCKETS},
    )
    allocated = None if totals is None else quantize_money(sum(totals.values(), Decimal('0')))
    plants = {}
    for row in rows:
        if row.target_type != TargetType.SPECIFIC_PLANT or row.amount is None:
            continue
        held = plants.setdefault(row.specific_plant_id, {})
        held[row.currency_code] = held.get(row.currency_code, Decimal('0')) + row.amount
    last_run = CostAllocationRun.objects.filter(batch=batch).order_by('created', 'pk').last()
    return {
        'batch': batch.pk,
        'code': batch.code,
        'status': batch.status,
        'currency_code': currency,
        'mixed_currency': len(codes) > 1,
        'provisional': not frozen,
        'output_finalized_at': batch.output_finalized_at,
        'unknown_cost': unknown,
        'provisional_total': None if frozen or allocated is None else f'{allocated:f}',
        'final_total': f'{allocated:f}' if frozen and allocated is not None else None,
        'totals': {
            bucket: None if totals is None else f'{quantize_money(totals[bucket]):f}'
            for bucket in VALUE_BUCKETS
        },
        'currencies': [
            {
                'currency_code': code,
                'amount': f'{quantize_money(sum(by_currency[code].values(), Decimal("0"))):f}',
                'totals': {
                    bucket: f'{quantize_money(value):f}'
                    for bucket, value in by_currency[code].items()
                },
            }
            for code in codes
        ],
        'layers': [_layer_row(row) for row in rows],
        'plants': [
            {
                'plant': plant_id,
                'cost': None if len(held) > 1 else f'{quantize_money(next(iter(held.values()))):f}',
                'currency_code': None if len(held) > 1 else next(iter(held)),
                'state': dispositions.get(plant_id, (None, None))[0],
                'disposition': dispositions.get(plant_id, (None, None))[1],
            }
            for plant_id, held in sorted(plants.items())
        ],
        'last_run': None if last_run is None else {
            'run': last_run.pk,
            'trigger': last_run.trigger,
            'reason': last_run.reason,
            'posted_count': last_run.posted_count,
            'reversed_count': last_run.reversed_count,
            'created': last_run.created,
        },
    }


def plant_cost_breakdown(plant):
    """Report what one seedling cost, from which inputs, and where it went.

    A plant raised on inputs bought in two currencies has no single committed
    value, so `provisional_value`, `final_value` and both sale projections go
    null with `mixed_currency` saying why, and `currencies` lists what it cost
    in each. The projections go null for a plant whose committed cost is in one
    foreign currency too: the pending media and pot shares are projected in the
    workspace's own currency, and adding the two would total the same two
    currencies one step further along.
    """
    batch = ProductionBatch.objects.get(
        pk=plant.batch_id,
    )
    rows = list(
        CostAllocation.objects
        .filter(specific_plant=plant, reversal_of__isnull=True, reversal__isnull=True)
        .select_related(
            'application_line__lot__item',
            'sowing_posting__movement__lot__item',
            'generation_residual__lot__item',
            'container_unit__source_lot__item',
            'container_dispatch__stock_movement__lot__item',
        )
        .order_by('pk')
    )
    held, unknown = held_by_currency(rows)
    codes = sorted(held)
    currency = stated_currency(codes, batch.workspace.currency_code)
    value = None if currency is None else held.get(currency, Decimal('0'))
    state, disposition = plant_dispositions(batch).get(plant.pk, (None, None))
    frozen = is_frozen(batch)
    pending = plant_pending_cost(plant)
    projectable = value is not None and currency == batch.workspace.currency_code
    return {
        **pending,
        **plant_sale_totals(value or Decimal('0'), unknown or not projectable, pending),
        'plant': plant.pk,
        'batch': batch.pk,
        'currency_code': currency,
        'mixed_currency': len(codes) > 1,
        'currencies': currency_amounts(held),
        'provisional': not frozen,
        'unknown_cost': unknown,
        'state': state,
        'disposition': disposition,
        'provisional_value': None if frozen or value is None else f'{quantize_money(value):f}',
        'final_value': f'{quantize_money(value):f}' if frozen and value is not None else None,
        'layers': [_layer_row(row) for row in rows],
    }


def cohort_cost_breakdown(cohort):
    """Report what one anonymous block cost, and what one unit of it cost.

    Stock still standing and stock already sold are read together on purpose.
    Cost divides evenly per unit across a cohort — that is what managing it as
    a count means — so dividing the whole block's layers by the whole block's
    units gives the same figure before and after part of it is dispatched. A
    sale valued against the stock left behind would drift upward every time
    another order shipped.

    The per-unit figure is what a dispatch out of the cohort is charged at:
    the cohort holds a count, so there is nothing more exact to value it with.
    Where a recount found units that carried no cost, the two halves no longer
    hold the same per unit, so the block's layers are read per unit of weight
    and the figure is what one unit standing there now carries;
    `costing.cohort_weights` says why.

    A block fed from two currencies has no unit value at all. Dividing a total
    that added them would hand every draw on the block a per-unit figure that
    is not money in any currency, so `unit_value` and the block's value go null
    with `mixed_currency` saying why, and `currencies` lists each side.
    """
    rows = list(
        CostAllocation.objects
        .filter(
            plant_cohort=cohort,
            target_type__in=COHORT_TARGETS,
            reversal_of__isnull=True,
            reversal__isnull=True,
        )
        .select_related(
            'application_line__lot__item',
            'sowing_posting__movement__lot__item',
            'generation_residual__lot__item',
            'container_unit__source_lot__item',
            'container_dispatch__stock_movement__lot__item',
        )
        .order_by('pk')
    )
    held, unknown = held_by_currency(rows)
    codes = sorted(held)
    currency = stated_currency(codes, cohort.workspace.currency_code)
    value = None if currency is None else quantize_money(held.get(currency, Decimal('0')))
    sold = sold_cohort_quantities(cohort.batch).get(cohort.pk, 0)
    units = cohort.quantity + sold
    weights = cohort_weights(cohort.batch)
    weight = weights.weigh('cohort', cohort.pk, cohort.quantity) + weights.weigh('cohort_sale', cohort.pk, sold)
    per_unit = weights.weigh('cohort', cohort.pk, 1) if cohort.quantity else weights.weigh('cohort_sale', cohort.pk, 1)
    frozen = is_frozen(cohort.batch)
    return {
        'cohort': cohort.pk,
        'batch': cohort.batch_id,
        'currency_code': currency,
        'mixed_currency': len(codes) > 1,
        'currencies': currency_amounts(held),
        'provisional': not frozen,
        'unknown_cost': unknown,
        'units': units,
        'provisional_value': None if frozen or value is None else f'{value:f}',
        'final_value': f'{value:f}' if frozen and value is not None else None,
        'unit_value': None if not units or value is None else f'{value * per_unit / Decimal(weight):f}',
        'layers': [_layer_row(row) for row in rows],
    }
