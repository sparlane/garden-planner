"""A cost of sale posted before its batch was finalized is settled (task 139).

The sale is dispatched at a figure that can still move and the line says so.
Finalizing the batch is what makes it final, so the same transaction writes the
final figure beside the line, and the profitability report stops treating the
period as unreportable because of it.
"""

# pylint: disable=duplicate-code

from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.management import call_command

from costing.models import CostAllocationRun
from costing.services import reallocate_batch
from inventory.models import StockLot
from plantings.batches import finalize_batch_output, reopen_batch
from plantings.models import SeedTrayPlanting
from reporting.commerce import profitability_report
from tests.factories import apply_costed_input

from .cost_of_sale import plant_cost_of_sale
from .models import FulfillmentLine, FulfillmentLineSettlement
from .settlement import cost_of_line, restored_cost, settle_batch_sales
from .test_commerce import CommerceFixtureTestCase


class SettleCostOfSaleTests(CommerceFixtureTestCase):
    """One plant raised on a 1.0800 input, sold before its batch is final."""

    def setUp(self):
        super().setUp()
        self.plant = self.available_plant()
        apply_costed_input(self.workspace, self.user, self.plant, '1.0800')

    def sell(self):
        """Sell the plant and return its order and fulfillment line."""
        order, allocations = self.confirmed_order([self.plant])
        fulfillment = self.fulfill(order, [row['pk'] for row in allocations])
        return order, fulfillment, FulfillmentLine.objects.get(pk=fulfillment['lines'][0]['pk'])

    def finalize(self):
        """Close the batch's sowings and finalize its output."""
        SeedTrayPlanting.objects.filter(batch=self.plant.batch).update(removed=True)
        return finalize_batch_output(self.plant.batch, self.user, 'All output recorded.')

    def margin(self):
        """Return the profitability totals for every period."""
        return profitability_report(self.workspace, {}).totals

    def test_a_period_with_a_provisional_sale_reports_no_margin_until_settled(self):
        """Finalizing the batch turns an unreportable period into a reportable one."""
        _order, _fulfillment, line = self.sell()
        self.assertTrue(line.cogs_provisional)
        before = self.margin()
        self.assertEqual(before['provisional_rows'], 1)
        self.assertFalse(before['finalized_margin_available'])

        self.finalize()

        after = self.margin()
        self.assertEqual(after['provisional_rows'], 0)
        self.assertTrue(after['finalized_margin_available'])
        settlement = line.settlements.get()
        self.assertEqual(settlement.cogs_amount, plant_cost_of_sale(self.plant).amount)
        self.assertEqual(
            after['currencies'][0]['plant_cogs'], f'{settlement.cogs_amount:.4f}',
        )

    def test_the_dispatch_snapshot_is_left_as_it_was_recorded(self):
        """Settling writes beside the line and never over it."""
        _order, _fulfillment, line = self.sell()
        recorded = (line.cogs_amount, line.cogs_provisional)
        self.finalize()
        line.refresh_from_db()
        self.assertEqual((line.cogs_amount, line.cogs_provisional), recorded)
        self.assertTrue(line.cogs_provisional)
        self.assertFalse(cost_of_line(line).provisional)

    def test_the_report_row_says_it_was_settled(self):
        """A margin that appeared because cost arrived can be told from one that moved."""
        self.sell()
        self.finalize()
        report = profitability_report(self.workspace, {})
        row = next(row for row in report.rows if row['kind'] == 'fulfillment')
        self.assertTrue(row['settled'])
        self.assertFalse(row['provisional'])

    def test_a_sale_from_an_already_finalized_batch_is_not_settled(self):
        """A line that was final when posted has nothing to settle."""
        self.finalize()
        _order, _fulfillment, line = self.sell()
        self.assertFalse(line.cogs_provisional)
        self.assertFalse(line.settlements.exists())
        self.assertEqual(settle_batch_sales(
            self.plant.batch, self.user, FulfillmentLineSettlement.Source.BACKFILL,
        ), [])

    def test_settling_twice_changes_nothing(self):
        """A second settlement of the same figure writes no row."""
        self.sell()
        batch = self.finalize()
        again = settle_batch_sales(batch, self.user, FulfillmentLineSettlement.Source.BACKFILL)
        self.assertEqual(again, [])
        self.assertEqual(FulfillmentLineSettlement.objects.count(), 1)

    def test_a_batch_that_is_not_final_settles_nothing(self):
        """An open batch can still move, so nothing is settled."""
        self.sell()
        self.assertEqual(settle_batch_sales(
            self.plant.batch, self.user, FulfillmentLineSettlement.Source.BACKFILL,
        ), [])

    def test_a_reversed_dispatch_is_not_settled(self):
        """A reversed line must not have its cost resurrected."""
        order, fulfillment, _line = self.sell()
        reversed_ = self.client.post(
            f"{self.orders_url}{order['pk']}/fulfillments/{fulfillment['pk']}/reverse/",
            {'operation_key': str(uuid4()), 'reason': 'Dispatch was entered in error.'},
            format='json',
        )
        self.assertEqual(reversed_.status_code, 201, reversed_.data)
        self.finalize()
        self.assertFalse(FulfillmentLineSettlement.objects.exists())

    def test_a_cost_still_unpriced_at_finalization_stays_unvalued(self):
        """Settling is not a way to make an unknown cost known (task 138)."""
        lot = StockLot.objects.latest('pk')
        StockLot.objects.filter(pk=lot.pk).update(base_unit_cost=None, acquisition_total=None)
        reallocate_batch(self.plant.batch, self.user, CostAllocationRun.Trigger.MANUAL_RECALCULATE)
        order, allocations = self.confirmed_order([self.plant])
        self.fulfill(order, [row['pk'] for row in allocations])
        self.finalize()
        settlement = FulfillmentLineSettlement.objects.get()
        self.assertIsNone(settlement.cogs_amount)
        totals = self.margin()
        self.assertEqual(totals['unvalued_rows'], 1)
        self.assertFalse(totals['finalized_margin_available'])

    def test_reopening_the_batch_withdraws_the_settlement(self):
        """A reopened batch puts the plant's share back in play."""
        _order, _fulfillment, line = self.sell()
        batch = self.finalize()
        reopen_batch(batch, self.user, 'Counted wrongly.')
        line = FulfillmentLine.objects.get(pk=line.pk)
        self.assertTrue(cost_of_line(line).provisional)
        self.assertEqual(self.margin()['provisional_rows'], 1)

    def test_the_fulfillment_api_shows_both_figures(self):
        """The snapshot and the settled figure are read side by side."""
        order, _fulfillment, _line = self.sell()
        self.finalize()
        response = self.client.get(f"{self.orders_url}{order['pk']}/fulfillments/")
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data['results'] if isinstance(response.data, dict) else response.data
        line = rows[0]['lines'][0]
        self.assertTrue(line['cogs_provisional'])
        self.assertEqual(Decimal(line['settlement']['cogs_amount']), Decimal('1.0800'))

    def test_the_backfill_command_settles_lines_finalized_before_it_existed(self):
        """The command settles earlier history as an audited run."""
        self.sell()
        self.finalize()
        FulfillmentLineSettlement.objects.all()._raw_delete(  # pylint: disable=protected-access
            FulfillmentLineSettlement.objects.db,
        )
        out = StringIO()
        call_command('settle_cost_of_sale', '--dry-run', stdout=out)
        self.assertIn('would settle 1', out.getvalue())
        self.assertIn(f'fulfillment line {FulfillmentLine.objects.get().pk}', out.getvalue())
        self.assertFalse(FulfillmentLineSettlement.objects.exists())
        call_command('settle_cost_of_sale', stdout=out)
        settlement = FulfillmentLineSettlement.objects.get()
        self.assertEqual(settlement.source, FulfillmentLineSettlement.Source.BACKFILL)
        self.assertIn('settled 1', out.getvalue())

    def test_a_return_of_a_line_settled_as_unstateable_restores_no_figure(self):
        """The return-time snapshot must not stand in for a cost settled as unknown."""
        _order, _fulfillment, line = self.sell()
        self.finalize()
        FulfillmentLineSettlement.objects.filter(line=line).update(
            cogs_amount=None, cogs_currency_code='',
        )
        line = FulfillmentLine.objects.get(pk=line.pk)
        return_line = SimpleNamespace(
            fulfillment_line=line, quantity=line.quantity, cogs_amount=Decimal('1.0800'),
        )
        cost = cost_of_line(line)
        self.assertIsNone(cost.amount)
        self.assertIsNone(restored_cost(return_line, cost))

    def test_an_unknown_settlement_source_is_refused(self):
        """Audit readers can interpret every source a settlement carries."""
        _order, _fulfillment, line = self.sell()
        with self.assertRaises(ValidationError):
            FulfillmentLineSettlement.objects.create(
                line=line, cogs_amount=Decimal('1.0800'), cogs_currency_code='USD',
                source='guess',
            )
