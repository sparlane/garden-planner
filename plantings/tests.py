"""
Tests for plantings
"""
import json
from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.test import TestCase

from garden.models import GardenArea, GardenBed, GardenSquare
from plants.models import Plant, PlantFamily, PlantVariety
from seeds.models import SeedPacket, Seeds
from seedtrays.models import SeedTray, SeedTrayCell, SeedTrayModel
from supplies.models import Supplier
from .models import (
    GardenSquareDirectSowPlanting,
    GardenSquareTransplant,
    SeedTrayCellPlanting,
    SeedTrayPlanting,
    SpecificPlant,
    SpecificPlantLocation,
)


class SeedTrayPlantingMembershipTests(TestCase):
    """
    Tests for keeping a seed tray planting's cells on its parent tray.
    """

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username='planting-tester'))
        variety = PlantVariety.objects.create(
            plant=Plant.objects.create(
                family=PlantFamily.objects.create(name='Brassicaceae'),
                name='Broccoli',
            ),
            name='Calabrese',
        )
        self.packet = SeedPacket.objects.create(
            seeds=Seeds.objects.create(
                supplier=Supplier.objects.create(name='Membership Supplier'),
                plant_variety=variety,
            ),
        )
        tray_model = SeedTrayModel.objects.create(
            identifier='membership-tray',
            height=10,
            x_size=20,
            y_size=30,
            x_cells=2,
            y_cells=2,
            cell_size_ml=40,
        )
        self.first_tray = SeedTray.objects.create(model=tray_model)
        self.first_cell = SeedTrayCell.objects.create(
            tray=self.first_tray,
            x_position=0,
            y_position=0,
        )
        self.second_tray = SeedTray.objects.create(model=tray_model)
        self.second_cell = SeedTrayCell.objects.create(
            tray=self.second_tray,
            x_position=0,
            y_position=0,
        )
        self.planting = SeedTrayPlanting.objects.create(
            seeds_used=self.packet,
            quantity=1,
            seed_tray=self.first_tray,
        )
        self.cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=self.planting,
            cell=self.first_cell,
            quantity=1,
        )

    def _patch_planting(self, data):
        return self.client.patch(
            f'/plantings/seedtray/{self.planting.pk}/',
            data=json.dumps(data),
            content_type='application/json',
        )

    def test_changing_tray_without_replacing_cells_is_rejected(self):
        """
        Retained child rows prevent a partial update from changing their parent tray.
        """
        response = self._patch_planting({'seed_tray': self.second_tray.pk})

        self.assertEqual(response.status_code, 400)
        self.planting.refresh_from_db()
        self.cell_planting.refresh_from_db()
        self.assertEqual(self.planting.seed_tray, self.first_tray)
        self.assertEqual(self.cell_planting.cell, self.first_cell)

    def test_changing_tray_with_valid_replacement_cells_succeeds(self):
        """
        A complete replacement can move a planting when every new cell is valid.
        """
        response = self._patch_planting({
            'seed_tray': self.second_tray.pk,
            'cell_plantings': [{'cell': self.second_cell.pk, 'quantity': 1}],
        })

        self.assertEqual(response.status_code, 200)
        self.planting.refresh_from_db()
        self.assertEqual(self.planting.seed_tray, self.second_tray)
        replacement = self.planting.cell_plantings.get()
        self.assertEqual(replacement.cell, self.second_cell)

    def test_invalid_replacement_leaves_planting_unchanged(self):
        """
        Membership validation happens before either the parent or children are saved.
        """
        response = self._patch_planting({
            'seed_tray': self.second_tray.pk,
            'cell_plantings': [{'cell': self.first_cell.pk, 'quantity': 1}],
        })

        self.assertEqual(response.status_code, 400)
        self.planting.refresh_from_db()
        self.cell_planting.refresh_from_db()
        self.assertEqual(self.planting.seed_tray, self.first_tray)
        self.assertEqual(self.cell_planting.cell, self.first_cell)

    def test_replacing_only_cells_with_another_trays_cell_is_rejected(self):
        """
        A replacement without seed_tray is checked against the retained parent tray.
        """
        response = self._patch_planting({
            'cell_plantings': [{'cell': self.second_cell.pk, 'quantity': 1}],
        })

        self.assertEqual(response.status_code, 400)
        self.planting.refresh_from_db()
        self.cell_planting.refresh_from_db()
        self.assertEqual(self.planting.seed_tray, self.first_tray)
        self.assertEqual(self.cell_planting.cell, self.first_cell)

    def test_partial_replacement_requires_a_cell(self):
        """Malformed replacement entries return 400 instead of raising a KeyError."""
        response = self._patch_planting({
            'cell_plantings': [{'quantity': 1}],
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {'cell_plantings': [{'cell': ['This field is required.']}]},
        )
        self.assertEqual(self.planting.cell_plantings.get(), self.cell_planting)

    def test_partial_replacement_requires_a_quantity(self):
        """Every replacement entry is complete even when its parent request is partial."""
        response = self._patch_planting({
            'cell_plantings': [{'cell': self.first_cell.pk}],
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {'cell_plantings': [{'quantity': ['This field is required.']}]},
        )
        self.assertEqual(self.planting.cell_plantings.get(), self.cell_planting)

    def test_explicitly_clearing_cells_allows_changing_tray(self):
        """
        An empty replacement list removes the old tray membership intentionally.
        """
        response = self._patch_planting({
            'seed_tray': self.second_tray.pk,
            'cell_plantings': [],
        })

        self.assertEqual(response.status_code, 200)
        self.planting.refresh_from_db()
        self.assertEqual(self.planting.seed_tray, self.second_tray)
        self.assertFalse(self.planting.cell_plantings.exists())


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

    def _drop_active_location_index(self):
        with connection.cursor() as cursor:
            cursor.execute('DROP INDEX IF EXISTS unique_active_location_per_plant')
        self.addCleanup(self._restore_active_location_index)

    def _restore_active_location_index(self):
        SpecificPlantLocation.objects.filter(specific_plant=self.plant, seed_tray_cell=self.other_cell).delete()
        with connection.cursor() as cursor:
            cursor.execute(
                'CREATE UNIQUE INDEX IF NOT EXISTS "unique_active_location_per_plant" '
                'ON "plantings_specificplantlocation" ("specific_plant_id") '
                'WHERE "ended" IS NULL'
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

    def test_move_without_started_defaults_to_current_time(self):
        """
        Moves without an explicit start time use the backend current time.
        """
        move_time = datetime(2026, 1, 2, 9, 30, tzinfo=datetime_timezone.utc)
        with mock.patch('plantings.rest.timezone.now', return_value=move_time):
            response = self.client.post(
                f'/plantings/specificplants/{self.plant.pk}/move/',
                data=json.dumps({
                    'location_type': SpecificPlantLocation.GARDEN_SQUARE,
                    'garden_square': self.square.pk,
                }),
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 201)
        self.active_location.refresh_from_db()
        self.assertEqual(self.active_location.ended, move_time)
        active_location = SpecificPlantLocation.objects.get(
            specific_plant=self.plant,
            ended__isnull=True,
        )
        self.assertEqual(active_location.started, move_time)

    def test_notes_only_patch_does_not_end_location(self):
        """Editing location metadata does not silently change lifecycle state."""
        response = self.client.patch(
            f'/plantings/specificplantlocations/{self.active_location.pk}/',
            data=json.dumps({'notes': 'Checked after rain'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.active_location.refresh_from_db()
        self.assertEqual(self.active_location.notes, 'Checked after rain')
        self.assertIsNone(self.active_location.ended)

    def test_empty_patch_preserves_existing_end_time(self):
        """A no-op edit cannot replace a location's historical end timestamp."""
        original_end = datetime(2026, 1, 1, 12, 0, tzinfo=datetime_timezone.utc)
        self.active_location.ended = original_end
        self.active_location.save(update_fields=['ended'])

        response = self.client.patch(
            f'/plantings/specificplantlocations/{self.active_location.pk}/',
            data=json.dumps({}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.active_location.refresh_from_db()
        self.assertEqual(self.active_location.ended, original_end)

    def test_end_action_is_idempotent(self):
        """Repeated explicit closes retain the timestamp from the first request."""
        first_end = datetime(2026, 1, 1, 12, 0, tzinfo=datetime_timezone.utc)
        later_end = first_end + timedelta(hours=1)

        with mock.patch('plantings.rest.timezone.now', return_value=first_end):
            first_response = self.client.post(
                f'/plantings/specificplantlocations/{self.active_location.pk}/end/',
                data=json.dumps({}),
                content_type='application/json',
            )
        with mock.patch('plantings.rest.timezone.now', return_value=later_end):
            second_response = self.client.post(
                f'/plantings/specificplantlocations/{self.active_location.pk}/end/',
                data=json.dumps({}),
                content_type='application/json',
            )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.active_location.refresh_from_db()
        self.assertEqual(self.active_location.ended, first_end)

    def test_move_rolls_back_current_location_when_create_violates_invariant(self):
        """
        If the destination creation violates the active-location invariant, the previous location remains active.
        """
        error = IntegrityError('UNIQUE constraint failed: plantings_specificplantlocation.specific_plant_id')
        with mock.patch('plantings.rest.SpecificPlantLocation.objects.create', side_effect=error):
            response = self.client.post(
                f'/plantings/specificplants/{self.plant.pk}/move/',
                data=json.dumps({
                    'location_type': SpecificPlantLocation.SEED_TRAY_CELL,
                    'seed_tray_cell': self.other_cell.pk,
                    'started': '2026-01-02T09:30:00Z',
                }),
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 400)
        self.active_location.refresh_from_db()
        self.assertIsNone(self.active_location.ended)
        active_locations = SpecificPlantLocation.objects.filter(
            specific_plant=self.plant,
            ended__isnull=True,
        )
        self.assertEqual(active_locations.count(), 1)
        self.assertEqual(active_locations.get(), self.active_location)

    def test_move_without_active_location_creates_new_active_location(self):
        """
        A plant with no active location can still be moved to a new active location.
        """
        ended = datetime(2026, 1, 1, 12, 0, tzinfo=datetime_timezone.utc)
        self.active_location.ended = ended
        self.active_location.save(update_fields=['ended'])

        response = self.client.post(
            f'/plantings/specificplants/{self.plant.pk}/move/',
            data=json.dumps({
                'location_type': SpecificPlantLocation.GARDEN_SQUARE,
                'garden_square': self.square.pk,
                'started': '2026-01-02T09:30:00Z',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.active_location.refresh_from_db()
        self.assertEqual(self.active_location.ended, ended)
        active_location = SpecificPlantLocation.objects.get(
            specific_plant=self.plant,
            ended__isnull=True,
        )
        self.assertEqual(active_location.garden_square, self.square)
        self.assertEqual(
            SpecificPlantLocation.objects.filter(specific_plant=self.plant).count(),
            2,
        )

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

    def test_move_rejects_multiple_active_locations_without_ending_any_location(self):
        """
        A move is rejected when existing data already has multiple active locations.
        """
        self._drop_active_location_index()
        second_active_location = SpecificPlantLocation.objects.create(
            specific_plant=self.plant,
            location_type=SpecificPlantLocation.SEED_TRAY_CELL,
            seed_tray_cell=self.other_cell,
            started=self.active_location.started + timedelta(hours=1),
        )

        response = self.client.post(
            f'/plantings/specificplants/{self.plant.pk}/move/',
            data=json.dumps({
                'location_type': SpecificPlantLocation.GARDEN_SQUARE,
                'garden_square': self.square.pk,
                'started': '2026-01-02T09:30:00Z',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {'specific_plant': 'Plant has multiple active locations.'},
        )
        self.active_location.refresh_from_db()
        second_active_location.refresh_from_db()
        self.assertIsNone(self.active_location.ended)
        self.assertIsNone(second_active_location.ended)
        self.assertEqual(
            SpecificPlantLocation.objects.filter(
                specific_plant=self.plant,
                ended__isnull=True,
            ).count(),
            2,
        )


class GardenSquareCurrentMissingMetadataTests(TestCase):
    """
    Tests for garden square current summaries with missing date metadata.
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
        self.packet = SeedPacket.objects.create(
            seeds=Seeds.objects.create(
                supplier=Supplier.objects.create(name='Test Supplier'),
                plant_variety=variety,
            ),
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
        self.tray_model = SeedTrayModel.objects.create(
            identifier='missing-metadata-tray',
            height=10,
            x_size=20,
            y_size=30,
            x_cells=2,
            y_cells=2,
            cell_size_ml=40,
        )

    def _create_seed_tray_cell_planting(self):
        tray = SeedTray.objects.create(model=self.tray_model)
        cell = SeedTrayCell.objects.create(tray=tray, x_position=0, y_position=0)
        planting = SeedTrayPlanting.objects.create(
            seeds_used=self.packet,
            quantity=1,
            seed_tray=tray,
        )
        cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=planting,
            cell=cell,
            quantity=1,
        )
        return planting, cell_planting

    def _assert_missing_metadata_dates(self, planting):
        self.assertIsNone(planting['germination_date_early'])
        self.assertIsNone(planting['germination_date_late'])
        self.assertIsNone(planting['maturity_date_early'])
        self.assertIsNone(planting['maturity_date_late'])

    def test_direct_sow_with_missing_metadata_returns_null_computed_dates(self):
        """
        Direct sow rows tolerate missing germination and maturity offsets.
        """
        GardenSquareDirectSowPlanting.objects.create(
            seeds_used=self.packet,
            quantity=1,
            location=self.square,
        )

        response = self.client.get('/plantings/garden/squares/current/')

        self.assertEqual(response.status_code, 200)
        planting = response.json()['plantings'][0]
        self._assert_missing_metadata_dates(planting)

    def test_transplant_with_missing_metadata_returns_null_computed_dates(self):
        """
        Aggregate transplant rows tolerate missing germination and maturity offsets.
        """
        planting, _ = self._create_seed_tray_cell_planting()
        GardenSquareTransplant.objects.create(
            original_planting=planting,
            quantity=1,
            location=self.square,
        )

        response = self.client.get('/plantings/garden/squares/current/')

        self.assertEqual(response.status_code, 200)
        planting = response.json()['plantings'][0]
        self._assert_missing_metadata_dates(planting)

    def test_specific_plant_location_with_missing_metadata_returns_null_computed_dates(self):
        """
        Specific plant location rows tolerate missing germination and maturity offsets.
        """
        _, cell_planting = self._create_seed_tray_cell_planting()
        plant = SpecificPlant.objects.create(cell_planting=cell_planting)
        SpecificPlantLocation.objects.create(
            specific_plant=plant,
            location_type=SpecificPlantLocation.GARDEN_SQUARE,
            garden_square=self.square,
        )

        response = self.client.get('/plantings/garden/squares/current/')

        self.assertEqual(response.status_code, 200)
        planting = response.json()['plantings'][0]
        self._assert_missing_metadata_dates(planting)
