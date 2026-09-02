"""Behavioral tests for lot stock that can also be individually numbered."""

# These deliberately mirror the shape of `test_serialized.py`'s fixtures,
# because the point of most of them is that a mixed item behaves like a lot
# item until something is actually numbered; reading the two side by side is
# how that stays checkable.
# pylint: disable=duplicate-code

from datetime import date
from uuid import uuid4
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from labels.models import LabelCode, LabelIdentity, LabelPrintItem, LabelPrintJob
from locations.models import Location
from locations.occupancy import location_occupancy
from plantings.lifecycle import (
    EventType,
    LifecycleState,
    OutcomeRequest,
    plant_lifecycle_summary,
    record_lifecycle_event,
)
from plantings.models import SpecificPlantLocation
from reporting.inventory import inventory_balances
from sales.commerce import post_fulfillment, post_return
from sales.models import SalesOrderLine, SalesReturnLine
from sales.services import allocate_targets, confirm_order, create_order
from supplies.models import Supplier
from tests.factories import make_seed_tray, make_specific_plant
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
from .stocktakes import _plant_location, resolve_identity_target, scope_rows
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

    def test_holding_an_unprinted_code_does_not_block_the_undo(self):
        """Every numbered pot is issued a code, so a code cannot be the test."""
        lot = self.receive(quantity='10', cost='5.0000')
        unit = self.number_one(lot)
        self.assertTrue(LabelIdentity.objects.filter(
            target_content_type=ContentType.objects.get_for_model(InventoryUnit),
            target_object_id=unit.pk,
        ).exists())

        discard_numbering(self.workspace, unit)

        self.assertFalse(InventoryUnit.objects.filter(pk=unit.pk).exists())

    def test_a_printed_label_keeps_the_unit(self):
        """A label loose in the nursery must still resolve to a real pot."""
        lot = self.receive(quantity='10', cost='5.0000')
        unit = self.number_one(lot)
        identity = LabelIdentity.objects.get(
            target_content_type=ContentType.objects.get_for_model(InventoryUnit),
            target_object_id=unit.pk,
        )
        job = LabelPrintJob.objects.create(workspace=self.workspace)
        LabelPrintItem.objects.create(
            job=job,
            identity=identity,
            code=identity.codes.get(status=LabelCode.Status.ACTIVE),
            position=1,
            payload=unit.asset_code,
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
        identity = LabelIdentity.objects.get(
            target_content_type=ContentType.objects.get_for_model(InventoryUnit),
            target_object_id=unit.pk,
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

    def test_a_balance_row_names_its_location_in_full(self):
        """The numbering picker chooses between places, so bare names will not do."""
        bay = Location.objects.create(
            workspace=self.workspace,
            name='Bay 2',
            code='BAY-2',
            location_type=Location.LocationType.STORAGE,
            parent=self.store,
        )
        lot = self.receive(quantity='10', cost='5.0000')
        post_stock_movement(
            self.workspace, self.user,
            MovementRequest(
                lot=lot,
                movement_type=StockMovement.MovementType.TRANSFER,
                quantity=Decimal('4'),
                source=self.store,
                destination=bay,
                reason='Split across bays',
            ),
        )

        row = self.balance_rows(lot)[bay.pk]

        self.assertEqual(row['location_name'], 'Bay 2')
        self.assertEqual(row['location_full_name'], 'Pot store / Bay 2')

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

    def unit_identity(self):
        """Return the identity numbering issued for this pot."""
        return LabelIdentity.objects.get(
            target_content_type=ContentType.objects.get_for_model(InventoryUnit),
            target_object_id=self.unit.pk,
        )

    def resolve(self, code):
        """Resolve one scanned code the way the scanner does."""
        response = self.client.get('/labels/resolve/', {'value': code})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_a_numbered_pot_can_be_issued_a_code_and_scanned_back(self):
        """The pot is the target; the code leads to its own detail route."""
        identity = self.unit_identity()
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
        code = self.unit_identity().codes.get(status=LabelCode.Status.ACTIVE)
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


class PottedPlantPlacementTests(MixedTrackingTestCase):
    """A numbered pot is a place a plant can stand, and it can hold several."""

    def setUp(self):
        super().setUp()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.lot = self.receive(quantity='10', cost='5.0000')
        self.pot = individualize_lot_units(
            self.workspace, self.user,
            IndividualizationRequest(lot=self.lot, location=self.store, count=1),
        )[0]

    def stand_in_pot(self, plant, pot=None):
        """Put one plant into a numbered container."""
        return SpecificPlantLocation.objects.create(
            specific_plant=plant,
            location_type=SpecificPlantLocation.CONTAINER_UNIT,
            container_unit=pot or self.pot,
        )

    def test_three_plants_can_share_one_pot(self):
        """Nothing constrains one plant per place, only one place per plant."""
        plants = [make_specific_plant() for _ in range(3)]

        for plant in plants:
            self.stand_in_pot(plant)

        held = SpecificPlantLocation.objects.filter(
            container_unit=self.pot, ended__isnull=True,
        )
        self.assertEqual(held.count(), 3)

    def test_a_shared_pot_is_one_container_and_all_of_its_plants(self):
        """Three bulbs in one pot must not read as three pots on the bench."""
        for _ in range(3):
            self.stand_in_pot(make_specific_plant())

        occupancy = location_occupancy(self.store)

        self.assertEqual(occupancy.containers, 1)
        self.assertEqual(occupancy.plants, 3)

    def test_a_potted_plant_is_found_at_the_bench_its_pot_stands_on(self):
        """The pot carries the location, exactly as a tray does for its cells."""
        bench = Location.objects.create(
            workspace=self.workspace,
            name='Specimen bench',
            code='SPEC-BENCH',
            location_type=Location.LocationType.GROWING,
        )
        plant = make_specific_plant()
        self.stand_in_pot(plant)
        post_unit_movement(
            self.workspace, self.user,
            UnitMovementRequest(
                unit=self.pot,
                movement_type=StockMovement.MovementType.TRANSFER,
                destination=bench,
                reason='Moved to the specimen bench',
            ),
        )

        self.assertEqual(_plant_location(plant), bench)

    def test_a_tray_unit_is_refused_as_a_container(self):
        """A tray says where its plants are through its cells, not through this."""
        tray = make_seed_tray()
        placement = SpecificPlantLocation(
            specific_plant=make_specific_plant(),
            location_type=SpecificPlantLocation.CONTAINER_UNIT,
            container_unit=tray.inventory_unit,
        )

        with self.assertRaises(ValidationError) as context:
            placement.full_clean()
        self.assertIn('container_unit', context.exception.message_dict)

    def test_a_pot_holding_a_plant_cannot_be_wasted_or_discarded(self):
        """Removing the pot would strand whatever is growing in it."""
        self.stand_in_pot(make_specific_plant())

        with self.assertRaises(ValidationError):
            post_unit_movement(
                self.workspace, self.user,
                UnitMovementRequest(
                    unit=self.pot,
                    movement_type=StockMovement.MovementType.WASTE,
                    reason='Cracked',
                ),
            )
        with self.assertRaises(ValidationError) as context:
            discard_numbering(self.workspace, self.pot)
        self.assertIn('unit', context.exception.message_dict)


class NumberedPotSalesLineTests(MixedTrackingTestCase):
    """A unit line accepts any individually identified stock, pots included."""

    def setUp(self):
        super().setUp()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.order = create_order(self.workspace, self.user)

    def make_line(self, item):
        """Build one unit line for an item without saving assumptions."""
        return SalesOrderLine(
            order=self.order,
            line_type=SalesOrderLine.LineType.UNIT,
            item=item,
            description='One numbered pot',
            quantity=1,
            unit_price=Decimal('20.0000'),
            tax_rate=Decimal('15'),
        )

    def test_a_numbered_pot_item_is_a_valid_unit_line(self):
        """The line type is about identity, not about trays."""
        line = self.make_line(self.item)

        line.full_clean()
        line.save()

        self.assertEqual(line.item_id, self.item.pk)

    def test_a_tray_item_is_still_a_valid_unit_line(self):
        """Widening the type must not cost trays their existing behaviour."""
        tray = make_seed_tray()

        line = self.make_line(tray.inventory_unit.item)
        line.full_clean()

        self.assertEqual(line.item_id, tray.inventory_unit.item_id)

    def test_bulk_stock_is_refused_on_a_unit_line(self):
        """Anonymous stock has no identity for an allocation to point at."""
        self.item.tracking_mode = InventoryItem.TrackingMode.LOT
        self.item.save()

        with self.assertRaises(ValidationError) as context:
            self.make_line(self.item).full_clean()
        self.assertIn('item', context.exception.message_dict)

    def test_a_numbered_pot_can_be_allocated_to_its_line(self):
        """The allocation path is the one trays already use, unchanged."""
        lot = self.receive(quantity='10', cost='5.0000')
        pot = individualize_lot_units(
            self.workspace, self.user,
            IndividualizationRequest(lot=lot, location=self.store, count=1),
        )[0]
        line = self.make_line(self.item)
        line.save()

        allocations = allocate_targets(line, self.user, unit_ids=[pot.pk])

        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].inventory_unit_id, pot.pk)


