"""
Tests for plantings data migrations
"""
from datetime import datetime, timezone as datetime_timezone
from importlib import import_module
from unittest import mock

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

    def test_quantity_audit_accepts_positive_rows(self):
        """Existing positive quantities do not block the database constraints."""
        migration = import_module(
            'plantings.migrations.0014_constrain_positive_quantities'
        )
        migration.audit_positive_quantities(django_apps, None)

    def test_quantity_audit_reports_model_and_row_ids(self):
        """Deployment failures identify the kind and IDs of corrupt rows."""
        migration = import_module(
            'plantings.migrations.0014_constrain_positive_quantities'
        )
        invalid_rows = mock.MagicMock()
        invalid_rows.count.return_value = 1
        values = invalid_rows.order_by.return_value.values_list.return_value
        values.__getitem__.return_value = [7]
        invalid_model = mock.MagicMock()
        invalid_model.objects.filter.return_value = invalid_rows

        valid_rows = mock.MagicMock()
        valid_rows.count.return_value = 0
        valid_model = mock.MagicMock()
        valid_model.objects.filter.return_value = valid_rows

        historical_models = dict.fromkeys(migration.QUANTITY_MODELS, valid_model)
        historical_models['GardenRowDirectSowPlanting'] = invalid_model
        historical_apps = mock.MagicMock()
        historical_apps.get_model.side_effect = (
            lambda _app, model_name: historical_models[model_name]
        )

        with self.assertRaisesMessage(
            RuntimeError,
            'GardenRowDirectSowPlanting IDs: [7]',
        ):
            migration.audit_positive_quantities(historical_apps, None)

        invalid_rows.count.assert_called_once_with()

    def test_quantity_audit_describe_rows_truncates_with_total(self):
        """Long audit reports show the first 20 IDs and the complete count."""
        migration = import_module(
            'plantings.migrations.0014_constrain_positive_quantities'
        )
        invalid_rows = mock.MagicMock()
        first_ids = list(range(1, 21))
        values = invalid_rows.order_by.return_value.values_list.return_value
        values.__getitem__.return_value = first_ids

        description = migration.describe_rows(invalid_rows, count=25)

        self.assertEqual(description, f'{first_ids} (first 20 of 25)')
        values.__getitem__.assert_called_once_with(slice(None, 20))

    def test_capacity_audit_accepts_consistent_rows(self):
        """Allocation and germination counts within capacity allow deployment."""
        migration = import_module(
            'plantings.migrations.0015_audit_seed_allocation_capacity'
        )

        migration.audit_seed_allocation_capacity(django_apps, None)

    def test_capacity_audit_reports_parent_and_cell_row_ids(self):
        """Deployment failures identify both kinds of capacity violation."""
        migration = import_module(
            'plantings.migrations.0015_audit_seed_allocation_capacity'
        )
        SeedTrayCellPlanting.objects.filter(pk=self.cell_planting.pk).update(
            quantity=2,
        )
        SpecificPlant.objects.bulk_create([
            SpecificPlant(cell_planting=self.cell_planting),
            SpecificPlant(cell_planting=self.cell_planting),
        ])

        with self.assertRaisesMessage(
            RuntimeError,
            'over-allocated SeedTrayPlanting IDs: '
            f'[{self.cell_planting.seed_tray_planting_id}]; '
            'over-germinated SeedTrayCellPlanting IDs: '
            f'[{self.cell_planting.pk}]',
        ):
            migration.audit_seed_allocation_capacity(django_apps, None)

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
