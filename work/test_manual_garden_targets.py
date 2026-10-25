"""Manual garden reminders link authoritative plants and crops."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from locations.models import Location
from plantings.models import GardenPlanting
from tests.factories import (
    make_garden_planting, make_garden_row_sowing, make_garden_square_sowing,
    make_plant_lifecycle_event, make_plant_variety, make_production_batch, make_specific_plant,
)
from workspaces.models import Workspace, get_current_workspace

from .models import WorkTask


class ManualGardenTargetTests(APITestCase):
    """Selections remain scoped and survive recurring task actions."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.save()
        self.client.force_authenticate(get_user_model().objects.create_user(username='gardener'))
        now = timezone.now()
        self.payload = {
            'title': 'Check growth', 'task_type': 'custom',
            'due_start': now.isoformat(), 'due_end': (now + timedelta(hours=1)).isoformat(),
        }

    def schedule(self, key, **extra):
        """Post the same due window with a selected register identity."""
        return self.client.post('/work/tasks/', {
            **self.payload, 'garden_target': key, **extra,
        }, format='json')

    def test_crop_recurrence_and_completion_preserve_the_planting(self):
        """Completing work copies the link without changing living quantity."""
        crop = make_garden_planting(
            workspace=self.workspace, quantity=5,
            batch=make_production_batch(variety=make_plant_variety(name='L' * 250)),
        )
        key = f'aggregate-{crop.pk}'
        response = self.schedule(key, recurrence={'frequency': 'daily', 'interval': 1})
        self.assertEqual(response.status_code, 201, response.data)
        task = response.data
        self.assertEqual(task['links'][0]['target_type'], 'gardenplanting')
        self.assertEqual(len(task['links'][0]['label']), 255)
        self.assertEqual(task['links'][0]['url'], f'/plantings/garden-register/{key}')
        detail = self.client.get(f'/plantings/garden-register/{key}/').data
        self.assertEqual(detail['next_task']['id'], task['pk'])
        response = self.client.post(f"/work/tasks/{task['pk']}/act/", {'action': 'complete'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(WorkTask.objects.get(parent_id=task['pk']).links.get().target, crop)
        crop.refresh_from_db()
        self.assertEqual(crop.quantity, 5)
        self.assertFalse(crop.status_events.exists())

    def test_individual_and_legacy_crops_link_their_source_records(self):
        """Both planting styles use the register's existing identities."""
        plant = make_specific_plant(workspace=self.workspace)
        make_plant_lifecycle_event(workspace=self.workspace, plant=plant)
        row = make_garden_row_sowing(workspace=self.workspace)
        square = make_garden_square_sowing(workspace=self.workspace)
        rows = self.client.get('/plantings/garden-register/').data['results']
        self.assertEqual(len(rows), 3)
        targets = {plant._meta.model_name: plant, row._meta.model_name: row, square._meta.model_name: square}
        for entry in rows:
            response = self.schedule(entry['key'])
            self.assertEqual(response.status_code, 201, response.data)
            link = WorkTask.objects.get(pk=response.data['pk']).links.get()
            self.assertEqual(link.target, targets[link.content_type.model])

    def test_invalid_targets_create_no_task(self):
        """Reject foreign, missing, malformed, and hidden individual origins."""
        other = Workspace.objects.create(name='Other garden')
        foreign = make_garden_planting(
            workspace=other,
            batch=make_production_batch(workspace=other, variety=make_plant_variety(workspace=other)),
        )
        origin = make_garden_planting(workspace=self.workspace, tracking=GardenPlanting.Tracking.INDIVIDUAL)
        for key in (f'aggregate-{foreign.pk}', f'aggregate-{origin.pk}', 'aggregate-999999', 'bad-key'):
            response = self.schedule(key)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn('garden_target', response.data)
        self.assertFalse(WorkTask.objects.exists())

    def test_finished_crop_and_conflicting_location_are_rejected(self):
        """Stale selections cannot silently become whole-garden reminders."""
        crop = make_garden_planting(workspace=self.workspace)
        key = f'aggregate-{crop.pk}'
        location = Location.objects.create(
            workspace=self.workspace, code='patio', name='Patio', location_type='growing',
        )
        response = self.schedule(key, location=location.pk)
        self.assertEqual(response.status_code, 400)
        response = self.client.post(f'/plantings/garden-register/{key}/finish/', {
            'event_type': 'finished', 'reason': 'Season ended',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        response = self.schedule(key)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('garden_target', response.data)
        self.assertFalse(WorkTask.objects.exists())
