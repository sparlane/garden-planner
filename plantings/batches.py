"""Transactional lifecycle services for shared production batches."""

# pylint: disable=duplicate-code

from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Sum
from django.utils import timezone

from .lifecycle import LifecycleState, is_final, lifecycle_summaries
from .models import (
    GardenRowDirectSowPlanting,
    GardenSquareDirectSowPlanting,
    Harvest,
    ProductionBatch,
    ProductionBatchTransition,
    SeedTrayPlanting,
    SpecificPlant,
    SpecificPlantLocation,
)


SOWING_MODELS = (
    GardenRowDirectSowPlanting,
    GardenSquareDirectSowPlanting,
    SeedTrayPlanting,
)

REOPEN_TARGETS = {
    ProductionBatch.Status.OUTPUT_FINALIZED: ProductionBatch.Status.ACTIVE,
    ProductionBatch.Status.COMPLETED: ProductionBatch.Status.OUTPUT_FINALIZED,
}

#: Lifecycle stamps cleared when a batch moves back to an earlier status.
SUPERSEDED_TIMESTAMPS = {
    ProductionBatch.Status.ACTIVE: ('output_finalized_at', 'completed_at', 'cancelled_at'),
    ProductionBatch.Status.OUTPUT_FINALIZED: ('completed_at', 'cancelled_at'),
    ProductionBatch.Status.PLANNED: (
        'actual_start',
        'output_finalized_at',
        'completed_at',
        'cancelled_at',
    ),
}


class BatchRequest(NamedTuple):
    """Caller intent for one new standalone production batch."""

    code: str
    variety: object
    planned_start: object = None
    notes: str = ''


def _sowing_querysets(batch):
    """Return one queryset of attached sowings per concrete planting model."""
    return [model.objects.filter(batch=batch) for model in SOWING_MODELS]


def batch_sowing_count(batch):
    """Return how many sowings of any kind belong to this batch."""
    return sum(queryset.count() for queryset in _sowing_querysets(batch))


def batch_seeds_sown(batch):
    """Return the total seeds or seed clusters sown into this batch."""
    total = 0
    for queryset in _sowing_querysets(batch):
        total += queryset.aggregate(total=Sum('quantity'))['total'] or 0
    return total


def batch_open_sowings(batch):
    """Return the labels and IDs of sowing activities still open."""
    open_sowings = []
    for queryset in _sowing_querysets(batch):
        for pk in queryset.filter(removed=False).order_by('pk').values_list('pk', flat=True):
            open_sowings.append(f'{queryset.model.__name__} #{pk}')
    return open_sowings


def batch_specific_plants(batch):
    """Return every individual plant observed from this batch's sowings."""
    return SpecificPlant.objects.filter(
        cell_planting__seed_tray_planting__batch=batch,
    )


def batch_plants_with_active_location(batch):
    """Return this batch's plants that currently occupy a tracked location.

    An `ended__isnull=True` join would also match plants with no location
    history at all, so the presence of an open interval is asserted directly.
    """
    open_location = SpecificPlantLocation.objects.filter(
        specific_plant=OuterRef('pk'),
        ended__isnull=True,
    )
    return batch_specific_plants(batch).filter(Exists(open_location))


def _batch_lifecycle_summaries(batch):
    """Return the derived lifecycle summary of every plant in this batch."""
    return lifecycle_summaries(
        batch_specific_plants(batch).order_by('pk').values_list('pk', flat=True),
    )


def batch_unresolved_plant_ids(batch):
    """Return the plants whose lifecycle has recorded no final outcome.

    An ended location records where a plant stopped being, not what became of
    it, so resolution comes from the lifecycle history alone.
    """
    return sorted(
        plant_id
        for plant_id, summary in _batch_lifecycle_summaries(batch).items()
        if not is_final(summary.state)
    )


def batch_final_outcome_count(batch):
    """Return how many of this batch's plants have a recorded final outcome."""
    return sum(
        1
        for summary in _batch_lifecycle_summaries(batch).values()
        if is_final(summary.state)
    )


def batch_posted_harvest_count(batch):
    """Return how many harvests still count as output from this batch."""
    return Harvest.objects.filter(
        batch=batch,
        status=Harvest.Status.POSTED,
    ).count()


def batch_lifecycle_counts(batch):
    """Return how many of this batch's plants sit in each derived state."""
    counts = {state.value: 0 for state in LifecycleState}
    for summary in _batch_lifecycle_summaries(batch).values():
        counts[summary.state] += 1
    return counts


def _record_transition(batch, previous_status, user, reason=''):
    """Append one immutable row describing a lifecycle change."""
    return ProductionBatchTransition.objects.create(
        batch=batch,
        previous_status=previous_status,
        new_status=batch.status,
        created_by=user if user is not None and user.is_authenticated else None,
        reason=reason,
    )


def lock_batch(batch):
    """Reload one batch under a row lock for a lifecycle change."""
    return ProductionBatch.objects.select_for_update().get(pk=batch.pk)


def _require_status(batch, allowed, action):
    """Reject a lifecycle action that its current status does not permit."""
    if batch.status not in allowed:
        raise ValidationError({
            'status': f'A {batch.get_status_display().lower()} batch cannot {action}.',
        })


def _require_reason(reason):
    """Reject an audit-critical action without a stated reason."""
    if not reason or not reason.strip():
        raise ValidationError({'reason': 'A reason is required.'})


def _clear_superseded(batch, status):
    """Blank the lifecycle stamps that a move back to `status` supersedes."""
    for field in SUPERSEDED_TIMESTAMPS.get(status, ()):
        setattr(batch, field, None)


