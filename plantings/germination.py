"""Closing a sowing's germination, and everything that follows from it.

A germination count against a sown quantity is a running total. Nothing in the
sowing itself says whether it is still climbing, so three seedlings from ten
seeds reads the same on day seven as it does when the tray is finished — and
the re-sow decision is exactly the judgement that difference decides.

`SowingGerminationClosure` is the fact that ends that ambiguity. This module
owns writing it, reading what it means, and the two figures a closed sowing
carries:

- the **snapshot**, stored on the closure, is what the operator decided and is
  never rewritten;
- the **current** figures are derived from the plants that exist right now, and
  are what cost allocation and the reports work from.

**The late-germination policy.** A seedling that comes up after the close is a
real event, so it is recorded as an ordinary germination and never rejected.
Because it contradicts a stated judgement it requires a reason, which is kept
on the plant's `GERMINATED` lifecycle event where the rest of that plant's
history lives. The closure is left standing: it remains true that somebody
declared the sowing finished on that date with that count, and the current
figures move on their own as the late seedling is counted. Cost follows the
same way, because `costing` reads the current remainder rather than the
snapshot, so the share the late seedling earns comes back out of production
loss on the next reallocation.

Reopening is the other half of the decision, and it means something different:
the close itself was a mistake — the wrong tray, or a count taken before an
observation had been entered. It needs a reason too, and it puts the sowing
back into the provisional state it was in before.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .batches import lock_batch_with_plants
from .models import (
    SeedTrayCellPlanting,
    SeedTrayPlanting,
    SowingGerminationClosure,
    SpecificPlant,
)


def _actor(user):
    """Return the user to credit, or None for an unauthenticated caller."""
    return user if user is not None and user.is_authenticated else None


def _require_reason(reason, field='reason'):
    """Reject an audit-critical action without a stated reason."""
    if not reason or not reason.strip():
        raise ValidationError({field: 'A reason is required.'})


def current_closure(sowing):
    """Return the closure that still stands for this sowing, or None."""
    return (
        SowingGerminationClosure.objects
        .filter(sowing=sowing, reopened_at__isnull=True)
        .first()
    )


def is_closed(sowing):
    """Return whether this sowing has been declared finished germinating."""
    return SowingGerminationClosure.objects.filter(
        sowing=sowing, reopened_at__isnull=True,
    ).exists()


def sown_into_cells(sowing):
    """Return the seed this sowing actually placed in cells.

    Seed drawn from the packet but never allocated to a cell is not part of the
    germination question — it reached no cell and could not come up — which is
    the same boundary `costing.allocation.seed_shares` draws when it leaves the
    unplaced remainder in the batch pool.
    """
    return sum(
        SeedTrayCellPlanting.objects
        .filter(seed_tray_planting=sowing)
        .values_list('quantity', flat=True)
    )


def observed_plants(sowing):
    """Return how many seedlings this sowing has produced, ever.

    Counted from every plant raised on its cells, including plants that later
    failed or were sold: germination rate is a question about what came up, not
    about what survived to be counted later.
    """
    return SpecificPlant.objects.filter(
        cell_planting__seed_tray_planting=sowing,
    ).count()


def ungerminated_by_cell(sowing):
    """Return each cell's seed that produced nothing, or {} while open.

    Read by `costing.sources` to retire the closed remainder's cost, so it is
    deliberately derived from the plants that exist now rather than from the
    closure's snapshot: a late seedling reclaims its share without anybody
    having to correct a record.

    A cell can produce more plants than it was sown — one multigerm cluster is
    three seedlings — so a cell that over-delivered has no remainder rather
    than a negative one.
    """
    if not is_closed(sowing):
        return {}
    rows = (
        SeedTrayCellPlanting.objects
        .filter(seed_tray_planting=sowing)
        .annotate(observed=Count('specific_plants'))
        .values_list('cell_id', 'quantity', 'observed')
    )
    return {
        cell_id: quantity - observed
        for cell_id, quantity, observed in rows
        if quantity > observed
    }


def _summary(sown, observed, closure, late):
    """Assemble one germination summary from figures already counted.

    `rate` is a `Decimal` so it can be rendered at a fixed scale, and is None
    rather than zero when nothing was sown into cells, because no seed placed
    is an absent measurement and not a rate of nought.
    """
    return {
        'sown_quantity': sown,
        'observed_count': observed,
        'ungerminated': max(sown - observed, 0),
        'rate': (Decimal(observed) / Decimal(sown)) if sown else None,
        'provisional': closure is None,
        'closed_at': closure.closed_at if closure else None,
        'closed_observed_count': closure.observed_count if closure else None,
        'closed_ungerminated': closure.ungerminated if closure else None,
        'loss_cause': closure.loss_cause if closure else '',
        'late_germinations': late,
        'closure': closure.pk if closure else None,
    }


def germination_summary(sowing, closure=None):
    """Describe one sowing's germination as a screen or report needs it."""
    closure = closure if closure is not None else current_closure(sowing)
    late = 0
    if closure is not None:
        late = SpecificPlant.objects.filter(
            cell_planting__seed_tray_planting=sowing,
            germinated__gt=closure.closed_at,
        ).count()
    return _summary(sown_into_cells(sowing), observed_plants(sowing), closure, late)


