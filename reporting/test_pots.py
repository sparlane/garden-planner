"""Contracts for the pot number to variety report."""

# Test names state their contracts more clearly than repeated docstrings.
# pylint: disable=missing-function-docstring

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from plantings.models import SpecificPlantLocation
from tests.factories import (
    make_numbered_container,
    make_specific_plant,
    make_specific_plant_location,
)
from workspaces.models import get_current_workspace


class PotVarietyReportTests(APITestCase):
    """The list a nursery keeps beside pots that only show a number."""

    url = '/reports/pot-varieties/'

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = self.workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        self.user = get_user_model().objects.create_user(username='potter')
        self.client.force_authenticate(self.user)

    def put_plant_in_pot(self, pot):
        """Stand one new plant, with a batch and variety of its own, in the pot."""
        return make_specific_plant_location(
            specific_plant=make_specific_plant(),
            location_type=SpecificPlantLocation.CONTAINER_UNIT,
            seed_tray_cell=None,
            container_unit=pot,
        )

    def test_each_pot_lists_its_variety_in_natural_number_order(self):
        pot_ten = make_numbered_container(asset_code='10')
        pot_two = make_numbered_container(asset_code='2')
        first = self.put_plant_in_pot(pot_ten)
        second = self.put_plant_in_pot(pot_two)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data['results']
        self.assertEqual([row['asset_code'] for row in rows], ['2', '10'])
        self.assertEqual(
            rows[0]['variety_name'],
            second.specific_plant.batch.variety.name,
        )
        self.assertEqual(
            rows[1]['variety_name'],
            first.specific_plant.batch.variety.name,
        )
        self.assertEqual(response.data['totals']['pots'], 2)

    def test_plants_of_one_variety_in_a_pot_share_a_counted_row(self):
        pot = make_numbered_container(asset_code='7')
        first = self.put_plant_in_pot(pot)
        second = make_specific_plant_location(
            specific_plant=make_specific_plant(
                cell_planting=first.specific_plant.cell_planting,
            ),
            location_type=SpecificPlantLocation.CONTAINER_UNIT,
            seed_tray_cell=None,
            container_unit=pot,
        )
        self.assertEqual(
            first.specific_plant.batch_id, second.specific_plant.batch_id,
        )

        rows = self.client.get(self.url).data['results']

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['plant_count'], 2)
        self.assertEqual(rows[0]['asset_code'], '7')

    def test_a_plant_that_left_the_pot_is_no_longer_listed(self):
        pot = make_numbered_container(asset_code='3')
        placement = self.put_plant_in_pot(pot)
        placement.ended = timezone.now()
        placement.save()

        self.assertEqual(self.client.get(self.url).data['results'], [])

    def test_variety_filter_and_export(self):
        wanted = self.put_plant_in_pot(make_numbered_container(asset_code='5'))
        self.put_plant_in_pot(make_numbered_container(asset_code='6'))
        variety = wanted.specific_plant.batch.variety

        response = self.client.get(self.url, {'variety': variety.pk})
        export = self.client.get(f'{self.url}export/', {'variety': variety.pk})

        self.assertEqual(
            [row['asset_code'] for row in response.data['results']], ['5'],
        )
        self.assertEqual(export.status_code, 200)
        self.assertIn('asset_code', export.content.decode())

    def test_unknown_filters_are_refused(self):
        self.assertEqual(self.client.get(self.url, {'nope': 1}).status_code, 400)
