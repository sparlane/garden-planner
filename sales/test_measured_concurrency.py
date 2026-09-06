"""PostgreSQL races over measured reservations and dispatch ceilings."""

# pylint: disable=duplicate-code

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import skipUnlessDBFeature

from inventory.ledger import unpromised_bulk
from inventory.models import InventoryItem
from inventory.units import UnitCode
from locations.models import Location
from tests.factories import make_stock_lot
from workspaces.models import Workspace

from .commerce import post_fulfillment, post_return
from .models import FulfillmentLine, SalesOrder, SalesOrderAllocation, SalesOrderLine, SalesReturnLine
from .services import LotRequest, allocate_targets, confirm_order, create_order
from .test_concurrency import ReservationConcurrencyTestCase


@skipUnlessDBFeature('has_select_for_update')
class MeasuredConcurrencyTests(ReservationConcurrencyTestCase):
    """Two transactions must observe one shared lot and one quantity ceiling."""

    def setUp(self):
        super().setUp()
        self.workspace = Workspace.objects.get(pk=settings.CURRENT_WORKSPACE_ID)
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='measured-racer')
        self.store = Location.objects.create(workspace=self.workspace, name='Measured store', code='MS', location_type=Location.LocationType.STORAGE)
        self.item = InventoryItem.objects.create(
            workspace=self.workspace, name='Medium', category=InventoryItem.Category.GROWING_MEDIA,
            tracking_mode=InventoryItem.TrackingMode.LOT, base_unit=UnitCode.LITRE,
        )
        self.lot = make_stock_lot(item=self.item, location=self.store, quantity=Decimal('1.2'))

    def promise(self):
        """Prepare one pending draw that fits alone but not alongside another."""
        order = create_order(self.workspace, self.user)
        line = SalesOrderLine.objects.create(
            order=order, item=self.item, line_type=SalesOrderLine.LineType.LOT_QUANTITY,
            quantity=Decimal('0.8'), unit=UnitCode.LITRE, description='Medium',
            unit_price=Decimal('2'), tax_rate=Decimal('15'),
        )
        allocation = allocate_targets(line, self.user, lot_requests=[
            LotRequest(self.lot.pk, self.store.pk, Decimal('0.8')),
        ])[0]
        return order, allocation

    def race(self, operation):
        """Start independent connections together and propagate unexpected errors."""
        barrier = Barrier(2)

        def attempt(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                operation(index)
                return 'posted'
            except ValidationError:
                return 'rejected'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(attempt, range(2))), ['posted', 'rejected'])

    def test_two_customers_cannot_overpromise_one_measured_lot(self):
        """The second confirmation sees the first customer's decimal hold."""
        orders = [self.promise()[0] for _ in range(2)]
        self.race(lambda index: confirm_order(SalesOrder.objects.get(pk=orders[index].pk), self.user))
        self.assertEqual(unpromised_bulk(self.lot, self.store), Decimal('0.4'))

    def test_two_partial_dispatches_cannot_exceed_one_reservation(self):
        """Locking the order and lot protects the sum of immutable dispatches."""
        order, allocation = self.promise()
        confirm_order(order, self.user)
        self.race(lambda _index: post_fulfillment(
            order, self.user, operation_key=uuid4(), allocation_ids=[allocation.pk],
            quantities={allocation.pk: Decimal('0.5')},
        ))
        self.assertEqual(FulfillmentLine.objects.count(), 1)
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, SalesOrderAllocation.Status.RESERVED)
        self.assertEqual(unpromised_bulk(self.lot, self.store), Decimal('0.4'))

    def test_two_returns_cannot_exceed_the_measured_dispatch(self):
        """The second return reads the first return under the same order lock."""
        order, allocation = self.promise()
        confirm_order(order, self.user)
        fulfillment = post_fulfillment(order, self.user, operation_key=uuid4(), allocation_ids=[allocation.pk])
        line = fulfillment.lines.get()
        self.race(lambda _index: post_return(
            order, self.user, operation_key=uuid4(), reason='Surplus',
            items=[{'fulfillment_line': line, 'quantity': Decimal('0.5'),
                    'outcome': SalesReturnLine.Outcome.AVAILABLE, 'destination': self.store}],
        ))
        self.assertEqual(SalesReturnLine.objects.count(), 1)
