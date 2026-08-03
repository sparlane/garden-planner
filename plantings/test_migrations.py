"""
Tests for plantings data migrations
"""
# pylint: disable=duplicate-code
from datetime import datetime, timezone as datetime_timezone
from importlib import import_module
from unittest import mock

from django.apps import apps as django_apps
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from plants.models import Plant, PlantFamily, PlantVariety
from seeds.models import SeedPacket, Seeds
from seedtrays.models import SeedTrayCell, SeedTrayModel
from supplies.models import Supplier
from tests.factories import (
    make_batch_for_packet,
    make_garden_row_sowing,
    make_garden_square,
    make_garden_square_sowing,
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_cell_planting,
    make_seed_tray_planting,
    make_specific_plant,
)
from workspaces.models import Workspace

from .models import (
    GardenSquareTransplant,
    ProductionBatch,
    SeedTrayCellPlanting,
    SeedTrayPlanting,
    SpecificPlant,
    SpecificPlantLocation,
)


def latest_plantings_state():
    """Return the newest migration state for the plantings app.

    Backfill tests rewind the schema and must restore it completely, so the
    target is resolved from the migration graph instead of a pinned name that
    every later migration would silently invalidate.
    """
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return list(executor.loader.graph.leaf_nodes('plantings'))


class PlantingsDataMigrationTests(TestCase):  # pylint: disable=too-many-instance-attributes
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
        self.tray = make_seed_tray(model=self.tray_model)
        self.cell = SeedTrayCell.objects.create(
            tray=self.tray,
            x_position=0,
            y_position=0,
        )
        planting = SeedTrayPlanting.objects.create(
            seeds_used=packet,
            batch=make_batch_for_packet(packet),
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
        transplant_migration = import_module(
            'plantings.migrations.0016_audit_transplant_ownership'
        )
        self.transplant_audit = transplant_migration.audit_transplant_ownership

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
        """Seed allocation totals within the parent sowing allow deployment."""
        migration = import_module(
            'plantings.migrations.0015_audit_seed_allocation_capacity'
        )

        migration.audit_seed_allocation_capacity(django_apps, None)

    def test_capacity_audit_accepts_multigerm_rows(self):
        """Seedling counts may exceed a cell's sown seed quantity."""
        migration = import_module(
            'plantings.migrations.0015_audit_seed_allocation_capacity'
        )
        SpecificPlant.objects.bulk_create([
            SpecificPlant(cell_planting=self.cell_planting),
            SpecificPlant(cell_planting=self.cell_planting),
        ])

        migration.audit_seed_allocation_capacity(django_apps, None)

    def test_transplant_audit_accepts_separate_representations(self):
        """Aggregate and individual transplants may belong to different plantings."""
        square = make_garden_square()
        legacy_packet = self.cell_planting.seed_tray_planting.seeds_used
        legacy_planting = SeedTrayPlanting.objects.create(
            seeds_used=legacy_packet,
            batch=make_batch_for_packet(legacy_packet),
            quantity=1,
        )
        GardenSquareTransplant.objects.create(
            original_planting=legacy_planting,
            quantity=1,
            location=square,
        )
        SpecificPlantLocation.objects.create(
            specific_plant=self.plant,
            location_type=SpecificPlantLocation.GARDEN_SQUARE,
            garden_square=square,
        )

        self.transplant_audit(django_apps, None)

    def test_transplant_audit_reports_conflicting_aggregate_rows(self):
        """Deployment failures identify aggregate rows with individual overlap."""
        square = make_garden_square()
        transplant = GardenSquareTransplant.objects.create(
            original_planting=self.cell_planting.seed_tray_planting,
            quantity=1,
            location=square,
        )
        SpecificPlantLocation.objects.create(
            specific_plant=self.plant,
            location_type=SpecificPlantLocation.GARDEN_SQUARE,
            garden_square=square,
        )

        with self.assertRaisesMessage(
            RuntimeError,
            f'Conflicting GardenSquareTransplant IDs: [{transplant.pk}]',
        ):
            self.transplant_audit(django_apps, None)

    def test_capacity_audit_reports_parent_row_ids(self):
        """Deployment failures identify over-allocated parent sowings."""
        migration = import_module(
            'plantings.migrations.0015_audit_seed_allocation_capacity'
        )
        SeedTrayCellPlanting.objects.filter(pk=self.cell_planting.pk).update(
            quantity=2,
        )
        with self.assertRaisesMessage(
            RuntimeError,
            'over-allocated SeedTrayPlanting IDs: '
            f'[{self.cell_planting.seed_tray_planting_id}]',
        ):
            migration.audit_seed_allocation_capacity(django_apps, None)

    def test_audit_reports_cross_tray_cell_planting(self):
        """The failure identifies a cell planting whose parent tray differs."""
        other_tray = make_seed_tray(model=self.tray_model)
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


class ProductionBatchBackfillTests(TransactionTestCase):
    """The legacy backfill gives every historical sowing one stable batch."""

    UNLINKED_STATE = [('plantings', '0019_productionbatch')]

    def _post_teardown(self):
        """Restore migration seed data removed by transactional test flushing."""
        super()._post_teardown()
        if not Workspace.objects.filter(pk=settings.CURRENT_WORKSPACE_ID).exists():
            Workspace.objects.create(
                pk=settings.CURRENT_WORKSPACE_ID,
                name='My Garden',
            )

    def setUp(self):
        super().setUp()
        self.addCleanup(self._migrate, latest_plantings_state())

    @staticmethod
    def _migrate(targets):
        """Move the test database to one explicit migration state."""
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)

    @staticmethod
    def _unlink_batches():
        """Return the database to its pre-batch shape without dropping sowings."""
        with connection.cursor() as cursor:
            for table in (
                'plantings_gardenrowdirectsowplanting',
                'plantings_gardensquaredirectsowplanting',
                'plantings_seedtrayplanting',
            ):
                cursor.execute(f'UPDATE {table} SET batch_id = NULL')
            cursor.execute('DELETE FROM plantings_productionbatchtransition')
            cursor.execute('DELETE FROM plantings_productionbatch')

    def _run_backfill(self):
        """Strip the batch links, then replay the backfill over them."""
        self._migrate(self.UNLINKED_STATE)
        self._unlink_batches()
        self._migrate(latest_plantings_state())

    def test_every_historical_sowing_gets_one_stable_legacy_batch(self):
        """Each sowing keeps its own deterministic code, dates, and identity."""
        row_sowing = make_garden_row_sowing()
        square_sowing = make_garden_square_sowing()
        tray_sowing = make_seed_tray_planting()
        cell_planting = make_seed_tray_cell_planting(
            seed_tray_planting=tray_sowing,
        )
        plants = [
            make_specific_plant(cell_planting=cell_planting),
            make_specific_plant(cell_planting=cell_planting),
        ]

        self._run_backfill()

        for sowing, code_prefix in (
            (row_sowing, 'LEGACY-ROW'),
            (square_sowing, 'LEGACY-SQUARE'),
            (tray_sowing, 'LEGACY-TRAY'),
        ):
            with self.subTest(code_prefix=code_prefix):
                sowing.refresh_from_db()
                batch = sowing.batch
                self.assertEqual(batch.code, f'{code_prefix}-{sowing.pk}')
                self.assertEqual(batch.status, ProductionBatch.Status.ACTIVE)
                self.assertEqual(batch.actual_start, sowing.planted)
                self.assertEqual(batch.workspace_id, sowing.workspace_id)
                self.assertEqual(
                    batch.variety_id,
                    sowing.seeds_used.seeds.plant_variety_id,
                )
                self.assertIsNone(batch.created_by)
                self.assertEqual(batch.repair_state, ProductionBatch.RepairState.NONE)
                transitions = list(batch.transitions.all())
                self.assertEqual(len(transitions), 1)
                self.assertEqual(transitions[0].previous_status, '')
                self.assertEqual(
                    transitions[0].new_status,
                    ProductionBatch.Status.ACTIVE,
                )

        self.assertEqual(ProductionBatch.objects.count(), 3)
        self.assertEqual(
            SpecificPlant.objects.filter(cell_planting=cell_planting).count(),
            len(plants),
        )

    def test_multigerm_and_recovered_plants_share_their_sowing_batch(self):
        """Individual plants inherit one batch through their cell planting."""
        tray_sowing = make_seed_tray_planting(quantity=2)
        cell_planting = make_seed_tray_cell_planting(
            seed_tray_planting=tray_sowing,
            quantity=2,
        )
        recovered = [
            make_specific_plant(
                cell_planting=cell_planting,
                notes='Recovered from legacy GardenSquareTransplant #1.',
            )
            for _index in range(5)
        ]
        square = make_garden_square()
        legacy_transplant = GardenSquareTransplant.objects.create(
            original_planting=tray_sowing,
            quantity=5,
            location=square,
        )

        self._run_backfill()

        tray_sowing.refresh_from_db()
        batches = {
            plant.cell_planting.seed_tray_planting.batch_id
            for plant in SpecificPlant.objects.filter(pk__in=[p.pk for p in recovered])
        }
        self.assertEqual(batches, {tray_sowing.batch_id})
        legacy_transplant.refresh_from_db()
        self.assertEqual(
            legacy_transplant.original_planting.batch_id,
            tray_sowing.batch_id,
        )
        self.assertEqual(ProductionBatch.objects.count(), 1)
        self.assertEqual(legacy_transplant.quantity, 5)

    def test_anomalous_cell_membership_is_flagged_for_repair(self):
        """A stray cell allocation is reported instead of silently guessed at."""
        tray_sowing = make_seed_tray_planting()
        stray_cell = make_seed_tray_cell()
        SeedTrayCellPlanting.objects.create(
            seed_tray_planting=tray_sowing,
            cell=stray_cell,
            quantity=1,
        )

        self._run_backfill()

        tray_sowing.refresh_from_db()
        batch = tray_sowing.batch
        self.assertEqual(batch.repair_state, ProductionBatch.RepairState.NEEDS_REPAIR)
        self.assertIn(str(stray_cell.pk), batch.repair_details)
        self.assertIn(f'seed tray #{tray_sowing.seed_tray_id}', batch.repair_details)
