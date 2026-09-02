"""Selling anonymous stock by the count rather than by identity.

A nursery that buys five hundred pots to get a trade price sells the surplus
as a stack, not as five hundred asset codes. These cover the line type, the
quantity-bearing allocation, and the arithmetic that decides how much of a lot
is still free — which is arithmetic rather than a unique index, because many
customers may legitimately hold parts of one lot at once.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from inventory.ledger import IndividualizationRequest, individualize_lot_units
from inventory.models import InventoryItem
from inventory.units import UnitCode
from locations.models import Location
from tests.factories import make_stock_lot
from workspaces.models import Workspace, get_current_workspace

from .models import SalesOrderAllocation, SalesOrderLine
from .services import create_order


class CountedStockTestCase(TestCase):
    """A nursery holding one boxed pot item received as anonymous bulk."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.currency_code = 'NZD'
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='counted-user')
        self.client.force_login(self.user)
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

    def receive(self, quantity='500', unit_cost='0.5000', item=None):
        """Stock one anonymous lot of pots at the store, priced per pot."""
        quantity = Decimal(quantity)
        return make_stock_lot(
            item=item or self.item,
            location=self.store,
            quantity=quantity,
            base_unit_cost=Decimal(unit_cost),
            acquisition_total=quantity * Decimal(unit_cost),
        )

    def number(self, lot, count):
        """Individualise part of a lot the way the catalog screen does."""
        return individualize_lot_units(
            self.workspace, self.user,
            IndividualizationRequest(lot=lot, location=self.store, count=count),
        )

    def counted_line(self, order=None, quantity=50, **overrides):
        """Build one saved counted line for the fixture's pot item."""
        values = {
            'order': order or create_order(self.workspace, self.user),
            'line_type': SalesOrderLine.LineType.LOT_QUANTITY,
            'item': self.item,
            'description': 'Fifty loose pots',
            'quantity': quantity,
            'unit_price': Decimal('0.8000'),
            'tax_rate': Decimal('15'),
        }
        values.update(overrides)
        line = SalesOrderLine(**values)
        line.save()
        return line


class CountedLineTargetTests(CountedStockTestCase):
    """A counted line promises an item by the count, never by identity."""

    def build(self, **overrides):
        """Build an unsaved counted line so validation can be inspected."""
        values = {
            'order': create_order(self.workspace, self.user),
            'line_type': SalesOrderLine.LineType.LOT_QUANTITY,
            'item': self.item,
            'description': 'Fifty loose pots',
            'quantity': 50,
            'unit_price': Decimal('0.8000'),
            'tax_rate': Decimal('15'),
        }
        values.update(overrides)
        return SalesOrderLine(**values)

    def test_a_mixed_item_counted_in_each_is_a_valid_counted_line(self):
        """Bulk pots out of a mixed lot are exactly what this line sells."""
        line = self.build()

        line.full_clean()
        line.save()

        self.assertEqual(line.item_id, self.item.pk)

    def test_a_purely_lot_tracked_item_is_also_valid(self):
        """Nothing about the mechanism needs the item to be numberable."""
        self.item.tracking_mode = InventoryItem.TrackingMode.LOT
        self.item.save()

        self.build().full_clean()

    def test_a_serialized_item_is_refused(self):
        """Every unit of a serialized item is somebody's identity already."""
        self.item.tracking_mode = InventoryItem.TrackingMode.SERIALIZED
        self.item.save()

        with self.assertRaises(ValidationError) as context:
            self.build().full_clean()
        self.assertIn('item', context.exception.message_dict)

    def test_an_item_measured_by_weight_is_refused(self):
        """Counting is whole units; measured stock is task 114's problem."""
        media = InventoryItem.objects.create(
            workspace=self.workspace,
            name='Potting mix',
            category=InventoryItem.Category.GROWING_MEDIA,
            base_unit=UnitCode.LITRE,
            tracking_mode=InventoryItem.TrackingMode.LOT,
        )

        with self.assertRaises(ValidationError) as context:
            self.build(item=media).full_clean()
        self.assertIn('item', context.exception.message_dict)

    def test_a_variety_is_refused_on_a_counted_line(self):
        """A pot is not a plant, so there is no variety to promise."""
        with self.assertRaises(ValidationError) as context:
            self.build(item=None).full_clean()
        self.assertIn('item', context.exception.message_dict)


