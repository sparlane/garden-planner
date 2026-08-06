"""The ledger treats application movements as owned by their document."""

# pylint: disable=duplicate-code

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from inventory.ledger import (
    MovementRequest,
    physical_balance,
    post_stock_movement,
    reverse_application_movements,
    reverse_movement,
)
from inventory.models import InventoryItem, StockMovement
from inventory.units import UnitCode
from tests.factories import (
    make_inventory_item,
    make_inventory_location,
    make_stock_lot,
)

from .models import InputApplication, InputApplicationLine


class ApplicationMovementOwnershipTests(TestCase):
    """Who may reverse a movement an input application posted."""

    def setUp(self):
        super().setUp()
        self.location = make_inventory_location()
        self.item = make_inventory_item()
        self.lot = make_stock_lot(item=self.item, location=self.location, quantity='50')
        self.application = InputApplication.objects.create(
            applied_at=timezone.now(),
            source_location=self.location,
        )

    def consume(self, quantity='2'):
        """Post a consumption and link it to a line, as posting will."""
        movement = post_stock_movement(
            self.application.workspace,
            None,
            MovementRequest(
                lot=self.lot,
                movement_type=StockMovement.MovementType.CONSUMPTION,
                quantity=Decimal(quantity),
                source=self.location,
                reference=f'application:{self.application.pk}',
            ),
        )
        line = InputApplicationLine.objects.create(
            application=self.application,
            item=self.item,
            lot=self.lot,
            usage_basis=InventoryItem.UsageBasis.MANUAL,
            base_unit=self.item.base_unit,
            applied_quantity=Decimal(quantity),
            unit_code=UnitCode.LITRE,
            applied_base_quantity=Decimal(quantity),
        )
        line.consumption_movement = movement
        line.save()
        return movement

    def test_an_application_movement_cannot_be_reversed_on_its_own(self):
        """Reversing one row would leave the document half undone."""
        movement = self.consume()
        with self.assertRaises(ValidationError) as caught:
            reverse_movement(movement, None, 'Wrong amount')
        self.assertIn(
            'Reverse application movements through their application.',
            caught.exception.message_dict['movement'],
        )

    def test_a_waste_movement_is_owned_too(self):
        """Both rows a line posts belong to the same document."""
        movement = self.consume()
        line = self.application.lines.get()
        line.consumption_movement = None
        line.waste_movement = movement
        line.save()
        with self.assertRaises(ValidationError):
            reverse_movement(movement, None, 'Wrong amount')

    def test_the_document_may_reverse_its_own_movements(self):
        """The application restores exactly what it consumed."""
        movement = self.consume(quantity='2')
        self.assertEqual(physical_balance(self.lot, self.location), Decimal('48'))

        reversals = reverse_application_movements(
            self.application.workspace,
            [movement],
            None,
            'Applied to the wrong tray',
        )

        self.assertEqual(len(reversals), 1)
        self.assertEqual(reversals[0].reversal_of, movement)
        self.assertEqual(physical_balance(self.lot, self.location), Decimal('50'))

    def test_an_unlinked_movement_is_still_freely_reversible(self):
        """The guard applies to application rows, not to every consumption."""
        movement = post_stock_movement(
            self.application.workspace,
            None,
            MovementRequest(
                lot=self.lot,
                movement_type=StockMovement.MovementType.CONSUMPTION,
                quantity=Decimal('1'),
                source=self.location,
            ),
        )
        reversal = reverse_movement(movement, None, 'Miscounted')
        self.assertEqual(reversal.reversal_of, movement)
