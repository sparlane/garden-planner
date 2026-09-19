"""Withdrawing a germination that was recorded but never happened.

The correction is a fact of its own rather than a deletion: the germination is
struck out, the plant stops counting as something that came up, and the cost it
was holding goes back to the cell. `observed_only` and `germination_withdrawals`
in `plantings.lifecycle` are how every other figure reads the result.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .lifecycle import (
    EventType,
    OutcomeRequest,
    _close_active_location,
    _create_event,
    _plant_batch,
    _plant_events,
    _require_chronology,
    _require_reason,
    germination_correction,
)
from .models import SpecificPlant


#: What a plant may still be attached to and have been imagined. Each of these
#: is either the germination's own paperwork or a ledger that reverses rather
#: than deletes, so none of them is somebody having treated the plant as real.
#:
#: Every other relation blocks, and the blocking set is derived from the model
#: rather than listed, so a relation added later denies the withdrawal until
#: somebody decides it belongs here. That is the safe default: admitting a new
#: way of using a plant would silently let a withdrawal strand it.
#:
#: `_meta.related_objects` holds reverse relations only, so the derivation
#: cannot see a forward key on `SpecificPlant`. Each of those is weighed by hand
#: in `_require_withdrawable`, against one question: is the germination the fact
#: that created the plant? For `cell_planting` it is; that is the seedling this
#: correction exists for. `batch` is carried by every plant, so it says nothing
#: about origin. `promoted_from_cohort` says the plant came up as part of a
#: cohort, so it is refused. `garden_planting` on a plant individualized from a
#: direct-sown crop has the same shape and is not yet refused (task 151). A
#: forward key added later has to be weighed here too.
WITHDRAWAL_KEEPS = frozenset({
    'lifecycle_events',
    'locations',
    'bulk_operation_results',
    'cost_allocations',
})


def withdrawal_blocking_relations(keeps=None):
    """Return the relations whose rows deny that a plant was never observed."""
    keeps = WITHDRAWAL_KEEPS if keeps is None else keeps
    return tuple(sorted(
        relation.get_accessor_name()
        for relation in SpecificPlant._meta.related_objects
        if relation.get_accessor_name() not in keeps
    ))


def _require_withdrawable(plant):
    """Return the germination this plant may withdraw, or explain why it may not."""
    # A promotion's germination is the cohort's count carried onto an identity,
    # and the promotion took the unit out of the cohort. Withdrawing it would
    # leave that unit recorded nowhere, and no relation checked below can see
    # it, because the origin is a forward key. It is asked first so the answer
    # does not depend on whatever else the promotion happened to record.
    if plant.promoted_from_cohort_id is not None:
        raise ValidationError({
            'plant': (
                'This plant was promoted from cohort '
                f'{plant.promoted_from_cohort_id}. It came up as part of that '
                'cohort, so it has no germination of its own to withdraw. A '
                'promotion cannot be reversed yet. Do not recount the cohort to '
                'put the unit back, because the plant would still be counted '
                'beside it.'
            ),
        })
    events = _plant_events(plant)
    germination = next(
        (event for event in events if event.event_type == EventType.GERMINATED),
        None,
    )
    if germination is None:
        raise ValidationError({
            'plant': 'No germination was ever recorded for this plant.',
        })
    if germination_correction(events) is not None:
        raise ValidationError({
            'plant': "This plant's germination has already been withdrawn.",
        })
    recorded = sorted({
        EventType(event.event_type).label.lower()
        for event in events
        if event.pk != germination.pk
    })
    if recorded:
        raise ValidationError({
            'plant': (
                'This plant has been worked on since it came up '
                f'({", ".join(recorded)}), so its germination is not the only '
                'thing that would have to be untrue. Record what actually '
                'happened to it instead.'
            ),
        })
    # Moving a plant records no lifecycle event, so the events alone would let
    # a seedling somebody carried to a garden square be called imaginary. The
    # one placement germination itself created is the only one allowed, and it
    # has to be the one the plant is still standing in.
    locations = list(plant.locations.all())
    if len(locations) > 1 or any(location.ended is not None for location in locations):
        raise ValidationError({
            'plant': (
                'This plant has been moved since it came up, so somebody '
                'handled it. Record what actually happened to it instead.'
            ),
        })
    attached = [
        name for name in withdrawal_blocking_relations()
        if getattr(plant, name).exists()
    ]
    if attached:
        raise ValidationError({
            'plant': (
                'This plant is referred to by records that assume it existed '
                f'({", ".join(attached)}). Resolve those first.'
            ),
        })
    return germination


def _withdraw_one(plant, user, reason, occurred_at):
    """Append one plant's withdrawal, leaving its batch to the caller."""
    germination = _require_withdrawable(plant)
    _require_chronology(_plant_events(plant), occurred_at)
    _close_active_location(plant, occurred_at)
    return _create_event(
        plant,
        user,
        OutcomeRequest(EventType.CORRECTED, occurred_at=occurred_at, reason=reason),
        reversal_of=germination,
    )


