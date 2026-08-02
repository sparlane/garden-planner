"""Behavioral tests for exact serialized-unit stock workflows."""

# pylint: disable=duplicate-code

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from supplies.models import Supplier
from workspaces.models import get_current_workspace

from .ledger import unit_physical_state
from .models import (
    InventoryItem,
    InventoryLocation,
    InventoryUnit,
    InventoryUnitReconciliation,
    StockLot,
    StockMovement,
    StockReceipt,
    StockReceiptLine,
)
from .units import UnitCode


class SerializedInventoryTests(TestCase):
    """Receipts and unit actions preserve exact identity and audit history."""

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
        self.store = InventoryLocation.objects.create(
            workspace=self.workspace,
            name='Tray store',
            code='TRAY-STORE',
            location_type=InventoryLocation.LocationType.STORAGE,
        )
        self.growing = InventoryLocation.objects.create(
            workspace=self.workspace,
            name='Propagation house',
            code='PROP-HOUSE',
            location_type=InventoryLocation.LocationType.GROWING,
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

    def test_serialized_collection_filters_and_generic_actions_reject_units(self):
        """The public collection reports derived unit facts without lot writes."""
        self.post_receipt(quantity='1', cost='5')
        unit = InventoryUnit.objects.get()
        response = self.client.get(
            '/inventory/serialized-units/',
            {'physical_state': 'available', 'in_use': 'false'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['pk'] for row in response.data], [unit.pk])
        self.assertEqual(response.data[0]['asset_code'], unit.asset_code)
        self.assertFalse(response.data[0]['reconciliation_required'])

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
        unknown = InventoryLocation.objects.create(
            workspace=self.workspace,
            name='Unknown tray location',
            code='SYSTEM-TRAY-UNKNOWN',
            location_type=InventoryLocation.LocationType.ADJUSTMENT,
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
