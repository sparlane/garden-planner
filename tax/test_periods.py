"""Pin taxable-period arithmetic and the one timestamp-to-date conversion.

Every case here is a boundary: a cycle that spans a year end, a leap-year
February, and the timezone offset that decides which GST return a fulfillment
recorded on the first morning of a period belongs to.
"""

from datetime import date, datetime, timezone
from types import SimpleNamespace

from django.test import SimpleTestCase

from .periods import PERIOD_MONTHS, local_date, period_bounds, period_label


class PeriodBoundsTests(SimpleTestCase):
    """The anchor names a month a period ends in, not one it starts in."""

    def test_monthly_ignores_the_anchor(self):
        """Every month is an end month, so the anchor cannot change the answer."""
        for anchor in range(1, 13):
            self.assertEqual(
                period_bounds('monthly', anchor, date(2026, 7, 15)),
                (date(2026, 7, 1), date(2026, 7, 31)),
            )

    def test_two_monthly_odd_alternate(self):
        """The cycle ending in January, March, May, July, September, November."""
        self.assertEqual(
            period_bounds('two_monthly', 3, date(2026, 2, 14)),
            (date(2026, 2, 1), date(2026, 3, 31)),
        )
        self.assertEqual(
            period_bounds('two_monthly', 3, date(2026, 3, 31)),
            (date(2026, 2, 1), date(2026, 3, 31)),
        )

    def test_two_monthly_even_alternate(self):
        """The other alternate must not share a single boundary with the first."""
        self.assertEqual(
            period_bounds('two_monthly', 4, date(2026, 3, 31)),
            (date(2026, 3, 1), date(2026, 4, 30)),
        )
        self.assertEqual(
            period_bounds('two_monthly', 4, date(2026, 4, 1)),
            (date(2026, 3, 1), date(2026, 4, 30)),
        )

    def test_a_cycle_spanning_a_year_end(self):
        """December and January are one period; the month index must not wrap wrong."""
        expected = (date(2026, 12, 1), date(2027, 1, 31))
        self.assertEqual(period_bounds('two_monthly', 1, date(2026, 12, 31)), expected)
        self.assertEqual(period_bounds('two_monthly', 1, date(2027, 1, 1)), expected)

    def test_a_leap_year_february_ends_on_the_twenty_ninth(self):
        """monthrange, not a 28-day constant, decides where the period ends."""
        self.assertEqual(
            period_bounds('two_monthly', 2, date(2028, 1, 5)),
            (date(2028, 1, 1), date(2028, 2, 29)),
        )
        self.assertEqual(
            period_bounds('two_monthly', 2, date(2026, 1, 5)),
            (date(2026, 1, 1), date(2026, 2, 28)),
        )

    def test_six_monthly_march_september(self):
        """A six-month cycle must cover its whole half of the year, both halves."""
        self.assertEqual(
            period_bounds('six_monthly', 3, date(2026, 1, 20)),
            (date(2025, 10, 1), date(2026, 3, 31)),
        )
        self.assertEqual(
            period_bounds('six_monthly', 3, date(2026, 4, 1)),
            (date(2026, 4, 1), date(2026, 9, 30)),
        )

    def test_consecutive_periods_leave_no_gap_and_no_overlap(self):
        """A supply on any day must land in exactly one period, for two years."""
        for frequency, anchor in (('monthly', 1), ('two_monthly', 3), ('six_monthly', 5)):
            seen = {}
            day = date(2025, 1, 1)
            while day < date(2027, 1, 1):
                start, end = period_bounds(frequency, anchor, day)
                self.assertTrue(start <= day <= end, f'{frequency} {day}')
                seen.setdefault((start, end), []).append(day)
                day = date.fromordinal(day.toordinal() + 1)
            ordered = sorted(seen)
            for earlier, later in zip(ordered, ordered[1:]):
                self.assertEqual(
                    later[0].toordinal(), earlier[1].toordinal() + 1,
                    f'{frequency}: {earlier} then {later}',
                )

    def test_period_label_is_stable(self):
        """The label is a drill-down key, so its shape is a contract."""
        self.assertEqual(
            period_label(date(2026, 2, 1), date(2026, 3, 31)),
            '2026-02-01..2026-03-31',
        )


class LocalDateTests(SimpleTestCase):
    """Filing a supply into the wrong period is the bug this function prevents."""

    def setUp(self):
        self.workspace = SimpleNamespace(timezone='Pacific/Auckland')

    def test_a_date_passes_through(self):
        """paid_on and received_date are already local business dates."""
        self.assertEqual(local_date(self.workspace, date(2026, 4, 1)), date(2026, 4, 1))

    def test_a_utc_instant_becomes_the_local_business_date(self):
        """9am on 1 April in Auckland is stored as 31 March 20:00Z."""
        stored = datetime(2026, 3, 31, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(local_date(self.workspace, stored), date(2026, 4, 1))
        self.assertEqual(stored.date(), date(2026, 3, 31))

    def test_the_conversion_follows_the_workspace_timezone(self):
        """The same instant belongs to different periods in different workspaces."""
        stored = datetime(2026, 3, 31, 20, 0, tzinfo=timezone.utc)
        utc_workspace = SimpleNamespace(timezone='UTC')
        self.assertEqual(local_date(utc_workspace, stored), date(2026, 3, 31))


class PeriodMonthsTests(SimpleTestCase):
    """A frequency with no entry here would raise KeyError at report time."""

    def test_every_frequency_spans_a_whole_number_of_periods_per_year(self):
        """Twelve months must divide evenly, or a year would end mid-period."""
        for frequency, months in PERIOD_MONTHS.items():
            self.assertEqual(12 % months, 0, frequency)
