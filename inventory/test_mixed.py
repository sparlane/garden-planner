"""Behavioral tests for lot stock that can also be individually numbered."""

# These deliberately mirror the shape of `test_serialized.py`'s fixtures,
# because the point of most of them is that a mixed item behaves like a lot
# item until something is actually numbered; reading the two side by side is
# how that stays checkable.
# pylint: disable=duplicate-code

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from labels.models import LabelCode, LabelIdentity
from labels.services import ensure_identity
from reporting.inventory import inventory_balances
from locations.models import Location
from supplies.models import Supplier
from workspaces.models import Workspace, get_current_workspace

from .ledger import (
    IndividualizationRequest,
    MovementRequest,
    UnitMovementRequest,
    bulk_balance,
    discard_numbering,
    individualize_lot_units,
    physical_balance,
    post_stock_movement,
    post_unit_movement,
    quantize_money,
)
from .models import (
    InventoryItem,
    InventoryUnit,
    StockLot,
    StockMovement,
    StockReceipt,
    StockReceiptLine,
    Stocktake,
    StocktakeTarget,
)
from .stocktakes import resolve_identity_target, scope_rows
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


class IndividualizationTests(MixedTrackingTestCase):
    """Numbering part of a lot is deliberate, bounded, and free of movements."""

    def individualize(self, lot, count, location=None):
        """Number part of a lot through the service."""
        return individualize_lot_units(
            self.workspace, self.user,
            IndividualizationRequest(
                lot=lot,
                location=location or self.store,
                count=count,
                reason='Specimen containers',
            ),
        )

    def test_numbering_creates_identities_and_posts_no_movements(self):
        """The units are the whole record; the ledger has nothing to say."""
        lot = self.receive()
        before = StockMovement.objects.filter(lot=lot).count()

        units = self.individualize(lot, 6)

        self.assertEqual(len(units), 6)
        self.assertEqual(len({unit.asset_code for unit in units}), 6)
        self.assertEqual(StockMovement.objects.filter(lot=lot).count(), before)
        self.assertEqual(physical_balance(lot, self.store), Decimal('500'))
        self.assertEqual(bulk_balance(lot, self.store), Decimal('494'))

    def test_numbering_records_who_did_it(self):
        """Without a movement, the unit itself has to carry the actor."""
        lot = self.receive()

        units = self.individualize(lot, 1)

        self.assertEqual(units[0].created_by, self.user)

    def test_unit_costs_sum_to_the_bulk_they_replaced(self):
        """A price that does not divide evenly still loses no cent."""
        lot = self.receive(quantity='3', cost='1.0000')
        self.assertIsNotNone(lot.base_unit_cost)

        units = self.individualize(lot, 3)

        total = sum(unit.acquisition_cost for unit in units)
        self.assertEqual(total, quantize_money(lot.base_unit_cost * 3))

    def test_numbering_cannot_exceed_the_unnumbered_remainder(self):
        """The bulk figure, not the whole balance, is what is available."""
        lot = self.receive(quantity='10', cost='5.0000')
        self.individualize(lot, 8)

        with self.assertRaises(ValidationError) as context:
            self.individualize(lot, 3)
        self.assertIn('count', context.exception.message_dict)

        # The two that are left are still numberable.
        self.assertEqual(len(self.individualize(lot, 2)), 2)

    def test_lot_and_serialized_items_are_refused(self):
        """Numbering is the mixed mode's privilege, not every item's."""
        self.item.tracking_mode = InventoryItem.TrackingMode.LOT
        self.item.save()
        lot = self.receive()

        with self.assertRaises(ValidationError) as context:
            self.individualize(lot, 1)
        self.assertIn('lot', context.exception.message_dict)


class IndividualizationBulkOutflowTests(MixedTrackingTestCase):
    """Bulk stock leaving a mixed lot is held to the bulk figure."""

    def test_a_bulk_sale_cannot_draw_down_numbered_pots(self):
        """Numbered pots are on hand but are not anonymous stock to sell."""
        lot = self.receive(quantity='10', cost='5.0000')
        individualize_lot_units(
            self.workspace, self.user,
            IndividualizationRequest(lot=lot, location=self.store, count=6),
        )

        with self.assertRaises(ValidationError) as context:
            post_stock_movement(
                self.workspace, self.user,
                MovementRequest(
                    lot=lot,
                    movement_type=StockMovement.MovementType.CONSUMPTION,
                    quantity=Decimal('5'),
                    source=self.store,
                    reason='Potting on',
                ),
            )
        self.assertIn('quantity', context.exception.message_dict)

        # The four that are genuinely anonymous still move.
        post_stock_movement(
            self.workspace, self.user,
            MovementRequest(
                lot=lot,
                movement_type=StockMovement.MovementType.CONSUMPTION,
                quantity=Decimal('4'),
                source=self.store,
                reason='Potting on',
            ),
        )
        self.assertEqual(bulk_balance(lot, self.store), Decimal('0'))


