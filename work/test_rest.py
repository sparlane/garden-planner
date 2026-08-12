"""REST contract tests for the nursery work queue."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from workspaces.models import get_current_workspace

from .models import WorkTask, WorkTaskRule


class WorkRESTTests(APITestCase):
    """Operators can create, filter, and act on work through explicit actions."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = self.workspace.Mode.NURSERY
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='operator')
        self.client.force_authenticate(self.user)

    def _create(self, recurrence=None):
        now = timezone.now()
        response = self.client.post('/work/tasks/', {
            'task_type': 'watering',
            'title': 'Water propagation bench',
            'due_start': (now - timedelta(minutes=5)).isoformat(),
            'due_end': (now + timedelta(minutes=5)).isoformat(),
            'recurrence': recurrence or {},
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_today_lists_manual_work(self):
        """The default queue view contains work whose window intersects today."""
        created = self._create()
        response = self.client.get('/work/tasks/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(created['key'], [row['key'] for row in response.data])

    def test_claim_snooze_complete_and_reopen_are_historical(self):
        """Explicit actions update state and append readable audit rows."""
        task = self._create()
        response = self.client.post(f"/work/tasks/{task['pk']}/act/", {
            'action': 'claim', 'idempotency_key': 'claim-1',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['assignee'], self.user.pk)
        response = self.client.post(f"/work/tasks/{task['pk']}/act/", {
            'action': 'complete', 'idempotency_key': 'complete-1',
        }, format='json')
        self.assertEqual(response.data['status'], WorkTask.Status.COMPLETED)
        response = self.client.post(f"/work/tasks/{task['pk']}/act/", {
            'action': 'reopen', 'reason': 'More work is needed.',
        }, format='json')
        self.assertEqual(response.data['status'], WorkTask.Status.OPEN)
        self.assertEqual(len(response.data['history']), 4)

    def test_skip_requires_a_reason(self):
        """Skipping cannot erase the operational explanation."""
        task = self._create()
        response = self.client.post(f"/work/tasks/{task['pk']}/act/", {
            'action': 'skip',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WorkTask.objects.get(pk=task['pk']).status, WorkTask.Status.OPEN)

    def test_routes_require_authentication_and_nursery_mode(self):
        """The queue is authenticated and belongs to the Nursery profile."""
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get('/work/tasks/').status_code, 403)
        self.client.force_authenticate(self.user)
        self.workspace.mode = self.workspace.Mode.GARDEN
        self.workspace.save()
        self.assertEqual(self.client.get('/work/tasks/').status_code, 403)

    def test_switching_to_nursery_installs_safe_default_rules(self):
        """A profile change after migration receives the conservative defaults."""
        WorkTaskRule.objects.filter(workspace=self.workspace).delete()
        self.workspace.mode = self.workspace.Mode.GARDEN
        self.workspace.save()
        self.workspace.mode = self.workspace.Mode.NURSERY
        self.workspace.save()
        response = self.client.get('/work/rules/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 5)
