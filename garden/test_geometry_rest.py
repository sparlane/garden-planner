"""REST contract for confirming what garden geometry physically measures."""

# pylint: disable=duplicate-code

from decimal import Decimal

from tests.api import RESTContractTestCase
from tests.factories import make_garden_area, make_garden_geometry_confirmation

from .models import GardenGeometryConfirmation


class ConfirmGeometryTests(RESTContractTestCase):
    """Recording and reading an area's confirmed physical scale."""

    def setUp(self):
        super().setUp()
        self.area = make_garden_area(size_x=2000, size_y=1000)
        self.confirm_url = f'/garden/areas/{self.area.pk}/confirm-geometry/'
        self.history_url = f'/garden/areas/{self.area.pk}/geometry-confirmations/'

    def test_an_area_reports_itself_unconfirmed(self):
        """Nothing claims a physical extent before an operator states one."""
        response = self.client.get(f'/garden/areas/{self.area.pk}/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['geometry_confirmed'])
        self.assertIsNone(response.data['length_unit'])
        self.assertIsNone(response.data['cell_length'])
        self.assertIsNone(response.data['square_metres'])

    def test_confirming_reports_the_normalized_extent(self):
        """A 2000 x 1000 mm area measures 2 m2 once its unit is confirmed."""
        response = self.client.post(
            self.confirm_url,
            {'length_unit': 'mm', 'cell_length': '1'},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)

        area = self.client.get(f'/garden/areas/{self.area.pk}/')
        self.assertTrue(area.data['geometry_confirmed'])
        self.assertEqual(area.data['length_unit'], 'mm')
        self.assertEqual(area.data['cell_length'], '1.000000')
        self.assertEqual(area.data['square_metres'], '2.000000')

    def test_a_confirmation_records_its_operator(self):
        """The audited statement names whoever made it."""
        self.client.post(
            self.confirm_url,
            {'length_unit': 'm', 'cell_length': '1', 'notes': 'Measured'},
            format='json',
        )
        confirmation = GardenGeometryConfirmation.objects.get(area=self.area)
        self.assertEqual(confirmation.confirmed_by, self.user)
        self.assertEqual(confirmation.notes, 'Measured')

    def test_confirming_again_supersedes_without_erasing(self):
        """A mistaken unit is corrected by a new statement, not a rewrite."""
        self.client.post(
            self.confirm_url,
            {'length_unit': 'm', 'cell_length': '1'},
            format='json',
        )
        self.client.post(
            self.confirm_url,
            {'length_unit': 'mm', 'cell_length': '1'},
            format='json',
        )
        area = self.client.get(f'/garden/areas/{self.area.pk}/')
        self.assertEqual(area.data['length_unit'], 'mm')

        history = self.client.get(self.history_url)
        self.assertEqual(history.status_code, 200, history.data)
        self.assertEqual([row['length_unit'] for row in history.data], ['mm', 'm'])

    def test_a_zero_grid_step_is_refused(self):
        """A step of no length would collapse every area to nothing."""
        response = self.client.post(
            self.confirm_url,
            {'length_unit': 'm', 'cell_length': '0'},
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)

    def test_an_unknown_unit_is_refused(self):
        """Only units with an exact metre equivalent may be recorded."""
        response = self.client.post(
            self.confirm_url,
            {'length_unit': 'furlong', 'cell_length': '1'},
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('length_unit', response.data)

    def test_confirmation_requires_authentication(self):
        """Anonymous callers cannot state what an area measures."""
        self.client.logout()
        response = self.client.post(
            self.confirm_url,
            {'length_unit': 'm', 'cell_length': '1'},
            format='json',
        )
        self.assertEqual(response.status_code, 403, response.data)

    def _confirmed_areas(self, count):
        """Create `count` confirmed one-square-metre areas."""
        for _ in range(count):
            make_garden_geometry_confirmation(
                area=make_garden_area(size_x=1000, size_y=1000),
                length_unit=GardenGeometryConfirmation.LengthUnit.MILLIMETRE,
                cell_length=Decimal('1'),
            )

    def test_listing_areas_costs_the_same_however_many_are_confirmed(self):
        """Deriving each scale reuses one prefetch instead of a query per area.

        The count is asserted at both sizes because the number itself is
        incidental; what matters is that it does not grow with the collection.
        """
        self._confirmed_areas(3)
        with self.assertNumQueries(3):
            response = self.client.get('/garden/areas/')
        self.assertEqual(response.status_code, 200, response.data)
        confirmed = [row for row in response.data if row['geometry_confirmed']]
        self.assertEqual(len(confirmed), 3)
        self.assertEqual(confirmed[0]['square_metres'], '1.000000')

        self._confirmed_areas(9)
        with self.assertNumQueries(3):
            self.client.get('/garden/areas/')
