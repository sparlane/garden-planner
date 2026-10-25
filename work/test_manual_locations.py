"""Manual reminders retain a workspace-owned location through recurrence."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from locations.models import Location
from workspaces.models import Workspace, get_current_workspace

from .models import WorkTask


class ManualLocationTests(APITestCase):
    """Garden reminders may target an active location or the whole garden."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.save()
        self.client.force_authenticate(get_user_model().objects.create_user(username='gardener'))
        parent = Location.objects.create(
            workspace=self.workspace, code='home', name='Home', location_type='site',
        )
        self.location = Location.objects.create(
            workspace=self.workspace, code='patio', name='Patio', location_type='growing', parent=parent,
        )
        now = timezone.now()
        self.payload = {
            'title': 'Water the pots', 'task_type': 'watering',
            'due_start': now.isoformat(), 'due_end': (now + timedelta(hours=1)).isoformat(),
        }

    def test_location_survives_recurrence_and_completion_does_not_change_it(self):
        """The target is filterable and copied without recording garden care."""
        response = self.client.post('/work/tasks/', {
            **self.payload, 'location': self.location.pk,
            'recurrence': {'frequency': 'daily', 'interval': 1},
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        task = response.data
        self.assertEqual(task['links'][0]['label'], 'Home / Patio')
        self.assertEqual(task['links'][0]['target_type'], 'location')
        rows = self.client.get('/work/tasks/', {'location': self.location.pk}).data
        self.assertIn(task['key'], [row['key'] for row in rows])
        response = self.client.post(f"/work/tasks/{task['pk']}/act/", {
            'action': 'complete',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.location.active = False
        self.location.save()
        following = WorkTask.objects.get(parent_id=task['pk'])
        self.assertEqual(following.links.get().target, self.location)
        self.location.refresh_from_db()
        self.assertEqual(self.location.name, 'Patio')
        self.assertFalse(self.location.active)
        rows = self.client.get('/work/tasks/', {'view': 'completed'}).data
        recorded = next(row for row in rows if row['key'] == task['key'])
        self.assertEqual(recorded['links'][0]['label'], 'Home / Patio')

    def test_whole_garden_requires_no_location(self):
        """Existing callers and an explicitly empty picker need no setup."""
        for extra in ({}, {'location': None}):
            response = self.client.post('/work/tasks/', {**self.payload, **extra}, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(response.data['links'], [])

    def test_invalid_locations_create_no_task(self):
        """Retired, missing, and other-workspace targets are rejected atomically."""
        other = Workspace.objects.create(name='Other garden')
        foreign = Location.objects.create(
            workspace=other, code='patio', name='Private patio', location_type='growing',
        )
        self.location.active = False
        self.location.save()
        for location_id in (self.location.pk, foreign.pk, foreign.pk + 1000):
            response = self.client.post('/work/tasks/', {
                **self.payload, 'location': location_id,
            }, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn('location', response.data)
        self.assertFalse(WorkTask.objects.exists())
