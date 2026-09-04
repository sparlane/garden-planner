"""Dispatching, returning, and re-costing anonymous nursery stock on an order.

Kept beside `sales.containers` rather than inside `sales.commerce` for the same
reason that is: these are the handful of steps one kind of promise needs, and
the posting commands stay readable when each kind's own detail is one call.

Every one of them leaves the cost alone. Which units of a batch count as sold
is read from the order allocation's own status, and the posting command settles
that in the same transaction, so `recost_cohort_batches` is called once at the
end rather than by each step as it goes.


These take the whole audited request at their boundary, exactly as the cohort
commands they wrap do: grouping the values into a dictionary would hide the
contract and move the same arguments somewhere less checkable.
"""

# pylint: disable=too-many-arguments,too-many-positional-arguments

from uuid import uuid5

from costing.models import CostAllocationRun
from costing.services import reallocate_batches
from plantings.cohorts import change_cohort, return_cohort, sell_cohort
from plantings.loss import LossCause
from plantings.models import CohortOperation, ProductionBatch

from .models import SalesReturnLine
from .services import cohort_draw_cost


def recost_cohort_batches(cohorts, user, reason):
    """Recalculate the batches whose anonymous stock just changed hands.

    Always called after the allocations involved have reached their new status,
    never before. Which units count as sold is read from those statuses — see
    `costing.sources.sold_cohort_quantities` — so a recalculation run while a
    promise was still mid-flight would divide the batch's cost over the wrong
    number of outputs and have to be corrected by the next one.
    """
    if not cohorts:
        return []
    batches = list(ProductionBatch.objects.filter(
        pk__in={cohort.batch_id for cohort in cohorts},
    ))
    return reallocate_batches(
        batches, user, CostAllocationRun.Trigger.MANUAL_RECALCULATE, reason,
    )


def dispatch_cohort_stock(order, user, allocation, cohort, *, fulfillment, fulfilled_at):
    """Ship anonymous nursery stock by the count and value it per unit.

    The cost is read before the sale takes the quantity out. A block's unit
    cost is its layers over its units, and both halves move together when a
    dispatch is recorded, so reading it afterwards would price this sale
    against a division this sale had already changed.
    """
    cogs_amount, _unknown, provisional = cohort_draw_cost(cohort, allocation.quantity)
    event = sell_cohort(
        order.workspace, user,
        cohort_id=cohort.pk,
        quantity=allocation.quantity,
        idempotency_key=uuid5(fulfillment.operation_key, f'sell:{allocation.pk}'),
        occurred_at=fulfilled_at,
        reason='Order fulfillment',
        reference=f'fulfillment:{fulfillment.pk}:allocation:{allocation.pk}',
    )
    return event, cogs_amount, provisional


def return_cohort_stock(order, user, line, sales_return, *, returned_at,
                        reason, outcome, destination):
    """Bring a whole counted cohort dispatch back into a block of its own.

    The returned count lands in a new block rather than back in the one it left
    — `plantings.cohorts.return_cohort` carries the reasoning — and a discarded
    return lands first and is then written off, so the history records both
    facts rather than quietly never taking the stock back. The write-off is a
    cull, which is what `plantings.loss` calls stock deliberately destroyed.
    """
    allocation = line.allocation
    event = return_cohort(
        order.workspace, user,
        source_cohort_id=allocation.plant_cohort_id,
        quantity=allocation.quantity,
        idempotency_key=uuid5(sales_return.operation_key, f'return:{line.pk}'),
        location=destination,
        occurred_at=returned_at,
        reason=reason,
        reference=f'return:{sales_return.pk}:line:{line.pk}',
    )
    if outcome == SalesReturnLine.Outcome.DISCARDED:
        change_cohort(
            order.workspace, user,
            cohort_id=event.cohort_id,
            expected_revision=event.cohort.revision,
            action=CohortOperation.Action.LOSS,
            idempotency_key=uuid5(sales_return.operation_key, f'discard:{line.pk}'),
            occurred_at=returned_at,
            reason=reason,
            quantity=allocation.quantity,
            loss_cause=LossCause.CULLED,
        )
    return event


def restore_cohort_stock(user, line, reversal, *, occurred_at, reason):
    """Put a reversed dispatch's count back into the block it came out of.

    A customer return opens a new block because the stock has been somewhere;
    a reversal says the dispatch never happened at all, so the count goes back
    exactly where it was, in the state the sale found it — which is the state
    the sale's own history entry recorded on its way past.
    """
    sold = line.cohort_event
    event = return_cohort(
        reversal.workspace, user,
        source_cohort_id=sold.cohort_id,
        quantity=-sold.quantity_delta,
        idempotency_key=uuid5(reversal.operation_key, f'restore:{line.pk}'),
        into_source=True,
        state=sold.state_before,
        occurred_at=occurred_at,
        reason=reason,
        reference=f'fulfillment:{reversal.pk}:line:{line.pk}',
    )
    return event.cohort


def withdraw_returned_cohort(user, line, reversal, *, occurred_at, reason):
    """Take a reversed return's count back out of the block that received it.

    Recorded as a sale, because that is where those plants are: the return is
    being undone, so they are with the customer again. The block they leave
    need not be on sale or free of quarantine — a return that quarantined its
    own stock would otherwise be impossible to reverse — which is what
    dropping `require_available` says.
    """
    returned = line.cohort_event
    sell_cohort(
        reversal.workspace, user,
        cohort_id=returned.cohort_id,
        quantity=returned.quantity_delta,
        idempotency_key=uuid5(reversal.operation_key, f'withdraw:{line.pk}'),
        occurred_at=occurred_at,
        reason=reason,
        reference=f'return:{reversal.pk}:line:{line.pk}',
        require_available=False,
    )
    return returned.cohort
