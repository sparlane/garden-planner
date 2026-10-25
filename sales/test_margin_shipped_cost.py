"""An order's margin prices what shipped at the cost it shipped for (task 140)."""

# pylint: disable=duplicate-code,protected-access

from decimal import Decimal
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from costing.models import CostAllocationRun
from costing.services import reallocate_batch
from inventory.models import StockLot
from reporting.commerce import profitability_report
from tests.factories import apply_costed_input

from .models import FulfillmentLine, SalesOrder
from .cost_of_sale import CostOfSale
from .services import _shipped_cost, order_margin
from .test_commerce import CommerceFixtureTestCase


class ShippedCostMarginTests(CommerceFixtureTestCase):
    """One plant raised on a 1.0800 input and sold."""

    def setUp(self):
        super().setUp()
        self.plant = self.available_plant()
        apply_costed_input(self.workspace, self.user, self.plant, '1.0800')
        self.order, allocations = self.confirmed_order([self.plant])
        self.allocation_ids = [row['pk'] for row in allocations]

    def reprice_input(self):
        """Change what the input cost and recalculate the plant's batch."""
        lot = StockLot.objects.latest('pk')
        StockLot.objects.filter(pk=lot.pk).update(
            base_unit_cost=lot.base_unit_cost * 2, acquisition_total=lot.acquisition_total * 2,
        )
        reallocate_batch(self.plant.batch, self.user, CostAllocationRun.Trigger.MANUAL_RECALCULATE)

    def margin(self):
        """Return the order's margin as it reads now."""
        return order_margin(SalesOrder.objects.get(pk=self.order['pk']))

    def test_a_shipped_order_does_not_move_when_its_batch_is_recalculated(self):
        """Later costing activity on the batch cannot restate a closed order."""
        self.fulfill(self.order, self.allocation_ids)
        before = self.margin()
        line = FulfillmentLine.objects.get()
        self.reprice_input()
        after = self.margin()
        self.assertEqual(before['cost_total'], f'{line.cogs_amount:f}')
        self.assertEqual(after, before)

    def test_an_unshipped_order_still_prices_live(self):
        """Only the shipped part is read off the record."""
        before = self.margin()
        self.reprice_input()
        self.assertNotEqual(self.margin()['cost_total'], before['cost_total'])

    def test_a_dispatch_with_an_unknown_cost_is_an_incomplete_cost(self):
        """A snapshot of None is unknown, never zero."""
        self.fulfill(self.order, self.allocation_ids)
        FulfillmentLine.objects.update(cogs_amount=None, cogs_currency_code='')
        margin = self.margin()
        self.assertFalse(margin['cost_complete'])
        self.assertIsNone(margin['cost_total'])

    def test_a_returned_unit_is_priced_live_again(self):
        """The cost of a unit that came back is back with the stock."""
        fulfillment = self.fulfill(self.order, self.allocation_ids)
        returned = self.client.post(f"{self.orders_url}{self.order['pk']}/returns/", {
            'operation_key': str(uuid4()), 'reason': 'Customer changed plans.',
            'items': [{
                'fulfillment_line': fulfillment['lines'][0]['pk'],
                'outcome': 'available', 'destination': self.store.pk,
            }],
        }, format='json')
        self.assertEqual(returned.status_code, 201, returned.data)
        FulfillmentLine.objects.update(cogs_amount=Decimal('9.0000'))
        self.assertNotEqual(self.margin()['cost_total'], '9.0000')

    def test_the_margin_cost_is_the_profitability_reports_cost_for_the_order(self):
        """Both screens read one record of what the order cost."""
        self.fulfill(self.order, self.allocation_ids)
        self.reprice_input()
        rows = [
            row for row in profitability_report(self.workspace, {}).rows
            if row.get('order_id') == self.order['pk'] and row['kind'] == 'fulfillment'
        ]
        reported = sum(Decimal(row['plant_cogs']) for row in rows)
        self.assertEqual(self.margin()['cost_total'], f'{reported:f}')

    def test_part_of_a_line_is_priced_in_proportion(self):
        """A partly returned line keeps the matching share of its recorded cost."""
        line = SimpleNamespace(quantity=Decimal('4'))
        with mock.patch('sales.services.cost_of_line', return_value=CostOfSale(Decimal('1.0800'), 'NZD')):
            kept = _shipped_cost(line, Decimal('3'))
            whole = _shipped_cost(line, Decimal('4'))
        self.assertEqual(kept.amount, Decimal('0.8100'))
        self.assertEqual(whole.amount, Decimal('1.0800'))
        with mock.patch('sales.services.cost_of_line', return_value=CostOfSale(None, '', True)):
            self.assertIsNone(_shipped_cost(line, Decimal('3')).amount)
