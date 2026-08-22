"""Taxable-period arithmetic for a GST-registered workspace.

A taxable period is named by the month it *ends* in, which is how Inland
Revenue names its cycles: the two-monthly alternates are the one ending in
January, March, May, July, September and November, and the one ending in
February, April, June, August, October and December; the six-monthly options
end in March/September, April/October, or May/November. Storing the anchor as
an end month rather than a start month means the stored value is the one an
operator reads off their registration letter.

The arithmetic in this module is pure. The functions that need to know which
registration was in force live beside it and read the database; keeping the
month maths separate is what lets the boundary cases — a cycle spanning a year
end, a leap-year February — be tested without a fixture.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo


#: How many months one taxable period spans, keyed by the stored filing
#: frequency. The keys are the values of ``GstRegistration.Frequency``; a test
#: keeps the two in step, so adding a frequency cannot silently skip this map.
PERIOD_MONTHS = {
    'monthly': 1,
    'two_monthly': 2,
    'six_monthly': 6,
}


def period_bounds(frequency, anchor_month, on_date):
    """Return the inclusive first and last day of the cycle containing a date.

    ``anchor_month`` is a month a period ends in. Monthly filing needs no
    anchor and ignores whatever is passed, because every month is an end
    month; that falls out of the modulo rather than being special-cased.
    """
    span = PERIOD_MONTHS[frequency]
    month_index = on_date.year * 12 + on_date.month - 1
    anchor_index = (anchor_month or 1) - 1
    end_index = month_index + (anchor_index - month_index) % span
    start_index = end_index - (span - 1)
    start = date(start_index // 12, start_index % 12 + 1, 1)
    end_year, end_month = end_index // 12, end_index % 12 + 1
    end = date(end_year, end_month, monthrange(end_year, end_month)[1])
    return start, end


def period_label(start, end):
    """Return the stable identifier a period is reported and drilled into by."""
    return f'{start.isoformat()}..{end.isoformat()}'


def local_date(workspace, value):
    """Return the workspace-local business date of a commerce timestamp.

    This is the only place a timestamp is allowed to become a date. Commerce
    records mix the two deliberately: ``Fulfillment.fulfilled_at`` and its
    siblings are stored as UTC instants, while ``Payment.paid_on`` and
    ``StockReceipt.received_date`` are already local business dates. Calling
    ``.date()`` on the former files a supply into the wrong period for every
    workspace east of UTC — a 1 April 09:00 NZDT fulfillment is stored as
    31 March 20:00Z, and a GST return that puts it in March is wrong.
    """
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo(workspace.timezone)).date()
    return value


@dataclass(frozen=True)
class TaxablePeriod:
    """One filing period, clipped to the arrangement that produced it.

    ``start`` and ``end`` are the days a return actually covers, which is not
    always the whole cycle: registering on the 15th of a two-monthly cycle
    produces a short first period, and changing frequency mid-cycle closes the
    old one early. ``cycle_start`` and ``cycle_end`` keep the unclipped bounds
    so a report can say which cycle a short period belongs to.
    """

    start: date
    end: date
    cycle_start: date
    cycle_end: date
    frequency: str
    basis: str
    registration_id: int

    @property
    def label(self):
        """Return the stable identifier this period is drilled into by."""
        return period_label(self.start, self.end)

    @property
    def clipped(self):
        """Whether the period covers less than its whole cycle."""
        return (self.start, self.end) != (self.cycle_start, self.cycle_end)

    def contains(self, day):
        """Whether a business date falls inside this period."""
        return self.start <= day <= self.end


def registration_history(workspace):
    """Return this workspace's live arrangements, oldest first.

    Superseded rows are excluded: they record what somebody entered, not what
    applied. They stay readable through the row that replaced them.
    """
    from .models import GstRegistration  # pylint: disable=import-outside-toplevel
    return list(
        GstRegistration.objects
        .filter(workspace=workspace, superseded_by__isnull=True)
        .order_by('effective_from', 'pk')
    )


def registration_in_force(workspace, on_date, history=None):
    """Return the arrangement applying on a date, or None if there is none.

    None answers two different questions the same way on purpose — the date is
    before anything was ever recorded, or it falls in a gap after a
    deregistration. Both mean there was no GST obligation, and a caller that
    treats either as a zero would report a period the workspace never had.
    Callers distinguish them by checking whether the history is empty.
    """
    rows = registration_history(workspace) if history is None else history
    applying = None
    for row in rows:
        if row.effective_from > on_date:
            break
        applying = row
    if applying is None or not applying.registered:
        return None
    return applying


def _next_change_after(rows, registration):
    """Return the arrangement that replaced one, or None if it still applies."""
    following = [
        row for row in rows
        if (row.effective_from, row.pk) > (registration.effective_from, registration.pk)
    ]
    return following[0] if following else None


def taxable_period_for(workspace, on_date, history=None):
    """Return the taxable period a business date falls in, or None.

    None means the workspace was not registered on that date. Nothing invents a
    period to hold the supply; the report says so instead.
    """
    rows = registration_history(workspace) if history is None else history
    registration = registration_in_force(workspace, on_date, history=rows)
    if registration is None:
        return None
    cycle_start, cycle_end = period_bounds(
        registration.filing_frequency, registration.period_anchor_month, on_date,
    )
    start = max(cycle_start, registration.effective_from)
    end = cycle_end
    following = _next_change_after(rows, registration)
    if following is not None and following.effective_from <= cycle_end:
        end = date.fromordinal(following.effective_from.toordinal() - 1)
    return TaxablePeriod(
        start=start,
        end=end,
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        frequency=registration.filing_frequency,
        basis=registration.basis,
        registration_id=registration.pk,
    )


def enumerate_periods(workspace, start, end, history=None):
    """Return every taxable period overlapping a range, oldest first.

    A range covering a deregistration produces periods on both sides of it and
    nothing in the gap, which is the honest answer: no return was due.
    """
    rows = registration_history(workspace) if history is None else history
    periods = []
    day = start
    while day <= end:
        period = taxable_period_for(workspace, day, history=rows)
        if period is None:
            day = _next_boundary(rows, day, end)
            continue
        periods.append(period)
        day = date.fromordinal(period.end.toordinal() + 1)
    return periods


def _next_boundary(rows, day, end):
    """Return the next date worth testing after an unregistered day.

    Stepping a day at a time through years of pre-registration history would be
    correct and slow, so skip straight to the next recorded change.
    """
    upcoming = [row.effective_from for row in rows if row.effective_from > day]
    return min(upcoming) if upcoming else date.fromordinal(end.toordinal() + 1)
