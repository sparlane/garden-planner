"""The guided setup walked end to end, as the wizard walks it.

There is no JavaScript test runner in this repository, so the wizard's own
sequence of requests is pinned here: what it sends, in what order, and what a
gardener has at the end of it. A change that breaks the frontend's contract
breaks these.
"""

# pylint: disable=duplicate-code

from tests.api import RESTContractTestCase
from tests.factories import make_batch_for_packet, make_seed_packet

from locations.models import Location
from workspaces.current import get_current_workspace

from .models import GardenArea, GardenBed, GardenRow, GardenSquare


class GuidedSetupWalkthroughTests(RESTContractTestCase):
    """A new workspace reaching a recorded planting through the normal API."""

    def _describe_garden(self, name, timezone, measurement_system):
        """Step one: the profile every later dated record is written in."""
        return self.client.patch(
            '/settings/workspace/',
            {'name': name, 'timezone': timezone, 'measurement_system': measurement_system},
            format='json',
        )

    def _create_space(self, name, size_x, size_y, length_unit):
        """Step two: the ground, and what one of its grid steps measures."""
        area = self.client.post(
            '/garden/areas/',
            {'name': name, 'size_x': size_x, 'size_y': size_y},
            format='json',
        )
        self.assertEqual(area.status_code, 201, area.data)
        confirmation = self.client.post(
            f'/garden/areas/{area.data["pk"]}/confirm-geometry/',
            {'length_unit': length_unit, 'cell_length': '1'},
            format='json',
        )
        self.assertEqual(confirmation.status_code, 201, confirmation.data)
        # Re-read, as the wizard does: the create response predates the
        # confirmation and still says the area is unconfirmed.
        return self.client.get(f'/garden/areas/{area.data["pk"]}/').data

    def _lay_bed(self, area_pk, **overrides):
        """Step three, part one: one rectangle of growing ground."""
        payload = {
            'area': area_pk,
            'name': 'Bed 1',
            'kind': 'raised',
            'placement_x': 0,
            'placement_y': 0,
            'size_x': 240,
            'size_y': 120,
        }
        payload.update(overrides)
        return self.client.post('/garden/beds/', payload, format='json')

    def _mark_grid(self, bed_pk, columns, rows, cell):
        """Step three, part two: the whole grid in one request."""
        return self.client.post(
            '/garden/squares/',
            [
                {
                    'bed': bed_pk,
                    'name': f'{chr(ord("A") + row)}{column + 1}',
                    'placement_x': column * cell,
                    'placement_y': row * cell,
                    'size_x': cell,
                    'size_y': cell,
                }
                for row in range(rows)
                for column in range(columns)
            ],
            format='json',
        )

    def test_a_new_workspace_reaches_a_recorded_planting(self):
        """The whole path, ending where the roadmap says P0 has to end."""
        self._describe_garden('Our garden', 'Pacific/Auckland', 'metric')

        area = self._create_space('Back garden', 1000, 800, 'cm')
        self.assertTrue(area['geometry_confirmed'])

        bed = self._lay_bed(area['pk'])
        self.assertEqual(bed.status_code, 201, bed.data)

        squares = self._mark_grid(bed.data['pk'], columns=8, rows=4, cell=30)
        self.assertEqual(squares.status_code, 201, squares.data)
        self.assertEqual(len(squares.data), 32)

        places = self.client.post('/garden/setup/household-locations/', {}, format='json')
        self.assertEqual(places.status_code, 201, places.data)

        finished = self.client.patch(
            '/settings/workspace/',
            {'garden_setup_state': 'complete'},
            format='json',
        )
        self.assertEqual(finished.status_code, 200, finished.data)

        packet = make_seed_packet()
        batch = make_batch_for_packet(packet)
        sowing = self.client.post(
            '/plantings/directsowgardensquare/',
            {
                'seeds_used': packet.pk,
                'batch': batch.pk,
                'quantity': 4,
                'location': squares.data[0]['pk'],
            },
            format='json',
        )
        self.assertEqual(sowing.status_code, 201, sowing.data)

    def test_the_grid_measures_what_the_gardener_was_shown(self):
        """A 30 cm cell is 0.09 m2, so an area-based input has something true to read."""
        self._describe_garden('Our garden', 'UTC', 'metric')
        area = self._create_space('Back garden', 1000, 800, 'cm')
        bed = self._lay_bed(area['pk'])
        squares = self._mark_grid(bed.data['pk'], columns=2, rows=2, cell=30)

        detail = self.client.get(f'/garden/squares/{squares.data[0]["pk"]}/')
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['size_x'], 30)

        area_detail = self.client.get(f'/garden/areas/{area["pk"]}/')
        self.assertEqual(area_detail.data['length_unit'], 'cm')
        self.assertEqual(area_detail.data['square_metres'], '80.000000')

    def test_an_imperial_workspace_walks_the_same_path(self):
        """Every template has to work in feet and inches, not only in metres."""
        self._describe_garden('Our garden', 'UTC', 'imperial')

        area = self._create_space('Back yard', 360, 300, 'in')
        bed = self._lay_bed(area['pk'], name='Raised bed', size_x=96, size_y=48)
        self.assertEqual(bed.status_code, 201, bed.data)

        # Eight square feet by four, each a foot across.
        squares = self._mark_grid(bed.data['pk'], columns=8, rows=4, cell=12)
        self.assertEqual(squares.status_code, 201, squares.data)
        self.assertEqual(len(squares.data), 32)

        area_detail = self.client.get(f'/garden/areas/{area["pk"]}/')
        self.assertEqual(area_detail.data['length_unit'], 'in')

    def test_rows_and_containers_are_reachable_too(self):
        """The other templates and the pot type are part of the same walk."""
        self._describe_garden('Our garden', 'UTC', 'metric')
        area = self._create_space('Back garden', 1000, 800, 'cm')
        bed = self._lay_bed(area['pk'], name='Row bed', kind='in_ground')

        rows = self.client.post(
            '/garden/rows/',
            [
                {
                    'bed': bed.data['pk'],
                    'name': f'Row {index + 1}',
                    'placement_x': 0,
                    'placement_y': index * 30,
                    'size_x': 240,
                    'size_y': 15,
                }
                for index in range(4)
            ],
            format='json',
        )
        self.assertEqual(rows.status_code, 201, rows.data)
        self.assertEqual(GardenRow.objects.count(), 4)

        pots = self.client.post(
            '/locations/',
            {'name': 'Patio pots', 'code': 'PATIO-POTS', 'location_type': 'container'},
            format='json',
        )
        self.assertEqual(pots.status_code, 201, pots.data)