class CountedAllocationShapeTests(CountedStockTestCase):
    """The quantity-bearing allocation keeps every identity rule literal."""

    def test_a_lot_allocation_records_its_lot_place_and_count(self):
        """Anonymous stock has no identity, so the promise carries a figure."""
        lot = self.receive()
        line = self.counted_line()

        allocation = SalesOrderAllocation.objects.create(
            line=line, stock_lot=lot, source_location=self.store, quantity=50,
        )

        self.assertEqual(allocation.target_kind, 'stock_lot')
        self.assertEqual(allocation.quantity, 50)

    def test_a_lot_allocation_without_a_place_is_refused(self):
        """Stock is only available somewhere, so a draw has to say where."""
        lot = self.receive()
        line = self.counted_line()

        with self.assertRaises(ValidationError):
            SalesOrderAllocation.objects.create(
                line=line, stock_lot=lot, quantity=50,
            )

    def test_a_lot_allocation_without_a_quantity_is_refused(self):
        """Without a count the allocation says nothing about how much."""
        lot = self.receive()
        line = self.counted_line()

        with self.assertRaises(ValidationError):
            SalesOrderAllocation.objects.create(
                line=line, stock_lot=lot, source_location=self.store,
            )

    def test_a_lot_from_another_item_is_refused(self):
        """The line named an item; a lot of something else is not it."""
        other = InventoryItem.objects.create(
            workspace=self.workspace,
            name='P11 pot',
            category=InventoryItem.Category.POT_CONTAINER,
            base_unit=UnitCode.EACH,
            tracking_mode=InventoryItem.TrackingMode.LOT,
        )
        lot = self.receive(item=other)
        line = self.counted_line()

        with self.assertRaises(ValidationError) as context:
            SalesOrderAllocation.objects.create(
                line=line, stock_lot=lot, source_location=self.store, quantity=5,
            )
        self.assertIn('stock_lot', context.exception.message_dict)

    def test_a_lot_cannot_be_allocated_to_an_identity_line(self):
        """A unit line allocates identities; a count is not one."""
        lot = self.receive()
        line = self.counted_line(line_type=SalesOrderLine.LineType.UNIT, quantity=1)

        with self.assertRaises(ValidationError) as context:
            SalesOrderAllocation.objects.create(
                line=line, stock_lot=lot, source_location=self.store, quantity=1,
            )
        self.assertIn('stock_lot', context.exception.message_dict)

    def test_a_numbered_unit_cannot_be_allocated_to_a_counted_line(self):
        """Numbered stock leaves as itself, not as an anonymous count."""
        lot = self.receive()
        pot = self.number(lot, 1)[0]
        line = self.counted_line()

        with self.assertRaises(ValidationError) as context:
            SalesOrderAllocation.objects.create(line=line, inventory_unit=pot)
        self.assertIn('inventory_unit', context.exception.message_dict)

    def test_an_identity_allocation_still_carries_no_quantity(self):
        """The existing invariant stays literally true, not merely implied."""
        lot = self.receive()
        pot = self.number(lot, 1)[0]
        line = self.counted_line(
            line_type=SalesOrderLine.LineType.UNIT, quantity=1,
        )

        with self.assertRaises(ValidationError):
            SalesOrderAllocation.objects.create(
                line=line, inventory_unit=pot, quantity=1,
            )

    def test_a_counted_allocation_is_immutable_once_written(self):
        """Rewriting a promised count would rewrite what was reserved."""
        lot = self.receive()
        line = self.counted_line()
        allocation = SalesOrderAllocation.objects.create(
            line=line, stock_lot=lot, source_location=self.store, quantity=50,
        )

        allocation.quantity = 60
        with self.assertRaises(ValidationError):
            allocation.save()
