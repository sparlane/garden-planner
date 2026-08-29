"""Release reservations whose recorded hold time has run out.

`SalesOrderAllocation.expires_at` is set by whoever allocated the stock, and
setting it is the decision that this particular hold may lapse without anyone
being asked again. So the sweep releases automatically rather than raising work
for a person to approve: a hold nobody chose to time out never carries an
expiry, and is never touched here.

The release is not silent. It goes through `close_reservations`, so every
expiry appends the same `ReservationEvent` an operator's manual expiry would,
and the reservation history stays the one record of why a hold ended. What the
work queue projects (see `work.projections`) is the warning before the lapse
and the reallocation left behind after it, not a gate in front of it.
"""

from django.db import transaction
from django.utils import timezone

from .models import SalesOrder, SalesOrderAllocation
from .services import close_reservations


#: Recorded against every automatic expiry, so the history distinguishes a
#: hold the schedule let go from one an operator expired by hand.
SWEEP_REASON = 'Expired automatically: the recorded hold time elapsed.'


def due_reservations(workspace, now=None):
    """Return the workspace's reserved allocations whose expiry has passed.

    An allocation with no expiry is an open-ended hold and never becomes due,
    which is why the null is excluded rather than compared.
    """
    return (
        SalesOrderAllocation.objects
        .filter(
            line__order__workspace=workspace,
            status=SalesOrderAllocation.Status.RESERVED,
            expires_at__isnull=False,
            expires_at__lte=now or timezone.now(),
        )
        .select_related('line__order')
        .order_by('line__order_id', 'expires_at', 'pk')
    )


@transaction.atomic
def _expire_one_order(order, user, allocation_ids, now, reason):
    """Expire whatever is still due on one order, under that order's lock.

    Taking the order lock before re-reading the allocations is what makes a
    second sweep a no-op instead of a conflict: it waits, then finds nothing
    still reserved and writes nothing. The status transition is itself the
    idempotency key — an allocation leaves `RESERVED` exactly once, so exactly
    one `ReservationEvent` can be written for its expiry.
    """
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    still_due = list(
        SalesOrderAllocation.objects
        .filter(
            pk__in=allocation_ids,
            status=SalesOrderAllocation.Status.RESERVED,
            expires_at__isnull=False,
            expires_at__lte=now,
        )
        .values_list('pk', flat=True)
    )
    if not still_due:
        return []
    return close_reservations(order, user, still_due, 'expire', reason)


def expire_due_reservations(workspace, user=None, now=None, reason=SWEEP_REASON):
    """Release every lapsed hold in the workspace, one order at a time.

    Each order is its own transaction so that an order whose stock has moved
    under it cannot abandon the sweep for every other order behind it. The
    caller is normally the schedule rather than a person, so `user` is
    optional and the events it writes record no actor.
    """
    now = now or timezone.now()
    grouped = {}
    for allocation in due_reservations(workspace, now):
        grouped.setdefault(allocation.line.order, []).append(allocation.pk)
    expired = []
    for order, allocation_ids in grouped.items():
        expired.extend(_expire_one_order(order, user, allocation_ids, now, reason))
    return expired
