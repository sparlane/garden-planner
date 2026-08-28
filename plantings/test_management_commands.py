"""Tests for planting data-recovery management commands."""

from datetime import datetime, timezone as datetime_timezone
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import TestCase

from tests.factories import (
    make_garden_square,
    make_seed_tray_cell,
    make_seed_tray_cell_planting,
    make_seed_tray_planting,
    make_specific_plant,
)
from .models import (
    GardenSquareTransplant,
    SpecificPlant,
    SpecificPlantLocation,
)


class ConvertLegacyTransplantTests(TestCase):  # pylint: disable=too-many-instance-attributes
    """Legacy aggregate conversion is explicit, inspectable, and atomic."""

    def setUp(self):
        self.planted_at = datetime(
            2026, 1, 1, 8, 0, tzinfo=datetime_timezone.utc,
        )
        self.germinated_at = datetime(
            2026, 1, 5, 8, 0, tzinfo=datetime_timezone.utc,
        )
        self.transplanted_at = datetime(
            2026, 2, 1, 8, 0, tzinfo=datetime_timezone.utc,
        )
        self.cell = make_seed_tray_cell()
        self.planting = make_seed_tray_planting(
            seed_tray=self.cell.tray,
            planted=self.planted_at,
            quantity=2,
        )
        self.cell_planting = make_seed_tray_cell_planting(
            seed_tray_planting=self.planting,
            cell=self.cell,
            quantity=2,
        )
        self.output = StringIO()
        self.square = make_garden_square()
        self.transplant = GardenSquareTransplant.objects.create(
            original_planting=self.planting,
            transplanted=self.transplanted_at,
            quantity=5,
            location=self.square,
            notes='Legacy transplant notes',
        )

    def run_command(self, *args, **options):
        """Run the conversion command with its output captured.

        The text is kept on `self.output` as well as returned, so a test whose
        invocation raises can still read what the operator would have seen.
        Nothing calls the command any other way, so no invocation can dump a
        transplant listing into the test run.
        """
        self.output = StringIO()
        call_command(
            'convert_legacy_transplant', *args, stdout=self.output, **options,
        )
        return self.output.getvalue()

    def _command_options(self, **overrides):
        options = {
            'cell_planting': self.cell_planting.pk,
            'germinated_at': self.germinated_at.isoformat(),
        }
        options.update(overrides)
        return options

    def _make_existing_plant(self):
        plant = make_specific_plant(cell_planting=self.cell_planting)
        SpecificPlantLocation.objects.create(
            specific_plant=plant,
            location_type=SpecificPlantLocation.GARDEN_SQUARE,
            garden_square=self.square,
        )
        return plant

    def test_inspection_lists_labels_and_source_allocations(self):
        """An incomplete invocation gives the operator actionable context."""
        with self.assertRaisesMessage(CommandError, 'Choose --cell-planting'):
            self.run_command(self.transplant.pk)

        details = self.output.getvalue()
        variety = self.planting.seeds_used.seeds.plant_variety
        self.assertIn(f'Plant: {variety.plant.name}', details)
        self.assertIn(f'Variety: {variety.name}', details)
        self.assertIn(self.square.bed.area.name, details)
        self.assertIn(self.square.bed.name, details)
        self.assertIn(self.square.name, details)
        self.assertIn(f'#{self.cell_planting.pk}: tray', details)

    def test_default_mode_previews_without_writing(self):
        """Supplying a valid mapping remains non-mutating without --apply."""
        details = self.run_command(self.transplant.pk, **self._command_options())

        self.assertIn('New plants to create: 5', details)
        self.assertIn('Dry run only', details)
        self.assertTrue(
            GardenSquareTransplant.objects.filter(pk=self.transplant.pk).exists()
        )
        self.assertFalse(SpecificPlant.objects.exists())

    def test_apply_uses_existing_plants_to_reach_total_target(self):
        """Only the missing individuals are created before deleting the aggregate."""
        existing_plants = [self._make_existing_plant() for _index in range(2)]
        existing_location_ids = set(
            SpecificPlantLocation.objects.values_list('pk', flat=True)
        )

        details = self.run_command(
            self.transplant.pk,
            apply_changes=True,
            existing_plant=[plant.pk for plant in existing_plants],
            **self._command_options(),
        )
        self.assertIn('Converted and deleted', details)

        self.assertFalse(
            GardenSquareTransplant.objects.filter(pk=self.transplant.pk).exists()
        )
        self.assertEqual(SpecificPlant.objects.count(), 5)
        self.assertEqual(
            SpecificPlantLocation.objects.exclude(
                pk__in=existing_location_ids,
            ).count(),
            6,
        )
        recovered_plants = SpecificPlant.objects.exclude(
            pk__in=[plant.pk for plant in existing_plants],
        )
        for plant in recovered_plants:
            self.assertEqual(plant.germinated, self.germinated_at)
            locations = list(plant.locations.order_by('started'))
            self.assertEqual(len(locations), 2)
            self.assertEqual(locations[0].seed_tray_cell, self.cell)
            self.assertEqual(locations[0].ended, self.transplanted_at)
            self.assertEqual(locations[1].garden_square, self.square)
            self.assertIsNone(locations[1].ended)
            self.assertEqual(locations[1].notes, self.transplant.notes)

    def test_invalid_source_allocation_leaves_everything_unchanged(self):
        """A source from another sowing is rejected before any conversion."""
        other_cell_planting = make_seed_tray_cell_planting()

        with self.assertRaisesMessage(CommandError, 'does not belong'):
            self.run_command(
                self.transplant.pk,
                apply_changes=True,
                **self._command_options(
                    cell_planting=other_cell_planting.pk,
                ),
            )

        self.assertTrue(
            GardenSquareTransplant.objects.filter(pk=self.transplant.pk).exists()
        )
        self.assertFalse(SpecificPlant.objects.exists())

    def test_existing_plant_requires_matching_square_history(self):
        """Explicit overlap IDs cannot silently refer to another destination."""
        plant = make_specific_plant(cell_planting=self.cell_planting)

        with self.assertRaisesMessage(CommandError, 'has no garden history'):
            self.run_command(
                self.transplant.pk,
                existing_plant=[plant.pk],
                **self._command_options(),
            )

    def test_removed_transplant_is_rejected(self):
        """The command does not invent an end time for completed history."""
        self.transplant.removed = True
        self.transplant.save(update_fields=['removed'])

        with self.assertRaisesMessage(CommandError, 'no truthful'):
            self.run_command(self.transplant.pk, **self._command_options())

    def test_apply_rolls_back_when_aggregate_delete_fails(self):
        """No partial individual history survives a failed final deletion."""
        with mock.patch.object(
            GardenSquareTransplant,
            'delete',
            side_effect=IntegrityError('simulated delete failure'),
        ):
            with self.assertRaises(IntegrityError):
                self.run_command(
                    self.transplant.pk,
                    apply_changes=True,
                    **self._command_options(),
                )

        self.assertTrue(
            GardenSquareTransplant.objects.filter(pk=self.transplant.pk).exists()
        )
        self.assertFalse(SpecificPlant.objects.exists())
        self.assertFalse(SpecificPlantLocation.objects.exists())
