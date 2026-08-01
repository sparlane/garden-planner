"""PostgreSQL row-locking tests for inventory availability."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from workspaces.models import Workspace, get_current_workspace

from .ledger import (
    MovementRequest,
    OpeningBalanceRequest,
    post_opening_balance,
    post_stock_movement,
)
from .models import InventoryItem, InventoryLocation, StockLot, StockMovement
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
        location = InventoryLocation.objects.create(
            workspace=workspace,
            name='Concurrency store',
            code='CONCURRENT',
            location_type=InventoryLocation.LocationType.STORAGE,
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
        location = InventoryLocation.objects.get(pk=self.location_pk)
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