class DiscardNumberingTests(MixedTrackingTestCase):
    """A numbering typo is correctable only while the unit is untouched."""

    def number_one(self, lot):
        """Number a single pot and return it."""
        return individualize_lot_units(
            self.workspace, self.user,
            IndividualizationRequest(lot=lot, location=self.store, count=1),
        )[0]

    def test_an_unused_numbering_can_be_discarded(self):
        """Nothing was posted, so there is nothing to unwind but the row."""
        lot = self.receive(quantity='10', cost='5.0000')
        unit = self.number_one(lot)
        self.assertEqual(bulk_balance(lot, self.store), Decimal('9'))

        discard_numbering(self.workspace, unit)

        self.assertFalse(InventoryUnit.objects.filter(pk=unit.pk).exists())
        self.assertEqual(bulk_balance(lot, self.store), Decimal('10'))

    def test_a_unit_with_stock_history_keeps_its_identity(self):
        """Once a pot has moved, its identity is part of the record."""
        bench = Location.objects.create(
            workspace=self.workspace,
            name='Bench',
            code='BENCH',
            location_type=Location.LocationType.GROWING,
        )
        lot = self.receive(quantity='10', cost='5.0000')
        unit = self.number_one(lot)
        post_unit_movement(
            self.workspace, self.user,
            UnitMovementRequest(
                unit=unit,
                movement_type=StockMovement.MovementType.TRANSFER,
                destination=bench,
                reason='Moved',
            ),
        )

        with self.assertRaises(ValidationError) as context:
            discard_numbering(self.workspace, unit)
        self.assertIn('unit', context.exception.message_dict)
        self.assertTrue(InventoryUnit.objects.filter(pk=unit.pk).exists())

    def test_a_labelled_unit_keeps_its_identity(self):
        """A printed code that resolved to nothing would be worse than a typo."""
        lot = self.receive(quantity='10', cost='5.0000')
        unit = self.number_one(lot)
        # Built directly, because units become a labelable target type in a
        # later change. The guard reads the identity row either way, so this
        # keeps working once they do.
        LabelIdentity.objects.create(
            workspace=self.workspace,
            target_content_type=ContentType.objects.get_for_model(InventoryUnit),
            target_object_id=unit.pk,
            target_snapshot={'display': unit.asset_code, 'pk': unit.pk},
        )

        with self.assertRaises(ValidationError) as context:
            discard_numbering(self.workspace, unit)
        self.assertIn('unit', context.exception.message_dict)

    def test_ordinary_deletion_is_still_refused(self):
        """The service is the only door; the model guard stays absolute."""
        lot = self.receive(quantity='10', cost='5.0000')
        unit = self.number_one(lot)

        with self.assertRaisesMessage(ValidationError, 'cannot be deleted'):
            unit.delete()


class MixedStocktakeTests(MixedTrackingTestCase):
    """A stocktake counts the loose pots and the numbered ones separately."""

    def scope_rows_here(self):
        """Build the count sheet for everything standing in the store."""
        return scope_rows(self.workspace, {'location': self.store.pk})

    def test_bulk_and_numbered_pots_are_counted_as_separate_targets(self):
        """Neither is invisible, and neither is counted twice."""
        lot = self.receive(quantity='10', cost='5.0000')
        individualize_lot_units(
            self.workspace, self.user,
            IndividualizationRequest(lot=lot, location=self.store, count=3),
        )

        rows = self.scope_rows_here()
        lots = [row for row in rows if row['target_type'] == StocktakeTarget.TargetType.LOT]
        units = [row for row in rows if row['target_type'] == StocktakeTarget.TargetType.UNIT]

        # Seven loose pots to find, and three identities to scan.
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0]['expected_quantity'], Decimal('7.000000000'))
        self.assertEqual(len(units), 3)
        self.assertEqual({row['expected_quantity'] for row in units}, {Decimal('1')})

    def test_a_lot_item_is_still_counted_whole(self):
        """The bulk figure only differs where something has been numbered."""
        self.item.tracking_mode = InventoryItem.TrackingMode.LOT
        self.item.save()
        lot = self.receive(quantity='10', cost='5.0000')

        rows = self.scope_rows_here()
        lots = [row for row in rows if row['target_type'] == StocktakeTarget.TargetType.LOT]

        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0]['expected_quantity'], Decimal('10.000000000'))
        self.assertEqual(lot.item.tracking_mode, InventoryItem.TrackingMode.LOT)

    def test_a_numbered_pot_resolves_when_its_label_is_scanned(self):
        """Counting by scan needs the unit registered as an identity target."""
        lot = self.receive(quantity='10', cost='5.0000')
        unit = individualize_lot_units(
            self.workspace, self.user,
            IndividualizationRequest(lot=lot, location=self.store, count=1),
        )[0]
        stocktake = Stocktake.objects.create(
            workspace=self.workspace,
            scope={'location': self.store.pk},
            counted_at=timezone.now(),
            created_by=self.user,
        )
        identity = LabelIdentity.objects.create(
            workspace=self.workspace,
            target_content_type=ContentType.objects.get_for_model(InventoryUnit),
            target_object_id=unit.pk,
            target_snapshot={'display': unit.asset_code, 'pk': unit.pk},
        )

        target = resolve_identity_target(stocktake, identity)

        self.assertEqual(target.target_type, StocktakeTarget.TargetType.UNIT)
        self.assertEqual(target.target_object_id, unit.pk)


