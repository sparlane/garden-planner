"""
Tests for plantings data migrations
"""
from datetime import datetime, timezone as datetime_timezone
from importlib import import_module

from django.apps import apps as django_apps
from django.test import SimpleTestCase, TestCase

from plants.models import Plant, PlantFamily, PlantVariety
from seeds.models import SeedPacket, Seeds
from seedtrays.models import SeedTray, SeedTrayCell, SeedTrayModel
from supplies.models import Supplier
from .models import (
    SeedTrayCellPlanting,
    SeedTrayPlanting,
    SpecificPlant,
    SpecificPlantLocation,
)


class PlantingsDataMigrationTests(TestCase):
    """
    Tests for deployment-time planting integrity audits.
    """

    def setUp(self):
        variety = PlantVariety.objects.create(
            plant=Plant.objects.create(
                family=PlantFamily.objects.create(name='Apiaceae'),
                name='Carrot',
            ),
            name='Nantes',
        )
        packet = SeedPacket.objects.create(
            seeds=Seeds.objects.create(
                supplier=Supplier.objects.create(name='Audit Supplier'),
                plant_variety=variety,
            ),
        )
        self.tray_model = SeedTrayModel.objects.create(
            identifier='audit-tray',
            height=10,
            x_size=20,
            y_size=20,
            x_cells=2,
            y_cells=2,
            cell_size_ml=40,
        )
        self.tray = SeedTray.objects.create(model=self.tray_model)
        self.cell = SeedTrayCell.objects.create(
            tray=self.tray,
            x_position=0,
            y_position=0,
        )
        planting = SeedTrayPlanting.objects.create(
            seeds_used=packet,
            quantity=1,
            seed_tray=self.tray,
        )
        self.cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=planting,
            cell=self.cell,
            quantity=1,
        )
        self.plant = SpecificPlant.objects.create(cell_planting=self.cell_planting)
        migration = import_module(
            'plantings.migrations.0011_audit_seed_tray_integrity'
        )
        self.audit = migration.audit_seed_tray_integrity
        chronology_migration = import_module(
            'plantings.migrations.0012_specificplantlocation_chronology'
        )
        self.chronology_audit = chronology_migration.audit_location_chronology

    def test_audit_accepts_consistent_rows(self):
        """Valid parent membership and coordinates do not block deployment."""
        self.audit(django_apps, None)

    def test_audit_reports_cross_tray_cell_planting(self):
        """The failure identifies a cell planting whose parent tray differs."""
        other_tray = SeedTray.objects.create(model=self.tray_model)
        other_cell = SeedTrayCell.objects.create(
            tray=other_tray,
            x_position=0,
            y_position=0,
        )
        self.cell_planting.cell = other_cell
        self.cell_planting.save(update_fields=['cell'])

        with self.assertRaisesMessage(
            RuntimeError,
            f'cross-tray SeedTrayCellPlanting IDs: [{self.cell_planting.pk}]',
        ):
            self.audit(django_apps, None)

    def test_chronology_audit_accepts_adjacent_intervals(self):
        """Intervals that meet at a boundary pass the deployment audit."""
        boundary = datetime(2026, 1, 2, 8, 0, tzinfo=datetime_timezone.utc)
        SpecificPlantLocation.objects.create(
            specific_plant=self.plant,
            location_type=SpecificPlantLocation.SEED_TRAY_CELL,
            seed_tray_cell=self.cell,
            started=datetime(2026, 1, 1, 8, 0, tzinfo=datetime_timezone.utc),
            ended=boundary,
        )
        SpecificPlantLocation.objects.create(
            specific_plant=self.plant,
            location_type=SpecificPlantLocation.SEED_TRAY_CELL,
            seed_tray_cell=self.cell,
            started=boundary,
        )

        self.chronology_audit(django_apps, None)

    def test_chronology_audit_reports_overlapping_intervals(self):
        """The deployment failure identifies both rows in an overlap."""
        first_location = SpecificPlantLocation.objects.create(
            specific_plant=self.plant,
            location_type=SpecificPlantLocation.SEED_TRAY_CELL,
            seed_tray_cell=self.cell,
            started=datetime(2026, 1, 1, 8, 0, tzinfo=datetime_timezone.utc),
            ended=datetime(2026, 1, 3, 8, 0, tzinfo=datetime_timezone.utc),
        )
        second_location = SpecificPlantLocation.objects.create(
            specific_plant=self.plant,
            location_type=SpecificPlantLocation.SEED_TRAY_CELL,
            seed_tray_cell=self.cell,
            started=datetime(2026, 1, 2, 8, 0, tzinfo=datetime_timezone.utc),
            ended=datetime(2026, 1, 4, 8, 0, tzinfo=datetime_timezone.utc),
        )

        with self.assertRaisesMessage(
            RuntimeError,
            f'overlapping location ID pairs: [({first_location.pk}, {second_location.pk})]',
        ):
            self.chronology_audit(django_apps, None)

    def test_audit_reports_out_of_bounds_cell(self):
        """The failure identifies a cell outside its tray model's grid."""
        invalid_cell = SeedTrayCell.objects.create(
            tray=self.tray,
            x_position=self.tray_model.x_cells,
            y_position=0,
        )

        with self.assertRaisesMessage(
            RuntimeError,
            f'out-of-bounds SeedTrayCell IDs: [{invalid_cell.pk}]',
        ):
            self.audit(django_apps, None)


class LocationChronologyAuditHelperTests(SimpleTestCase):
    """Tests for detecting historical overlaps before adding the constraint."""

    def test_overlap_scan_allows_boundaries_and_zero_duration_intervals(self):
        """Only intervals with shared duration are reported as overlapping."""
        migration = import_module(
            'plantings.migrations.0012_specificplantlocation_chronology'
        )
        locations = [
            {'pk': 1, 'specific_plant_id': 1, 'started': 1, 'ended': 3},
            {'pk': 2, 'specific_plant_id': 1, 'started': 2, 'ended': 4},
            {'pk': 3, 'specific_plant_id': 1, 'started': 4, 'ended': None},
            {'pk': 4, 'specific_plant_id': 2, 'started': 1, 'ended': 1},
            {'pk': 5, 'specific_plant_id': 2, 'started': 1, 'ended': None},
        ]

        pairs, count = migration.find_location_overlaps(locations)

        self.assertEqual(pairs, [(1, 2)])
        self.assertEqual(count, 1)
