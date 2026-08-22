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
