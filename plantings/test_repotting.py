"""Emptying a tray into a bench of numbered pots as one repotting run."""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError as RESTValidationError

from inventory.units import UnitCode
from inventory.models import InventoryItem
from seedtrays.container_fills import open_numbered_fill
from tests.factories import (
    make_inventory_item,
    make_location,
    make_numbered_container,
    make_specific_plant,
    make_specific_plant_location,
)
from workspaces.models import get_current_workspace

from .models import SpecificPlantLocation
from .movement import move_specific_plant
from .repotting import MAX_REPOTTED_PLANTS, repot_into_pots


class RepottingRunTestCase(TestCase):
    """Three seedlings in a tray, and a bench of numbered pots for them."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.user = get_user_model().objects.create_user(username='repotter')
        self.client.force_login(self.user)
        self.bench = make_location(name='Potting bench')
        self.item = make_inventory_item(
            category=InventoryItem.Category.POT_CONTAINER,
            base_unit=UnitCode.EACH,
            tracking_mode=InventoryItem.TrackingMode.MIXED,
        )
        self.plants = [make_specific_plant() for _ in range(3)]
        self.cells = [make_specific_plant_location(specific_plant=plant) for plant in self.plants]
        self.pots = [make_numbered_container(item=self.item, location=self.bench) for _ in range(3)]

    def pairings(self, plants=None, pots=None):
        """Pair the fixture's seedlings with the fixture's pots, in order."""
        return [
            (plant.pk, pot.pk)
            for plant, pot in zip(plants or self.plants, pots or self.pots)
        ]

    def repot(self, pairings=None, **kwargs):
        """Run one repotting through the service the API calls."""
        return repot_into_pots(
            self.workspace, self.user,
            self.pairings() if pairings is None else pairings, **kwargs,
        )

    def standing(self, plant):
        """Return the plant's one open placement."""
        return SpecificPlantLocation.objects.get(specific_plant=plant, ended__isnull=True)

    def post(self, payload):
        """Post one repotting run the way the tray screen does."""
        return self.client.post(
            '/plantings/specificplants/bulk-repot/',
            data=json.dumps(payload),
            content_type='application/json',
        )


class RepottingRunTests(RepottingRunTestCase):
    """One request empties the selected cells into the pots they were paired with."""

    def test_each_seedling_stands_in_the_pot_it_was_paired_with(self):
        """The pairing table is the answer to where every plant went."""
        rows = self.repot()
        self.assertEqual(len(rows), 3)
        for plant, pot in zip(self.plants, self.pots):
            placement = self.standing(plant)
            self.assertEqual(placement.location_type, SpecificPlantLocation.CONTAINER_UNIT)
            self.assertEqual(placement.container_unit_id, pot.pk)
        for cell in self.cells:
            cell.refresh_from_db()
            self.assertIsNotNone(cell.ended)

    def test_the_run_shares_one_arrival_time_with_the_departures_it_ends(self):
        """A tray emptied in one pass is not recorded as three separate afternoons."""
        rows = self.repot(notes='Potted on into P9s')
        self.assertEqual({row.started for row in rows}, {rows[0].started})
        self.assertEqual({row.notes for row in rows}, {'Potted on into P9s'})
        for cell in self.cells:
            cell.refresh_from_db()
            self.assertEqual(cell.ended, rows[0].started)

    def test_a_potted_seedling_joins_the_fill_already_open_on_its_pot(self):
        """Media applied to the bench of pots still belongs to the plant that used it."""
        fill = open_numbered_fill(self.workspace, self.user, self.pots[0])
        rows = self.repot(self.pairings(plants=self.plants[:1], pots=self.pots[:1]))
        self.assertEqual(rows[0].container_fill_id, fill.pk)

    def test_one_refused_pairing_leaves_the_whole_tray_where_it_was(self):
        """A half-finished run would leave the operator reading the grid to find out."""
        move_specific_plant(
            self.plants[2],
            {'location_type': SpecificPlantLocation.LOCATION, 'location': self.bench,
             'started': timezone.now() + timedelta(hours=1)},
            self.user,
        )
        with self.assertRaises(RESTValidationError):
            self.repot()
        for plant, cell in zip(self.plants[:2], self.cells[:2]):
            self.assertEqual(self.standing(plant).pk, cell.pk)

    def test_a_pot_named_twice_is_a_number_typed_twice(self):
        """Several plants may share a pot; a paired run is not how that is said."""
        with self.assertRaises(ValidationError) as refusal:
            self.repot(self.pairings(pots=[self.pots[0], self.pots[0], self.pots[1]]))
        self.assertIn('Pair each pot once', str(refusal.exception))
        self.assertEqual(self.standing(self.plants[0]).pk, self.cells[0].pk)

    def test_a_plant_named_twice_is_refused(self):
        """One seedling cannot be stood in two pots at once."""
        with self.assertRaises(ValidationError) as refusal:
            self.repot(self.pairings(plants=[self.plants[0], self.plants[0], self.plants[1]]))
        self.assertIn('Pair each plant once', str(refusal.exception))

    def test_an_empty_run_and_an_oversized_one_are_both_refused(self):
        """The bound is real work under row locks, not a formality."""
        with self.assertRaises(ValidationError):
            self.repot([])
        with self.assertRaises(ValidationError) as refusal:
            self.repot([(plant, plant) for plant in range(1, MAX_REPOTTED_PLANTS + 2)])
        self.assertIn(str(MAX_REPOTTED_PLANTS), str(refusal.exception))

    def test_plants_and_pots_outside_the_workspace_are_named_in_the_refusal(self):
        """A selection that names nothing real says which side of it was wrong."""
        with self.assertRaises(ValidationError) as missing_plant:
            self.repot([(self.plants[0].pk + 10000, self.pots[0].pk)])
        self.assertIn('No such plants', str(missing_plant.exception))
        with self.assertRaises(ValidationError) as missing_pot:
            self.repot([(self.plants[0].pk, self.pots[0].pk + 10000)])
        self.assertIn('No such containers', str(missing_pot.exception))


class RepottingRunRestTests(RepottingRunTestCase):
    """The tray screen posts one run and reads back where every plant stands."""

    def test_the_run_is_posted_as_a_table_of_pairings(self):
        """Each row carries its own plant and the number written on its pot."""
        response = self.post({
            'placements': [
                {'plant': plant.pk, 'container_unit': pot.pk}
                for plant, pot in zip(self.plants, self.pots)
            ],
            'notes': 'Potted on',
        })
        self.assertEqual(response.status_code, 201)
        rows = response.json()
        self.assertEqual(
            {(row['specific_plant'], row['container_unit']) for row in rows},
            {(plant.pk, pot.pk) for plant, pot in zip(self.plants, self.pots)},
        )

    def test_a_repeated_pot_comes_back_as_a_field_error(self):
        """The refusal reaches the form that built the pairing table."""
        response = self.post({
            'placements': [
                {'plant': self.plants[0].pk, 'container_unit': self.pots[0].pk},
                {'plant': self.plants[1].pk, 'container_unit': self.pots[0].pk},
            ],
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('placements', response.json())
        self.assertEqual(self.standing(self.plants[0]).pk, self.cells[0].pk)

    def test_an_empty_table_is_refused_before_any_lock_is_taken(self):
        """A run with nothing in it is a mistake, not a no-op."""
        self.assertEqual(self.post({'placements': []}).status_code, 400)
