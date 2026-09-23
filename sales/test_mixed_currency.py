"""Selling stock costed in two currencies records no cost of sale (task 142).

`_plant_cost` read a committed value that `_totals` had produced by adding a
USD amount to a EUR one, and `FulfillmentLine` then stored it under the order's
currency — relabelling the mixture a second time. There is no exchange rate in
this application to make one figure out of two, so the dispatch records an
unknown cost of sale, for the reason task 138 gives for an unpriced input: a
number with a conversion missing is not a smaller true cost.
"""

# pylint: disable=duplicate-code

from decimal import Decimal

from costing.models import CostAllocationRun
from costing.services import cohort_cost_breakdown, reallocate_batch
from costing.test_services import CohortStockTestCase
from inventory.models import StockLot
from plantings.batches import finalize_batch_output
from plantings.models import SeedTrayPlanting
from reporting.commerce import profitability_report
from tests.factories import apply_costed_input

from .models import FulfillmentLine, SalesOrder
from .services import order_margin
from .test_commerce import CommerceFixtureTestCase
from . import test_counted_lines as counted_fixtures


class MixedCurrencyDispatchTests(CommerceFixtureTestCase):
    """One plant raised on a 1.08 home-currency input and a 0.08 euro one."""

    def setUp(self):
        super().setUp()
        self.plant = self.available_plant()
        apply_costed_input(self.workspace, self.user, self.plant, '1.0800')
        apply_costed_input(self.workspace, self.user, self.plant, '0.0800', currency_code='EUR')

    def dispatch(self):
        """Sell the plant and return the fulfillment line it recorded."""
        order, allocations = self.confirmed_order([self.plant])
        self.fulfill(order, [row['pk'] for row in allocations])
        return SalesOrder.objects.get(pk=order['pk']), FulfillmentLine.objects.get()

    def test_the_cost_of_sale_is_unknown_rather_than_two_currencies_added(self):
        """Verification 3: 1.1600 is not what this plant cost the nursery."""
        _order, line = self.dispatch()
        self.assertIsNone(line.cogs_amount)

    def test_the_sale_shows_as_unvalued_in_the_profitability_report(self):
        """An unstateable cost keeps the sale out of a finalized margin."""
        self.dispatch()

        report = profitability_report(self.workspace, {})
        row = next(row for row in report.rows if row['kind'] == 'fulfillment')
        self.assertTrue(row['unvalued'])
        self.assertFalse(report.totals['finalized_margin_available'])
        self.assertEqual(report.totals['unvalued_rows'], 1)

    def test_the_order_states_no_cost_total_and_no_margin(self):
        """A margin over a cost that cannot be stated would be an invention."""
        order, _line = self.dispatch()

        margin = order_margin(order)
        self.assertFalse(margin['cost_complete'])
        self.assertIsNone(margin['cost_total'])
        self.assertIsNone(margin['estimated_margin'])


class SingleCurrencyDispatchTests(CommerceFixtureTestCase):
    """The same plant bought entirely at home still sells at a firm figure."""

    def test_one_currency_records_the_cost_of_sale_it_always_did(self):
        """Refusing two currencies must not refuse the ordinary sale."""
        plant = self.available_plant()
        apply_costed_input(self.workspace, self.user, plant, '1.0800')
        apply_costed_input(self.workspace, self.user, plant, '0.0800')
        order, allocations = self.confirmed_order([plant])
        self.fulfill(order, [row['pk'] for row in allocations])

        line = FulfillmentLine.objects.get()
        self.assertEqual(line.cogs_amount, Decimal('1.1600'))
        self.assertEqual(line.currency_code, self.workspace.currency_code)


class IncompletePlantCostTests(CommerceFixtureTestCase):
    """Dispatch preserves unknown costs and the input currency boundary."""

    def test_part_priced_plants_remain_unvalued_after_finalization(self):
        """Unknown pricing is independent of whether output is final."""
        plant = self.available_plant()
        apply_costed_input(self.workspace, self.user, plant, '1.0800')
        apply_costed_input(self.workspace, self.user, plant, '0.0800')
        lot = StockLot.objects.latest('pk')
        StockLot.objects.filter(pk=lot.pk).update(base_unit_cost=None, acquisition_total=None)
        reallocate_batch(plant.batch, self.user, CostAllocationRun.Trigger.MANUAL_RECALCULATE)
        SeedTrayPlanting.objects.filter(batch=plant.batch).update(removed=True)
        finalize_batch_output(plant.batch, self.user, 'All output recorded.')
        order, allocations = self.confirmed_order([plant])
        margin = order_margin(SalesOrder.objects.get(pk=order['pk']))
        self.assertIsNone(margin['cost_total'])
        self.fulfill(order, [row['pk'] for row in allocations])
        line = FulfillmentLine.objects.get()
        self.assertIsNone(line.cogs_amount)
        self.assertFalse(line.cogs_provisional)
        report = profitability_report(self.workspace, {})
        self.assertEqual(report.totals['unvalued_rows'], 1)
        self.assertFalse(report.totals['finalized_margin_available'])

    def test_single_foreign_currency_is_not_relabelled_as_order_currency(self):
        """Known euros cannot become dollars just because the order uses them."""
        plant = self.available_plant()
        apply_costed_input(self.workspace, self.user, plant, '1.0800', currency_code='EUR')
        order, allocations = self.confirmed_order([plant])
        margin = order_margin(SalesOrder.objects.get(pk=order['pk']))
        self.assertIsNone(margin['cost_total'])
        self.assertIsNone(margin['estimated_margin'])
        self.fulfill(order, [row['pk'] for row in allocations])
        self.assertIsNone(FulfillmentLine.objects.get().cogs_amount)


