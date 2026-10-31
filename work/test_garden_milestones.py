"""Garden reminder dates follow facts while completion remains a separate act."""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase

from plantings.models import GardenPlanting
from tests.factories import (
    make_garden_planting, make_garden_row_sowing, make_plant_variety,
    make_production_batch, make_work_rule,
)
from workspaces.models import Workspace, get_current_workspace

from .models import WorkTask, WorkTaskRule, WorkTaskType
from .projections import projected_tasks
from .services import acknowledge_projection, act_on_task


class GardenMilestoneTests(TestCase):
    """The household register supplies scoped, stable milestone identities."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.timezone = 'Pacific/Auckland'
        self.workspace.save()
        self.variety = make_plant_variety(
            germination_days_min=3, germination_days_max=7,
            maturity_days_min=60, maturity_days_max=75,
        )
        self.crop = make_garden_planting(
            batch=make_production_batch(variety=self.variety),
            source=GardenPlanting.Source.DIRECT_SEED,
            date_basis=GardenPlanting.DateBasis.PLANTED,
            recorded_on=date(2026, 9, 25), quantity=20,
        )

    def tasks(self, task_type):
        """Select one kind of review from the mixed queue."""
        return [task for task in projected_tasks(self.workspace) if task.task_type == task_type]

    def test_seeded_rules_cover_quick_add_and_legacy_direct_sowing(self):
        """Both aggregate entry paths get germination and harvest windows."""
        legacy = make_garden_row_sowing(batch=self.crop.batch)
        for kind in (WorkTaskType.GERMINATION, WorkTaskType.HARVEST):
            tasks = self.tasks(kind)
            self.assertEqual(len(tasks), 2)
            self.assertEqual({task.targets[0].target.pk for task in tasks}, {self.crop.pk, legacy.pk})
            quick = next(task for task in tasks if task.targets[0].target == self.crop)
            self.assertIn('/plantings/garden-register/', quick.targets[0].url)
        self.assertFalse(WorkTask.objects.exists())

    def test_offsets_and_metadata_move_one_identity_across_dst(self):
        """Calendar days, not elapsed hours, determine a gardener's window."""
        rule = make_work_rule(
            task_type=WorkTaskType.THINNING, trigger=WorkTaskRule.Trigger.GERMINATION,
            due_start_offset_days=2, due_end_offset_days=4,
        )
        before = next(task for task in projected_tasks(self.workspace) if task.rule == rule)
        local = ZoneInfo(self.workspace.timezone)
        self.assertEqual(before.due_start.astimezone(local).date(), date(2026, 9, 30))
        self.crop.recorded_on += timedelta(days=2)
        self.crop.save()
        after = next(task for task in projected_tasks(self.workspace) if task.rule == rule)
        self.assertEqual(after.key, before.key)
        self.assertEqual(after.due_start.astimezone(local).date(), date(2026, 10, 2))
        task = acknowledge_projection(self.workspace, None, after.key)
        act_on_task(self.workspace, None, task.pk, 'complete', {})
        self.crop.refresh_from_db()
        self.assertEqual(self.crop.quantity, 20)
        self.assertIsNone(self.crop.finished_on)
        self.assertNotIn(after.key, [row.key for row in projected_tasks(self.workspace)])

    def test_configured_milestones_share_recorded_anchors(self):
        """Broader care timings are explicit choices, not invented defaults."""
        for kind in (WorkTaskType.HARDENING, WorkTaskType.TRANSPLANTING,
                     WorkTaskType.SUCCESSION, WorkTaskType.FEEDING, WorkTaskType.SEED_SAVING):
            rule = make_work_rule(
                task_type=kind, trigger=WorkTaskRule.Trigger.GARDEN_PLANTED,
                due_start_offset_days=10, due_end_offset_days=14,
                variety=self.variety,
            )
            task = next(task for task in projected_tasks(self.workspace) if task.rule == rule)
            self.assertEqual(task.source_snapshot['anchor_start'], '2026-09-25')
            self.assertEqual(task.due_end.astimezone(ZoneInfo(self.workspace.timezone)).date(), date(2026, 10, 9))

    def test_finished_crop_stops_growth_work_and_starts_cleanup(self):
        """Cleanup follows the recorded outcome date and its correction."""
        rule = make_work_rule(task_type=WorkTaskType.CLEANUP, trigger=WorkTaskRule.Trigger.GARDEN_FINISHED)
        self.assertFalse(self.tasks(WorkTaskType.CLEANUP))
        self.crop.finished_on = date(2026, 10, 30)
        self.crop.save()
        self.assertFalse(self.tasks(WorkTaskType.GERMINATION))
        self.assertFalse(self.tasks(WorkTaskType.HARVEST))
        task = self.tasks(WorkTaskType.CLEANUP)[0]
        self.assertEqual(task.rule, rule)
        self.assertEqual(task.source_snapshot['anchor_start'], '2026-10-30')
        self.crop.finished_on = None
        self.crop.save()
        self.assertFalse(self.tasks(WorkTaskType.CLEANUP))

    def test_unknown_dates_and_foreign_workspaces_generate_nothing(self):
        """A first observation is not a sowing date; other gardens stay private."""
        self.crop.date_basis = GardenPlanting.DateBasis.FIRST_OBSERVED
        self.crop.save()
        self.assertFalse(projected_tasks(self.workspace))
        other = Workspace.objects.create(name='Other garden', mode=Workspace.Mode.GARDEN)
        self.assertFalse(projected_tasks(other))

    def test_existing_legacy_maturity_acknowledgement_stays_suppressed(self):
        """Turning on Garden mode cannot resurrect an acknowledged review."""
        legacy = make_garden_row_sowing(batch=self.crop.batch)
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        task = next(task for task in self.tasks(WorkTaskType.HARVEST)
                    if task.targets[0].target == legacy)
        acknowledge_projection(self.workspace, None, task.key)
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.save()
        self.assertNotIn(task.key, [row.key for row in self.tasks(WorkTaskType.HARVEST)])
