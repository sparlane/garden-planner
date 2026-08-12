"""Foundational model tests for nursery work scheduling."""

from datetime import datetime, timezone as datetime_timezone

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from workspaces.models import get_current_workspace

from .models import WorkTask, WorkTaskHistory, WorkTaskRule, WorkTaskType


class WorkModelTests(TestCase):
    """Rules and acknowledged tasks preserve scheduling invariants."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.user = get_user_model().objects.create_user(username='work-user')

    def test_rule_due_window_must_be_ordered(self):
        """A rule cannot produce a backwards due window."""
        rule = WorkTaskRule(
            workspace=self.workspace,
            code='bad-window',
            name='Bad window',
            task_type=WorkTaskType.WATERING,
            trigger=WorkTaskRule.Trigger.CALENDAR,
            frequency=WorkTaskRule.Frequency.DAILY,
            due_start_offset_days=2,
            due_end_offset_days=1,
        )
        with self.assertRaises(ValidationError):
            rule.save()

    def test_task_key_is_workspace_unique(self):
        """One logical occurrence can be acknowledged only once."""
        values = {
            'workspace': self.workspace,
            'key': 'manual:stable',
            'origin': WorkTask.Origin.MANUAL,
            'task_type': WorkTaskType.CUSTOM,
            'title': 'Inspect bench',
            'due_start': datetime(2026, 8, 12, tzinfo=datetime_timezone.utc),
            'due_end': datetime(2026, 8, 12, 1, tzinfo=datetime_timezone.utc),
        }
        WorkTask.objects.create(**values)
        with self.assertRaises(ValidationError):
            WorkTask.objects.create(**values)

    def test_history_cannot_be_rewritten(self):
        """Recorded user actions remain chronological immutable evidence."""
        task = WorkTask.objects.create(
            workspace=self.workspace,
            key='manual:audit',
            origin=WorkTask.Origin.MANUAL,
            task_type=WorkTaskType.CUSTOM,
            title='Inspect bench',
            due_start=datetime(2026, 8, 12, tzinfo=datetime_timezone.utc),
            due_end=datetime(2026, 8, 12, 1, tzinfo=datetime_timezone.utc),
        )
        row = WorkTaskHistory.objects.create(task=task, action='created', actor=self.user)
        row.reason = 'Changed'
        with self.assertRaises(ValidationError):
            row.save()
