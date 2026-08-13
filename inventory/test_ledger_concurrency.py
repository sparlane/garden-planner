"""PostgreSQL row-locking tests for inventory availability."""

# Transactional concurrency fixtures deliberately restore migration seed data.
# pylint: disable=duplicate-code

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from locations.models import Location
from workspaces.models import Workspace, get_current_workspace

from .ledger import (
    MovementRequest,
    OpeningBalanceRequest,
    UnitMovementRequest,
    post_opening_balance,
    post_stock_movement,
    post_unit_movement,
)
from .models import InventoryItem, Location, InventoryUnit, StockLot, StockMovement
from .models import Stocktake, StocktakeVariance
from .stocktakes import (
    approve_stocktake,
    begin_review,
    open_stocktake,
    post_reviewed_stocktake,
    record_count,
    resolve_variance,
)
from .units import UnitCode


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentLedgerTests(TransactionTestCase):
    """A locked lot serializes competing final-quantity consumers."""

    def _post_teardown(self):
        """Restore migration seed data removed by transactional test flushing."""
        super()._post_teardown()
        if not Workspace.objects.filter(pk=settings.CURRENT_WORKSPACE_ID).exists():
            Workspace.objects.create(
                pk=settings.CURRENT_WORKSPACE_ID,
                name='My Garden',
            )

    def setUp(self):
        super().setUp()
        workspace = get_current_workspace()
        workspace.currency_code = 'NZD'
        workspace.save()
        self.user = get_user_model().objects.create_user(
            username='ledger-concurrency-user',
        )
        item = InventoryItem.objects.create(
            workspace=workspace,
            name='Final quantity item',
            category=InventoryItem.Category.PACKAGING,
            base_unit=UnitCode.EACH,
        )
        location = Location.objects.create(
            workspace=workspace,
            name='Concurrency store',
            code='CONCURRENT',
            location_type=Location.LocationType.STORAGE,
        )
        lot, _movement = post_opening_balance(
            workspace,
            self.user,
            OpeningBalanceRequest(
                item=item,
                quantity=Decimal('10'),
                destination=location,
                acquisition_total=Decimal('10'),
                received_on=date(2026, 8, 1),
            ),
        )
        self.workspace_pk = workspace.pk
        self.lot_pk = lot.pk
        self.location_pk = location.pk

    def _consume_final_quantity(self):
        """Attempt one independent transaction from a separate DB connection."""
        close_old_connections()
        workspace = get_current_workspace()
        lot = StockLot.objects.get(pk=self.lot_pk)
        location = Location.objects.get(pk=self.location_pk)
        user = get_user_model().objects.get(pk=self.user.pk)
        try:
            post_stock_movement(
                workspace,
                user,
                MovementRequest(
                    lot=lot,
                    movement_type=StockMovement.MovementType.CONSUMPTION,
                    quantity=Decimal('10'),
                    source=location,
                ),
            )
        except ValidationError:
            result = 'rejected'
        else:
            result = 'posted'
        close_old_connections()
        return result

    def test_only_one_request_consumes_the_final_available_quantity(self):
        """The second transaction observes the first transaction's committed row."""
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda _index: self._consume_final_quantity(),
                range(2),
            ))

        self.assertCountEqual(results, ['posted', 'rejected'])
        self.assertEqual(
            StockMovement.objects.filter(
                lot_id=self.lot_pk,
                movement_type=StockMovement.MovementType.CONSUMPTION,
            ).count(),
            1,
        )


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentReviewedStocktakeTests(TransactionTestCase):
    """One approved posting cannot create duplicate variance corrections."""

    def _post_teardown(self):
        super()._post_teardown()
        if not Workspace.objects.filter(pk=settings.CURRENT_WORKSPACE_ID).exists():
            Workspace.objects.create(pk=settings.CURRENT_WORKSPACE_ID, name='My Garden')

    def setUp(self):
        super().setUp()
        workspace = get_current_workspace()
        user = get_user_model().objects.create_user(username='concurrent-stocktake')
        item = InventoryItem.objects.create(
            workspace=workspace, name='Counted media',
            category=InventoryItem.Category.GROWING_MEDIA,
            base_unit=UnitCode.MILLILITRE,
        )
        location = Location.objects.create(
            workspace=workspace, name='Count store', code='COUNT-STORE',
            location_type=Location.LocationType.STORAGE,
        )
        lot, _movement = post_opening_balance(
            workspace, user,
            OpeningBalanceRequest(
                item=item, quantity=Decimal('10'), destination=location,
                acquisition_total=Decimal('10'), received_on=date(2026, 8, 1),
            ),
        )
        stocktake = open_stocktake(
            workspace, user,
            {'location': location.pk, 'target_types': ['lot']},
        )
        target = stocktake.targets.get()
        record_count(stocktake, user, target.pk, counted_quantity=Decimal('8'))
        begin_review(stocktake, user)
        variance = StocktakeVariance.objects.get(target=target)
        resolve_variance(variance, user, action='adjust', reason='Physical count')
        approve_stocktake(stocktake, user)
        self.stocktake_pk = stocktake.pk
        self.user_pk = user.pk
        self.lot_pk = lot.pk

    def _post(self):
        close_old_connections()
        try:
            post_reviewed_stocktake(
                Stocktake.objects.get(pk=self.stocktake_pk),
                get_user_model().objects.get(pk=self.user_pk),
            )
        except ValidationError:
            result = 'rejected'
        else:
            result = 'posted'
        close_old_connections()
        return result

    def test_only_one_concurrent_post_applies_the_count(self):
        """The stocktake row lock serializes identical approved submissions."""
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: self._post(), range(2)))

        self.assertCountEqual(results, ['posted', 'rejected'])
        self.assertEqual(
            StockMovement.objects.filter(
                lot_id=self.lot_pk,
                movement_type=StockMovement.MovementType.ADJUSTMENT_LOSS,
            ).count(),
            1,
        )


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentSerializedUnitTests(TransactionTestCase):
    """A unit lock prevents two callers moving one physical identity twice."""

    def _post_teardown(self):
        """Restore migration seed data removed by transactional test flushing."""
        super()._post_teardown()
        if not Workspace.objects.filter(pk=settings.CURRENT_WORKSPACE_ID).exists():
            Workspace.objects.create(
                pk=settings.CURRENT_WORKSPACE_ID,
                name='My Garden',
            )

    def setUp(self):
        super().setUp()
        workspace = get_current_workspace()
        self.user = get_user_model().objects.create_user(
            username='unit-concurrency-user',
        )
        item = InventoryItem.objects.create(
            workspace=workspace,
            name='Concurrent serialized tray',
            category=InventoryItem.Category.TRAY,
            base_unit=UnitCode.EACH,
            tracking_mode=InventoryItem.TrackingMode.SERIALIZED,
        )
        source = Location.objects.create(
            workspace=workspace,
            name='Unit source',
            code='UNIT-SOURCE',
            location_type=Location.LocationType.STORAGE,
        )
        destination = Location.objects.create(
            workspace=workspace,
            name='Unit destination',
            code='UNIT-DESTINATION',
            location_type=Location.LocationType.GROWING,
        )
        lot = StockLot.objects.create(
            workspace=workspace,
            item=item,
            origin=StockLot.Origin.OPENING,
            received_on=date(2026, 8, 1),
            initial_base_quantity=Decimal('1'),
            acquisition_total=Decimal('5'),
            base_unit_cost=Decimal('5'),
            currency_code='NZD',
        )
        unit = InventoryUnit.objects.create(
            workspace=workspace,
            item=item,
            source_lot=lot,
            acquisition_cost=Decimal('5'),
            currency_code='NZD',
            current_location=source,
        )
        StockMovement.objects.create(
            workspace=workspace,
            lot=lot,
            unit=unit,
            movement_type=StockMovement.MovementType.OPENING,
            quantity=Decimal('1'),
            destination=source,
            occurred_at=timezone.now(),
        )
        self.unit_pk = unit.pk
        self.destination_pk = destination.pk

    def _transfer_unit(self):
        """Attempt one move from an independent database connection."""
        close_old_connections()
        workspace = get_current_workspace()
        unit = InventoryUnit.objects.get(pk=self.unit_pk)
        destination = Location.objects.get(pk=self.destination_pk)
        user = get_user_model().objects.get(pk=self.user.pk)
        try:
            post_unit_movement(
                workspace,
                user,
                UnitMovementRequest(
                    unit=unit,
                    movement_type=StockMovement.MovementType.TRANSFER,
                    destination=destination,
                ),
            )
        except ValidationError:
            result = 'rejected'
        else:
            result = 'posted'
        close_old_connections()
        return result

    def test_only_one_request_moves_the_unit(self):
        """The second caller sees the destination committed by the first."""
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda _index: self._transfer_unit(),
                range(2),
            ))

        self.assertCountEqual(results, ['posted', 'rejected'])
        self.assertEqual(
            StockMovement.objects.filter(
                unit_id=self.unit_pk,
                movement_type=StockMovement.MovementType.TRANSFER,
            ).count(),
            1,
        )
