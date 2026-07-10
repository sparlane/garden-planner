"""
Tests for plantings
"""
import json
from datetime import datetime, timezone as datetime_timezone
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from garden.models import GardenArea, GardenBed, GardenSquare
from plants.models import Plant, PlantFamily, PlantVariety
from seeds.models import SeedPacket, Seeds
from seedtrays.models import SeedTray, SeedTrayCell, SeedTrayModel
from supplies.models import Supplier
from .models import SeedTrayCellPlanting, SeedTrayPlanting, SpecificPlant, SpecificPlantLocation


class SpecificPlantMoveTests(TestCase):
    """
    Tests for moving a specific plant between tracked locations.
    """

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username='tester'))
        variety = PlantVariety.objects.create(
            plant=Plant.objects.create(
                family=PlantFamily.objects.create(name='Nightshade'),
                name='Tomato',
            ),
            name='Roma',
        )
        packet = SeedPacket.objects.create(
            seeds=Seeds.objects.create(
                supplier=Supplier.objects.create(name='Test Supplier'),
                plant_variety=variety,
            ),
        )
        tray_model = SeedTrayModel.objects.create(
            identifier='test-tray',
            height=10,
            x_size=20,
            y_size=30,
            x_cells=2,
            y_cells=2,
            cell_size_ml=40,
        )
        tray = SeedTray.objects.create(model=tray_model)
        cell = SeedTrayCell.objects.create(tray=tray, x_position=0, y_position=0)
        self.other_cell = SeedTrayCell.objects.create(
            tray=SeedTray.objects.create(model=tray_model),
            x_position=1,
            y_position=1,
        )
        planting = SeedTrayPlanting.objects.create(
            seeds_used=packet,
            quantity=1,
            seed_tray=tray,
        )
        cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=planting,
            cell=cell,
            quantity=1,
        )
        area = GardenArea.objects.create(name='Back garden', size_x=10, size_y=10)
        bed = GardenBed.objects.create(
            area=area,
            name='Bed 1',
            placement_x=0,
            placement_y=0,
            size_x=10,
            size_y=10,
        )
        self.square = GardenSquare.objects.create(
            bed=bed,
            name='A1',
            placement_x=0,
            placement_y=0,
            size_x=1,
            size_y=1,
        )
        self.plant = SpecificPlant.objects.create(
            cell_planting=cell_planting,
            germinated=datetime(2026, 1, 1, 8, 0, tzinfo=datetime_timezone.utc),
        )
        self.active_location = SpecificPlantLocation.objects.create(
            specific_plant=self.plant,
            location_type=SpecificPlantLocation.SEED_TRAY_CELL,
            seed_tray_cell=cell,
            started=datetime(2026, 1, 1, 8, 0, tzinfo=datetime_timezone.utc),
        )

    def test_move_ends_current_location_and_creates_new_active_location(self):
        """
        Moving a plant is persisted as one ended location and one active location.
        """
        response = self.client.post(
            f'/plantings/specificplants/{self.plant.pk}/move/',
            data=json.dumps({
                'location_type': SpecificPlantLocation.GARDEN_SQUARE,
                'garden_square': self.square.pk,
                'started': '2026-01-02T09:30:00Z',
                'notes': 'Moved outside',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.active_location.refresh_from_db()
        self.assertEqual(
            self.active_location.ended,
            datetime(2026, 1, 2, 9, 30, tzinfo=datetime_timezone.utc),
        )
        active_location = SpecificPlantLocation.objects.get(
            specific_plant=self.plant,
            ended__isnull=True,
        )
        self.assertEqual(active_location.location_type, SpecificPlantLocation.GARDEN_SQUARE)
        self.assertEqual(active_location.garden_square, self.square)
        self.assertEqual(active_location.notes, 'Moved outside')

    def test_move_rolls_back_current_location_when_create_fails(self):
        """
        If the destination creation fails, the previous active location remains active.
        """
        self.client.raise_request_exception = False
        with mock.patch('plantings.rest.SpecificPlantLocation.objects.create', side_effect=RuntimeError('create failed')):
            response = self.client.post(
                f'/plantings/specificplants/{self.plant.pk}/move/',
                data=json.dumps({
                    'location_type': SpecificPlantLocation.SEED_TRAY_CELL,
                    'seed_tray_cell': self.other_cell.pk,
                    'started': '2026-01-02T09:30:00Z',
                }),
                content_type='application/json',
            )
        self.client.raise_request_exception = True

        self.assertEqual(response.status_code, 500)
        self.active_location.refresh_from_db()
        self.assertIsNone(self.active_location.ended)
        active_locations = SpecificPlantLocation.objects.filter(
            specific_plant=self.plant,
            ended__isnull=True,
        )
        self.assertEqual(active_locations.count(), 1)
        self.assertEqual(active_locations.get(), self.active_location)

    def test_move_rejects_invalid_destination_without_ending_current_location(self):
        """
        Mismatched location fields are rejected before changing the active location.
        """
        response = self.client.post(
            f'/plantings/specificplants/{self.plant.pk}/move/',
            data=json.dumps({
                'location_type': SpecificPlantLocation.GARDEN_SQUARE,
                'seed_tray_cell': self.other_cell.pk,
                'started': '2026-01-02T09:30:00Z',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.active_location.refresh_from_db()
        self.assertIsNone(self.active_location.ended)
        self.assertEqual(
            SpecificPlantLocation.objects.filter(
                specific_plant=self.plant,
                ended__isnull=True,
            ).count(),
            1,
        )
