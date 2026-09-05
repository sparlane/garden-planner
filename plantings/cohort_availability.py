"""What an anonymous cohort quantity is currently free to promise.

A cohort holds a count rather than a set of identities, so nothing about a
reservation can be written on the stock itself: two orders may legitimately
hold parts of one block at once, exactly as they may hold parts of one stock
lot. Availability is therefore arithmetic over the live reservations, which is
the same shape `inventory.ledger.unpromised_bulk` has and is trustworthy for
the same reason — only while the caller holds the cohort's row lock.

Only a reserved allocation counts. A pending one is a tentative selection
somebody is still drafting, which warns rather than blocks, the same way it
does for a plant and for a lot.
"""

from django.db.models import Case, F, IntegerField, OuterRef, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce

from .models import PlantCohort


#: The cohort states a dispatch may take stock out of. Grading a block ready is
#: a judgement somebody makes, and `available` is the fact that records it, so
#: nothing leaves for a customer before it exists.
DISPATCHABLE_STATES = frozenset({PlantCohort.LifecycleState.AVAILABLE})

#: The cohort states an order may promise stock out of. Growing is here because
#: nursery trade commits long before it dispatches: spring orders are placed in
#: winter against plants still in plugs. Refusing those left an operator two bad
#: options -- grade the stock ready months early, which falsifies the very
#: signal the register exists to give, or keep the commitment outside the
#: application, where it holds no stock and two customers can be promised the
#: same plants.
#:
#: Retained stock is kept for the operation's own use and a depleted block has
#: nothing left, so neither may be promised. `DISPATCHABLE_STATES` is a subset
#: on purpose: committing and shipping are different questions, asked months
#: apart, and only the second one is about whether the plants are ready.
COMMITTABLE_STATES = frozenset({
    PlantCohort.LifecycleState.GROWING,
    PlantCohort.LifecycleState.AVAILABLE,
})


def _reserved_subquery():
    """Return the per-cohort reserved total as a correlated subquery.

    Sales is built on the nursery rather than the other way round, so the
    import is deferred exactly as `inventory.ledger.promised_bulk` defers its
    own reach back into sales.
    """
    from sales.models import SalesOrderAllocation  # pylint: disable=import-outside-toplevel

    return (
        SalesOrderAllocation.objects
        .filter(
            plant_cohort=OuterRef('pk'),
            status=SalesOrderAllocation.Status.RESERVED,
        )
        .values('plant_cohort')
        .annotate(total=Sum('quantity'))
        .values('total')
    )


def with_availability(queryset):
    """Annotate reserved and unreserved quantity onto a cohort queryset.

    One subquery for a whole register page, rather than one query per row:
    the cohort register is the screen an operator picks sellable stock from,
    so it reads this for every row it shows.
    """
    return queryset.annotate(
        reserved_quantity=Coalesce(
            Subquery(_reserved_subquery(), output_field=IntegerField()), 0,
        ),
    ).annotate(
        available_quantity=F('quantity') - F('reserved_quantity'),
        # What is promised out of stock nobody has graded ready yet. It is the
        # same reservations counted a second way, so a screen can tell a block
        # that is sold and shippable from one that is sold and still in plugs
        # without reading each row's state itself.
        committed_forward_quantity=Case(
            When(
                lifecycle_state=PlantCohort.LifecycleState.GROWING,
                then=F('reserved_quantity'),
            ),
            default=Value(0),
            output_field=IntegerField(),
        ),
    )


def reserved_quantity(cohort):
    """Return how many of one cohort's plants a live reservation holds."""
    from sales.models import SalesOrderAllocation  # pylint: disable=import-outside-toplevel

    total = SalesOrderAllocation.objects.filter(
        plant_cohort=cohort,
        status=SalesOrderAllocation.Status.RESERVED,
    ).aggregate(total=Sum('quantity'))['total']
    return total or 0


def available_quantity(cohort):
    """Return the part of one cohort's count nothing has been promised out of.

    Only trustworthy while the caller holds the cohort's row lock, which is
    what makes a new reservation serialise against another order taking the
    same units.
    """
    return cohort.quantity - reserved_quantity(cohort)


def batch_commitments(batch):
    """Return one batch's anonymous stock, split by state and by what is sold.

    The batch detail is where production looks at a crop, so it is where "how
    much of this is already somebody else's" belongs. Depleted blocks are left
    out: they hold nothing, and counting them would report a batch's whole
    history rather than what is standing on the bench now.
    """
    rows = with_availability(
        PlantCohort.objects.filter(batch=batch, quantity__gt=0),
    ).values('lifecycle_state', 'quantity', 'reserved_quantity')
    totals = {state.value: 0 for state in PlantCohort.LifecycleState}
    quantity = reserved = committed_forward = 0
    for row in rows:
        totals[row['lifecycle_state']] += row['quantity']
        quantity += row['quantity']
        reserved += row['reserved_quantity']
        if row['lifecycle_state'] == PlantCohort.LifecycleState.GROWING:
            committed_forward += row['reserved_quantity']
    return {
        'quantity': quantity,
        'state_quantities': totals,
        # Sold, whether or not it is ready. The forward half is the part a
        # grower has to keep alive rather than a part a picker can go and get.
        'reserved_quantity': reserved,
        'committed_forward_quantity': committed_forward,
        'free_quantity': quantity - reserved,
    }
