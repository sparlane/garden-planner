"""Upcoming reminders remain discoverable beyond the current week."""

# Queue tests intentionally share the authenticated Garden workspace setup.
# pylint: disable=duplicate-code

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from workspaces.models import Workspace, get_current_workspace

from .models import WorkTask, WorkTaskRule
from .projections import ProjectedTask
from .services import create_manual_task


class UpcomingReminderTests(APITestCase):
    """The future queue shares workspace scoping and occurrence state rules."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.timezone = 'Pacific/Auckland'
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='gardener')
        self.client.force_authenticate(self.user)
        self.now = datetime(2026, 9, 27, 0, 30, tzinfo=ZoneInfo('Pacific/Auckland'))
        self.tomorrow = datetime(2026, 9, 28, tzinfo=ZoneInfo('Pacific/Auckland'))

    def _task(self, end, **values):
        return create_manual_task(self.workspace, self.user, {
            'title': 'Water beans', 'task_type': 'watering',
            'due_start': end - timedelta(hours=1), 'due_end': end, **values,
        })

    def _rows(self):
        with patch('work.rest.timezone.now', return_value=self.now):
            response = self.client.get('/work/tasks/?view=upcoming')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_workspace_midnight_across_daylight_saving(self):
        """Tomorrow means local midnight, even on a 23-hour day."""
        self._task(self.now - timedelta(seconds=1))
        self._task(self.tomorrow - timedelta(microseconds=1))
        boundary = self._task(self.tomorrow, due_start=self.now)
        future = self._task(self.tomorrow + timedelta(days=60))
        self.assertEqual([row['key'] for row in self._rows()], [boundary.key, future.key])

    def test_closed_snoozed_and_other_workspace_tasks_are_excluded(self):
        """Only open owned reminders, including expired snoozes, appear."""
        for status in (WorkTask.Status.COMPLETED, WorkTask.Status.SKIPPED,
                       WorkTask.Status.SNOOZED):
            task = self._task(self.tomorrow)
            task.status = status
            task.snoozed_until = self.tomorrow
            if status == WorkTask.Status.COMPLETED:
                task.completed_at = self.now
            if status == WorkTask.Status.SKIPPED:
                task.skipped_at = self.now
            task.save()
        expired = self._task(self.tomorrow)
        expired.status = WorkTask.Status.SNOOZED
        expired.snoozed_until = self.now
        expired.save()
        other = self._task(self.tomorrow)
        other.workspace = Workspace.objects.create(name='Other garden')
        other.save()
        self.assertEqual([row['key'] for row in self._rows()], [expired.key])

    def test_projected_reminders_are_read_without_materializing(self):
        """Derived and manual windows use the same upcoming filter."""
        rule = WorkTaskRule.objects.filter(workspace=self.workspace).first()
        projection = ProjectedTask(
            occurrence='future-sowing', rule=rule, task_type='germination_check',
            title='Check emergence', priority=20, due_start=self.tomorrow,
            due_end=self.tomorrow + timedelta(days=2), assignee=None,
            targets=(), source_snapshot={},
        )
        with patch('work.rest.projected_tasks', return_value=[projection]):
            self.assertEqual([row['key'] for row in self._rows()], [projection.key])
        self.assertFalse(WorkTask.objects.exists())

    def test_rescheduled_reminder_can_be_found_beyond_this_week(self):
        """Moving dates makes the same occurrence reachable in Upcoming."""
        task = self._task(self.now)
        self.assertEqual(self._rows(), [])
        future = self.tomorrow + timedelta(days=30)
        response = self.client.post(f'/work/tasks/{task.pk}/act/', {
            'action': 'reschedule', 'due_start': future.isoformat(),
            'due_end': (future + timedelta(hours=1)).isoformat(),
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['key'] for row in self._rows()], [task.key])