class MixedBalanceReportingTests(MixedTrackingTestCase):
    """Balance surfaces separate what is loose from what has a number."""

    def balance_rows(self, lot):
        """Return this lot's balance rows keyed by location."""
        response = self.client.get('/inventory/balances/', {'lot': lot.pk})
        self.assertEqual(response.status_code, 200, response.data)
        return {row['location']: row for row in response.data}

    def test_balances_split_bulk_from_numbered_without_changing_the_total(self):
        """On hand is still on hand; the split says how much can be picked."""
        lot = self.receive(quantity='10', cost='5.0000')
        individualize_lot_units(
            self.workspace, self.user,
            IndividualizationRequest(lot=lot, location=self.store, count=3),
        )

        row = self.balance_rows(lot)[self.store.pk]

        self.assertEqual(row['physical_quantity'], '10.000000000')
        self.assertEqual(row['bulk_quantity'], '7.000000000')
        self.assertEqual(row['numbered_quantity'], '3.000000000')

    def test_a_plain_lot_reports_all_of_itself_as_bulk(self):
        """Nothing is numbered, so the split is the whole quantity and zero."""
        self.item.tracking_mode = InventoryItem.TrackingMode.LOT
        self.item.save()
        lot = self.receive(quantity='10', cost='5.0000')

        row = self.balance_rows(lot)[self.store.pk]

        self.assertEqual(row['bulk_quantity'], '10.000000000')
        self.assertEqual(row['numbered_quantity'], '0.000000000')

    def test_the_inventory_report_carries_the_same_split(self):
        """The report and the endpoint must not disagree about one lot."""
        lot = self.receive(quantity='10', cost='5.0000')
        individualize_lot_units(
            self.workspace, self.user,
            IndividualizationRequest(lot=lot, location=self.store, count=4),
        )

        report = inventory_balances(self.workspace, {'lot': lot.pk})
        row = next(row for row in report.rows if row['location_id'] == self.store.pk)

        self.assertEqual(row['physical_quantity'], '10.000000000')
        self.assertEqual(row['bulk_quantity'], '6.000000000')
        self.assertEqual(row['numbered_quantity'], '4.000000000')


class NumberedPotLabelTests(MixedTrackingTestCase):
    """A numbered pot carries a code an operator can print and scan."""

    def setUp(self):
        super().setUp()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.lot = self.receive(quantity='10', cost='5.0000')
        self.unit = individualize_lot_units(
            self.workspace, self.user,
            IndividualizationRequest(lot=self.lot, location=self.store, count=1),
        )[0]

    def resolve(self, code):
        """Resolve one scanned code the way the scanner does."""
        response = self.client.get('/labels/resolve/', {'value': code})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_a_numbered_pot_can_be_issued_a_code_and_scanned_back(self):
        """The pot is the target; the code leads to its own detail route."""
        identity = ensure_identity(self.unit, user=self.user)
        code = identity.codes.get(status=LabelCode.Status.ACTIVE)

        resolution = self.resolve(code.code)

        self.assertEqual(resolution['status'], 'active')
        self.assertTrue(code.code.startswith('UNT-'))
        self.assertEqual(resolution['target']['object_id'], self.unit.pk)
        self.assertEqual(resolution['deep_link'], f'/inventory/serialized-units/{self.unit.pk}')
        self.assertIn(self.unit.asset_code, resolution['target']['display'])
        self.assertIn('stocktake_count', resolution['capabilities'])
        self.assertEqual(identity.target_object_id, self.unit.pk)

    def test_a_pot_that_has_left_stock_resolves_as_inactive(self):
        """Scanning a wasted pot should say so, not point at a live asset."""
        identity = ensure_identity(self.unit, user=self.user)
        code = identity.codes.get(status=LabelCode.Status.ACTIVE)
        post_unit_movement(
            self.workspace, self.user,
            UnitMovementRequest(
                unit=self.unit,
                movement_type=StockMovement.MovementType.WASTE,
                reason='Cracked',
            ),
        )

        resolution = self.resolve(code.code)

        self.assertEqual(resolution['status'], 'inactive')