class SellingAPottedSpecimenTests(MixedTrackingTestCase):
    """Selling a numbered pot sells what is growing in it."""

    def setUp(self):
        super().setUp()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.lot = self.receive(quantity='10', cost='5.0000')
        self.pot = individualize_lot_units(
            self.workspace, self.user,
            IndividualizationRequest(lot=self.lot, location=self.store, count=1),
        )[0]

    def plant_in_pot(self, pot=None):
        """Stand one ready plant in a numbered container."""
        plant = make_specific_plant()
        record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.READY))
        SpecificPlantLocation.objects.create(
            specific_plant=plant,
            location_type=SpecificPlantLocation.CONTAINER_UNIT,
            container_unit=pot or self.pot,
        )
        return plant

    def sell_the_pot(self):
        """Take one numbered pot all the way through a confirmed order."""
        order = create_order(self.workspace, self.user)
        line = SalesOrderLine.objects.create(
            order=order,
            line_type=SalesOrderLine.LineType.UNIT,
            item=self.item,
            description='One specimen in its pot',
            quantity=1,
            unit_price=Decimal('40'),
            tax_rate=Decimal('15'),
        )
        allocation = allocate_targets(line, self.user, unit_ids=[self.pot.pk])[0]
        confirm_order(order, self.user)
        fulfillment = post_fulfillment(
            order, self.user, operation_key=uuid4(),
            allocation_ids=[allocation.pk],
        )
        return order, fulfillment.lines.get()

    def test_the_plants_in_a_sold_pot_are_sold_with_it(self):
        """One line, one pot, and every passenger recorded as gone."""
        plants = [self.plant_in_pot() for _ in range(3)]

        _order, line = self.sell_the_pot()

        riders = line.riders.all()
        self.assertEqual(riders.count(), 3)
        self.assertEqual(
            {rider.plant_id for rider in riders},
            {plant.pk for plant in plants},
        )
        for plant in plants:
            self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.SOLD)

    def test_a_sold_pot_stops_holding_its_plants(self):
        """The pot has left, so nothing is still standing in it here."""
        self.plant_in_pot()

        self.sell_the_pot()

        self.assertFalse(SpecificPlantLocation.objects.filter(
            container_unit=self.pot, ended__isnull=True,
        ).exists())

    def test_cost_of_sale_covers_the_pot_and_its_passengers(self):
        """The plants are the larger half of what went out of the door."""
        self.plant_in_pot()

        _order, line = self.sell_the_pot()

        rider_costs = [
            rider.cogs_amount or Decimal('0') for rider in line.riders.all()
        ]
        expected = (self.pot.acquisition_cost or Decimal('0')) + sum(
            rider_costs, Decimal('0'),
        )
        self.assertEqual(line.cogs_amount, expected)

    def test_an_empty_pot_sells_with_no_riders(self):
        """Nothing growing in it means nothing rides along."""
        _order, line = self.sell_the_pot()

        self.assertEqual(line.riders.count(), 0)
        self.assertEqual(line.cogs_amount, self.pot.acquisition_cost)

    def test_a_plant_promised_on_another_order_blocks_the_container_sale(self):
        """A specimen is sellable once, not once loose and once potted."""
        plant = self.plant_in_pot()
        other = create_order(self.workspace, self.user)
        other_line = SalesOrderLine.objects.create(
            order=other,
            line_type=SalesOrderLine.LineType.SEEDLING,
            variety=plant.batch.variety,
            description='The same specimen, loose',
            quantity=1,
            unit_price=Decimal('30'),
            tax_rate=Decimal('15'),
        )
        allocate_targets(other_line, self.user, plant_ids=[plant.pk])
        confirm_order(other, self.user)

        with self.assertRaises(ValidationError) as context:
            self.sell_the_pot()
        self.assertIn('allocations', context.exception.message_dict)

    def test_returning_the_pot_brings_its_plants_back_into_it(self):
        """They left as passengers on this line, so they come back on it."""
        plant = self.plant_in_pot()
        order, line = self.sell_the_pot()

        post_return(
            order, self.user, operation_key=uuid4(),
            items=[{
                'fulfillment_line': line,
                'outcome': SalesReturnLine.Outcome.AVAILABLE,
                'destination': self.store,
            }],
            reason='Customer changed their mind',
        )

        self.assertEqual(
            plant_lifecycle_summary(plant).state, LifecycleState.AVAILABLE,
        )
        self.assertTrue(SpecificPlantLocation.objects.filter(
            specific_plant=plant, container_unit=self.pot, ended__isnull=True,
        ).exists())
        self.assertIsNotNone(line.riders.get().return_event_id)
