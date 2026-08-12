"""Tests for live source projections and local-time recurrence."""

from datetime import datetime, time, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo

from django.test import TestCase

from plantings.models import ProductionBatch, SeedTrayPlanting
from tests.factories import make_plant_variety, make_seed_packet, make_seeds
from workspaces.models import get_current_workspace

from .models import WorkTaskRule, WorkTaskType
from .projections import next_recurrence, projected_tasks


class WorkProjectionTests(TestCase):
    """Unacknowledged tasks follow their source facts without stale rows."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = self.workspace.Mode.NURSERY
        self.workspace.timezone = 'Pacific/Auckland'
        self.workspace.save()

    def test_germination_projection_moves_when_sowing_date_changes(self):
        """A live projection retains its key while its due window moves."""
        variety = make_plant_variety(
            workspace=self.workspace, germination_days_min=3, germination_days_max=5,
        )
        seeds = make_seeds(workspace=self.workspace, plant_variety=variety)
        packet = make_seed_packet(workspace=self.workspace, seeds=seeds)
        batch = ProductionBatch.objects.create(
            workspace=self.workspace, code='B-1', variety=variety,
        )
        sowing = SeedTrayPlanting.objects.create(
            workspace=self.workspace, batch=batch, seeds_used=packet,
            quantity=10, planted=datetime(2026, 8, 1, tzinfo=datetime_timezone.utc),
        )
        WorkTaskRule.objects.create(
            workspace=self.workspace, code='germination', name='Germination',
            task_type=WorkTaskType.GERMINATION,
            trigger=WorkTaskRule.Trigger.GERMINATION,
        )
        before = projected_tasks(self.workspace)
        sowing.planted = datetime(2026, 8, 4, tzinfo=datetime_timezone.utc)
        sowing.save()
        after = projected_tasks(self.workspace)
        self.assertEqual(before[0].key, after[0].key)
        self.assertEqual(after[0].due_start - before[0].due_start, timedelta(days=3))

    def test_daily_recurrence_retains_local_time_across_dst(self):
        """Calendar arithmetic does not shift a task at the DST boundary."""
        before = datetime(2026, 9, 26, 9, tzinfo=ZoneInfo('Pacific/Auckland'))
        after = next_recurrence(
            self.workspace, before, WorkTaskRule.Frequency.DAILY,
        )
        self.assertEqual(after.timetz().replace(tzinfo=None), time(9))
        self.assertNotEqual(before.utcoffset(), after.utcoffset())
