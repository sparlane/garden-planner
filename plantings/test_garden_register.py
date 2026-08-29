"""Contract tests for the household garden plant register."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from tests.factories import (
    make_garden_planting,
    make_garden_row_sowing,
    make_garden_square,
    make_plant_lifecycle_event,
    make_plant_variety,
    make_production_batch,
    make_specific_plant,
    make_specific_plant_location,
)
from workspaces.models import Workspace, get_current_workspace

from .models import GardenPlanting, PlantLifecycleEvent, SpecificPlantLocation


class GardenRegisterTests(APITestCase):
    """Aggregate crops and individual plants form one non-duplicating list."""

    url = '/plantings/garden-register/'

    def setUp(self):
        """Use an authenticated Garden workspace for every request."""
        super().setUp()
        self.user = get_user_model().objects.create_user(username='garden-register')
        self.client.force_authenticate(self.user)
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.save(update_fields=['mode'])

    def page(self, **params):
        """Return one successful register page."""
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def make_individual_origin(self, quantity=2, **overrides):
        """Create a quick-add origin and its lifecycle-backed plants."""
        origin = make_garden_planting(
            workspace=self.workspace,
            tracking=GardenPlanting.Tracking.INDIVIDUAL,
            quantity=quantity,
            **overrides,
        )
        plants = []
        for index in range(quantity):
            plant = make_specific_plant(
                workspace=self.workspace, cell_planting=None,
                garden_planting=origin, name=f'Plant {index + 1}',
            )
            PlantLifecycleEvent.objects.create(
                workspace=self.workspace, plant=plant, batch=origin.batch,
                event_type=PlantLifecycleEvent.EventType.GERMINATED,
                occurred_at=plant.germinated,
            )
            plants.append(plant)
        return origin, plants

    def test_mixed_tracking_counts_origins_once(self):
        """An individual origin is replaced by its plants, not added to them."""
        make_garden_planting(workspace=self.workspace, quantity=6)
        self.make_individual_origin(quantity=2, perennial=True)

        payload = self.page()

        self.assertEqual(payload['count'], 3)
        self.assertEqual(payload['totals']['rows'], 3)
        self.assertEqual(payload['totals']['quantity'], 8)
        self.assertEqual(payload['totals']['aggregate_rows'], 1)
        self.assertEqual(payload['totals']['individual_plants'], 2)
        self.assertEqual(payload['totals']['perennials'], 2)

    def test_tray_raised_plant_is_included_as_indoor_raised(self):
        """A plant need not have originated in quick-add to appear."""
        plant = make_specific_plant(workspace=self.workspace)
        make_plant_lifecycle_event(workspace=self.workspace, plant=plant)
        make_specific_plant_location(
            specific_plant=plant,
            location_type=SpecificPlantLocation.GARDEN_SQUARE,
            seed_tray_cell=None,
            garden_square=make_garden_square(workspace=self.workspace),
        )

        row = self.page()['results'][0]

        self.assertEqual(row['record_type'], 'individual')
        self.assertEqual(row['source'], GardenPlanting.Source.INDOOR_RAISED_SEED)
        self.assertTrue(row['location'].startswith('square:'))

    def test_legacy_direct_sowing_remains_visible(self):
        """Existing garden data does not disappear behind the new origin model."""
        sowing = make_garden_row_sowing(workspace=self.workspace, quantity=5)

        payload = self.page(source=GardenPlanting.Source.DIRECT_SEED)

        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['results'][0]['key'], f'direct-row-{sowing.pk}')
        self.assertEqual(payload['totals']['quantity'], 5)

    def test_finished_rows_are_historical_by_default(self):
        """Finished crops remain searchable without cluttering today's view."""
        current = make_garden_planting(workspace=self.workspace)
        finished = make_garden_planting(
            workspace=self.workspace,
            recorded_on=date(2025, 1, 1), finished_on=date(2025, 2, 1),
        )

        self.assertEqual([row['record_id'] for row in self.page()['results']], [current.pk])
        history = self.page(state='finished')
        self.assertEqual([row['record_id'] for row in history['results']], [finished.pk])

    def test_filters_and_totals_use_the_same_rows(self):
        """Summary quantities describe the filtered result, not all rows."""
        matching = make_garden_planting(
            workspace=self.workspace,
            source=GardenPlanting.Source.DIRECT_SEED,
            recorded_on=date(2026, 8, 1), quantity=4,
        )
        make_garden_planting(
            workspace=self.workspace,
            source=GardenPlanting.Source.PURCHASED_PLANT,
            recorded_on=date(2026, 1, 1), quantity=9,
        )

        payload = self.page(
            source=GardenPlanting.Source.DIRECT_SEED,
            planted_from='2026-07-01', search=matching.batch.variety.plant.name,
        )

        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['totals']['rows'], len(payload['results']))
        self.assertEqual(payload['totals']['quantity'], 4)

    def test_final_individual_is_hidden_from_current(self):
        """Final plant lifecycle outcomes move individuals into history."""
        _origin, (plant,) = self.make_individual_origin(quantity=1)
        PlantLifecycleEvent.objects.create(
            workspace=self.workspace, plant=plant, batch=plant.batch,
            event_type=PlantLifecycleEvent.EventType.FAILED,
            occurred_at=timezone.now() + timedelta(minutes=1),
        )

        self.assertEqual(self.page()['count'], 0)
        self.assertEqual(self.page(state='finished')['count'], 1)

    def test_profile_and_workspace_boundaries_are_server_side(self):
        """Neither another tenant nor Nursery mode can read this register."""
        other = Workspace.objects.create(name='Other garden', mode=Workspace.Mode.GARDEN)
        variety = make_plant_variety(workspace=other)
        make_garden_planting(
            workspace=other,
            batch=make_production_batch(workspace=other, variety=variety),
        )
        self.assertEqual(self.page()['count'], 0)

        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_authentication_is_required(self):
        """Household growing records are private."""
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_aggregate_finish_failure_and_correction_are_audited(self):
        """Aggregate quick actions append facts and can reverse mistakes."""
        planting = make_garden_planting(workspace=self.workspace)
        key = f'aggregate-{planting.pk}'

        failed = self.client.post(
            f'{self.url}{key}/finish/',
            {'event_type': 'failed', 'occurred_on': '2026-08-20', 'reason': 'Frost'},
            format='json',
        )
        self.assertEqual(failed.status_code, 201, failed.data)
        self.assertEqual(self.page()['count'], 0)
        self.assertEqual(self.page(state='failed')['results'][0]['key'], key)

        correction = self.client.post(
            f'{self.url}{key}/correct-status/',
            {'event': failed.data['pk'], 'occurred_on': '2026-08-21', 'reason': 'Wrong bed'},
            format='json',
        )
        self.assertEqual(correction.status_code, 201, correction.data)
        self.assertEqual(self.page()['results'][0]['key'], key)

        detail = self.client.get(f'{self.url}{key}/')
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual([event['type'] for event in detail.data['history']], ['failed', 'corrected'])
        self.assertEqual(detail.data['links']['batch'], f'/plantings/batches/{planting.batch_id}')
