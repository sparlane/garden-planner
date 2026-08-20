"""Domain invariants for source-neutral household garden plantings."""

from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from tests.factories import (
    make_garden_planting,
    make_location,
    make_specific_plant,
)
from workspaces.models import Workspace

from .models import GardenPlanting


class GardenPlantingModelTests(TestCase):
    """The origin remains truthful before any API convenience is involved."""

    def test_one_square_or_catalog_location_is_required(self):
        """An origin never points at two physical places."""
        planting = make_garden_planting()
        planting.location = make_location(workspace=planting.workspace)

        with self.assertRaisesMessage(ValidationError, 'Select exactly one'):
            planting.save()

    def test_individual_quantity_cannot_be_approximate(self):
        """A count that creates identities must be exact."""
        planting = make_garden_planting()
        planting.tracking = GardenPlanting.Tracking.INDIVIDUAL
        planting.quantity_is_approximate = True

        with self.assertRaisesMessage(ValidationError, 'must be exact'):
            planting.save()

    def test_seed_packet_details_stay_together(self):
        """Packet attribution cannot omit how much stock it used."""
        planting = make_garden_planting()
        planting.seed_quantity_used = 2

        with self.assertRaisesMessage(ValidationError, 'seed packet and exact quantity'):
            planting.save()

    def test_finish_cannot_precede_the_recorded_date(self):
        """An aggregate cannot finish before it entered the garden."""
        planting = make_garden_planting(recorded_on=date(2026, 3, 2))
        planting.finished_on = date(2026, 3, 1)

        with self.assertRaisesMessage(ValidationError, 'on or after'):
            planting.save()

    def test_places_must_share_the_planting_workspace(self):
        """A garden origin cannot escape workspace isolation."""
        other = Workspace.objects.create(name='Other garden')
        planting = make_garden_planting()
        planting.garden_square = None
        planting.location = make_location(workspace=other)

        with self.assertRaisesMessage(ValidationError, 'different workspace'):
            planting.save()

    def test_specific_plant_accepts_one_garden_origin(self):
        """A quick-add origin supplies the plant's durable batch."""
        origin = make_garden_planting(tracking=GardenPlanting.Tracking.INDIVIDUAL)

        plant = make_specific_plant(
            workspace=origin.workspace,
            cell_planting=None,
            garden_planting=origin,
        )

        self.assertEqual(plant.batch, origin.batch)

    def test_specific_plant_rejects_two_origins(self):
        """Garden origins cannot be layered over nursery lineage."""
        origin = make_garden_planting(tracking=GardenPlanting.Tracking.INDIVIDUAL)
        plant = make_specific_plant(workspace=origin.workspace)
        plant.garden_planting = origin

        with self.assertRaisesMessage(ValidationError, 'exactly one'):
            plant.save()
