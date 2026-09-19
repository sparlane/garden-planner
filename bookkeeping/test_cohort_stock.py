"""Closing stock valued on the anonymous units still held (task 135).

Driven through the real posting paths on `costing.test_services`'s block of
four units worth 1.08: sold, reversed and returned through the sales commands,
then captured the way an income year captures its stock.
"""

from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from uuid import uuid4

from django.utils import timezone

from costing.models import CostAllocation
from costing.test_services import CohortStockTestCase
from plantings.models import CohortOperation, PlantCohort
from sales.commerce import reverse_fulfillment
from tests.factories import make_stock_lot

from .models import IncomeTaxYear, StockValuationLine
from .services import build_report, capture_inventory


class CohortClosingStockTests(CohortStockTestCase):
    """A cohort's closing value covers the units left, not the ones sold.

    The `plant_cohort` column also carries `COHORT_SALE` layers, so reading it
    without the target type once valued three units left of four at the whole
    block's 1.0800, counting the sold 0.2700 in cost of sale and again in stock.
    """

    def setUp(self):
        super().setUp()
        # Always a future balance date, so everything posted here is before it.
        self.income_year = IncomeTaxYear.objects.create(
            workspace=self.workspace, basis=IncomeTaxYear.Basis.ACCRUAL,
            year_end=date(timezone.localdate().year + 1, 3, 31),
        )

    def capture(self):
        """Capture the year's stock and return its cohort lines by cohort."""
        capture_inventory(self.income_year, self.user)
        lines = StockValuationLine.objects.filter(
            income_year=self.income_year, source_type='plant_cohort',
        )
        return {int(line.source_id): line for line in lines}

    def test_a_partly_sold_cohort_is_valued_on_the_units_left(self):
        """Verification 1: three units of four left carry 0.8100, not 1.0800."""
        fulfillment = self.sell()

        line = self.capture()[self.cohort.pk]
        self.assertEqual(f'{line.quantity:.9f}', '3.000000000')
        self.assertEqual(f'{line.value:.4f}', '0.8100')
        self.assertEqual(f'{line.original_cost:.4f}', '0.8100')
        self.assertFalse(line.provisional)
        # Remaining units times the unit value the dispatch was charged at.
        cogs = fulfillment.lines.get().cogs_amount
        self.assertEqual(line.value, line.quantity * cogs)
        # The sold cost stays on the column, as cost of sale and nowhere else.
        sold = CostAllocation.objects.filter(
            plant_cohort=self.cohort, target_type=CostAllocation.TargetType.COHORT_SALE,
            reversal_of=None, reversal__isnull=True,
        )
        self.assertEqual(sum(row.amount for row in sold), cogs)
        self.assertEqual(f'{line.value + cogs:.4f}', '1.0800')

    def test_a_fully_sold_cohort_captures_no_line(self):
        """Verification 2: no zero-quantity line carrying the whole cost."""
        self.sell(quantity=4)

        self.assertEqual(self.capture(), {})

    def test_an_unsold_cohort_is_unchanged(self):
        """Verification 3: all four units still carry the whole 1.0800."""
        line = self.capture()[self.cohort.pk]
        self.assertEqual(f'{line.quantity:.9f}', '4.000000000')
        self.assertEqual(f'{line.value:.4f}', '1.0800')
        self.assertFalse(line.provisional)

    def test_an_unknown_layer_still_marks_the_cohort_provisional(self):
        """Verification 4: an unpriced input is a gap, not a zero."""
        self.media_lot = make_stock_lot(
            item=self.media, location=self.location, quantity='50',
            acquisition_total=None, base_unit_cost=None,
        )
        self.apply_media([self.cells[0]], '0.04')
        self.reallocate()
        self.sell()

        line = self.capture()[self.cohort.pk]
        self.assertTrue(line.provisional)
        self.assertEqual(f'{line.value:.4f}', '0.8100')

    def test_a_reversed_dispatch_is_valued_back_in_its_block(self):
        """The dispatch never happened, so the whole block is stock again."""
        fulfillment = self.sell()
        reverse_fulfillment(
            fulfillment, self.user,
            operation_key=uuid4(), reason='Dispatched in error.',
        )

        line = self.capture()[self.cohort.pk]
        self.assertEqual(f'{line.quantity:.9f}', '4.000000000')
        self.assertEqual(f'{line.value:.4f}', '1.0800')

    def test_a_returned_unit_is_valued_in_the_block_that_holds_it(self):
        """Verification 5: the return's new block carries its unit, not the source."""
        fulfillment = self.sell()
        self.return_sale(fulfillment)
        returned = PlantCohort.objects.exclude(pk=self.cohort.pk).get(batch=self.batch)

        lines = self.capture()
        self.assertEqual(f'{lines[self.cohort.pk].quantity:.9f}', '3.000000000')
        self.assertEqual(f'{lines[self.cohort.pk].value:.4f}', '0.8100')
        self.assertEqual(f'{lines[returned.pk].quantity:.9f}', '1.000000000')
        self.assertEqual(f'{lines[returned.pk].value:.4f}', '0.2700')

    def test_the_report_closing_stock_moves_by_the_sold_cost(self):
        """Verification 6: selling a unit lowers closing stock by its cost of sale."""
        self.capture()
        before = Decimal(build_report(self.income_year)['totals']['closing_stock'])

        fulfillment = self.sell()
        self.capture()
        after = Decimal(build_report(self.income_year)['totals']['closing_stock'])

        self.assertEqual(f'{before - after:.4f}', '0.2700')
        self.assertEqual(before - after, fulfillment.lines.get().cogs_amount)
        self.assertEqual(self.capture()[self.cohort.pk].value, Decimal('0.8100'))


