"""Standing an individual plant at a location in the shared catalog.

A plant in a tray is described by its cell; a plant that has been potted on and
set down on a bench is somewhere in its own right, and these cover that third
kind of place.
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from locations.models import Location
from tests.factories import (
    make_garden_square,
    make_location,
    make_numbered_container,
    make_specific_plant,
    make_specific_plant_location,
)
from workspaces.models import Workspace

from .models import PlantLifecycleEvent, SpecificPlantLocation


class PlantPlacementTestCase(TestCase):
    """One plant in a tray, and the bench it can be moved onto."""

    def setUp(self):
        super().setUp()
        self.client.force_login(
            get_user_model().objects.create_user(username='placement-tester'),
        )
        self.plant = make_specific_plant()
        self.active_location = make_specific_plant_location(specific_plant=self.plant)
        self.bench = make_location(
            name='Bench A',
            location_type=Location.LocationType.BENCH,
        )

    def move(self, **payload):
        """Post one move for the fixture plant."""
        return self.client.post(
            f'/plantings/specificplants/{self.plant.pk}/move/',
            data=json.dumps(payload),
            content_type='application/json',
        )

    def active(self):
        """Return the plant's one open location."""
        return SpecificPlantLocation.objects.get(
            specific_plant=self.plant,
            ended__isnull=True,
        )


class PlantPlacementTests(PlantPlacementTestCase):
    """A plant can be moved onto a bench without being planted out."""

    def test_a_plant_can_stand_at_a_location(self):
        """A potted plant on a bench is somewhere in its own right."""
        response = self.move(
            location_type=SpecificPlantLocation.LOCATION,
            location=self.bench.pk,
        )

        self.assertEqual(response.status_code, 201, response.json())
        active = self.active()
        self.assertEqual(active.location_type, SpecificPlantLocation.LOCATION)
        self.assertEqual(active.location, self.bench)
        self.assertIsNone(active.seed_tray_cell)
        self.assertIsNone(active.garden_square)

    def test_the_previous_location_closes_when_the_plant_moves_to_a_bench(self):
        """History stays continuous: leaving the tray is part of the same move."""
        self.move(
            location_type=SpecificPlantLocation.LOCATION,
            location=self.bench.pk,
        )

        self.active_location.refresh_from_db()
        self.assertIsNotNone(self.active_location.ended)
        self.assertEqual(self.plant.locations.count(), 2)

    def test_standing_at_a_location_is_not_planting_out(self):
        """Moving to a bench is nursery work, not the end of propagation."""
        self.move(
            location_type=SpecificPlantLocation.LOCATION,
            location=self.bench.pk,
        )

        self.assertFalse(
            PlantLifecycleEvent.objects.filter(
                plant=self.plant,
                event_type=PlantLifecycleEvent.EventType.TRANSPLANTED,
            ).exists(),
        )

    def test_moving_into_a_garden_square_still_plants_the_plant_out(self):
        """The third kind of place must not have loosened the second."""
        self.move(
            location_type=SpecificPlantLocation.GARDEN_SQUARE,
            garden_square=make_garden_square().pk,
        )

        self.assertTrue(
            PlantLifecycleEvent.objects.filter(
                plant=self.plant,
                event_type=PlantLifecycleEvent.EventType.TRANSPLANTED,
            ).exists(),
        )

    def test_a_move_names_exactly_one_kind_of_place(self):
        """Two places at once would make the active location ambiguous."""
        response = self.move(
            location_type=SpecificPlantLocation.LOCATION,
            location=self.bench.pk,
            garden_square=make_garden_square().pk,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('garden_square', response.json())

    def test_standing_at_a_location_requires_naming_one(self):
        """A location move with no location says nothing about where the plant is."""
        response = self.move(location_type=SpecificPlantLocation.LOCATION)

        self.assertEqual(response.status_code, 400)
        self.assertIn('location', response.json())

    def test_a_location_in_another_workspace_is_not_selectable(self):
        """A plant cannot be stood on premises the workspace does not have."""
        outsider = make_location(
            workspace=Workspace.objects.create(name='Another nursery'),
        )
        response = self.move(
            location_type=SpecificPlantLocation.LOCATION,
            location=outsider.pk,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('location', response.json())


class PottedPlacementNamingTests(PlantPlacementTestCase):
    """A plant in a numbered pot is named by the code printed on the pot.

    Every other kind of place is drawn on a screen and found by the row it
    came from, so the primary key is enough to name it. A pot is found in the
    nursery by reading it, which is why the placement carries its asset code.
    """

    def setUp(self):
        super().setUp()
        self.pot = make_numbered_container(location=self.bench)

    def test_a_plant_can_be_moved_into_a_numbered_pot(self):
        """The fourth kind of place is reachable through the same move."""
        response = self.move(
            location_type=SpecificPlantLocation.CONTAINER_UNIT,
            container_unit=self.pot.pk,
        )

        self.assertEqual(response.status_code, 201, response.json())
        active = self.active()
        self.assertEqual(active.location_type, SpecificPlantLocation.CONTAINER_UNIT)
        self.assertEqual(active.container_unit, self.pot)
        self.assertIsNone(active.seed_tray_cell)

    def test_the_placement_carries_the_code_printed_on_the_pot(self):
        """A bare container id names nothing an operator can go and find."""
        response = self.move(
            location_type=SpecificPlantLocation.CONTAINER_UNIT,
            container_unit=self.pot.pk,
        )

        body = response.json()
        self.assertEqual(body['container_unit'], self.pot.pk)
        self.assertEqual(body['container_unit_code'], self.pot.asset_code)

    def test_a_placement_that_is_not_a_pot_carries_no_code(self):
        """The field is null rather than absent, so one shape reads them all."""
        response = self.move(
            location_type=SpecificPlantLocation.LOCATION,
            location=self.bench.pk,
        )

        self.assertIsNone(response.json()['container_unit_code'])
