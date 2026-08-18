"""REST contract for placing garden beds, rows, and squares."""

# pylint: disable=duplicate-code

from tests.api import RESTContractTestCase
from tests.factories import make_garden_area, make_garden_bed, make_garden_square

from .models import GardenBed, GardenSquare


class BedPlacementRESTTests(RESTContractTestCase):
    """Placing a bed through the API explains a refusal in field terms."""

    def setUp(self):
        super().setUp()
        self.area = make_garden_area(size_x=100, size_y=100)

    def _post_bed(self, **overrides):
        payload = {
            'area': self.area.pk,
            'name': 'New bed',
            'placement_x': 0,
            'placement_y': 0,
            'size_x': 20,
            'size_y': 20,
        }
        payload.update(overrides)
        return self.client.post('/garden/beds/', payload, format='json')

    def test_a_bed_inside_its_area_is_created(self):
        """The ordinary case still works once placement is validated."""
        response = self._post_bed()
        self.assertEqual(response.status_code, 201, response.data)

    def test_a_bed_past_the_area_edge_is_a_field_error(self):
        """A placement failure is a 400 naming the axis, not a server error."""
        response = self._post_bed(placement_x=90, size_x=20)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('placement_x', response.data)
        self.assertEqual(GardenBed.objects.count(), 0)

    def test_an_overlapping_bed_names_its_neighbour(self):
        """The refusal says which bed is already there and where it sits."""
        make_garden_bed(area=self.area, name='North bed', placement_x=0, placement_y=0, size_x=20, size_y=20)
        response = self._post_bed(placement_x=10, placement_y=10)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('North bed', str(response.data['non_field_errors'][0]))
        self.assertEqual(GardenBed.objects.count(), 1)

    def test_moving_a_bed_onto_another_is_refused(self):
        """A PATCH is validated exactly as a POST is."""
        make_garden_bed(area=self.area, name='North bed', placement_x=0, placement_y=0, size_x=20, size_y=20)
        bed = make_garden_bed(area=self.area, name='South bed', placement_x=40, placement_y=0, size_x=20, size_y=20)
        response = self.client.patch(
            f'/garden/beds/{bed.pk}/',
            {'placement_x': 10},
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        bed.refresh_from_db()
        self.assertEqual(bed.placement_x, 40)


class BedKindRESTTests(RESTContractTestCase):
    """A bed carries what the gardener said they were making."""

    def test_a_bed_defaults_to_in_ground(self):
        """Not saying what a bed is means open ground, the household default."""
        response = self.client.post(
            '/garden/beds/',
            {
                'area': make_garden_area(size_x=50, size_y=50).pk,
                'name': 'Unstated bed',
                'placement_x': 0,
                'placement_y': 0,
                'size_x': 10,
                'size_y': 10,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['kind'], 'in_ground')

    def test_a_stated_kind_round_trips(self):
        """A raised bed is still a raised bed when it is read back."""
        self.assert_create_retrieve(
            '/garden/beds/',
            {
                'area': make_garden_area(size_x=50, size_y=50).pk,
                'name': 'Raised bed',
                'kind': 'raised',
                'placement_x': 0,
                'placement_y': 0,
                'size_x': 10,
                'size_y': 10,
            },
        )

    def test_an_unknown_kind_is_refused(self):
        """The vocabulary is controlled, like every other choice field."""
        response = self.client.post(
            '/garden/beds/',
            {
                'area': make_garden_area(size_x=50, size_y=50).pk,
                'name': 'Hydroponic bed',
                'kind': 'aquaponic',
                'placement_x': 0,
                'placement_y': 0,
                'size_x': 10,
                'size_y': 10,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('kind', response.data)


class SquarePlacementRESTTests(RESTContractTestCase):
    """Squares are placed on their bed's grid."""

    def setUp(self):
        super().setUp()
        self.bed = make_garden_bed(
            area=make_garden_area(size_x=100, size_y=100),
            placement_x=0,
            placement_y=0,
            size_x=10,
            size_y=40,
        )

    def test_a_square_past_the_bed_edge_is_refused(self):
        """A square is measured against its bed, not against the area."""
        response = self.client.post(
            '/garden/squares/',
            {
                'bed': self.bed.pk,
                'name': 'Overhang',
                'placement_x': 9,
                'placement_y': 0,
                'size_x': 5,
                'size_y': 1,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('placement_x', response.data)
        self.assertEqual(GardenSquare.objects.count(), 0)

    def _fill_row(self, placement_y, width):
        """Lay a row of one-cell squares across the bed."""
        for index in range(width):
            make_garden_square(
                bed=self.bed,
                name=f'{placement_y}-{index}',
                placement_x=index,
                placement_y=placement_y,
                size_x=1,
                size_y=1,
            )

    def _post_square(self, placement_y):
        """Add one more square above the rows already laid."""
        return self.client.post(
            '/garden/squares/',
            {
                'bed': self.bed.pk,
                'name': f'New {placement_y}',
                'placement_x': 0,
                'placement_y': placement_y,
                'size_x': 1,
                'size_y': 1,
            },
            format='json',
        )

    def test_placement_validation_does_not_scale_with_the_neighbours(self):
        """Checking for a collision costs the same against 2 squares or 20."""
        self._fill_row(0, 2)
        with self.assertNumQueries(9):
            small = self._post_square(1)
        self.assertEqual(small.status_code, 201, small.data)

        for placement_y in range(2, 12):
            self._fill_row(placement_y, 2)
        with self.assertNumQueries(9):
            large = self._post_square(12)
        self.assertEqual(large.status_code, 201, large.data)


class LayoutBatchRESTTests(RESTContractTestCase):
    """A whole layout template arrives, and is refused, as one thing."""

    def setUp(self):
        super().setUp()
        self.bed = make_garden_bed(
            area=make_garden_area(size_x=100, size_y=100),
            placement_x=0,
            placement_y=0,
            size_x=8,
            size_y=8,
        )

    def _grid(self, columns, rows, size=1):
        """Describe a tiled grid of squares filling part of the bed."""
        return [
            {
                'bed': self.bed.pk,
                'name': f'{chr(ord("A") + row)}{column + 1}',
                'placement_x': column * size,
                'placement_y': row * size,
                'size_x': size,
                'size_y': size,
            }
            for row in range(rows)
            for column in range(columns)
        ]

    def test_a_grid_is_created_in_one_request(self):
        """A square-foot template is one choice, so it is one request."""
        response = self.client.post('/garden/squares/', self._grid(4, 4), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data), 16)
        self.assertEqual(GardenSquare.objects.count(), 16)

    def test_a_batch_that_overruns_the_bed_leaves_nothing_behind(self):
        """A dimension typed wrong does not half-build a bed."""
        grid = self._grid(4, 4)
        grid[-1]['size_x'] = 20
        response = self.client.post('/garden/squares/', grid, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(GardenSquare.objects.count(), 0)

    def test_a_batch_that_overlaps_itself_is_refused(self):
        """Squares are checked against each other, not only against what exists."""
        grid = self._grid(2, 2)
        grid[-1]['placement_x'] = 0
        grid[-1]['placement_y'] = 0
        response = self.client.post('/garden/squares/', grid, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(GardenSquare.objects.count(), 0)

    def test_an_implausible_batch_is_refused_before_any_work(self):
        """A garden is not four hundred squares wide; that is a typo."""
        response = self.client.post(
            '/garden/squares/',
            [
                {
                    'bed': self.bed.pk,
                    'name': f'Square {index}',
                    'placement_x': 0,
                    'placement_y': 0,
                    'size_x': 1,
                    'size_y': 1,
                }
                for index in range(401)
            ],
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('400 is the most', str(response.data[0]))
        self.assertEqual(GardenSquare.objects.count(), 0)

    def test_rows_batch_the_same_way(self):
        """The rows template gets the same guarantee the squares one does."""
        response = self.client.post(
            '/garden/rows/',
            [
                {
                    'bed': self.bed.pk,
                    'name': f'Row {index + 1}',
                    'placement_x': 0,
                    'placement_y': index * 2,
                    'size_x': 8,
                    'size_y': 1,
                }
                for index in range(4)
            ],
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data), 4)


class GeometryDeletionRESTTests(RESTContractTestCase):
    """Removing geometry that recorded activity still refers to."""

    def test_an_unused_bed_can_be_removed(self):
        """Nothing stands in the way of correcting a fresh mistake."""
        bed = make_garden_bed()
        response = self.client.delete(f'/garden/beds/{bed.pk}/')
        self.assertEqual(response.status_code, 204, response.data)

    def test_a_bed_holding_squares_is_refused_with_a_message(self):
        """A protected reference is explained, not surfaced as a 500."""
        square = make_garden_square()
        response = self.client.delete(f'/garden/beds/{square.bed.pk}/')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('recorded activity', str(response.data[0]))
        self.assertTrue(GardenBed.objects.filter(pk=square.bed.pk).exists())