class ForeignCohortCostTests(CohortStockTestCase):
    """An anonymous block obeys the same currency boundary as a plant."""

    def test_foreign_cohort_dispatch_and_margin_remain_unknown(self):
        """A per-unit foreign value cannot be labelled as domestic COGS."""
        StockLot.objects.filter(workspace=self.workspace).update(currency_code='EUR')
        self.reallocate()
        self.assertEqual(cohort_cost_breakdown(self.cohort)['currency_code'], 'EUR')
        fulfillment = self.sell()
        self.assertIsNone(fulfillment.lines.get().cogs_amount)
        self.assertIsNone(order_margin(fulfillment.order)['cost_total'])


class CountedCostIntegrityTests(counted_fixtures.CountedStockTestCase):
    """Counted lot costs are compared in the order's currency."""

    sell = counted_fixtures.CountedFulfillmentTests.sell

    def test_foreign_lot_cost_is_unknown_in_the_order_currency(self):
        """A priced foreign lot ships without inventing an exchange rate."""
        lot = self.receive()
        StockLot.objects.filter(pk=lot.pk).update(currency_code='EUR')
        lot.refresh_from_db()
        _lot, line, fulfillment = self.sell(lot=lot)
        self.assertIsNone(fulfillment.lines.get().cogs_amount)
        self.assertFalse(fulfillment.lines.get().cogs_provisional)
        self.assertIsNone(order_margin(line.order)['cost_total'])


class ContainerCostIntegrityTests(counted_fixtures.CountedStockTestCase):
    """The pot and every passenger must have a comparable known cost."""

    plant_in = counted_fixtures.SoldContainerCostTests.plant_in
    sell = counted_fixtures.SoldContainerCostTests.sell

    def setUp(self):
        super().setUp()
        self.lot = self.receive(quantity='10', unit_cost='5.0000')
        self.pot = self.number(self.lot, 1)[0]

    def test_foreign_container_cost_cannot_be_hidden_by_priced_passengers(self):
        """The pot is part of the cost even when its riders are priced locally."""
        self.pot.currency_code = 'EUR'
        self.pot.save(update_fields=['currency_code'])
        plant = self.plant_in(self.pot)
        apply_costed_input(self.workspace, self.user, plant, '1.0000')
        self.assertIsNone(self.sell().lines.get().cogs_amount)

    def test_foreign_passenger_makes_the_container_sale_unvalued(self):
        """A known pot cannot conceal a rider needing currency conversion."""
        plant = self.plant_in(self.pot)
        apply_costed_input(self.workspace, self.user, plant, '1.0000', currency_code='EUR')
        self.assertIsNone(self.sell().lines.get().cogs_amount)

    def test_unpriced_container_cannot_be_hidden_by_priced_passengers(self):
        """Unknown acquisition cost remains unknown when plants ride along."""
        self.pot.acquisition_cost = None
        self.pot.save(update_fields=['acquisition_cost'])
        plant = self.plant_in(self.pot)
        apply_costed_input(self.workspace, self.user, plant, '1.0000')
        self.assertIsNone(self.sell().lines.get().cogs_amount)

    def test_unpriced_passenger_remains_unknown_in_sale_and_order_margin(self):
        """Ending a rider's placement must not erase its unpriced inputs."""
        priced = self.plant_in(self.pot)
        apply_costed_input(self.workspace, self.user, priced, '2.0000')
        unpriced = self.plant_in(self.pot)
        apply_costed_input(self.workspace, self.user, unpriced, '1.0000')
        lot = StockLot.objects.latest('pk')
        StockLot.objects.filter(pk=lot.pk).update(base_unit_cost=None, acquisition_total=None)
        reallocate_batch(unpriced.batch, self.user, CostAllocationRun.Trigger.MANUAL_RECALCULATE)
        fulfillment = self.sell()
        self.assertIsNone(fulfillment.lines.get().cogs_amount)
        self.assertIsNone(order_margin(fulfillment.order)['cost_total'])
