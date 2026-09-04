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

from django.db.models import F, IntegerField, OuterRef, Subquery, Sum
from django.db.models.functions import Coalesce


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
