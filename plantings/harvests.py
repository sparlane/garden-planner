"""Transactional services for recording and reversing crop harvests.

A harvest is posted the moment it is recorded, so there is no draft to assemble
and no post step. A mistake is corrected by reversing the record, which keeps
the original visible while excluding its quantity from every total.

Harvesting is kept separate from ending a plant. Many crops are picked
repeatedly, so recording a harvest changes no plant's lifecycle unless the
caller explicitly says this harvest finished the plants it took.
"""

# pylint: disable=duplicate-code

from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from inventory.ledger import quantize_quantity

from .batches import batch_specific_plants, lock_batch
from .lifecycle import EventType, OutcomeRequest, record_bulk_outcome
from .models import (
    Harvest,
    HarvestPlant,
    PlantLifecycleEvent,
    ProductionBatch,
    SpecificPlant,
)


#: Batch statuses that may still yield a crop. `output_finalized` declares that
#: no further seedlings will come from the batch, not that no further fruit
#: will; `planned` has sown nothing, and `cancelled` has declared it produced
#: nothing at all.
HARVESTABLE_STATUSES = {
    ProductionBatch.Status.ACTIVE,
    ProductionBatch.Status.OUTPUT_FINALIZED,
    ProductionBatch.Status.COMPLETED,
}


class HarvestRequest(NamedTuple):
    """Caller intent for one recorded harvest."""

    batch: object
    harvested_at: object
    quantity: object
    unit_code: str
    garden_square: object = None
    garden_row: object = None
    quality_rating: object = None
    grade: str = Harvest.Grade.UNGRADED
    notes: str = ''
    plant_ids: tuple = ()
    finish_plants: bool = False
    finish_reason: str = ''


def _require_reason(reason):
    """Reject an audit-critical action without a stated reason."""
    if not reason or not reason.strip():
        raise ValidationError({'reason': 'A reason is required.'})


def _require_harvestable(batch):
    """Reject a harvest from a batch that has not grown anything yet."""
    if batch.status not in HARVESTABLE_STATUSES:
        raise ValidationError({
            'batch': (
                f'A {batch.get_status_display().lower()} batch cannot be '
                'harvested.'
            ),
        })


def _require_within_batch(batch, harvested_at):
    """Reject a harvest taken before its batch started growing."""
    if batch.actual_start is not None and harvested_at < batch.actual_start:
        raise ValidationError({
            'harvested_at': 'A harvest cannot predate the start of its batch.',
        })


def _resolve_plants(batch, plant_ids):
    """Return the named plants under a row lock, in primary-key order.

    Membership is checked before the lock is taken because a plant's batch is
    reached through links that never move: a sowing cannot change batches and a
    plant cannot change cell allocations, so no concurrent write can make a
    plant belong to a different crop while this runs.
    """
    wanted = sorted(set(plant_ids))
    if not wanted:
        return []
    known = set(
        batch_specific_plants(batch)
        .filter(pk__in=wanted)
        .values_list('pk', flat=True)
    )
    missing = [plant_id for plant_id in wanted if plant_id not in known]
    if missing:
        raise ValidationError({
            'plants': (
                f'These plants did not come from batch {batch.code}: {missing}.'
            ),
        })
    return list(
        SpecificPlant.objects
        .select_for_update()
        .filter(pk__in=wanted)
        .order_by('pk')
    )


def _finish_plants(harvest, plants, user, reason):
    """End the cultivation of every plant this harvest took.

    The reference ties each appended fact back to the harvest that caused it,
    which is what lets the API report whether a harvest resolved any plants.
    """
    if not plants:
        raise ValidationError({
            'plants': 'Select the plants this harvest finished.',
        })
    return record_bulk_outcome(
        [plant.pk for plant in plants],
        user,
        OutcomeRequest(
            EventType.HARVEST_FINISHED,
            occurred_at=harvest.harvested_at,
            reason=reason,
            reference=f'harvest:{harvest.pk}',
        ),
    )


@transaction.atomic
def record_harvest(workspace, user, request):
    """Post one harvest and, when asked, finish the plants it took.

    Plants are locked in primary-key order before the batch is locked, and that
    ordering is load-bearing. Recording any plant outcome writes a lifecycle
    event carrying a batch reference, so it holds the plant while the database
    takes a key-share lock on the batch row. Taking the batch first here would
    close the cycle and deadlock the two writers against each other.
    """
    plants = _resolve_plants(request.batch, request.plant_ids)
    batch = lock_batch(request.batch)
    _require_harvestable(batch)
    _require_within_batch(batch, request.harvested_at)
    harvest = Harvest(
        workspace=workspace,
        batch=batch,
        harvested_at=request.harvested_at,
        quantity=quantize_quantity(request.quantity),
        unit_code=request.unit_code,
        garden_square=request.garden_square,
        garden_row=request.garden_row,
        quality_rating=request.quality_rating,
        grade=request.grade,
        notes=request.notes,
        status=Harvest.Status.POSTED,
        posted_at=timezone.now(),
        created_by=user if user is not None and user.is_authenticated else None,
    )
    harvest.save()
    for plant in plants:
        HarvestPlant.objects.create(harvest=harvest, plant=plant)
    events = []
    if request.finish_plants:
        events = _finish_plants(harvest, plants, user, request.finish_reason)
    return harvest, events


@transaction.atomic
def reverse_harvest(harvest, user, reason):
    """Exclude a mistaken harvest from totals while keeping it on file.

    Reversal is a status change on the original row rather than a compensating
    negative harvest, because there is no ledger to keep in balance and a second
    row would only be one more thing every report had to filter out.

    The lifecycle events a harvest created are deliberately left alone. A
    finished plant's location was closed when the fact was recorded, and where a
    plant has been remains true; correcting a plant is its own decision, made
    through that plant's `reverse-event` action.
    """
    _require_reason(reason)
    harvest = Harvest.objects.select_for_update().get(pk=harvest.pk)
    if harvest.status != Harvest.Status.POSTED:
        raise ValidationError({'status': 'That harvest is already reversed.'})
    Harvest.objects.filter(pk=harvest.pk, status=Harvest.Status.POSTED).update(
        status=Harvest.Status.REVERSED,
        reversed_at=timezone.now(),
        reverse_reason=reason.strip(),
        reversed_by=user if user is not None and user.is_authenticated else None,
    )
    harvest.refresh_from_db()
    return harvest


def harvest_finished_plant_ids(harvest):
    """Return the plants this harvest resolved, if it finished any.

    Derived from the recorded facts rather than stored, so a later correction to
    one plant is reflected here without a second write.
    """
    return sorted(
        PlantLifecycleEvent.objects.filter(
            event_type=EventType.HARVEST_FINISHED,
            reference=f'harvest:{harvest.pk}',
        ).values_list('plant_id', flat=True)
    )
