"""PostgreSQL proofs for exact-stock reservation locking."""

# pylint: disable=duplicate-code,missing-function-docstring

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from inventory.ledger import IndividualizationRequest, individualize_lot_units
from inventory.models import InventoryItem, InventoryUnit, StockLot
from inventory.units import UnitCode
from locations.models import Location
from plantings.lifecycle import EventType, OutcomeRequest, record_germination_event, record_lifecycle_event
from tests.factories import make_seed_tray, make_specific_plant, make_stock_lot
from workspaces.models import Workspace

from .commerce import post_fulfillment
from .expiry import expire_due_reservations
from .models import ReservationEvent, SalesOrder, SalesOrderAllocation, SalesOrderLine
from .services import LotRequest, allocate_targets, confirm_order, create_order


class ReservationConcurrencyTestCase(TransactionTestCase):
    """Shared transaction fixtures and post-flush workspace restoration."""

    workspace = None
    user = None

    def _post_teardown(self):
        """Restore migration seed data removed by transactional flushing."""
        super()._post_teardown()
        if not Workspace.objects.filter(pk=settings.CURRENT_WORKSPACE_ID).exists():
            Workspace.objects.create(pk=settings.CURRENT_WORKSPACE_ID, name='My Garden')

    def _order_with_line(self, line_type, variety=None, item=None):
        """Create one draft with a single exact-quantity line."""
        order = create_order(self.workspace, self.user)
        line = SalesOrderLine.objects.create(
            order=order,
            line_type=line_type,
            variety=variety,
            item=item,
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
            order, line = self._order_with_line(SalesOrderLine.LineType.UNIT, item=tray.inventory_unit.item)
            allocate_targets(line, self.user, unit_ids=[tray.inventory_unit_id])
            self.order_pks.append(order.pk)

    def test_exactly_one_order_wins_the_tray(self):
        """The unit lock makes one confirmation observe the other's reservation."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(pool.map(self._confirm, self.order_pks))
        self.assertEqual(results, ['confirmed', 'rejected'])
        self.assertEqual(SalesOrderAllocation.objects.filter(status='reserved').count(), 1)


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentFulfillmentTests(ReservationConcurrencyTestCase):
    """One reserved allocation cannot be sold by two posting requests."""

    def setUp(self):
        super().setUp()
        self.workspace = Workspace.objects.get(pk=settings.CURRENT_WORKSPACE_ID)
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='fulfillment-racer')
        plant = make_specific_plant(workspace=self.workspace)
        record_germination_event(plant, self.user)
        record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.READY))
        order, line = self._order_with_line(
            SalesOrderLine.LineType.SEEDLING, variety=plant.batch.variety,
        )
        allocation = allocate_targets(line, self.user, plant_ids=[plant.pk])[0]
        confirm_order(order, self.user)
        self.order_pk = order.pk
        self.allocation_pk = allocation.pk

    def _fulfill(self, _index):
        close_old_connections()
        order = SalesOrder.objects.get(pk=self.order_pk)
        user = get_user_model().objects.get(pk=self.user.pk)
        try:
            post_fulfillment(
                order, user, operation_key=uuid4(),
                allocation_ids=[self.allocation_pk],
            )
        except ValidationError:
            result = 'rejected'
        else:
            result = 'fulfilled'
        close_old_connections()
        return result

    def test_exactly_one_fulfillment_posts(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(pool.map(self._fulfill, range(2)))
        self.assertEqual(results, ['fulfilled', 'rejected'])
        self.assertEqual(SalesOrderAllocation.objects.filter(status='fulfilled').count(), 1)


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentReservationExpiryTests(ReservationConcurrencyTestCase):
    """Two schedules sweeping at once expire one lapsed hold exactly once.

    The sweep is meant to be safe to run as often as a deployment likes, which
    means a slow run and the next tick overlapping has to be uneventful rather
    than a second release of the same hold. The order lock is what makes the
    later sweep re-read and find nothing due.
    """

    def setUp(self):
        super().setUp()
        self.workspace = Workspace.objects.get(pk=settings.CURRENT_WORKSPACE_ID)
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='expiry-racer')
        plant = make_specific_plant(workspace=self.workspace)
        record_germination_event(plant, self.user)
        record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.READY))
        order, line = self._order_with_line(
            SalesOrderLine.LineType.SEEDLING, variety=plant.batch.variety,
        )
        allocation = allocate_targets(line, self.user, plant_ids=[plant.pk])[0]
        confirm_order(order, self.user)
        SalesOrderAllocation.objects.filter(pk=allocation.pk).update(
            expires_at=timezone.now() - timedelta(hours=1),
        )
        self.allocation_pk = allocation.pk

    def _sweep(self, _index):
        close_old_connections()
        expired = expire_due_reservations(self.workspace)
        close_old_connections()
        return len(expired)

    def test_only_one_sweep_expires_the_hold(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(pool.map(self._sweep, range(2)))
        self.assertEqual(results, [0, 1])
        self.assertEqual(
            ReservationEvent.objects.filter(
                allocation_id=self.allocation_pk,
                event_type=ReservationEvent.EventType.EXPIRED,
            ).count(),
            1,
        )


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentCountedDrawTests(ReservationConcurrencyTestCase):
    """Two counted draws cannot both take the last of one anonymous pool."""

    def setUp(self):
        super().setUp()
        self.workspace = Workspace.objects.get(pk=settings.CURRENT_WORKSPACE_ID)
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='counted-draw-racer')
        self.store = Location.objects.create(
            workspace=self.workspace,
            name='Pot store',
            code='RACE-POT-STORE',
            location_type=Location.LocationType.STORAGE,
        )
        self.item = InventoryItem.objects.create(
            workspace=self.workspace,
            name='P9 pot',
            category=InventoryItem.Category.POT_CONTAINER,
            base_unit=UnitCode.EACH,
            tracking_mode=InventoryItem.TrackingMode.MIXED,
        )
        self.lot = make_stock_lot(
            item=self.item,
            location=self.store,
            quantity=Decimal('10'),
            base_unit_cost=Decimal('0.5000'),
            acquisition_total=Decimal('5.0000'),
        )
        self.order_pks = []
        for _index in range(2):
            order, line = self._counted_order(quantity=8)
            allocate_targets(line, self.user, lot_requests=[
                LotRequest(self.lot.pk, self.store.pk, 8),
            ])
            self.order_pks.append(order.pk)

    def _counted_order(self, quantity):
        """Create one draft whose single line is filled by the count."""
        order = create_order(self.workspace, self.user)
        line = SalesOrderLine.objects.create(
            order=order,
            line_type=SalesOrderLine.LineType.LOT_QUANTITY,
            item=self.item,
            description='Loose pots',
            quantity=quantity,
            unit_price=Decimal('0.8000'),
            tax_rate=Decimal('15'),
        )
        return order, line

    def test_exactly_one_order_wins_the_remaining_pots(self):
        """Eight and eight out of ten, so the lot lock has to reject one."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(pool.map(self._confirm, self.order_pks))

        self.assertEqual(results, ['confirmed', 'rejected'])
        self.assertEqual(
            SalesOrderAllocation.objects.filter(status='reserved').count(), 1,
        )

    def test_a_draw_and_a_numbering_cannot_both_take_the_last_pots(self):
        """They share one pool, so they have to serialise against each other."""
        def number():
            """Number eight pots from an independent database connection."""
            close_old_connections()
            user = get_user_model().objects.get(pk=self.user.pk)
            try:
                individualize_lot_units(
                    self.workspace, user,
                    IndividualizationRequest(
                        lot=StockLot.objects.get(pk=self.lot.pk),
                        location=Location.objects.get(pk=self.store.pk),
                        count=8,
                    ),
                )
            except ValidationError:
                result = 'rejected'
            else:
                result = 'numbered'
            close_old_connections()
            return result

        with ThreadPoolExecutor(max_workers=2) as pool:
            confirmation = pool.submit(self._confirm, self.order_pks[0])
            numbering = pool.submit(number)
            results = sorted([confirmation.result(), numbering.result()])

        # Whichever won, the pool is never oversold: eight reserved and eight
        # numbered out of ten would put six pots in two places at once.
        self.assertIn(results, [['confirmed', 'rejected'], ['numbered', 'rejected']])
        reserved = SalesOrderAllocation.objects.filter(status='reserved').count()
        numbered = InventoryUnit.objects.filter(source_lot=self.lot, active=True).count()
        self.assertLessEqual(reserved * 8 + numbered, 10)
