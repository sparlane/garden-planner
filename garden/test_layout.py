"""Placement rules for garden beds, rows, and squares."""

# pylint: disable=duplicate-code

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.test import TestCase

from tests.factories import (
    make_garden_area,
    make_garden_bed,
    make_garden_row,
    make_garden_square,
)

from .layout import Rect, overlaps


class RectTests(TestCase):
    """The rectangle arithmetic every placement rule is built on."""

    def test_touching_edges_do_not_overlap(self):
        """Two beds laid side by side share a boundary, not a cell."""
        self.assertFalse(overlaps(Rect(0, 0, 10, 10), Rect(10, 0, 10, 10)))
        self.assertFalse(overlaps(Rect(0, 0, 10, 10), Rect(0, 10, 10, 10)))

    def test_a_shared_cell_overlaps(self):
        """One cell in common is enough to be a collision."""
        self.assertTrue(overlaps(Rect(0, 0, 10, 10), Rect(9, 9, 10, 10)))

    def test_containment_overlaps(self):
        """A rectangle wholly inside another still collides with it."""
        self.assertTrue(overlaps(Rect(0, 0, 10, 10), Rect(2, 2, 3, 3)))

    def test_separation_on_one_axis_is_enough(self):
        """Rectangles that share columns but no rows do not collide."""
        self.assertFalse(overlaps(Rect(0, 0, 10, 2), Rect(0, 5, 10, 2)))


class BedPlacementTests(TestCase):
    """A bed is placed on its area's grid."""

    def setUp(self):
        super().setUp()
        self.area = make_garden_area(size_x=100, size_y=100)

    def test_a_bed_fitting_its_area_is_accepted(self):
        """The area's far corner is an inclusive boundary."""
        bed = make_garden_bed(area=self.area, placement_x=50, placement_y=50, size_x=50, size_y=50)
        self.assertIsNotNone(bed.pk)

    def test_a_bed_running_past_the_area_is_refused(self):
        """A bed cannot occupy ground the area does not cover."""
        with self.assertRaises(ValidationError) as caught:
            make_garden_bed(area=self.area, placement_x=60, placement_y=0, size_x=50, size_y=10)
        self.assertIn('placement_x', caught.exception.message_dict)
        self.assertIn('only 100 wide', caught.exception.message_dict['placement_x'][0])

    def test_both_axes_are_reported_together(self):
        """A bed off the area on both axes is told about both."""
        with self.assertRaises(ValidationError) as caught:
            make_garden_bed(area=self.area, placement_x=60, placement_y=60, size_x=50, size_y=50)
        self.assertEqual(
            sorted(caught.exception.message_dict),
            ['placement_x', 'placement_y'],
        )

    def test_overlapping_beds_are_refused(self):
        """Two beds cannot claim the same ground."""
        make_garden_bed(area=self.area, name='North bed', placement_x=0, placement_y=0, size_x=20, size_y=20)
        with self.assertRaises(ValidationError) as caught:
            make_garden_bed(area=self.area, placement_x=10, placement_y=10, size_x=20, size_y=20)
        message = caught.exception.message_dict[NON_FIELD_ERRORS][0]
        self.assertIn('North bed', message)
        self.assertIn('0 to 20 across', message)

    def test_beds_in_different_areas_may_share_coordinates(self):
        """Placement is relative to the area, so two areas do not collide."""
        make_garden_bed(area=self.area, placement_x=0, placement_y=0, size_x=20, size_y=20)
        other = make_garden_bed(
            area=make_garden_area(size_x=100, size_y=100),
            placement_x=0,
            placement_y=0,
            size_x=20,
            size_y=20,
        )
        self.assertIsNotNone(other.pk)

    def test_a_bed_may_be_saved_again_unchanged(self):
        """A bed does not overlap itself when it is edited."""
        bed = make_garden_bed(area=self.area, placement_x=0, placement_y=0, size_x=20, size_y=20)
        bed.name = 'Renamed bed'
        bed.save()
        self.assertEqual(bed.name, 'Renamed bed')

    def test_moving_a_bed_onto_another_is_refused(self):
        """The rule holds on update, not only on create."""
        make_garden_bed(area=self.area, placement_x=0, placement_y=0, size_x=20, size_y=20)
        bed = make_garden_bed(area=self.area, placement_x=40, placement_y=0, size_x=20, size_y=20)
        bed.placement_x = 10
        with self.assertRaises(ValidationError):
            bed.save()


class ChildPlacementTests(TestCase):
    """Rows and squares are placed on their bed's grid."""

    def setUp(self):
        super().setUp()
        self.bed = make_garden_bed(
            area=make_garden_area(size_x=100, size_y=100),
            placement_x=0,
            placement_y=0,
            size_x=40,
            size_y=20,
        )

    def test_a_row_running_past_its_bed_is_refused(self):
        """A row is measured against its bed, not against the area."""
        with self.assertRaises(ValidationError) as caught:
            make_garden_row(bed=self.bed, placement_x=0, placement_y=0, size_x=50, size_y=1)
        self.assertIn('only 40 wide', caught.exception.message_dict['placement_x'][0])

    def test_overlapping_rows_are_refused(self):
        """Two rows cannot occupy the same strip of a bed."""
        make_garden_row(bed=self.bed, name='Carrots', placement_x=0, placement_y=0, size_x=40, size_y=2)
        with self.assertRaises(ValidationError) as caught:
            make_garden_row(bed=self.bed, placement_x=0, placement_y=1, size_x=40, size_y=2)
        self.assertIn('Carrots', caught.exception.message_dict[NON_FIELD_ERRORS][0])

    def test_overlapping_squares_are_refused(self):
        """A square-foot grid cannot double up a cell."""
        make_garden_square(bed=self.bed, name='A1', placement_x=0, placement_y=0, size_x=2, size_y=2)
        with self.assertRaises(ValidationError) as caught:
            make_garden_square(bed=self.bed, placement_x=1, placement_y=1, size_x=2, size_y=2)
        self.assertIn('A1', caught.exception.message_dict[NON_FIELD_ERRORS][0])

    def test_a_row_and_a_square_may_share_ground(self):
        """One bed may be described as rows and marked out in squares at once."""
        row = make_garden_row(bed=self.bed, placement_x=0, placement_y=0, size_x=40, size_y=2)
        square = make_garden_square(bed=self.bed, placement_x=0, placement_y=0, size_x=1, size_y=1)
        self.assertIsNotNone(row.pk)
        self.assertIsNotNone(square.pk)

    def test_an_adjacent_grid_is_accepted(self):
        """Squares laid edge to edge tile a bed without colliding."""
        squares = [
            make_garden_square(bed=self.bed, placement_x=x, placement_y=y, size_x=1, size_y=1)
            for x in range(4)
            for y in range(4)
        ]
        self.assertEqual(len(squares), 16)
