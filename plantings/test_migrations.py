"""
Tests for plantings data migrations
"""
from importlib import import_module

from django.apps import apps as django_apps
from django.test import TestCase

from plants.models import Plant, PlantFamily, PlantVariety
from seeds.models import SeedPacket, Seeds
from seedtrays.models import SeedTray, SeedTrayCell, SeedTrayModel
from supplies.models import Supplier
from .models import SeedTrayCellPlanting, SeedTrayPlanting


class SeedTrayIntegrityAuditTests(TestCase):
    """
    Tests for the deployment-time seed-tray integrity audit.
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
        migration = import_module(
            'plantings.migrations.0011_audit_seed_tray_integrity'
        )
        self.audit = migration.audit_seed_tray_integrity

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
