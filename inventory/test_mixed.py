"""Behavioral tests for lot stock that can also be individually numbered."""

# These deliberately mirror the shape of `test_serialized.py`'s fixtures,
# because the point of most of them is that a mixed item behaves like a lot
# item until something is actually numbered; reading the two side by side is
# how that stays checkable.
# pylint: disable=duplicate-code

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from locations.models import Location
from supplies.models import Supplier
from workspaces.models import get_current_workspace

from .ledger import (
    UnitMovementRequest,
    bulk_balance,
    physical_balance,
    post_unit_movement,
)
from .models import (
    InventoryItem,
    InventoryUnit,
    StockLot,
    StockMovement,
    StockReceipt,
    StockReceiptLine,
)
from .units import UnitCode


class MixedTrackingTestCase(TestCase):
    """A workspace holding a boxed pot item that may be numbered later."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.user = get_user_model().objects.create_user(username='mixed-user')
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(
            workspace=self.workspace,
            name='Pot supplier',
        )
        self.item = InventoryItem.objects.create(
            workspace=self.workspace,
            name='P9 pot',
            category=InventoryItem.Category.POT_CONTAINER,
            base_unit=UnitCode.EACH,
            tracking_mode=InventoryItem.TrackingMode.MIXED,
        )
        self.store = Location.objects.create(
            workspace=self.workspace,
            name='Pot store',
            code='POT-STORE',
            location_type=Location.LocationType.STORAGE,
        )

    def receive(self, quantity='500', cost='250.0000'):
        """Post one bulk receipt of pots and return its lot."""
        receipt = StockReceipt.objects.create(
            workspace=self.workspace,
            supplier=self.supplier,
            received_date=date(2026, 8, 2),
            currency_code=self.workspace.currency_code,
            created_by=self.user,
        )
        StockReceiptLine.objects.create(
            receipt=receipt,
            item=self.item,
            quantity=Decimal(quantity),
            unit_code=UnitCode.EACH,
            base_quantity=Decimal(quantity),
            line_cost_ex_tax=Decimal(cost),
            destination=self.store,
        )
        response = self.client.post(f'/inventory/receipts/{receipt.pk}/post/')
        self.assertEqual(response.status_code, 200, response.data)
        return StockLot.objects.get(receipt_line__receipt=receipt)


class MixedReceiptTests(MixedTrackingTestCase):
    """Receiving mixed stock is receiving bulk, not minting identities."""

    def test_receipt_creates_bulk_stock_and_no_units(self):
        """Numbering is a later, deliberate act, never a side effect."""
        lot = self.receive()

        self.assertEqual(InventoryUnit.objects.filter(source_lot=lot).count(), 0)
        movements = StockMovement.objects.filter(lot=lot)
        self.assertEqual(movements.count(), 1)
        self.assertIsNone(movements.get().unit_id)
        self.assertEqual(physical_balance(lot, self.store), Decimal('500'))

    def test_bulk_balance_matches_physical_balance_before_numbering(self):
        """The two figures only diverge once a unit is drawn from the lot."""
        lot = self.receive()

        self.assertEqual(
            bulk_balance(lot, self.store),
            physical_balance(lot, self.store),
        )


class MixedBulkBalanceTests(MixedTrackingTestCase):
    """The derived bulk figure tracks units without any ledger of its own."""

    def number(self, lot, count, location=None):
        """Create numbered units the way Stage 2's service will."""
        return [
            InventoryUnit.objects.create(
                workspace=self.workspace,
                item=self.item,
                source_lot=lot,
                acquisition_cost=Decimal('0.5000'),
                currency_code=self.workspace.currency_code,
                current_location=location or self.store,
            )
            for _ in range(count)
        ]

    def test_numbering_lowers_bulk_and_leaves_stock_on_hand_untouched(self):
        """Nothing entered or left, so only the anonymous remainder moves."""
        lot = self.receive()

        self.number(lot, 6)

        self.assertEqual(physical_balance(lot, self.store), Decimal('500'))
        self.assertEqual(bulk_balance(lot, self.store), Decimal('494'))

    def test_a_retired_unit_stops_counting_against_bulk(self):
        """A pot that has left stock is no longer holding a place in the lot."""
        lot = self.receive()
        units = self.number(lot, 6)

        InventoryUnit.objects.filter(pk=units[0].pk).update(
            active=False, current_location=None,
        )

        self.assertEqual(bulk_balance(lot, self.store), Decimal('495'))

    def test_transferring_a_numbered_pot_keeps_both_locations_honest(self):
        """Units and bulk draw down one lot, so a move shifts both figures."""
        bench = Location.objects.create(
            workspace=self.workspace,
            name='Sales bench',
            code='SALES-BENCH',
            location_type=Location.LocationType.GROWING,
        )
        lot = self.receive()
        units = self.number(lot, 6)

        for unit in units[:3]:
            post_unit_movement(
                self.workspace, self.user,
                UnitMovementRequest(
                    unit=unit,
                    movement_type=StockMovement.MovementType.TRANSFER,
                    destination=bench,
                    reason='Moved to the sales bench',
                ),
            )

        # The store keeps 500 received less the 3 that physically left, and
        # still holds 3 numbered pots of its own.
        self.assertEqual(physical_balance(lot, self.store), Decimal('497'))
        self.assertEqual(bulk_balance(lot, self.store), Decimal('494'))
        # The bench holds 3 pots, all of them numbered, so none of it is bulk.
        self.assertEqual(physical_balance(lot, bench), Decimal('3'))
        self.assertEqual(bulk_balance(lot, bench), Decimal('0'))

    def test_another_lots_units_never_reduce_this_lots_bulk(self):
        """The count is scoped to the lot the units were drawn from."""
        lot = self.receive()
        other = self.receive(quantity='10', cost='5.0000')
        self.number(other, 3)

        self.assertEqual(bulk_balance(lot, self.store), Decimal('500'))
        self.assertEqual(bulk_balance(other, self.store), Decimal('7'))
