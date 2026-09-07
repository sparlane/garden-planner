"""Storage guarantees for the shared container-fill identity and share basis."""
# pylint: disable=duplicate-code

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from inventory.models import InventoryItem, InventoryUnit
from inventory.units import UnitCode
from tests.factories import make_inventory_item, make_location, make_stock_lot, make_seed_tray
from workspaces.models import Workspace

from .models import SeedTrayGeneration


class ContainerFillModelTests(TestCase):
    """A counted fill is one record, while an identified fill is one container."""

    def setUp(self):
        self.location = make_location()
        self.item = make_inventory_item(
            category=InventoryItem.Category.POT_CONTAINER,
            base_unit=UnitCode.EACH,
            tracking_mode=InventoryItem.TrackingMode.MIXED,
        )
        self.lot = make_stock_lot(item=self.item, location=self.location)
        self.unit = InventoryUnit.objects.create(
            workspace=self.item.workspace, item=self.item,
            source_lot=self.lot, current_location=self.location, currency_code=self.item.workspace.currency_code,
        )

    def fill(self, **overrides):
        """Build an unsaved counted fill; callers choose model or raw insertion."""
        values = {
            'workspace': self.item.workspace, 'stock_lot': self.lot,
            'source_location': self.location, 'container_count': 50,
            'sequence': 1, 'code': 'POTS-1', 'opened_at': timezone.now(),
        }
        values.update(overrides)
        return SeedTrayGeneration(**values)

    def test_fifty_pots_need_no_invented_identities(self):
        """A counted fill uses one row without numbering any of its pots."""
        before = InventoryUnit.objects.count()
        fill = self.fill()
        fill.save()
        fill.refresh_from_db()
        self.assertEqual(fill.container_count, 50)
        self.assertEqual(fill.stock_lot, self.lot)
        self.assertEqual(fill.source_location, self.location)
        self.assertIsNone(fill.inventory_unit_id)
        self.assertIsNone(fill.tray_id)
        self.assertEqual(InventoryUnit.objects.count(), before)

    def test_a_numbered_pot_uses_the_same_fill_model(self):
        """The identified branch needs neither a lot target nor a tray wrapper."""
        fill = self.fill(
            inventory_unit=self.unit, stock_lot=None,
            source_location=None, container_count=1,
        )
        fill.save()
        self.assertEqual(self.unit.container_fills.get(), fill)
        self.assertIsNone(fill.tray_id)

    def test_database_refuses_ambiguous_or_incomplete_targets(self):
        """Bypassing save cannot turn a fill into two targets or no target."""
        cases = [
            {'inventory_unit': self.unit},
            {'stock_lot': None},
            {'source_location': None},
            {'container_count': 0},
            {'stock_lot': None, 'source_location': None, 'inventory_unit': self.unit},
            {'tray': make_seed_tray()},
        ]
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    SeedTrayGeneration.objects.bulk_create([self.fill(**values)])

    def test_fractional_container_counts_are_not_silently_truncated(self):
        """The media share basis is a count of whole pots, never a rounded input."""
        for value in (50.5, Decimal('50.5'), '50.5'):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError) as caught:
                    self.fill(container_count=value).save()
                self.assertIn('container_count', caught.exception.message_dict)
        self.assertFalse(SeedTrayGeneration.objects.exists())

    def test_one_open_fill_per_numbered_container(self):
        """A numbered pot cannot silently carry two media histories at once."""
        values = {'inventory_unit': self.unit, 'stock_lot': None, 'source_location': None, 'container_count': 1}
        self.fill(**values).save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SeedTrayGeneration.objects.bulk_create([self.fill(**values, sequence=2, code='POTS-2')])

    def test_numbered_container_can_be_refilled_after_cleaning(self):
        """Closed fills keep their sequence and allow a new open fill."""
        values = {'inventory_unit': self.unit, 'stock_lot': None, 'source_location': None, 'container_count': 1}
        self.fill(**values, status='closed', closed_at=timezone.now()).save()
        self.fill(**values, sequence=2, code='POTS-2').save()
        self.assertEqual(self.unit.container_fills.count(), 2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SeedTrayGeneration.objects.bulk_create([
                self.fill(**values, code='REPEATED', status='closed', closed_at=timezone.now()),
            ])

    def test_separate_counted_fills_can_share_one_lot(self):
        """A lot may supply several runs without pretending it is one pot."""
        self.fill(container_count=40).save()
        self.fill(code='POTS-2', container_count=40).save()
        self.assertEqual(self.lot.container_fills.count(), 2)

    def test_the_target_and_original_share_basis_are_immutable(self):
        """Later departures cannot increase the remaining plants' media shares."""
        fill = self.fill()
        fill.save()
        for field, replacement in (
            ('container_count', 49),
            ('stock_lot', make_stock_lot(item=self.item)),
            ('source_location', make_location()),
        ):
            with self.subTest(field=field):
                setattr(fill, field, replacement)
                with self.assertRaises(ValidationError) as caught:
                    fill.save()
                self.assertIn(field, caught.exception.message_dict)
                fill.refresh_from_db()

    def test_non_container_stock_cannot_be_filled(self):
        """Litres of mix are fill contents, never the physical container."""
        with self.assertRaises(ValidationError) as caught:
            self.fill(stock_lot=make_stock_lot()).save()
        self.assertIn('stock_lot', caught.exception.message_dict)

    def test_tray_units_must_keep_the_existing_tray_relationship(self):
        """A tray fill remains visible to the existing sowing and clean paths."""
        with self.assertRaises(ValidationError) as caught:
            self.fill(inventory_unit=make_seed_tray().inventory_unit,
                      stock_lot=None, source_location=None, container_count=1).save()
        self.assertIn('tray', caught.exception.message_dict)

    def test_target_location_and_lot_must_belong_to_the_workspace(self):
        """A location or stock target cannot bridge tenant boundaries."""
        other = Workspace.objects.create(name='Other nursery')
        for field, value in (
            ('source_location', make_location(workspace=other)),
            ('stock_lot', make_stock_lot(item=make_inventory_item(
                workspace=other, category=InventoryItem.Category.POT_CONTAINER, base_unit=UnitCode.EACH,
            ))),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError) as caught:
                    self.fill(**{field: value}).save()
                self.assertIn(field, caught.exception.message_dict)