def germination_summaries(sowings):
    """Describe several sowings' germination in a fixed number of queries.

    Every list that shows a germination count also has to say whether it is
    final, so the summary is needed once per row. Reading it a row at a time
    would put four queries behind each tray on the sowing screen.
    """
    ids = [sowing.pk for sowing in sowings]
    if not ids:
        return {}
    sown = dict(
        SeedTrayCellPlanting.objects
        .filter(seed_tray_planting_id__in=ids)
        .values('seed_tray_planting')
        .annotate(total=Sum('quantity'))
        .values_list('seed_tray_planting', 'total')
    )
    observed = dict(
        SpecificPlant.objects
        .filter(cell_planting__seed_tray_planting_id__in=ids)
        .values('cell_planting__seed_tray_planting')
        .annotate(total=Count('pk'))
        .values_list('cell_planting__seed_tray_planting', 'total')
    )
    closures = {
        closure.sowing_id: closure
        for closure in SowingGerminationClosure.objects.filter(
            sowing_id__in=ids, reopened_at__isnull=True,
        )
    }
    late = {}
    if closures:
        # One query rather than one per closure: each sowing was closed at its
        # own moment, so the condition carries that moment with it.
        condition = Q(pk__in=[])
        for sowing_id, closure in closures.items():
            condition |= Q(
                cell_planting__seed_tray_planting_id=sowing_id,
                germinated__gt=closure.closed_at,
            )
        late = dict(
            SpecificPlant.objects
            .filter(condition)
            .values('cell_planting__seed_tray_planting')
            .annotate(total=Count('pk'))
            .values_list('cell_planting__seed_tray_planting', 'total')
        )
    return {
        sowing_id: _summary(
            sown.get(sowing_id, 0),
            observed.get(sowing_id, 0),
            closures.get(sowing_id),
            late.get(sowing_id, 0),
        )
        for sowing_id in ids
    }


#: The scale germination rates are rendered at, matching the production
#: report's `output_rate` so the two figures can be compared digit for digit.
RATE_PLACES = 6


def summary_json(summary):
    """Return one already-built summary in a JSON-safe shape.

    The rate is rendered as a fixed-scale string for the same reason every
    other decimal in this API is: a float would arrive at the browser with
    artefacts the stored figures never had.
    """
    rate = summary['rate']
    return {
        **summary,
        'rate': None if rate is None else f'{rate:.{RATE_PLACES}f}',
    }


def germination_json(sowing, closure=None):
    """Return one sowing's germination summary in a JSON-safe shape."""
    return summary_json(germination_summary(sowing, closure))


def germination_json_map(sowings):
    """Return a JSON-safe summary per sowing, for a list that shows many."""
    return {
        sowing_id: summary_json(summary)
        for sowing_id, summary in germination_summaries(sowings).items()
    }


def validate_late_germination(cell_planting, reason):
    """Require a stated reason for a seedling that contradicts a close.

    Called from every path that creates a plant, so the policy holds whether
    one seedling was entered on the tray screen or forty through the bulk
    operation. A sowing that is still open imposes nothing.
    """
    if not is_closed(cell_planting.seed_tray_planting):
        return
    if not reason or not reason.strip():
        raise ValidationError({
            'reason': (
                'This sowing was declared finished germinating. Recording a '
                'late seedling needs a reason.'
            ),
        })


@transaction.atomic
def close_germination(sowing, user, *, closed_at=None, loss_cause='', reason=''):
    """Declare one sowing finished germinating and retire the remainder.

    The batch and its plants are locked first, in the order
    `lock_batch_with_plants` fixes, so a germination cannot be recorded between
    counting the seedlings and storing the count.
    """
    from costing.models import CostAllocationRun  # pylint: disable=import-outside-toplevel
    from costing.services import reallocate_batch  # pylint: disable=import-outside-toplevel

    sowing = SeedTrayPlanting.objects.select_for_update().get(pk=sowing.pk)
    batch = lock_batch_with_plants(sowing.batch)
    if is_closed(sowing):
        raise ValidationError({
            'sowing': 'This sowing has already been declared finished germinating.',
        })
    sown = sown_into_cells(sowing)
    if not sown:
        raise ValidationError({
            'sowing': 'A sowing with no seed in any cell has no germination to close.',
        })
    observed = observed_plants(sowing)
    remainder = max(sown - observed, 0)
    closure = SowingGerminationClosure(
        workspace=sowing.workspace,
        sowing=sowing,
        closed_at=closed_at or timezone.now(),
        sown_quantity=sown,
        observed_count=observed,
        ungerminated=remainder,
        loss_cause=loss_cause if remainder else '',
        reason=reason or '',
        created_by=_actor(user),
    )
    closure.full_clean()
    closure.save()
    reallocate_batch(batch, user, CostAllocationRun.Trigger.GERMINATION_CLOSED)
    return closure


@transaction.atomic
def reopen_germination(closure, user, reason):
    """Withdraw a close that should never have been recorded.

    Not the path for a late seedling — that is an ordinary germination with a
    reason, and leaves the close standing. This one says the sowing was not
    finished when somebody said it was, so the cost the close retired comes
    back onto the cells it came from.
    """
    from costing.models import CostAllocationRun  # pylint: disable=import-outside-toplevel
    from costing.services import reallocate_batch  # pylint: disable=import-outside-toplevel

    _require_reason(reason)
    closure = SowingGerminationClosure.objects.select_for_update().get(pk=closure.pk)
    if closure.reopened_at is not None:
        raise ValidationError({'closure': 'This close has already been withdrawn.'})
    batch = lock_batch_with_plants(closure.sowing.batch)
    closure.reopened_at = timezone.now()
    closure.reopened_reason = reason
    closure.reopened_by = _actor(user)
    closure.full_clean()
    closure.save(update_fields=['reopened_at', 'reopened_reason', 'reopened_by'])
    reallocate_batch(batch, user, CostAllocationRun.Trigger.GERMINATION_CLOSED)
    return closure
