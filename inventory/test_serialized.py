"""Behavioral tests for exact serialized-unit stock workflows."""

# pylint: disable=duplicate-code

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from locations.models import Location
from supplies.models import Supplier
from workspaces.models import get_current_workspace

from .ledger import (
    UnitMovementRequest,
    UnitReconciliationRequest,
    post_unit_movement,
    reconcile_unit_opening,
    unit_physical_state,
)
from .models import (
    InventoryItem,
    InventoryUnit,
    InventoryUnitReconciliation,
    StockLot,
    StockMovement,
    StockReceipt,
    StockReceiptLine,
)
from .units import UnitCode


class SerializedInventoryTestCase(TestCase):
    """A workspace with a serialized tray item, a store, and a growing house."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.user = get_user_model().objects.create_user(username='unit-user')
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(
            workspace=self.workspace,
            name='Tray supplier',
        )
        self.item = InventoryItem.objects.create(
            workspace=self.workspace,
            name='72-cell tray',
            category=InventoryItem.Category.TRAY,
            base_unit=UnitCode.EACH,
            tracking_mode=InventoryItem.TrackingMode.SERIALIZED,
        )
        self.store = Location.objects.create(
            workspace=self.workspace,
            name='Tray store',
            code='TRAY-STORE',
            location_type=Location.LocationType.STORAGE,
        )
        self.growing = Location.objects.create(
            workspace=self.workspace,
            name='Propagation house',
            code='PROP-HOUSE',
            location_type=Location.LocationType.GROWING,
        )

    def create_receipt(self, quantity='3', cost='10.0000'):
        """Create one valid serialized draft receipt."""
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
        return receipt

    def post_receipt(self, **overrides):
        """Post one serialized receipt through its public action."""
        receipt = self.create_receipt(**overrides)
        response = self.client.post(f'/inventory/receipts/{receipt.pk}/post/')
        self.assertEqual(response.status_code, 200, response.data)
        return receipt

    def unknown_opening_unit(self):
        """Build the migrated tray unit the reconciliation workflow exists for.

        Legacy openings arrived with neither a cost nor a real location, so
        they sit at the reserved `SYSTEM-TRAY-UNKNOWN` location until someone
        counts and values them.
        """
        unknown = Location.objects.create(
            workspace=self.workspace,
            name='Unknown tray location',
            code='SYSTEM-TRAY-UNKNOWN',
            location_type=Location.LocationType.ADJUSTMENT,
        )
        lot = StockLot.objects.create(
            workspace=self.workspace,
            item=self.item,
            origin=StockLot.Origin.OPENING,
            received_on=date(2026, 1, 1),
            initial_base_quantity=Decimal('1'),
            acquisition_total=None,
            base_unit_cost=None,
            currency_code=self.workspace.currency_code,
        )
        unit = InventoryUnit.objects.create(
            workspace=self.workspace,
            item=self.item,
            source_lot=lot,
            acquisition_cost=None,
            currency_code=self.workspace.currency_code,
            current_location=unknown,
        )
        StockMovement.objects.create(
            workspace=self.workspace,
            lot=lot,
            unit=unit,
            movement_type=StockMovement.MovementType.OPENING,
            quantity=Decimal('1'),
            destination=unknown,
            occurred_at=timezone.now(),
        )
        return unit, unknown


class SerializedInventoryTests(SerializedInventoryTestCase):
    """Receipts and unit actions preserve exact identity and audit history."""

    def test_receipt_creates_one_costed_unit_and_movement_per_each(self):
        """Per-unit costs retain the receipt total despite currency rounding."""
        receipt = self.post_receipt()
        units = list(
            InventoryUnit.objects.filter(source_lot__receipt_line__receipt=receipt)
            .order_by('acquisition_cost', 'pk')
        )
        self.assertEqual(len(units), 3)
        self.assertEqual(
            [unit.acquisition_cost for unit in units],
            [Decimal('3.3333'), Decimal('3.3333'), Decimal('3.3334')],
        )
        self.assertEqual(sum(unit.acquisition_cost for unit in units), Decimal('10'))
        self.assertEqual(len({unit.asset_code for unit in units}), 3)
        self.assertEqual(
            StockMovement.objects.filter(unit__in=units, movement_type='receipt').count(),
            3,
        )
        self.assertTrue(all(unit.current_location_id == self.store.pk for unit in units))

    def test_serialized_receipt_rejects_fractional_or_unknown_quantity(self):
        """Every posted unit must correspond to one exact physical each."""
        for quantity in ('1.5',):
            with self.subTest(quantity=quantity):
                receipt = self.create_receipt(quantity=quantity)
                response = self.client.post(f'/inventory/receipts/{receipt.pk}/post/')
                self.assertEqual(response.status_code, 400)
                self.assertEqual(InventoryUnit.objects.count(), 0)

        receipt = self.create_receipt(quantity='1')
        line = receipt.lines.get()
        line.quantity_certainty = 'estimated'
        line.save()
        response = self.client.post(f'/inventory/receipts/{receipt.pk}/post/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(InventoryUnit.objects.count(), 0)

    def test_transfer_loss_return_and_reversal_keep_unit_specific_history(self):
        """Moving one unit never changes another unit from the same lot."""
        self.post_receipt(quantity='2', cost='12')
        first, second = InventoryUnit.objects.order_by('pk')

        response = self.client.post(
            f'/inventory/serialized-units/{first.pk}/transfer/',
            {'destination': self.growing.pk, 'reason': 'Move into production'},
        )
        self.assertEqual(response.status_code, 201, response.data)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.current_location_id, self.growing.pk)
        self.assertEqual(second.current_location_id, self.store.pk)

        response = self.client.post(
            f'/inventory/serialized-units/{first.pk}/loss/',
            {'reason': 'Could not locate tray'},
        )
        self.assertEqual(response.status_code, 201, response.data)
        first.refresh_from_db()
        self.assertIsNone(first.current_location_id)
        self.assertEqual(unit_physical_state(first), 'lost')

        response = self.client.post(
            f'/inventory/serialized-units/{first.pk}/return/',
            {'destination': self.store.pk, 'reason': 'Tray recovered'},
        )
        self.assertEqual(response.status_code, 201, response.data)
        return_movement = StockMovement.objects.get(pk=response.data['pk'])
        first.refresh_from_db()
        self.assertEqual(unit_physical_state(first), 'returned')

        response = self.client.post(
            f'/inventory/movements/{return_movement.pk}/reverse/',
            {'reason': 'Recovery entered in error'},
        )
        self.assertEqual(response.status_code, 201, response.data)
        first.refresh_from_db()
        self.assertIsNone(first.current_location_id)
        self.assertEqual(unit_physical_state(first), 'lost')

    def test_commerce_dispatch_and_customer_return_are_exact_unit_actions(self):
        """Sales can dispatch and restore only the named serialized unit."""
        self.post_receipt(quantity='1', cost='12')
        unit = InventoryUnit.objects.get()
        sold = post_unit_movement(
            self.workspace,
            self.user,
            UnitMovementRequest(
                unit=unit,
                movement_type=StockMovement.MovementType.SALE,
                occurred_at=timezone.now(),
                reason='Order fulfillment',
                reference='fulfillment:1',
            ),
        )
        unit.refresh_from_db()
        self.assertEqual(unit_physical_state(unit), 'dispatched')
        self.assertFalse(unit.active)
        returned = post_unit_movement(
            self.workspace,
            self.user,
            UnitMovementRequest(
                unit=unit,
                movement_type=StockMovement.MovementType.CUSTOMER_RETURN,
                destination=self.store,
                occurred_at=timezone.now(),
                reason='Customer return',
                reference='return:1',
            ),
        )
        unit.refresh_from_db()
        self.assertEqual(sold.source_id, self.store.pk)
        self.assertEqual(returned.destination_id, self.store.pk)
        self.assertEqual(unit_physical_state(unit), 'returned')
        self.assertTrue(unit.active)

    def test_serialized_collection_filters_and_generic_actions_reject_units(self):
        """The public collection reports derived unit facts without lot writes."""
        self.post_receipt(quantity='1', cost='5')
        unit = InventoryUnit.objects.get()
        response = self.client.get(
            '/inventory/serialized-units/',
            {'physical_state': 'available', 'in_use': 'false'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['pk'] for row in response.data['results']], [unit.pk])
        self.assertEqual(response.data['results'][0]['asset_code'], unit.asset_code)
        self.assertFalse(response.data['results'][0]['reconciliation_required'])

        response = self.client.post(
            '/inventory/movements/transfer/',
            {
                'lot': unit.source_lot_id,
                'quantity': '1',
                'unit_code': UnitCode.EACH,
                'source': self.store.pk,
                'destination': self.growing.pk,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('lot', response.data)

    def test_legacy_opening_reconciliation_sets_cost_and_location_once(self):
        """Unknown opening facts become audited without rewriting the opening."""
        unit, _unknown = self.unknown_opening_unit()
        lot = unit.source_lot

        response = self.client.post(
            f'/inventory/serialized-units/{unit.pk}/reconcile-opening/',
            {
                'acquisition_cost': '7.2500',
                'destination': self.store.pk,
                'reason': 'Counted and valued opening stock',
            },
        )
        self.assertEqual(response.status_code, 200, response.data)
        unit.refresh_from_db()
        self.assertEqual(unit.acquisition_cost, Decimal('7.2500'))
        self.assertEqual(unit.current_location_id, self.store.pk)
        self.assertTrue(InventoryUnitReconciliation.objects.filter(unit=unit).exists())
        self.assertIsNone(lot.acquisition_total)

        response = self.client.post(
            f'/inventory/serialized-units/{unit.pk}/reconcile-opening/',
            {
                'acquisition_cost': '8.0000',
                'destination': self.growing.pk,
                'reason': 'Try again',
            },
        )
        self.assertEqual(response.status_code, 400)


class AssetCodeLookupTests(SerializedInventoryTestCase):
    """A unit is found by the code it carries, because that is what is read.

    Potting a seedling on, or counting a pot in a stocktake, starts from a code
    somebody is holding rather than from a list: one item's numbered containers
    outgrow any dropdown, and the identity is opaque by design, so there is
    nothing to guess at from a neighbouring code.
    """

    def setUp(self):
        super().setUp()
        self.post_receipt(quantity='3', cost='30.0000')
        self.units = list(InventoryUnit.objects.order_by('pk'))

    def lookup(self, value):
        """Return the units the public collection matches against one code."""
        response = self.client.get(
            '/inventory/serialized-units/',
            {'asset_code': value},
        )
        self.assertEqual(response.status_code, 200)
        return [row['pk'] for row in response.data['results']]

    def test_a_whole_code_finds_the_one_unit_that_carries_it(self):
        """The code is unique in a workspace, so a full one is an answer."""
        wanted = self.units[1]

        self.assertEqual(self.lookup(wanted.asset_code), [wanted.pk])

    def test_a_code_typed_in_lower_case_still_finds_it(self):
        """Codes are issued uppercase and are entered by hand as often as scanned."""
        wanted = self.units[0]

        self.assertEqual(self.lookup(wanted.asset_code.lower()), [wanted.pk])

    def test_a_fragment_matches_every_unit_holding_it(self):
        """Which is why a caller holding a partial code has to refuse to guess.

        The filter compares on a fragment so that a half-read code still
        narrows, and every unit shares the `ASSET-` prefix.
        """
        self.assertEqual(
            set(self.lookup('ASSET-')),
            {unit.pk for unit in self.units},
        )

    def test_a_code_no_unit_carries_matches_nothing(self):
        """An empty answer is what lets a screen say the code is unknown."""
        self.assertEqual(self.lookup('ASSET-NOT-A-CODE'), [])


class NumberRangeLookupTests(SerializedInventoryTestCase):
    """A run of units is selected by the numbers they were issued.

    A unit's number is its identity: issued once, never reused, and unique
    across the nursery, so a bench filled or worked on in one go is named as
    the range it occupies rather than code by code.
    """

    def setUp(self):
        super().setUp()
        self.post_receipt(quantity='5', cost='50.0000')
        self.units = list(InventoryUnit.objects.order_by('pk'))

    def lookup(self, **query):
        """Return the numbers the collection answers with, in its own order."""
        response = self.client.get('/inventory/serialized-units/', query)
        self.assertEqual(response.status_code, 200, response.data)
        return [row['pk'] for row in response.data['results']]

    def test_a_range_answers_in_number_order_with_both_ends_inside_it(self):
        """The bench asked for is the bench answered, ends included."""
        wanted = [unit.pk for unit in self.units[1:4]]

        self.assertEqual(
            self.lookup(number_from=wanted[0], number_to=wanted[-1]),
            wanted,
        )

    def test_one_bound_runs_to_the_end_of_the_numbers(self):
        """Half a range is still a range; the other end is simply open."""
        self.assertEqual(
            self.lookup(number_from=self.units[3].pk),
            [unit.pk for unit in self.units[3:]],
        )
        self.assertEqual(
            self.lookup(number_to=self.units[1].pk),
            [unit.pk for unit in self.units[:2]],
        )

    def test_a_range_combines_with_the_filters_beside_it(self):
        """A number range narrows a selection rather than replacing it."""
        other = InventoryItem.objects.create(
            workspace=self.workspace,
            name='Other tray',
            category=InventoryItem.Category.TRAY,
            base_unit=UnitCode.EACH,
            tracking_mode=InventoryItem.TrackingMode.SERIALIZED,
        )

        self.assertEqual(
            self.lookup(number_from=self.units[0].pk, item=other.pk),
            [],
        )

    def test_a_backwards_or_unreadable_range_is_refused(self):
        """Silently answering with nothing would look like an empty bench."""
        for query in (
            {'number_from': self.units[3].pk, 'number_to': self.units[1].pk},
            {'number_from': 'eighty-one'},
        ):
            with self.subTest(query=query):
                response = self.client.get('/inventory/serialized-units/', query)
                self.assertEqual(response.status_code, 400, response.data)

    def test_a_caller_can_ask_for_a_whole_bench_or_walk_it_in_pages(self):
        """A screen that got a silent first page would work on the wrong pots."""
        response = self.client.get('/inventory/serialized-units/', {'page_size': 2})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['results']), 2)
        self.assertIsNotNone(response.data['next'])

        self.assertEqual(len(self.lookup(page_size=500)), 5)


class OpeningReconciliationTests(SerializedInventoryTestCase):
    """A migrated unit leaves its unknown location only by being audited."""

    def reconcile(self, unit, destination, cost='7.2500'):
        """Supply the audited cost and location for one opening unit."""
        return reconcile_unit_opening(
            self.workspace,
            self.user,
            UnitReconciliationRequest(
                unit=unit,
                acquisition_cost=Decimal(cost),
                destination=destination,
                occurred_at=timezone.now(),
                reason='Counted and valued opening stock',
            ),
        )

    def test_a_negative_audited_cost_is_refused(self):
        """An audit supplies what the tray cost, which is never below zero."""
        unit, _unknown = self.unknown_opening_unit()
        with self.assertRaises(ValidationError) as caught:
            self.reconcile(unit, self.store, cost='-0.0001')
        self.assertIn('acquisition_cost', caught.exception.message_dict)
        unit.refresh_from_db()
        self.assertIsNone(unit.acquisition_cost)

    def test_the_unknown_location_cannot_be_the_audited_answer(self):
        """Naming where the unit already is settles nothing."""
        unit, unknown = self.unknown_opening_unit()
        with self.assertRaises(ValidationError) as caught:
            self.reconcile(unit, unknown)
        self.assertIn('destination', caught.exception.message_dict)
        self.assertFalse(InventoryUnitReconciliation.objects.exists())

    def test_an_inactive_destination_is_refused(self):
        """Audited stock lands somewhere that still exists."""
        unit, _unknown = self.unknown_opening_unit()
        Location.objects.filter(pk=self.store.pk).update(active=False)
        self.store.refresh_from_db()
        with self.assertRaises(ValidationError) as caught:
            self.reconcile(unit, self.store)
        self.assertIn('destination', caught.exception.message_dict)

    def test_only_an_opening_unit_can_be_reconciled(self):
        """A received tray already has an audited cost and a real location."""
        receipt = self.post_receipt(quantity='1')
        unit = InventoryUnit.objects.get(
            source_lot__receipt_line__receipt=receipt,
        )
        with self.assertRaisesMessage(
                ValidationError, 'Only opening units can be reconciled.'):
            self.reconcile(unit, self.growing)

    def test_an_unreconciled_unit_refuses_every_other_stock_action(self):
        """Nothing may be posted about a tray nobody has counted yet.

        This is what makes the workflow the only way out: a unit at the
        reserved location cannot be transferred, lost, or sold around it.
        """
        unit, _unknown = self.unknown_opening_unit()
        with self.assertRaisesMessage(
                ValidationError,
                'Reconcile this opening unit before another stock action.'):
            post_unit_movement(self.workspace, self.user, UnitMovementRequest(
                unit=unit,
                movement_type=StockMovement.MovementType.TRANSFER,
                destination=self.growing,
                occurred_at=timezone.now(),
                reason='Moved to the propagation house.',
            ))

    def test_the_reserved_location_is_never_a_destination(self):
        """No ordinary action may put a unit back into migration limbo."""
        receipt = self.post_receipt(quantity='1')
        unit = InventoryUnit.objects.get(source_lot__receipt_line__receipt=receipt)
        _unreconciled, unknown = self.unknown_opening_unit()
        with self.assertRaisesMessage(
                ValidationError,
                'The unknown tray location is reserved for migration.'):
            post_unit_movement(self.workspace, self.user, UnitMovementRequest(
                unit=unit,
                movement_type=StockMovement.MovementType.TRANSFER,
                destination=unknown,
                occurred_at=timezone.now(),
                reason='Moved back to limbo.',
            ))

    def test_a_reconciled_unit_moves_like_any_other(self):
        """The audit is what opens the unit up to ordinary stock actions."""
        unit, _unknown = self.unknown_opening_unit()
        reconciliation = self.reconcile(unit, self.store)
        self.assertEqual(reconciliation.acquisition_cost, Decimal('7.2500'))
        self.assertEqual(
            reconciliation.movement.movement_type,
            StockMovement.MovementType.TRANSFER,
        )
        unit.refresh_from_db()
        self.assertEqual(unit.current_location_id, self.store.pk)
        self.assertEqual(unit.acquisition_cost, Decimal('7.2500'))
        moved = post_unit_movement(self.workspace, self.user, UnitMovementRequest(
            unit=unit,
            movement_type=StockMovement.MovementType.TRANSFER,
            destination=self.growing,
            occurred_at=timezone.now(),
            reason='Moved to the propagation house.',
        ))
        self.assertEqual(moved.destination_id, self.growing.pk)
        self.assertEqual(unit_physical_state(unit), 'available')

    def test_a_unit_is_reconciled_only_once(self):
        """A second audit would rewrite a cost the books already carry."""
        unit, _unknown = self.unknown_opening_unit()
        self.reconcile(unit, self.store)
        with self.assertRaisesMessage(
                ValidationError, 'This unit has already been reconciled.'):
            self.reconcile(unit, self.growing, cost='8.0000')
        unit.refresh_from_db()
        self.assertEqual(unit.acquisition_cost, Decimal('7.2500'))
