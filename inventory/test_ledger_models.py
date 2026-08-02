"""Model-level tests for exact-lot stock ledger invariants."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from supplies.models import Supplier
from workspaces.models import Workspace, get_current_workspace

from .models import (
    InventoryItem,
    InventoryLocation,
    InventoryUnit,
    QuantityCertainty,
    StockLot,
    StockMovement,
    StockReceipt,
    StockReceiptLine,
    Stocktake,
    StocktakeLine,
)
from .units import UnitCode


class LedgerModelTests(TestCase):
    """Ledger records enforce ownership, normalization, and immutability."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.user = get_user_model().objects.create_user(username='ledger-model-user')
        self.supplier = Supplier.objects.create(
            workspace=self.workspace,
            name='Ledger Supplier',
        )
        self.item = InventoryItem.objects.create(
            workspace=self.workspace,
            name='Potting media',
            sku='MEDIA-LEDGER',
            category=InventoryItem.Category.GROWING_MEDIA,
            base_unit=UnitCode.MILLILITRE,
            reorder_level=Decimal('1000'),
        )
        self.location = InventoryLocation.objects.create(
            workspace=self.workspace,
            name='Main store',
            code='STORE',
            location_type=InventoryLocation.LocationType.STORAGE,
        )

    def make_opening_lot(self, **overrides):
        """Create a valid immutable opening lot."""
        values = {
            'workspace': self.workspace,
            'item': self.item,
            'origin': StockLot.Origin.OPENING,
            'received_on': date(2026, 8, 1),
            'initial_base_quantity': Decimal('1000'),
            'acquisition_total': Decimal('12.5000'),
            'base_unit_cost': Decimal('0.012500000000'),
            'currency_code': 'NZD',
        }
        values.update(overrides)
        return StockLot.objects.create(**values)

    def test_location_code_and_lot_identifier_are_workspace_scoped(self):
        """Operational identifiers can repeat only across workspace boundaries."""
        with self.assertRaises(ValidationError):
            InventoryLocation.objects.create(
                workspace=self.workspace,
                name='Duplicate store',
                code='STORE',
                location_type=InventoryLocation.LocationType.STORAGE,
            )

        first = self.make_opening_lot(identifier='KNOWN-LOT')
        with self.assertRaises(ValidationError):
            self.make_opening_lot(identifier=first.identifier)

        other = Workspace.objects.create(name='Other ledger workspace')
        other_item = InventoryItem.objects.create(
            workspace=other,
            name='Other media',
            category=InventoryItem.Category.GROWING_MEDIA,
            base_unit=UnitCode.MILLILITRE,
        )
        duplicate = StockLot.objects.create(
            workspace=other,
            item=other_item,
            identifier=first.identifier,
            origin=StockLot.Origin.OPENING,
            received_on=date(2026, 8, 1),
            initial_base_quantity=Decimal('1'),
            acquisition_total=Decimal('0'),
            base_unit_cost=Decimal('0'),
            currency_code='NZD',
        )
        self.assertEqual(duplicate.identifier, first.identifier)

    def test_receipt_line_requires_exact_normalization_and_workspace(self):
        """Draft lines cannot misstate canonical quantities or ownership."""
        receipt = StockReceipt.objects.create(
            workspace=self.workspace,
            supplier=self.supplier,
            received_date=date(2026, 8, 1),
            currency_code='NZD',
            created_by=self.user,
        )
        line = StockReceiptLine(
            receipt=receipt,
            item=self.item,
            quantity=Decimal('2'),
            unit_code=UnitCode.LITRE,
            base_quantity=Decimal('2000'),
            line_cost_ex_tax=Decimal('20'),
            destination=self.location,
        )
        line.save()
        self.assertEqual(line.normalized_quantity(), Decimal('2000'))

        line.base_quantity = Decimal('2')
        with self.assertRaises(ValidationError) as context:
            line.save()
        self.assertIn('base_quantity', context.exception.message_dict)

        other = Workspace.objects.create(name='Foreign receipt workspace')
        foreign_location = InventoryLocation.objects.create(
            workspace=other,
            name='Foreign store',
            code='STORE',
            location_type=InventoryLocation.LocationType.STORAGE,
        )
        line.base_quantity = Decimal('2000')
        line.destination = foreign_location
        with self.assertRaises(ValidationError) as context:
            line.save()
        self.assertIn('destination', context.exception.message_dict)

    def test_unknown_seed_receipt_omits_quantity_without_claiming_zero(self):
        """An uncounted packet keeps its quantity genuinely nullable."""
        seed_item = InventoryItem.objects.create(
            workspace=self.workspace,
            name='Uncounted beet clusters',
            category=InventoryItem.Category.SEED,
            base_unit=UnitCode.SEED_CLUSTER,
        )
        receipt = StockReceipt.objects.create(
            workspace=self.workspace,
            supplier=self.supplier,
            received_date=date(2026, 8, 1),
            currency_code='NZD',
            created_by=self.user,
        )
        line = StockReceiptLine.objects.create(
            receipt=receipt,
            item=seed_item,
            quantity_certainty=QuantityCertainty.UNKNOWN,
            unit_code=UnitCode.SEED_CLUSTER,
            line_cost_ex_tax=Decimal('4.50'),
            destination=self.location,
        )
        lot = StockLot.objects.create(
            workspace=self.workspace,
            item=seed_item,
            origin=StockLot.Origin.RECEIPT,
            receipt_line=line,
            received_on=receipt.received_date,
            initial_base_quantity=None,
            quantity_certainty=QuantityCertainty.UNKNOWN,
            acquisition_total=Decimal('4.50'),
            base_unit_cost=None,
            currency_code='NZD',
        )

        self.assertIsNone(line.quantity)
        self.assertIsNone(line.base_quantity)
        self.assertIsNone(lot.initial_base_quantity)

        line.quantity = Decimal('0')
        with self.assertRaises(ValidationError) as context:
            line.save()
        self.assertIn('quantity', context.exception.message_dict)

    def test_serialized_unit_requires_matching_serialized_identity(self):
        """A unit cannot cross workspaces, lots, or tracking modes."""
        serialized_item = InventoryItem.objects.create(
            workspace=self.workspace,
            name='Propagation tray',
            category=InventoryItem.Category.TRAY,
            base_unit=UnitCode.EACH,
            tracking_mode=InventoryItem.TrackingMode.SERIALIZED,
        )
        lot = StockLot.objects.create(
            workspace=self.workspace,
            item=serialized_item,
            origin=StockLot.Origin.OPENING,
            received_on=date(2026, 8, 1),
            initial_base_quantity=Decimal('1'),
            acquisition_total=Decimal('8.5000'),
            base_unit_cost=Decimal('8.500000000000'),
            currency_code='NZD',
        )
        unit = InventoryUnit.objects.create(
            workspace=self.workspace,
            item=serialized_item,
            source_lot=lot,
            acquisition_cost=Decimal('8.5000'),
            currency_code='NZD',
            current_location=self.location,
        )
        self.assertTrue(unit.asset_code.startswith('ASSET-'))

        unit.item = self.item
        with self.assertRaises(ValidationError) as context:
            unit.save()
        self.assertIn('item', context.exception.message_dict)

    def test_serialized_movement_requires_unit_and_quantity_one(self):
        """Serialized stock cannot re-enter aggregate lot accounting."""
        item = InventoryItem.objects.create(
            workspace=self.workspace,
            name='Cell tray',
            category=InventoryItem.Category.TRAY,
            base_unit=UnitCode.EACH,
            tracking_mode=InventoryItem.TrackingMode.SERIALIZED,
        )
        lot = StockLot.objects.create(
            workspace=self.workspace,
            item=item,
            origin=StockLot.Origin.OPENING,
            received_on=date(2026, 8, 1),
            initial_base_quantity=Decimal('1'),
            acquisition_total=Decimal('4'),
            base_unit_cost=Decimal('4'),
            currency_code='NZD',
        )
        with self.assertRaises(ValidationError) as context:
            StockMovement.objects.create(
                workspace=self.workspace,
                lot=lot,
                movement_type=StockMovement.MovementType.OPENING,
                quantity=Decimal('1'),
                destination=self.location,
                occurred_at=timezone.now(),
            )
        self.assertIn('unit', context.exception.message_dict)

        unit = InventoryUnit.objects.create(
            workspace=self.workspace,
            item=item,
            source_lot=lot,
            acquisition_cost=Decimal('4'),
            currency_code='NZD',
        )
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(
                workspace=self.workspace,
                lot=lot,
                unit=unit,
                movement_type=StockMovement.MovementType.OPENING,
                quantity=Decimal('2'),
                destination=self.location,
                occurred_at=timezone.now(),
            )

    def test_posted_documents_and_ledger_rows_are_immutable(self):
        """History remains append-only after its document posts."""
        lot = self.make_opening_lot()
        movement = StockMovement.objects.create(
            workspace=self.workspace,
            lot=lot,
            movement_type=StockMovement.MovementType.OPENING,
            quantity=Decimal('1000'),
            destination=self.location,
            occurred_at=timezone.now(),
            created_by=self.user,
        )

        lot.expires_on = date(2027, 1, 1)
        with self.assertRaisesMessage(ValidationError, 'Stock lots are immutable.'):
            lot.save()
        movement.reason = 'Silent edit'
        with self.assertRaisesMessage(
            ValidationError,
            'Stock movements are immutable.',
        ):
            movement.save()
        with self.assertRaises(ValidationError):
            movement.delete()

        receipt = StockReceipt.objects.create(
            workspace=self.workspace,
            supplier=self.supplier,
            received_date=date(2026, 8, 1),
            currency_code='NZD',
        )
        StockReceipt.objects.filter(pk=receipt.pk).update(
            status=StockReceipt.Status.POSTED,
            posted_at=timezone.now(),
        )
        receipt.refresh_from_db()
        receipt.notes = 'Silent edit'
        with self.assertRaisesMessage(ValidationError, 'Posted receipts are immutable.'):
            receipt.save()

    def test_movement_shapes_and_reversal_identity_are_validated(self):
        """Positive rows encode effects through exact inverted locations."""
        lot = self.make_opening_lot()
        original = StockMovement.objects.create(
            workspace=self.workspace,
            lot=lot,
            movement_type=StockMovement.MovementType.OPENING,
            quantity=Decimal('1000'),
            destination=self.location,
            occurred_at=timezone.now(),
        )

        invalid = StockMovement(
            workspace=self.workspace,
            lot=lot,
            movement_type=StockMovement.MovementType.CONSUMPTION,
            quantity=Decimal('1'),
            destination=self.location,
            occurred_at=timezone.now(),
        )
        with self.assertRaises(ValidationError) as context:
            invalid.save()
        self.assertIn('source', context.exception.message_dict)

        reversal = StockMovement.objects.create(
            workspace=self.workspace,
            lot=lot,
            movement_type=StockMovement.MovementType.REVERSAL,
            quantity=original.quantity,
            source=self.location,
            occurred_at=timezone.now(),
            reversal_of=original,
            reason='Correct opening balance',
        )
        self.assertEqual(reversal.source, original.destination)
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(
                workspace=self.workspace,
                lot=lot,
                movement_type=StockMovement.MovementType.REVERSAL,
                quantity=original.quantity,
                source=self.location,
                occurred_at=timezone.now(),
                reversal_of=original,
            )

    def test_stocktake_line_normalizes_zero_and_rejects_foreign_location(self):
        """Counts preserve their lot/location identity even when zero."""
        lot = self.make_opening_lot()
        stocktake = Stocktake.objects.create(
            workspace=self.workspace,
            counted_at=timezone.now(),
            created_by=self.user,
        )
        line = StocktakeLine.objects.create(
            stocktake=stocktake,
            lot=lot,
            location=self.location,
            counted_quantity=Decimal('0'),
            unit_code=UnitCode.LITRE,
            counted_base_quantity=Decimal('0'),
            reason='Initial count',
        )
        self.assertEqual(line.normalized_quantity(), Decimal('0'))

        other = Workspace.objects.create(name='Foreign count workspace')
        foreign_location = InventoryLocation.objects.create(
            workspace=other,
            name='Foreign count store',
            code='COUNT',
            location_type=InventoryLocation.LocationType.STORAGE,
        )
        line.location = foreign_location
        with self.assertRaises(ValidationError) as context:
            line.save()
        self.assertIn('location', context.exception.message_dict)

    def test_database_rejects_negative_reorder_level(self):
        """Bulk updates cannot bypass the non-negative stock threshold."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            InventoryItem.objects.filter(pk=self.item.pk).update(
                reorder_level=Decimal('-1'),
            )
