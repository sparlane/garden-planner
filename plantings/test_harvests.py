"""Tests for the harvest record and its attribution to individual plants."""
# pylint: disable=duplicate-code
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from inventory.units import UnitCode
from tests.factories import (
    make_garden_row,
    make_garden_square,
    make_harvest,
    make_harvest_plant,
    make_plant,
    make_plant_family,
    make_plant_variety,
    make_production_batch,
    make_specific_plant,
)
from workspaces.models import Workspace

from .models import Harvest, HarvestPlant


class HarvestModelTests(TestCase):
    """A harvest is posted when recorded and never edited afterwards."""

    def setUp(self):
        super().setUp()
        self.batch = make_production_batch()

    def test_a_new_harvest_is_posted_and_stamped(self):
        """Recording a harvest counts it immediately, with no draft step."""
        harvest = make_harvest(batch=self.batch)
        self.assertEqual(harvest.status, Harvest.Status.POSTED)
        self.assertIsNotNone(harvest.posted_at)
        self.assertIsNone(harvest.reversed_at)
        self.assertEqual(harvest.reverse_reason, '')

    def test_saving_an_existing_harvest_is_refused(self):
        """The record is immutable, so a correction must reverse it instead."""
        harvest = make_harvest(batch=self.batch)
        harvest.notes = 'Edited'
        with self.assertRaises(ValidationError) as caught:
            harvest.save()
        self.assertIn('immutable', str(caught.exception))

    def test_deleting_a_harvest_is_refused(self):
        """Yield history survives a mistake rather than disappearing."""
        harvest = make_harvest(batch=self.batch)
        with self.assertRaises(ValidationError):
            harvest.delete()
        self.assertTrue(Harvest.objects.filter(pk=harvest.pk).exists())

    def test_a_zero_quantity_is_rejected(self):
        """A harvest that measured nothing is not an observation."""
        with self.assertRaises(ValidationError) as caught:
            make_harvest(batch=self.batch, quantity=Decimal('0'))
        self.assertIn('quantity', caught.exception.message_dict)

    def test_a_negative_quantity_is_rejected(self):
        """Yield is never negative; a mistake is reversed, not subtracted."""
        with self.assertRaises(ValidationError) as caught:
            make_harvest(batch=self.batch, quantity=Decimal('-1'))
        self.assertIn('quantity', caught.exception.message_dict)

    def test_a_sub_quantum_quantity_is_rejected_as_a_field_error(self):
        """A value the column rounds to zero fails cleanly, not as a crash."""
        with self.assertRaises(ValidationError) as caught:
            make_harvest(batch=self.batch, quantity=Decimal('0.0000000004'))
        self.assertIn('quantity', caught.exception.message_dict)

    def test_input_and_area_units_are_rejected(self):
        """Seed and area units describe inputs and space, never a yield."""
        for unit in (UnitCode.SEED, UnitCode.SEED_CLUSTER, UnitCode.SQUARE_METRE):
            with self.subTest(unit=unit):
                with self.assertRaises(ValidationError) as caught:
                    make_harvest(batch=self.batch, unit_code=unit)
                self.assertIn('unit_code', caught.exception.message_dict)

    def test_every_yield_unit_is_accepted(self):
        """Count, mass, and volume all describe a real crop measurement."""
        for unit in (UnitCode.EACH, UnitCode.GRAM, UnitCode.KILOGRAM,
                     UnitCode.MILLILITRE, UnitCode.LITRE):
            with self.subTest(unit=unit):
                harvest = make_harvest(batch=self.batch, unit_code=unit)
                self.assertEqual(harvest.unit_code, unit)

    def test_a_square_and_a_row_cannot_both_be_recorded(self):
        """One harvest came from one place, so only one location may be named."""
        with self.assertRaises(ValidationError) as caught:
            make_harvest(
                batch=self.batch,
                garden_square=make_garden_square(),
                garden_row=make_garden_row(),
            )
        self.assertIn('garden_row', caught.exception.message_dict)

    def test_the_database_also_refuses_two_locations(self):
        """The single-location rule survives a write that skips validation."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Harvest.objects.bulk_create([Harvest(
                    workspace=self.batch.workspace,
                    batch=self.batch,
                    harvested_at=timezone.now(),
                    quantity=Decimal('1'),
                    unit_code=UnitCode.GRAM,
                    garden_square=make_garden_square(),
                    garden_row=make_garden_row(),
                )])

    def test_the_database_also_refuses_a_zero_quantity(self):
        """The positive-quantity rule survives a write that skips validation."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Harvest.objects.bulk_create([Harvest(
                    workspace=self.batch.workspace,
                    batch=self.batch,
                    harvested_at=timezone.now(),
                    quantity=Decimal('0'),
                    unit_code=UnitCode.GRAM,
                )])

    def test_a_location_is_optional(self):
        """An aggregate batch yield need not name where it came from."""
        harvest = make_harvest(batch=self.batch)
        self.assertIsNone(harvest.garden_square)
        self.assertIsNone(harvest.garden_row)

    def test_a_quality_rating_outside_one_to_five_is_rejected(self):
        """The subjective score is a fixed scale, not an arbitrary number."""
        for rating in (0, 6):
            with self.subTest(rating=rating):
                with self.assertRaises(ValidationError) as caught:
                    make_harvest(batch=self.batch, quality_rating=rating)
                self.assertIn('quality_rating', caught.exception.message_dict)