@transaction.atomic
def create_batch(workspace, user, request):
    """Create one planned batch and record its opening transition."""
    batch = ProductionBatch(
        workspace=workspace,
        code=request.code,
        variety=request.variety,
        status=ProductionBatch.Status.PLANNED,
        planned_start=request.planned_start,
        notes=request.notes,
        created_by=user if user is not None and user.is_authenticated else None,
    )
    batch.save()
    _record_transition(batch, '', user, 'Batch created.')
    return batch


@transaction.atomic
def activate_batch(batch, user, actual_start=None, reason=''):
    """Start a planned batch at a supplied or current time."""
    batch = lock_batch(batch)
    _require_status(batch, {ProductionBatch.Status.PLANNED}, 'be activated')
    previous_status = batch.status
    batch.status = ProductionBatch.Status.ACTIVE
    batch.actual_start = actual_start or timezone.now()
    batch.save()
    _record_transition(batch, previous_status, user, reason)
    return batch


@transaction.atomic
def create_and_activate_batch(workspace, user, request, actual_start=None):
    """Create and start one batch atomically for an inline sowing."""
    batch = create_batch(workspace, user, request)
    return activate_batch(
        batch,
        user,
        actual_start=actual_start,
        reason='Created with its first sowing.',
    )


@transaction.atomic
def finalize_batch_output(batch, user, reason=''):
    """Declare that no further seedlings will come from this batch."""
    batch = lock_batch(batch)
    _require_status(batch, {ProductionBatch.Status.ACTIVE}, 'finalize its output')
    if batch_sowing_count(batch) == 0:
        raise ValidationError({
            'detail': 'Record at least one sowing before finalizing output.',
        })
    open_sowings = batch_open_sowings(batch)
    if open_sowings:
        raise ValidationError({
            'detail': (
                'Close every sowing activity before finalizing output. '
                f'Still open: {", ".join(open_sowings)}.'
            ),
        })
    previous_status = batch.status
    batch.status = ProductionBatch.Status.OUTPUT_FINALIZED
    batch.output_finalized_at = timezone.now()
    batch.save()
    _record_transition(batch, previous_status, user, reason)
    return batch


@transaction.atomic
def complete_batch(batch, user, reason=''):
    """Complete a batch once every output has a final disposition."""
    batch = lock_batch(batch)
    _require_status(batch, {ProductionBatch.Status.OUTPUT_FINALIZED}, 'be completed')
    unresolved = batch_unresolved_plant_ids(batch)
    if unresolved:
        raise ValidationError({
            'detail': (
                f'{len(unresolved)} observed plants have no final disposition: '
                f'{unresolved}. Record their outcomes before completing this batch.'
            ),
        })
    previous_status = batch.status
    batch.status = ProductionBatch.Status.COMPLETED
    batch.completed_at = timezone.now()
    batch.save()
    _record_transition(batch, previous_status, user, reason)
    return batch


@transaction.atomic
def cancel_batch(batch, user, reason):
    """Abandon a batch that produced no tracked individual outputs."""
    _require_reason(reason)
    batch = lock_batch(batch)
    _require_status(
        batch,
        {ProductionBatch.Status.PLANNED, ProductionBatch.Status.ACTIVE},
        'be cancelled',
    )
    if batch.status == ProductionBatch.Status.ACTIVE:
        open_sowings = batch_open_sowings(batch)
        if open_sowings:
            raise ValidationError({
                'detail': (
                    'Close every sowing activity before cancelling. '
                    f'Still open: {", ".join(open_sowings)}.'
                ),
            })
        observed = batch_specific_plants(batch).count()
        if observed:
            raise ValidationError({
                'detail': (
                    f'{observed} observed plants came from this batch, so it '
                    'produced output and cannot be cancelled.'
                ),
            })
        harvested = batch_posted_harvest_count(batch)
        if harvested:
            raise ValidationError({
                'detail': (
                    f'{harvested} harvests came from this batch, so it '
                    'produced output and cannot be cancelled.'
                ),
            })
    previous_status = batch.status
    batch.status = ProductionBatch.Status.CANCELLED
    batch.cancelled_at = timezone.now()
    batch.save()
    _record_transition(batch, previous_status, user, reason)
    return batch


def _reopen_target(batch):
    """Return the status one audited correction step back."""
    if batch.status == ProductionBatch.Status.CANCELLED:
        if batch.actual_start is None:
            return ProductionBatch.Status.PLANNED
        return ProductionBatch.Status.ACTIVE
    return REOPEN_TARGETS[batch.status]


@transaction.atomic
def reopen_batch(batch, user, reason):
    """Correct a lifecycle mistake by returning to the previous status."""
    _require_reason(reason)
    batch = lock_batch(batch)
    _require_status(batch, set(REOPEN_TARGETS) | {ProductionBatch.Status.CANCELLED}, 'be reopened')
    previous_status = batch.status
    batch.status = _reopen_target(batch)
    _clear_superseded(batch, batch.status)
    batch.save()
    _record_transition(batch, previous_status, user, reason)
    return batch


def validate_batch_for_sowing(batch, packet, workspace):
    """Reject attaching a sowing to an unusable or mismatched batch."""
    errors = {}
    if batch.workspace_id != workspace.pk:
        errors['batch'] = 'The batch belongs to a different workspace.'
    elif batch.status != ProductionBatch.Status.ACTIVE:
        errors['batch'] = 'Sowings can only join an active batch.'
    elif batch.variety_id != packet.seeds.plant_variety_id:
        errors['batch'] = 'The batch grows a different plant variety.'
    if errors:
        raise ValidationError(errors)


@transaction.atomic
def lock_batch_for_sowing(batch, packet, workspace):
    """Lock and revalidate a batch so work cannot attach after finalization."""
    locked = lock_batch(batch)
    validate_batch_for_sowing(locked, packet, workspace)
    return locked
