"""How a live hold on stock is found, and how one ends.

These sit below `sales.services` because the lifecycle side needs them too: a
plant that dies under a hold has to end that hold in the same transaction that
records the death (task 125). `plantings.lifecycle` imports this module, and
`sales.services` imports `plantings.lifecycle`, so anything here may read
models only. Putting the release here rather than deferring an import of
`sales.services` is the "shared primitive below both callers" that
`docs/dependencies.md` asks for before a callback.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import ReservationEvent, SalesOrder, SalesOrderAllocation


#: The statuses that hold stock away from anybody else. A pending selection is
#: tentative by design and warns rather than blocks, exactly as it does for a
#: plant somebody else has put in a draft.
HOLDING_STATUSES = (SalesOrderAllocation.Status.RESERVED,)


def record_reservation_event(allocation, event_type, user, reason=''):
    """Append one reservation fact at the service action's current time."""
    return ReservationEvent.objects.create(
        allocation=allocation,
        event_type=event_type,
        occurred_at=timezone.now(),
        reason=reason.strip(),
        created_by=user,
    )


@transaction.atomic
def close_reservations(order, user, allocation_ids, action, reason=''):
    """Release or explicitly expire selected unfulfilled reservations."""
    statuses = {
        'release': (SalesOrderAllocation.Status.RELEASED, ReservationEvent.EventType.RELEASED),
        'expire': (SalesOrderAllocation.Status.EXPIRED, ReservationEvent.EventType.EXPIRED),
        'cancel': (SalesOrderAllocation.Status.RELEASED, ReservationEvent.EventType.CANCELLED),
    }
    if action not in statuses:
        raise ValueError('Unknown reservation closing action.')
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    allocations = list(
        SalesOrderAllocation.objects.select_for_update()
        .filter(line__order=order, status=SalesOrderAllocation.Status.RESERVED, pk__in=allocation_ids)
        .order_by('pk')
    )
    if len(allocations) != len(set(allocation_ids)):
        raise ValidationError({'allocations': 'One or more active reservations are unavailable.'})
    if action == 'expire':
        not_due = [
            allocation.pk for allocation in allocations
            if allocation.expires_at is None or allocation.expires_at > timezone.now()
        ]
        if not_due:
            raise ValidationError({'allocations': f'Reservations are not expired: {not_due}.'})
    next_status, event_type = statuses[action]
    for allocation in allocations:
        SalesOrderAllocation.objects.filter(pk=allocation.pk).update(status=next_status, updated=timezone.now())
        allocation.status = next_status
        record_reservation_event(allocation, event_type, user, reason)
    return allocations


def plant_holds(plant_ids):
    """Return the live holds standing against these plants, oldest first.

    A plant carries at most one (`sales_one_active_plant_reservation`), so for
    one plant this is that hold or nothing. The order is selected because every
    caller wants to name it.
    """
    return (
        SalesOrderAllocation.objects
        .filter(plant_id__in=list(plant_ids), status__in=HOLDING_STATUSES)
        .select_related('line__order')
        .order_by('pk')
    )


def lock_plant_holders(plant_ids):
    """Lock the orders holding these plants, before anything locks the plants.

    Every sales service takes an order before the plants it promises —
    `confirm_order`, `post_fulfillment` and `allocate_targets` all do — so a
    lifecycle fact that is about to end a hold has to take the order first as
    well, or a cull and a dispatch of the same plant can each hold the lock the
    other is waiting for. The holds are read without a lock, so one confirmed
    between this read and the plant lock is not covered; `close_reservations`
    still locks its order when it gets there, and the plant lock stops any
    further hold appearing after that.
    """
    order_ids = sorted(set(
        plant_holds(plant_ids).values_list('line__order_id', flat=True)
    ))
    return list(
        SalesOrder.objects.select_for_update(of=('self',))
        .filter(pk__in=order_ids)
        .order_by('pk')
    )


def release_plant_holds(plant, user, reason):
    """End every live hold on one plant the nursery no longer has to offer.

    Each goes through `close_reservations`, so the reservation history is the
    one record of why the hold ended, as it is for an expiry or a cancellation.
    The caller already holds the plant lock, which is what stops a new hold
    being confirmed against it before this commits.
    """
    released = []
    for allocation in plant_holds([plant.pk]):
        released.extend(close_reservations(
            allocation.line.order, user, [allocation.pk], 'release', reason,
        ))
    return released
