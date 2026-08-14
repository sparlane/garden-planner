"""PostgreSQL proofs for exact-stock reservation locking."""

# pylint: disable=duplicate-code

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from plantings.lifecycle import EventType, OutcomeRequest, record_germination_event, record_lifecycle_event
from tests.factories import make_seed_tray, make_specific_plant
from workspaces.models import Workspace

from .models import SalesOrder, SalesOrderAllocation, SalesOrderLine
from .services import allocate_targets, confirm_order, create_order


class ReservationConcurrencyTestCase(TransactionTestCase):
    """Shared transaction fixtures and post-flush workspace restoration."""

    workspace = None
    user = None

    def _post_teardown(self):
        """Restore migration seed data removed by transactional flushing."""
        super()._post_teardown()
        if not Workspace.objects.filter(pk=settings.CURRENT_WORKSPACE_ID).exists():
            Workspace.objects.create(pk=settings.CURRENT_WORKSPACE_ID, name='My Garden')

    def _order_with_line(self, line_type, variety=None, tray_item=None):
        """Create one draft with a single exact-quantity line."""
        order = create_order(self.workspace, self.user)
        line = SalesOrderLine.objects.create(
            order=order,
            line_type=line_type,
            variety=variety,
            tray_item=tray_item,
            description='Concurrent target',
            quantity=1,
            unit_price=Decimal('10'),
            tax_rate=Decimal('15'),
        )
        return order, line

    def _confirm(self, order_pk):
        """Attempt confirmation from an independent database connection."""
        close_old_connections()
        order = SalesOrder.objects.get(pk=order_pk)
        user = get_user_model().objects.get(pk=self.user.pk)
        try:
            confirm_order(order, user)
        except ValidationError:
            result = 'rejected'
        else:
            result = 'confirmed'
        close_old_connections()
        return result


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentPlantReservationTests(ReservationConcurrencyTestCase):
    """Two drafts cannot reserve the same individual seedling."""

    def setUp(self):
        super().setUp()
        self.workspace = Workspace.objects.get(pk=settings.CURRENT_WORKSPACE_ID)
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='plant-reservation-racer')
        plant = make_specific_plant(workspace=self.workspace)
        record_germination_event(plant, self.user)
        record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.READY))
        self.order_pks = []
        for _index in range(2):
            order, line = self._order_with_line(SalesOrderLine.LineType.SEEDLING, variety=plant.batch.variety)
            allocate_targets(line, self.user, plant_ids=[plant.pk])
            self.order_pks.append(order.pk)

    def test_exactly_one_order_wins_the_plant(self):
        """The plant lock makes one confirmation observe the other's reservation."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(pool.map(self._confirm, self.order_pks))
        self.assertEqual(results, ['confirmed', 'rejected'])
        self.assertEqual(SalesOrderAllocation.objects.filter(status='reserved').count(), 1)


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentTrayReservationTests(ReservationConcurrencyTestCase):
    """Two drafts cannot reserve the same serialized tray unit."""

    def setUp(self):
        super().setUp()
        self.workspace = Workspace.objects.get(pk=settings.CURRENT_WORKSPACE_ID)
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='tray-reservation-racer')
        tray = make_seed_tray(workspace=self.workspace)
        self.order_pks = []
        for _index in range(2):
            order, line = self._order_with_line(SalesOrderLine.LineType.TRAY, tray_item=tray.inventory_unit.item)
            allocate_targets(line, self.user, unit_ids=[tray.inventory_unit_id])
            self.order_pks.append(order.pk)

    def test_exactly_one_order_wins_the_tray(self):
        """The unit lock makes one confirmation observe the other's reservation."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(pool.map(self._confirm, self.order_pks))
        self.assertEqual(results, ['confirmed', 'rejected'])
        self.assertEqual(SalesOrderAllocation.objects.filter(status='reserved').count(), 1)