class CohortActivityAfterBalanceDateTests(CohortStockTestCase):
    """A block that changed after the balance date is flagged, not trusted.

    The capture reads the block as it stands, so a year captured while spring
    sales continue would value a block of four held on 31 March at the three
    left. Until task 148 values it as at the balance date, that line is marked
    provisional.
    """

    capture = CohortClosingStockTests.capture

    def setUp(self):
        super().setUp()
        self.income_year = IncomeTaxYear.objects.create(
            workspace=self.workspace, basis=IncomeTaxYear.Basis.ACCRUAL,
            year_end=date(2026, 3, 31),
        )
        # The block's whole history so far happened in January.
        january = datetime(2026, 1, 5, tzinfo=dt_timezone.utc)
        PlantCohort.objects.filter(pk=self.cohort.pk).update(created=january, observed_at=january)
        CohortOperation.objects.filter(events__cohort=self.cohort).update(occurred_at=january)

    def test_a_block_untouched_since_the_balance_date_is_final(self):
        """The control: four units held then and now, at 1.0800."""
        line = self.capture()[self.cohort.pk]
        self.assertEqual(f'{line.quantity:.9f}', '4.000000000')
        self.assertEqual(f'{line.value:.4f}', '1.0800')
        self.assertFalse(line.provisional)

    def test_a_sale_after_the_balance_date_marks_the_line_provisional(self):
        """Four worth 1.0800 were held on 31 March; the capture sees three at 0.8100."""
        self.sell()

        line = self.capture()[self.cohort.pk]
        self.assertEqual(f'{line.quantity:.9f}', '3.000000000')
        self.assertEqual(f'{line.value:.4f}', '0.8100')
        self.assertTrue(line.provisional)
        codes = [row['code'] for row in build_report(self.income_year)['data_quality']]
        self.assertIn('provisional_stock', codes)

    def test_a_block_sold_out_after_the_balance_date_is_still_listed(self):
        """Emptied after year end, the block stays on the schedule as a provisional zero."""
        self.sell(quantity=4)

        line = self.capture()[self.cohort.pk]
        self.assertEqual(f'{line.quantity:.9f}', '0.000000000')
        self.assertEqual(f'{line.value:.4f}', '0.0000')
        self.assertTrue(line.provisional)
