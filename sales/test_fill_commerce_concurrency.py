"""Dispatch and numbering contend for the same physical pot."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, skipUnlessDBFeature

from inventory.ledger import unpromised_bulk
from inventory.models import StockMovement
from plantings.counted_fills import plant_counted_fill
from plantings.fill_numbering import number_counted_pot
from plantings.lifecycle import EventType, OutcomeRequest, record_germination_event, record_lifecycle_event
from seedtrays.container_fills import open_counted_fill
from tests.factories import make_location, make_inventory_item, make_specific_plant, make_stock_lot
from workspaces.models import Workspace

from .commerce import post_fulfillment
from .models import SalesOrderLine, FulfillmentContainer
from .services import create_order, allocate_targets, confirm_order


@skipUnlessDBFeature('has_select_for_update')
class FillCommerceConcurrencyTests(TransactionTestCase):  # pylint: disable=too-many-instance-attributes
    """A pot may be numbered before dispatch, but it can leave only once."""

    def _post_teardown(self):
        super()._post_teardown()
        Workspace.objects.get_or_create(pk=settings.CURRENT_WORKSPACE_ID, defaults={'name': 'My Garden'})

    def setUp(self):
        super().setUp()
        self.workspace = Workspace.objects.get()
        self.workspace.mode = 'nursery'
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='fill-commerce-race')
        self.store = make_location()
        self.lot = make_stock_lot(item=make_inventory_item(category='pot_container', tracking_mode='mixed', base_unit='each'),
                                  location=self.store, quantity='2', base_unit_cost=Decimal('3'))
        self.fill = open_counted_fill(self.workspace, self.user, self.lot, self.store, 2)
        self.plant = make_specific_plant()
        record_germination_event(self.plant, self.user)
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))
        plant_counted_fill(self.workspace, self.user, self.fill, [self.plant.pk])
        self.order = create_order(self.workspace, self.user)
        line = SalesOrderLine.objects.create(order=self.order, line_type='seedling', variety=self.plant.batch.variety,
                                             description='Potted plant', quantity=1, unit_price=10, tax_rate=0)
        self.allocation, = allocate_targets(line, self.user, plant_ids=[self.plant.pk])
        confirm_order(self.order, self.user)

    def run_action(self, numbering):
        """Bound lock waits so a regression cannot leave the test run stuck."""
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
            if numbering:
                try:
                    number_counted_pot(self.workspace, self.user, self.fill, self.plant.pk)
                except ValidationError:
                    return 'departed'
                return 'numbered'
            post_fulfillment(self.order, self.user, operation_key=uuid4(), allocation_ids=[self.allocation.pk],
                             container_allocations=[self.allocation.pk])
            return 'shipped'
        finally:
            connection.close()

    def test_numbering_races_dispatch_without_releasing_or_selling_an_extra_pot(self):
        """Both serial outcomes retain one dispatch and one remaining filled pot."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.run_action, value) for value in (False, True)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(results[0], 'shipped')
        self.assertIn(results[1], ['numbered', 'departed'])
        self.assertEqual(FulfillmentContainer.objects.count(), 1)
        self.assertEqual(StockMovement.objects.filter(movement_type='sale').count(), 1)
        self.assertEqual(unpromised_bulk(self.lot, self.store), 0)
