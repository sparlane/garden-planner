"""Tests for suggesting how much of an input a set of targets consumes."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from inventory.models import InventoryItem
from inventory.units import UnitCode

from .usage import TargetInput, UsageInputs, calculate_usage


def cell(volume, weight='1', label='Cell'):
    """Build one tray-cell target of a known volume."""
    return TargetInput(
        target_type='seed_tray_cell',
        weight=Decimal(weight),
        cell_volume_ml=volume,
        label=label,
    )


def ground(area_m2, weight='1', target_type='garden_square'):
    """Build one measured piece of garden geometry."""
    return TargetInput(
        target_type=target_type,
        weight=Decimal(weight),
        area_m2=Decimal(area_m2),
        label='Square',
    )


def plant(weight='1'):
    """Build one plant target."""
    return TargetInput(target_type='specific_plant', weight=Decimal(weight), label='Plant')


class CellVolumeUsageTests(SimpleTestCase):
    """Filling tray cells with growing media."""

    def calculate(self, targets, base_unit=UnitCode.LITRE, fill_factor=None):
        """Run a cell-volume calculation over the given cells."""
        return calculate_usage(UsageInputs(
            basis=InventoryItem.UsageBasis.CELL_VOLUME,
            base_unit=base_unit,
            fill_factor=fill_factor,
            targets=tuple(targets),
        ))

    def test_cells_of_one_size_sum(self):
        """Twenty-four 40 ml cells hold 960 ml, which is 0.96 litres."""
        result = self.calculate([cell(40) for _ in range(24)])
        self.assertEqual(result.calculated_base_quantity, Decimal('0.960000000'))
        self.assertEqual(result.basis_quantity, Decimal('960.000000000'))
        self.assertEqual(result.basis_unit, UnitCode.MILLILITRE)
        self.assertEqual(result.target_count, 24)

    def test_cells_of_different_sizes_sum(self):
        """Mixed tray models simply add, because each cell carries its volume."""
        result = self.calculate([cell(40), cell(40), cell(100), cell(15)])
        self.assertEqual(result.basis_quantity, Decimal('195.000000000'))
        self.assertEqual(result.calculated_base_quantity, Decimal('0.195000000'))

    def test_a_fill_factor_scales_the_volume(self):
        """Filling cells to 85 per cent uses 85 per cent of their volume."""
        result = self.calculate([cell(40) for _ in range(24)], fill_factor=Decimal('0.85'))
        self.assertEqual(result.calculated_base_quantity, Decimal('0.816000000'))

    def test_a_partial_weight_scales_one_cell(self):
        """A half-filled cell contributes half its volume."""
        result = self.calculate([cell(40), cell(40, weight='0.5')])
        self.assertEqual(result.basis_quantity, Decimal('60.000000000'))

    def test_a_millilitre_item_needs_no_conversion(self):
        """An item measured in millilitres reports the summed volume directly."""
        result = self.calculate([cell(40)], base_unit=UnitCode.MILLILITRE)
        self.assertEqual(result.calculated_base_quantity, Decimal('40.000000000'))

    def test_a_zero_fill_factor_is_refused(self):
        """Filling cells to nothing is not an application."""
        with self.assertRaises(ValidationError) as caught:
            self.calculate([cell(40)], fill_factor=Decimal('0'))
        self.assertIn('fill_factor', caught.exception.message_dict)

    def test_cells_are_required(self):
        """A cell-volume line has nothing to measure without cells."""
        with self.assertRaises(ValidationError) as caught:
            self.calculate([])
        self.assertIn('targets', caught.exception.message_dict)

    def test_a_cell_without_a_recorded_volume_is_refused(self):
        """A tray model with no cell volume cannot silently contribute zero."""
        with self.assertRaises(ValidationError) as caught:
            self.calculate([cell(None)])
        self.assertIn('targets', caught.exception.message_dict)

    def test_the_formula_shows_its_working(self):
        """An operator can see where the suggestion came from."""
        result = self.calculate([cell(40) for _ in range(24)], fill_factor=Decimal('0.85'))
        self.assertEqual(
            result.formula,
            '24 cells totalling 960 ml, filled to 0.85 = 0.816 l',
        )


class SurfaceAreaUsageTests(SimpleTestCase):
    """Treating ground by its normalized area."""

    def calculate(self, targets, rate='2', rate_unit=UnitCode.SQUARE_METRE):
        """Run a surface-area calculation over the given geometry."""
        return calculate_usage(UsageInputs(
            basis=InventoryItem.UsageBasis.SURFACE_AREA,
            base_unit=UnitCode.GRAM,
            rate=Decimal(rate),
            rate_unit=rate_unit,
            targets=tuple(targets),
        ))

    def test_area_times_rate(self):
        """Six square metres at 2 g per m2 needs 12 g."""
        result = self.calculate([ground('4'), ground('2')])
        self.assertEqual(result.calculated_base_quantity, Decimal('12.000000000'))
        self.assertEqual(result.basis_quantity, Decimal('6.000000000'))

    def test_a_weight_scales_one_place(self):
        """Treating half a bed uses half its area."""
        result = self.calculate([ground('4', weight='0.5')])
        self.assertEqual(result.basis_quantity, Decimal('2.000000000'))

    def test_every_geometry_level_can_be_treated(self):
        """Areas, beds, rows, and squares all measure the same way."""
        levels = ('garden_area', 'garden_bed', 'garden_row', 'garden_square')
        for level in levels:
            with self.subTest(level=level):
                result = self.calculate([ground('3', target_type=level)])
                self.assertEqual(result.calculated_base_quantity, Decimal('6.000000000'))

    def test_a_rate_in_the_wrong_dimension_is_refused(self):
        """A treatment cannot be dosed per litre of ground."""
        with self.assertRaises(ValidationError) as caught:
            self.calculate([ground('4')], rate_unit=UnitCode.LITRE)
        self.assertIn('configured_rate_unit', caught.exception.message_dict)

    def test_a_missing_rate_is_refused(self):
        """Without a rate there is nothing to multiply the area by."""
        with self.assertRaises(ValidationError) as caught:
            calculate_usage(UsageInputs(
                basis=InventoryItem.UsageBasis.SURFACE_AREA,
                base_unit=UnitCode.GRAM,
                targets=(ground('4'),),
            ))
        self.assertIn('configured_rate', caught.exception.message_dict)

    def test_ground_without_a_measured_area_is_refused(self):
        """Unconfirmed geometry never reaches the calculation as a zero."""
        unmeasured = TargetInput(target_type='garden_square', label='Square A1')
        with self.assertRaises(ValidationError) as caught:
            self.calculate([unmeasured])
        self.assertIn('targets', caught.exception.message_dict)


class PerUnitUsageTests(SimpleTestCase):
    """Applying a rate once per plant or item."""

    def calculate(self, targets, rate='1', rate_unit=UnitCode.EACH):
        """Run a per-unit calculation over the given targets."""
        return calculate_usage(UsageInputs(
            basis=InventoryItem.UsageBasis.PER_UNIT,
            base_unit=UnitCode.EACH,
            rate=Decimal(rate),
            rate_unit=rate_unit,
            targets=tuple(targets),
        ))

    def test_one_label_per_plant(self):
        """Twelve plants need twelve labels."""
        result = self.calculate([plant() for _ in range(12)])
        self.assertEqual(result.calculated_base_quantity, Decimal('12.000000000'))
        self.assertEqual(result.target_count, 12)

    def test_a_rate_above_one_multiplies(self):
        """Two labels per plant doubles the count."""
        result = self.calculate([plant() for _ in range(12)], rate='2')
        self.assertEqual(result.calculated_base_quantity, Decimal('24.000000000'))

    def test_a_count_rate_in_the_wrong_dimension_is_refused(self):
        """Labels are not dosed per square metre."""
        with self.assertRaises(ValidationError) as caught:
            self.calculate([plant()], rate_unit=UnitCode.SQUARE_METRE)
        self.assertIn('configured_rate_unit', caught.exception.message_dict)

    def test_targets_are_required(self):
        """Nothing to label means nothing to suggest."""
        with self.assertRaises(ValidationError) as caught:
            self.calculate([])
        self.assertIn('targets', caught.exception.message_dict)


class FixedAndManualUsageTests(SimpleTestCase):
    """Bases that do not scale with what was targeted."""

    def test_a_fixed_quantity_ignores_the_target_count(self):
        """One dose is one dose however many plants received it."""
        result = calculate_usage(UsageInputs(
            basis=InventoryItem.UsageBasis.FIXED,
            base_unit=UnitCode.LITRE,
            fixed_quantity=Decimal('2.5'),
            targets=tuple(plant() for _ in range(7)),
        ))
        self.assertEqual(result.calculated_base_quantity, Decimal('2.500000000'))
        self.assertEqual(result.target_count, 7)
        self.assertIsNone(result.basis_quantity)

    def test_a_fixed_basis_needs_a_quantity(self):
        """A fixed usage with no configured amount suggests nothing usable."""
        with self.assertRaises(ValidationError) as caught:
            calculate_usage(UsageInputs(
                basis=InventoryItem.UsageBasis.FIXED,
                base_unit=UnitCode.LITRE,
            ))
        self.assertIn('configured_fixed_quantity', caught.exception.message_dict)

    def test_manual_usage_suggests_nothing(self):
        """No formula applies, so the operator supplies the amount."""
        result = calculate_usage(UsageInputs(
            basis=InventoryItem.UsageBasis.MANUAL,
            base_unit=UnitCode.LITRE,
            targets=(plant(),),
        ))
        self.assertIsNone(result.calculated_base_quantity)
        self.assertEqual(result.formula, 'Manual entry')

    def test_an_unknown_basis_is_refused(self):
        """Only the supported bases can produce a suggestion."""
        with self.assertRaises(ValidationError) as caught:
            calculate_usage(UsageInputs(basis='vibes', base_unit=UnitCode.LITRE))
        self.assertIn('usage_basis', caught.exception.message_dict)

    def test_an_unknown_base_unit_is_refused(self):
        """A quantity is meaningless in a unit the registry does not define."""
        with self.assertRaises(ValidationError):
            calculate_usage(UsageInputs(
                basis=InventoryItem.UsageBasis.MANUAL,
                base_unit='buckets',
            ))
