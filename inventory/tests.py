"""Tests for inventory units and catalog models."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from workspaces.models import Workspace, get_current_workspace

from .models import InventoryItem, ItemUnitConversion
from .units import UnitCode, UnitDimension, convert_standard_quantity


class UnitRegistryTests(SimpleTestCase):
    """Standard conversions retain exact decimal meaning."""

    def test_metric_volume_and_mass_conversions_are_exact(self):
        """Powers-of-one-thousand conversions do not introduce drift."""
        self.assertEqual(
            convert_standard_quantity(Decimal('1.25'), UnitCode.LITRE, UnitCode.MILLILITRE),
            Decimal('1250.00'),
        )
        self.assertEqual(
            convert_standard_quantity(Decimal('2.375'), UnitCode.KILOGRAM, UnitCode.GRAM),
            Decimal('2375.000'),
        )
        self.assertEqual(
            convert_standard_quantity(Decimal('750'), UnitCode.MILLILITRE, UnitCode.LITRE),
            Decimal('0.75'),
        )

    def test_incompatible_dimensions_and_count_semantics_are_rejected(self):
        """A shared count dimension does not equate seeds and clusters."""
        for source, target in (
            (UnitCode.GRAM, UnitCode.LITRE),
            (UnitCode.SEED, UnitCode.SEED_CLUSTER),
            (UnitCode.EACH, UnitCode.SEED),
        ):
            with self.subTest(source=source, target=target), self.assertRaises(ValidationError):
                convert_standard_quantity(Decimal('1'), source, target)


class InventoryItemModelTests(TestCase):
    """Catalog items enforce physical and historical invariants."""

    def make_item(self, **overrides):
        """Create a valid manual-use item."""
        values = {
            'name': 'Propagation media',
            'sku': 'MEDIA-1',
            'category': InventoryItem.Category.GROWING_MEDIA,
            'base_unit': UnitCode.MILLILITRE,
            'tracking_mode': InventoryItem.TrackingMode.LOT,
        }
        values.update(overrides)
        return InventoryItem.objects.create(**values)

    def test_every_category_and_tracking_mode_can_be_represented(self):
        """The catalog covers all first-release physical inputs."""
        categories = InventoryItem.Category.values
        for index, category in enumerate(categories):
            is_seed = category == InventoryItem.Category.SEED
            is_tray = category == InventoryItem.Category.TRAY
            item = self.make_item(
                name=f'Item {index}',
                sku=f'ITEM-{index}',
                category=category,
                base_unit=(
                    UnitCode.SEED
                    if is_seed
                    else UnitCode.EACH
                    if is_tray
                    else UnitCode.GRAM
                ),
                tracking_mode=(
                    InventoryItem.TrackingMode.SERIALIZED
                    if is_tray
                    else InventoryItem.TrackingMode.LOT
                ),
            )
            self.assertEqual(item.category, category)

        self.assertEqual(
            InventoryItem.default_tracking_mode(InventoryItem.Category.TRAY),
            InventoryItem.TrackingMode.SERIALIZED,
        )
        self.assertEqual(
            InventoryItem.default_tracking_mode(InventoryItem.Category.PACKAGING),
            InventoryItem.TrackingMode.LOT,
        )

    def test_seed_and_serialized_items_require_semantic_base_units(self):
        """Stock identities cannot be configured with misleading units."""
        with self.assertRaises(ValidationError):
            self.make_item(
                category=InventoryItem.Category.SEED,
                base_unit=UnitCode.GRAM,
            )
        with self.assertRaises(ValidationError):
            self.make_item(
                tracking_mode=InventoryItem.TrackingMode.SERIALIZED,
                base_unit=UnitCode.MILLILITRE,
            )

    def test_usage_basis_requires_matching_configuration(self):
        """Each automatic basis accepts only its meaningful denominator."""
        volume = self.make_item(
            sku='VOLUME-RATE',
            default_usage_basis=InventoryItem.UsageBasis.CELL_VOLUME,
            default_usage_rate=Decimal('1'),
            usage_rate_unit=UnitCode.MILLILITRE,
        )
        self.assertEqual(volume.default_usage_rate, Decimal('1'))

        with self.assertRaises(ValidationError) as context:
            self.make_item(
                sku='BAD-AREA-RATE',
                default_usage_basis=InventoryItem.UsageBasis.SURFACE_AREA,
                default_usage_rate=Decimal('2'),
                usage_rate_unit=UnitCode.EACH,
            )
        self.assertIn('usage_rate_unit', context.exception.message_dict)

        fixed = self.make_item(
            sku='FIXED-USAGE',
            default_usage_basis=InventoryItem.UsageBasis.FIXED,
            default_fixed_quantity=Decimal('25.5'),
        )
        self.assertEqual(fixed.default_fixed_quantity, Decimal('25.5'))

    def test_sku_is_unique_only_within_a_workspace(self):
        """Workspace catalogs have independent SKU namespaces."""
        item = self.make_item()
        with self.assertRaises(ValidationError):
            self.make_item(name='Duplicate SKU')

        other = Workspace.objects.create(name='Other inventory workspace')
        duplicate = self.make_item(
            workspace=other,
            name='Other workspace item',
        )
        self.assertEqual(duplicate.sku, item.sku)

    def test_stock_history_locks_base_unit_and_tracking_mode(self):
        """Identity fields stop changing when a first movement is recorded."""
        item = self.make_item()
        item.mark_stock_history_started()
        self.assertIsNotNone(item.stock_history_started_at)

        item.base_unit = UnitCode.LITRE
        with self.assertRaises(ValidationError) as context:
            item.save()
        self.assertIn('base_unit', context.exception.message_dict)

        item.refresh_from_db()
        item.tracking_mode = InventoryItem.TrackingMode.SERIALIZED
        item.base_unit = UnitCode.EACH
        with self.assertRaises(ValidationError) as context:
            item.save()
        self.assertIn('tracking_mode', context.exception.message_dict)

    def test_items_must_be_deactivated_instead_of_deleted(self):
        """Catalog identity remains available to future historical records."""
        item = self.make_item()
        with self.assertRaisesMessage(ValidationError, 'must be deactivated'):
            item.delete()


class ItemUnitConversionModelTests(TestCase):
    """Package conversions are positive, scoped, and persistent."""

    def setUp(self):
        super().setUp()
        self.item = InventoryItem.objects.create(
            name='Potting mix',
            category=InventoryItem.Category.GROWING_MEDIA,
            base_unit=UnitCode.MILLILITRE,
        )

    def test_package_multiplier_uses_documented_decimal_precision(self):
        """A forty-litre bag can normalize exactly into millilitres."""
        conversion = ItemUnitConversion.objects.create(
            item=self.item,
            label='40 L bag',
            multiplier=Decimal('40000'),
        )
        self.assertEqual(conversion.workspace, get_current_workspace())
        self.assertEqual(conversion.multiplier, Decimal('40000'))

    def test_zero_negative_duplicate_and_cross_workspace_values_are_rejected(self):
        """Ambiguous or non-positive package units never enter the catalog."""
        ItemUnitConversion.objects.create(
            item=self.item,
            label='Bag',
            multiplier=Decimal('1000'),
        )
        for multiplier in (Decimal('0'), Decimal('-1')):
            with self.subTest(multiplier=multiplier), self.assertRaises(ValidationError):
                ItemUnitConversion.objects.create(
                    item=self.item,
                    label=f'Invalid {multiplier}',
                    multiplier=multiplier,
                )
        with self.assertRaises(ValidationError):
            ItemUnitConversion.objects.create(
                item=self.item,
                label='Bag',
                multiplier=Decimal('2000'),
            )

        other = Workspace.objects.create(name='Other workspace')
        with self.assertRaises(ValidationError):
            ItemUnitConversion.objects.create(
                workspace=other,
                item=self.item,
                label='Foreign bag',
                multiplier=Decimal('1000'),
            )

    def test_conversions_must_be_deactivated_instead_of_deleted(self):
        """Historical package labels cannot be removed."""
        conversion = ItemUnitConversion.objects.create(
            item=self.item,
            label='Bag',
            multiplier=Decimal('1000'),
        )
        with self.assertRaisesMessage(ValidationError, 'must be deactivated'):
            conversion.delete()
