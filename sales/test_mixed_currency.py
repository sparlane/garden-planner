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

from reporting.commerce import profitability_report
from tests.factories import apply_costed_input

from .models import FulfillmentLine, SalesOrder
from .services import order_margin
from .test_commerce import CommerceFixtureTestCase


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