@transaction.atomic
def withdraw_germination(plant, user, reason, occurred_at=None):
    """Withdraw a germination that was recorded but never happened.

    This is the correction for a seedling that was entered twice, or entered
    against a tray the operator was not looking at. It is not the way to record
    one that came up and then died: that is `failed`, and the germination rate
    is right to keep counting it.

    The plant row stays, because the audit that created it and the cost layers
    that named it are both immutable and both still refer to it. What changes
    is that every figure derived from what came up stops counting it, the cost
    it was holding is reallocated back to its cell, and its state reads
    `withdrawn` rather than a seedling growing somewhere nobody can find.
    """
    return withdraw_germinations([plant.pk], user, reason, occurred_at)[0]


@transaction.atomic
def withdraw_germinations(plant_ids, user, reason, occurred_at=None):
    """Withdraw a selection of germinations as one all-or-nothing correction.

    The tray screen's pagination bug put whole fills in twice, so a selection
    rather than a plant is the unit an operator actually has to correct. Every
    plant is withdrawn or none is, for the reason the bulk outcomes are: half a
    correction leaves a germination figure that is wrong in a new way.

    Each affected batch is locked once, in key order, and reallocated once
    after the last withdrawal, so a hundred and forty-four corrections cost one
    reallocation rather than a hundred and forty-four of them.
    """
    # Withdrawal calls back into costing, which reads germination balances.
    from costing.models import CostAllocationRun  # pylint: disable=import-outside-toplevel,cyclic-import
    from costing.services import reallocate_batches  # pylint: disable=import-outside-toplevel,cyclic-import
    from .batches import lock_batch_with_plants  # pylint: disable=import-outside-toplevel,cyclic-import

    _require_reason(reason)
    wanted = sorted(set(plant_ids))
    if not wanted:
        raise ValidationError({'plants': 'Select at least one plant.'})
    # Read unlocked first, only to learn which batches are involved. Locking
    # the selection here instead would take the plants a caller happens to
    # name and then reach for the rest through `lock_batch_with_plants`, which
    # is the subset deadlock that function exists to avoid.
    batches = {}
    for plant in SpecificPlant.objects.filter(pk__in=wanted).order_by('pk'):
        batch = _plant_batch(plant)
        batches[batch.pk] = batch
    locked = [lock_batch_with_plants(batches[key]) for key in sorted(batches)]
    plants = list(
        SpecificPlant.objects.filter(pk__in=wanted).order_by('pk')
    )
    if len(plants) != len(wanted):
        raise ValidationError({'plants': 'One or more plants are unavailable.'})
    occurred_at = occurred_at or timezone.now()
    corrections = [
        _withdraw_one(plant, user, reason, occurred_at)
        for plant in plants
    ]
    reallocate_batches(
        locked, user, CostAllocationRun.Trigger.GERMINATION_WITHDRAWN,
    )
    return corrections