class HarvestWorkspaceTests(TestCase):
    """Every reference a harvest names belongs to its own workspace."""

    def setUp(self):
        super().setUp()
        self.other = Workspace.objects.create(name='Other workspace')

    def test_a_batch_from_another_workspace_is_rejected(self):
        """A harvest cannot claim yield from somebody else's crop."""
        family = make_plant_family(workspace=self.other)
        plant = make_plant(workspace=self.other, family=family)
        variety = make_plant_variety(workspace=self.other, plant=plant)
        foreign = make_production_batch(workspace=self.other, variety=variety)
        with self.assertRaises(ValidationError) as caught:
            make_harvest(batch=foreign)
        self.assertIn('batch', caught.exception.message_dict)

    def test_a_square_from_another_workspace_is_rejected(self):
        """The growing location is scoped like every other reference."""
        with self.assertRaises(ValidationError) as caught:
            make_harvest(garden_square=make_garden_square(workspace=self.other))
        self.assertIn('garden_square', caught.exception.message_dict)

    def test_a_row_from_another_workspace_is_rejected(self):
        """A row is scoped exactly as a square is."""
        with self.assertRaises(ValidationError) as caught:
            make_harvest(garden_row=make_garden_row(workspace=self.other))
        self.assertIn('garden_row', caught.exception.message_dict)


class HarvestPlantTests(TestCase):
    """An allocation attributes a harvest to plants that batch actually raised."""

    def setUp(self):
        super().setUp()
        self.plant = make_specific_plant()
        self.batch = self.plant.cell_planting.seed_tray_planting.batch

    def test_a_plant_from_the_same_batch_is_accepted(self):
        """Attribution records which of the batch's plants contributed."""
        allocation = make_harvest_plant(plant=self.plant)
        self.assertEqual(allocation.plant, self.plant)
        self.assertEqual(allocation.harvest.batch, self.batch)

    def test_a_plant_from_another_batch_is_rejected(self):
        """A harvest cannot be attributed to a crop it did not come from."""
        harvest = make_harvest(batch=make_production_batch())
        with self.assertRaises(ValidationError) as caught:
            HarvestPlant.objects.create(harvest=harvest, plant=self.plant)
        self.assertIn('plant', caught.exception.message_dict)

    def test_a_plant_from_another_workspace_is_rejected(self):
        """Attribution is scoped like every other cross-model reference."""
        other = Workspace.objects.create(name='Other workspace')
        stranger = make_specific_plant(workspace=other)
        harvest = make_harvest(
            batch=stranger.cell_planting.seed_tray_planting.batch,
        )
        with self.assertRaises(ValidationError) as caught:
            HarvestPlant.objects.create(harvest=harvest, plant=stranger)
        self.assertIn('plant', caught.exception.message_dict)

    def test_the_same_plant_cannot_be_allocated_twice(self):
        """One harvest names each contributing plant once."""
        allocation = make_harvest_plant(plant=self.plant)
        with self.assertRaises(ValidationError):
            HarvestPlant.objects.create(
                harvest=allocation.harvest,
                plant=self.plant,
            )

    def test_an_allocation_is_immutable_and_undeletable(self):
        """Attribution is part of the record the reversal keeps visible."""
        allocation = make_harvest_plant(plant=self.plant)
        with self.assertRaises(ValidationError):
            allocation.save()
        with self.assertRaises(ValidationError):
            allocation.delete()
        self.assertTrue(HarvestPlant.objects.filter(pk=allocation.pk).exists())
