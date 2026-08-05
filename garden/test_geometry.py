"""Tests for normalizing garden geometry into physical area."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from tests.factories import (
    make_garden_area,
    make_garden_bed,
    make_garden_geometry_confirmation,
    make_garden_row,
    make_garden_square,
)
from workspaces.models import Workspace

from .geometry import (
    area_confirmation,
    is_confirmed,
    metres_per_grid_step,
    owning_area,
    square_metres,
)
from .models import GardenGeometryConfirmation


class GeometryConfirmationModelTests(TestCase):
    """Rules that keep a confirmation an honest, append-only statement."""

    def test_a_confirmation_cannot_be_edited(self):
        """A wrong unit is corrected by confirming again, not by rewriting."""
        confirmation = make_garden_geometry_confirmation()
        confirmation.length_unit = GardenGeometryConfirmation.LengthUnit.METRE
        with self.assertRaises(ValidationError):
            confirmation.save()

    def test_a_confirmation_cannot_be_deleted(self):
        """The original statement stays on file after a correction."""
        confirmation = make_garden_geometry_confirmation()
        with self.assertRaises(ValidationError):
            confirmation.delete()

    def test_a_zero_grid_step_is_refused(self):
        """A grid step of no length would make every area zero."""
        area = make_garden_area()
        with self.assertRaises(ValidationError):
            make_garden_geometry_confirmation(
                area=area,
                cell_length=Decimal('0'),
            )

    def test_the_database_refuses_a_zero_grid_step(self):
        """The check constraint holds even when validation is bypassed."""
        area = make_garden_area()
        with self.assertRaises(IntegrityError), transaction.atomic():
            GardenGeometryConfirmation.objects.bulk_create([
                GardenGeometryConfirmation(
                    area=area,
                    length_unit=GardenGeometryConfirmation.LengthUnit.METRE,
                    cell_length=Decimal('0'),
                ),
            ])

    def test_an_area_from_another_workspace_is_refused(self):
        """A confirmation cannot describe another workspace's garden."""
        other = Workspace.objects.create(name='Other workspace')
        area = make_garden_area(workspace=other)
        with self.assertRaises(ValidationError):
            make_garden_geometry_confirmation(area=area)


class AreaConfirmationLookupTests(TestCase):
    """Which statement governs an area, and whether one exists at all."""

    def test_an_area_starts_unconfirmed(self):
        """Existing geometry means nothing until an operator says so."""
        area = make_garden_area()
        self.assertIsNone(area_confirmation(area))
        self.assertFalse(is_confirmed(area))

    def test_the_newest_confirmation_wins(self):
        """Confirming again corrects the scale without losing the original."""
        area = make_garden_area()
        make_garden_geometry_confirmation(
            area=area,
            length_unit=GardenGeometryConfirmation.LengthUnit.FOOT,
            cell_length=Decimal('1'),
            confirmed_at=timezone.now() - timezone.timedelta(days=1),
        )
        newest = make_garden_geometry_confirmation(
            area=area,
            length_unit=GardenGeometryConfirmation.LengthUnit.MILLIMETRE,
            cell_length=Decimal('1'),
        )
        self.assertEqual(area_confirmation(area), newest)
        self.assertEqual(area.geometry_confirmations.count(), 2)

    def test_a_confirmation_records_who_said_so(self):
        """An audited statement names its operator."""
        user = get_user_model().objects.create_user(username='geometry-user')
        confirmation = make_garden_geometry_confirmation(confirmed_by=user)
        self.assertEqual(confirmation.confirmed_by, user)


class OwningAreaTests(TestCase):
    """Every piece of geometry resolves to the area that scales it."""

    def test_each_geometry_type_resolves_to_its_area(self):
        """Beds, rows, and squares all inherit their area's grid step."""
        area = make_garden_area()
        bed = make_garden_bed(area=area)
        cases = {
            'area': area,
            'bed': bed,
            'row': make_garden_row(bed=bed),
            'square': make_garden_square(bed=bed),
        }
        for label, geometry in cases.items():
            with self.subTest(geometry=label):
                self.assertEqual(owning_area(geometry), area)

    def test_a_non_geometry_target_is_refused(self):
        """Only garden geometry carries a physical extent."""
        with self.assertRaises(ValidationError):
            owning_area(make_garden_geometry_confirmation())


class SquareMetreTests(TestCase):
    """Converting a confirmed grid into normalized square metres."""

    def test_unconfirmed_geometry_is_refused(self):
        """An area-based measurement never guesses what an integer meant."""
        square = make_garden_square()
        with self.assertRaises(ValidationError) as caught:
            square_metres(square)
        self.assertIn('target', caught.exception.message_dict)

    def test_a_millimetre_grid_normalizes_to_square_metres(self):
        """A 300 x 300 mm square is 0.09 m2."""
        bed = make_garden_bed()
        make_garden_geometry_confirmation(
            area=bed.area,
            length_unit=GardenGeometryConfirmation.LengthUnit.MILLIMETRE,
            cell_length=Decimal('1'),
        )
        square = make_garden_square(bed=bed, size_x=300, size_y=300)
        self.assertEqual(square_metres(square), Decimal('0.090000'))

    def test_a_multi_unit_grid_step_scales_the_area(self):
        """A step worth 2.5 cm squares to 0.000625 m2 per grid cell."""
        bed = make_garden_bed()
        make_garden_geometry_confirmation(
            area=bed.area,
            length_unit=GardenGeometryConfirmation.LengthUnit.CENTIMETRE,
            cell_length=Decimal('2.5'),
        )
        square = make_garden_square(bed=bed, size_x=4, size_y=4)
        self.assertEqual(square_metres(square), Decimal('0.010000'))

    def test_imperial_steps_convert_exactly(self):
        """One square foot is 0.09290304 m2, rounded to the stored precision."""
        bed = make_garden_bed()
        make_garden_geometry_confirmation(
            area=bed.area,
            length_unit=GardenGeometryConfirmation.LengthUnit.FOOT,
            cell_length=Decimal('1'),
        )
        square = make_garden_square(bed=bed, size_x=1, size_y=1)
        self.assertEqual(square_metres(square), Decimal('0.092903'))

    def test_every_geometry_level_measures(self):
        """An area, bed, row, and square each report their own extent."""
        area = make_garden_area(size_x=10, size_y=10)
        make_garden_geometry_confirmation(
            area=area,
            length_unit=GardenGeometryConfirmation.LengthUnit.METRE,
            cell_length=Decimal('1'),
        )
        bed = make_garden_bed(area=area, size_x=4, size_y=5)
        expected = {
            area: Decimal('100.000000'),
            bed: Decimal('20.000000'),
            make_garden_row(bed=bed, size_x=4, size_y=1): Decimal('4.000000'),
            make_garden_square(bed=bed, size_x=2, size_y=2): Decimal('4.000000'),
        }
        for geometry, area_m2 in expected.items():
            with self.subTest(geometry=str(geometry)):
                self.assertEqual(square_metres(geometry), area_m2)

    def test_the_grid_step_is_reported_in_metres(self):
        """A millimetre step is a thousandth of a metre."""
        area = make_garden_area()
        make_garden_geometry_confirmation(
            area=area,
            length_unit=GardenGeometryConfirmation.LengthUnit.MILLIMETRE,
            cell_length=Decimal('1'),
        )
        self.assertEqual(metres_per_grid_step(area), Decimal('0.001'))