class GuidedSetupResumptionTests(RESTContractTestCase):
    """Leaving part-way through and coming back duplicates nothing."""

    def setUp(self):
        super().setUp()
        self.area = self.client.post(
            '/garden/areas/',
            {'name': 'Back garden', 'size_x': 1000, 'size_y': 800},
            format='json',
        ).data

    def test_confirming_the_scale_again_supersedes_rather_than_duplicates(self):
        """A gardener who reloads on this step does not get two answers."""
        for _attempt in range(2):
            response = self.client.post(
                f'/garden/areas/{self.area["pk"]}/confirm-geometry/',
                {'length_unit': 'cm', 'cell_length': '1'},
                format='json',
            )
            self.assertEqual(response.status_code, 201, response.data)

        detail = self.client.get(f'/garden/areas/{self.area["pk"]}/')
        self.assertEqual(detail.data['length_unit'], 'cm')

    def test_the_household_places_are_installed_once(self):
        """The step is a request the wizard can safely repeat."""
        first = self.client.post('/garden/setup/household-locations/', {}, format='json')
        second = self.client.post('/garden/setup/household-locations/', {}, format='json')
        self.assertEqual([row['pk'] for row in first.data], [row['pk'] for row in second.data])
        self.assertEqual(Location.objects.count(), len(first.data))

    def test_a_repeated_bed_is_refused_rather_than_doubled(self):
        """Sending the same bed twice collides with the one already there."""
        payload = {
            'area': self.area['pk'],
            'name': 'Bed 1',
            'kind': 'raised',
            'placement_x': 0,
            'placement_y': 0,
            'size_x': 240,
            'size_y': 120,
        }
        first = self.client.post('/garden/beds/', payload, format='json')
        self.assertEqual(first.status_code, 201, first.data)

        second = self.client.post('/garden/beds/', payload, format='json')
        self.assertEqual(second.status_code, 400, second.data)
        self.assertIn('Bed 1', str(second.data['non_field_errors'][0]))
        self.assertEqual(GardenBed.objects.count(), 1)

    def test_setup_can_be_skipped_and_reopened(self):
        """Declining is recorded, and adding another area later is normal."""
        self.client.patch('/settings/workspace/', {'garden_setup_state': 'skipped'}, format='json')
        self.assertEqual(get_current_workspace().garden_setup_state, 'skipped')

        second = self.client.post(
            '/garden/areas/',
            {'name': 'Front garden', 'size_x': 500, 'size_y': 400},
            format='json',
        )
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(GardenArea.objects.count(), 2)
        self.assertEqual(GardenSquare.objects.count(), 0)
