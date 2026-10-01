"""Garden reminder corrections preserve history and source facts."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from tests.factories import make_seed_tray_planting
from workspaces.models import Workspace, get_current_workspace

from .models import WorkTask, WorkTaskRule
from .projections import projected_tasks
from .services import acknowledge_projection, create_manual_task


class ReminderRescheduleTests(APITestCase):
    """Rescheduling changes only an owned, open reminder occurrence."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.timezone = 'Pacific/Auckland'
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='gardener')
        self.client.force_authenticate(self.user)
        self.start = timezone.now() + timedelta(days=2)
        self.end = self.start + timedelta(hours=1)
        self.task = create_manual_task(self.workspace, self.user, {
            'title': 'Water beans', 'task_type': 'watering',
            'due_start': self.start, 'due_end': self.end,
        })

    def _act(self, **overrides):
        return self.client.post(f'/work/tasks/{self.task.pk}/act/', {
            'action': 'reschedule',
            'due_start': self.start.isoformat(),
            'due_end': self.end.isoformat(),
            **overrides,
        }, format='json')

    def test_reschedule_clears_snooze_and_preserves_audit_and_retry(self):
        """Moving a snoozed reminder returns it to the queue exactly once."""
        self._act(action='snooze', until=self.end.isoformat())
        self.start += timedelta(days=3)
        self.end += timedelta(days=3)
        response = self._act(reason='Rain forecast', idempotency_key='move-once')
        self.assertEqual(response.status_code, 200, response.data)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, WorkTask.Status.OPEN)
        self.assertIsNone(self.task.snoozed_until)
        self.assertEqual(self.task.due_start, self.start)
        history = self.task.history.get(action='reschedule')
        self.assertEqual(history.reason, 'Rain forecast')
        self.assertEqual(datetime.fromisoformat(history.changes['due_start']['before']),
                         self.start - timedelta(days=3))
        self.assertEqual(datetime.fromisoformat(history.changes['due_end']['after']), self.end)
        self.assertEqual(self._act(idempotency_key='move-once').status_code, 200)
        self.assertEqual(self.task.history.filter(action='reschedule').count(), 1)
        self.assertEqual(WorkTask.objects.count(), 1)
        self.assertEqual(self.client.get('/work/tasks/?view=snoozed').data, [])

    def test_invalid_windows_leave_dates_and_history_unchanged(self):
        """Missing or reversed dates are field errors, never partial writes."""
        for values, field in [
                ({'due_start': None}, 'due_start'),
                ({'due_end': None}, 'due_end'),
                ({'due_end': (self.start - timedelta(hours=1)).isoformat()}, 'due_end')]:
            with self.subTest(values=values):
                response = self._act(**values)
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)
        response = self.client.post(f'/work/tasks/{self.task.pk}/act/', {
            'action': 'reschedule',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('due_start', response.data)
        self.assertIn('due_end', response.data)
        self.task.refresh_from_db()
        self.assertEqual(self.task.due_start, self.start)
        self.assertEqual(self.task.history.count(), 1)

    def test_closed_reminders_require_reopening(self):
        """Moving a date cannot silently resurrect completed or skipped work."""
        for action in ('complete', 'skip'):
            self.assertEqual(self._act(action=action, reason='Done').status_code, 200)
            response = self._act()
            self.assertEqual(response.status_code, 400)
            self.assertIn('status', response.data)
            self.assertEqual(self._act(action='reopen', reason='Try again').status_code, 200)
        self.assertEqual(self._act().status_code, 200)

    def test_another_workspace_cannot_reschedule(self):
        """Knowing the primary key does not grant access to a reminder."""
        other = Workspace.objects.create(name='Other household')
        self.task.workspace = other
        self.task.save()
        response = self._act()
        self.assertEqual(response.status_code, 400)
        self.task.refresh_from_db()
        self.assertEqual(self.task.due_start, self.start)
        self.assertEqual(self.task.history.count(), 1)

    def test_recurrence_uses_corrected_window_across_dst(self):
        """The next daily reminder keeps local time after a date correction."""
        local = ZoneInfo(self.workspace.timezone)
        self.task.recurrence = {'frequency': 'daily', 'interval': 1}
        self.task.save()
        self.start = datetime(2026, 9, 26, 9, tzinfo=local)
        self.end = self.start + timedelta(hours=1)
        self.assertEqual(self._act().status_code, 200)
        self.assertEqual(self._act(action='complete').status_code, 200)
        following = self.task.following_occurrences.get()
        expected = datetime(2026, 9, 27, 9, tzinfo=local)
        self.assertEqual(following.due_start, expected)
        self.assertEqual(following.due_end, expected + timedelta(hours=1))
        self.assertEqual(following.due_start - self.start.astimezone(ZoneInfo('UTC')),
                         timedelta(hours=23))

    def test_reopened_occurrence_does_not_duplicate_its_successor(self):
        """Correcting a past occurrence leaves its existing successor intact."""
        self.task.recurrence = {'frequency': 'daily', 'interval': 1}
        self.task.save()
        self.assertEqual(self._act(action='complete').status_code, 200)
        following = self.task.following_occurrences.get()
        self.assertEqual(self._act(action='reopen', reason='More watering').status_code, 200)
        self.start += timedelta(days=3)
        self.end += timedelta(days=3)
        self.assertEqual(self._act().status_code, 200)
        self.assertEqual(self._act(action='complete').status_code, 200)
        self.assertEqual(self.task.following_occurrences.count(), 1)
        original_start = following.due_start
        following.refresh_from_db()
        self.assertEqual(following.due_start, original_start)

    def test_generated_reminder_keeps_source_and_identity(self):
        """Acknowledged date overrides never change a sowing or duplicate its task."""
        sowing = make_seed_tray_planting(workspace=self.workspace)
        variety = sowing.seeds_used.seeds.plant_variety
        variety.germination_days_min = 3
        variety.germination_days_max = 5
        variety.save()
        rule = WorkTaskRule.objects.get(workspace=self.workspace, code='germination-check')
        projection = next(row for row in projected_tasks(self.workspace) if row.rule == rule)
        self.task = acknowledge_projection(self.workspace, self.user, projection.key)
        planted = sowing.planted
        snapshot = self.task.source_snapshot
        self.assertEqual(self._act().status_code, 200)
        sowing.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(sowing.planted, planted)
        self.assertEqual(self.task.source_snapshot, snapshot)
        self.assertEqual(self.task.key, projection.key)
        self.assertNotIn(projection.key, [row.key for row in projected_tasks(self.workspace)])
