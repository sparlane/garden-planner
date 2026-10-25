"""Contracts for the pot number to variety report."""

# Test names state their contracts more clearly than repeated docstrings.
# pylint: disable=missing-function-docstring

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from plantings.models import SpecificPlantLocation
from tests.factories import (
    make_numbered_container,
    make_seed_tray_cell_planting,
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

    def sowing(self):
        """One sowing; every plant raised from it shares a batch and variety."""
        return make_seed_tray_cell_planting()

    def put_plant_in_pot(self, pot, sowing=None):
        """Stand one plant from the sowing (or a new one) in the numbered pot."""
        sowing = sowing or self.sowing()
        return make_specific_plant_location(
            specific_plant=make_specific_plant(cell_planting=sowing),
            location_type=SpecificPlantLocation.CONTAINER_UNIT,
            seed_tray_cell=None,
            container_unit=pot,
        )

    def fill(self, layout):
        """Number pots as laid out: {number: sowing, number: None for sold}."""
        placements = {}
        for number, sowing in layout.items():
            pot = make_numbered_container(asset_code=str(number))
            placement = self.put_plant_in_pot(pot, sowing)
            if sowing is None:
                placement.ended = timezone.now()
                placement.save()
            placements[number] = placement
        return placements

    def rows(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200, response.data)
        return [
            (row['pots'], row['variety_name']) for row in response.data['results']
        ]

    def name(self, sowing):
        return sowing.seed_tray_planting.batch.variety.name

    def example(self):
        tomato_a, tomato_b = self.sowing(), self.sowing()
        self.fill({
            1: tomato_a, 2: tomato_a, 3: None, 4: tomato_a,
            5: tomato_b, 6: tomato_a, 7: tomato_a,
        })
        return tomato_a, tomato_b

    def test_sold_gaps_continue_a_range_but_another_variety_splits_it(self):
        tomato_a, tomato_b = self.example()

        self.assertEqual(self.rows(), [
            ('1-4', self.name(tomato_a)),
            ('5', self.name(tomato_b)),
            ('6-7', self.name(tomato_a)),
        ])

    def test_pots_sort_in_natural_number_order(self):
        sowing = self.sowing()
        other = self.sowing()
        self.fill({10: sowing, 2: other})

        self.assertEqual(
            [pots for pots, _ in self.rows()], ['2', '10'],
        )

    def test_counts_cover_pots_and_plants_in_a_range(self):
        sowing = self.sowing()
        placements = self.fill({1: sowing, 2: sowing, 4: sowing})
        self.put_plant_in_pot(placements[2].container_unit, sowing)

        row = self.client.get(self.url).data['results'][0]

        self.assertEqual(row['pots'], '1-4')
        self.assertEqual((row['pot_count'], row['plant_count']), (3, 4))

    def test_number_range_clips_runs_without_merging_across_neighbours(self):
        tomato_a, tomato_b = self.example()

        name_a, name_b = self.name(tomato_a), self.name(tomato_b)
        self.assertEqual(
            self.rows(pot_from=5, pot_to=6),
            [('5', name_b), ('6', name_a)],
        )
        self.assertEqual(self.rows(pot_from=2, pot_to=4), [('2-4', name_a)])
        self.assertEqual(self.rows(pot_from=6), [('6-7', name_a)])

    def test_variety_filter_does_not_merge_across_other_varieties(self):
        tomato_a, _ = self.example()
        variety = tomato_a.seed_tray_planting.batch.variety

        self.assertEqual(
            self.rows(variety=variety.pk),
            [('1-4', variety.name), ('6-7', variety.name)],
        )

    def test_range_filter_must_not_run_backwards(self):
        response = self.client.get(self.url, {'pot_from': 5, 'pot_to': 2})

        self.assertEqual(response.status_code, 400)

    def test_a_plant_that_left_the_pot_is_no_longer_listed(self):
        self.fill({3: None})

        self.assertEqual(self.rows(), [])

    def test_a_pot_that_left_with_its_plant_is_also_a_gap(self):
        tomato_a, tomato_b = self.sowing(), self.sowing()
        placements = self.fill({1: tomato_a, 2: tomato_a, 3: tomato_a, 4: tomato_b})
        pot = placements[3].container_unit
        pot.active = False
        pot.current_location = None
        pot.save()

        self.assertEqual(self.rows(), [
            ('1-2', self.name(tomato_a)), ('4', self.name(tomato_b)),
        ])

    def test_export_and_unknown_filters(self):
        self.example()

        export = self.client.get(f'{self.url}export/')

        self.assertEqual(export.status_code, 200)
        self.assertIn('1-4', export.content.decode())
        self.assertEqual(self.client.get(self.url, {'nope': 1}).status_code, 400)
