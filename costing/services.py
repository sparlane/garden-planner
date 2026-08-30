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

from applications.models import FACTOR_DECIMAL_PLACES
from inventory.ledger import quantize_money, quantize_quantity
from plantings.batches import lock_batch_with_plants
from plantings.lifecycle import LifecycleState, lifecycle_summaries
from plantings.models import ProductionBatch, SpecificPlant

from .allocation import combine, loss_shares, value_shares
from .models import CostAllocation, CostAllocationRun
from .sources import batch_sources


FACTOR_QUANTUM = Decimal(1).scaleb(-FACTOR_DECIMAL_PLACES)

TargetType = CostAllocation.TargetType

#: Target types holding cost that has not reached a seedling and still might.
#: Finalizing output is the statement that it will not, which is what turns
#: these into production loss.
UNRESOLVED_TARGETS = (TargetType.SEED_TRAY_CELL, TargetType.BATCH_POOL)

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
}

#: Every bucket a batch's value can sit in. `cogs` stays empty until tasks 44
#: and 45 introduce the orders that sell a plant; it is listed so a report never
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


def _frozen_plan(intended, stored):
    """Return the only two changes a finalized batch still admits.

    Retire what never reached a seedling, and cancel what an input reversal took
    back. A plant's frozen share is never re-divided: that is what finalizing
    output means, and reopening the batch is the audited way to undo it.

    A layer whose source has vanished from the intended set had its input
    reversed, so its cost has to come back out even though the batch is frozen —
    stock that returned to the shelf cannot still be sitting in a seedling.
    """
    live_sources = {
        (spec['source_type'], spec['source'].pk)
        for spec in intended.values()
    }

    def retired(row):
        """Return whether a frozen batch still has to cancel this layer."""
        if row.target_type in UNRESOLVED_TARGETS:
            return True
        if row.target_type == TargetType.PLANT_COHORT:
            key = _stored_key(row)
            return key not in intended or not _matches(row, intended[key])
        return (row.source_type, row.source_id) not in live_sources

    reverse = [row for row in stored.values() if retired(row)]
    return reverse, [spec for key, spec in intended.items() if key not in stored]


def _plan(batch):
    """Return the layers to reverse and the layers to post, without writing."""
    intended = intended_layers(batch)
    stored = {_stored_key(row): row for row in effective_allocations(batch)}
    if is_frozen(batch):
        return _frozen_plan(intended, stored)
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


def _bucket_of(row, dispositions):
    """Return which value bucket one layer belongs in."""
    if row.target_type == TargetType.SPECIFIC_PLANT:
        return dispositions.get(row.specific_plant_id, (None, 'plant_inventory'))[1]
    if row.target_type == TargetType.PRODUCTION_LOSS:
        return 'production_loss'
    if row.target_type == TargetType.UNATTRIBUTED:
        return 'unattributed'
    return 'unresolved'


def _source_reference(row):
    """Return the identifiers that tie one layer back to its origin."""
    line = row.application_line
    posting = row.sowing_posting
    residual = row.generation_residual
    lot = None
    if line is not None:
        lot = line.lot
    elif posting is not None:
        lot = posting.movement.lot
    elif residual is not None:
        lot = residual.lot
    return {
        'source_type': row.source_type,
        'source': row.source_id,
        'application': line.application_id if line is not None else None,
        'application_line': line.pk if line is not None else None,
        'sowing_posting': posting.pk if posting is not None else None,
        'generation_residual': residual.pk if residual is not None else None,
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
        )
        .order_by('pk')
    )


def _totals(rows, dispositions):
    """Total each value bucket, reporting unknown cost rather than zero."""
    totals = {bucket: Decimal('0') for bucket in VALUE_BUCKETS}
    unknown = False
    for row in rows:
        if row.amount is None:
            unknown = True
            continue
        totals[_bucket_of(row, dispositions)] += row.amount
    return totals, unknown


def batch_cost_breakdown(batch):
    """Report where one batch's input cost went, and what it has not reached.

    Provisional and final figures are never added together here. A batch is
    wholly one or the other — finality is a property of its output finalization —
    so exactly one of `provisional_total` and `final_total` carries a number and
    the other is null. A caller cannot combine them by accident because there is
    never anything in both.
    """
    rows = _loaded_allocations(batch)
    dispositions = plant_dispositions(batch)
    totals, unknown = _totals(rows, dispositions)
    frozen = is_frozen(batch)
    allocated = sum(totals.values(), Decimal('0'))
    plants = {}
    for row in rows:
        if row.target_type != TargetType.SPECIFIC_PLANT or row.amount is None:
            continue
        plants[row.specific_plant_id] = plants.get(row.specific_plant_id, Decimal('0')) + row.amount
    last_run = CostAllocationRun.objects.filter(batch=batch).order_by('created', 'pk').last()
    return {
        'batch': batch.pk,
        'code': batch.code,
        'status': batch.status,
        'currency_code': batch.workspace.currency_code,
        'provisional': not frozen,
        'output_finalized_at': batch.output_finalized_at,
        'unknown_cost': unknown,
        'provisional_total': None if frozen else f'{quantize_money(allocated):f}',
        'final_total': f'{quantize_money(allocated):f}' if frozen else None,
        'totals': {
            bucket: f'{quantize_money(value):f}'
            for bucket, value in totals.items()
        },
        'layers': [_layer_row(row) for row in rows],
        'plants': [
            {
                'plant': plant_id,
                'cost': f'{quantize_money(cost):f}',
                'state': dispositions.get(plant_id, (None, None))[0],
                'disposition': dispositions.get(plant_id, (None, None))[1],
            }
            for plant_id, cost in sorted(plants.items())
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
    """Report what one seedling cost, from which inputs, and where it went."""
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
        )
        .order_by('pk')
    )
    known = [row.amount for row in rows if row.amount is not None]
    value = sum(known, Decimal('0'))
    state, disposition = plant_dispositions(batch).get(plant.pk, (None, None))
    frozen = is_frozen(batch)
    return {
        'plant': plant.pk,
        'batch': batch.pk,
        'currency_code': batch.workspace.currency_code,
        'provisional': not frozen,
        'unknown_cost': len(known) != len(rows),
        'state': state,
        'disposition': disposition,
        'provisional_value': None if frozen else f'{quantize_money(value):f}',
        'final_value': f'{quantize_money(value):f}' if frozen else None,
        'layers': [_layer_row(row) for row in rows],
    }
