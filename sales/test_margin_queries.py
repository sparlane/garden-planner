"""An order's margin costs a bounded number of queries (task 141).

The count is asserted against itself rather than against a literal: what must
not happen is for it to grow with the allocations an order holds or the plants
in the batch behind them, so the tests compare a small case with a large one.
"""

# pylint: disable=duplicate-code

from django.db import connection
from django.test.utils import CaptureQueriesContext

from .models import SalesOrder
from .services import order_margin
from .test_commerce import CommerceFixtureTestCase


class MarginQueryBoundTests(CommerceFixtureTestCase):
    """The same batch behind a growing number of allocations."""

    def order_of(self, count, bystanders=0):
        """Return an order promising `count` plants of one batch, and its allocations."""
        first = self.available_plant()
        plants = [first] + [self.available_plant(cell_planting=first.cell_planting) for _ in range(count - 1)]
        for _ in range(bystanders):
            self.available_plant(cell_planting=first.cell_planting)
        order, allocations = self.confirmed_order(plants)
        return SalesOrder.objects.get(pk=order['pk']), allocations

    @staticmethod
    def queries(order):
        """Return how many queries reading this order's margin takes."""
        with CaptureQueriesContext(connection) as captured:
            order_margin(order)
        return len(captured)

    def test_the_count_does_not_grow_with_the_allocations(self):
        """Six plants cost what two do."""
        small, _ = self.order_of(2)
        large, _ = self.order_of(6)
        self.assertEqual(self.queries(large), self.queries(small))

    def test_the_count_does_not_grow_with_the_batch(self):
        """A batch of many plants is not replayed to price a few of them."""
        small, _ = self.order_of(2)
        crowded, _ = self.order_of(2, bystanders=30)
        self.assertEqual(self.queries(crowded), self.queries(small))

    def test_the_count_does_not_grow_once_the_order_has_shipped(self):
        """Reading dispatches and returns is not a query per line either."""
        small, small_allocations = self.order_of(2)
        large, large_allocations = self.order_of(6)
        self.fulfill({'pk': small.pk}, [row['pk'] for row in small_allocations])
        self.fulfill({'pk': large.pk}, [row['pk'] for row in large_allocations])
        self.assertEqual(self.queries(large), self.queries(small))
