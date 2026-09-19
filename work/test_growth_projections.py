"""Nursery growth work is projected only for plants the nursery still holds.

The seeded stage-review and ready-review rules read a plant's last stage
observation, which is a fact about a moment and outlives the plant. Before task
124 a sold, failed or culled plant kept raising its review for as long as any
sibling kept the batch open, and because the outcome had closed its location it
landed in a group of its own: a second, permanently overdue task per batch.
"""

from datetime import date, datetime, timezone as datetime_timezone

from django.test import TestCase

from plantings.growth import record_observation
from plantings.lifecycle import (
    EventType,
    OutcomeRequest,
    record_germination_event,
    record_lifecycle_event,
)
from plantings.withdrawal import withdraw_germination
from tests.factories import (
    make_growth_stage,
    make_location,
    make_plant_at_location,
    make_seed_tray_cell_planting,
    make_work_rule,
)
from workspaces.models import get_current_workspace

from .models import WorkTask, WorkTaskRule, WorkTaskType
from .projections import projected_tasks
from .services import acknowledge_projection


OBSERVED_AT = datetime(2026, 3, 1, 9, tzinfo=datetime_timezone.utc)

#: The facts that leave a plant resolved and gone, each reached by the path an
#: operator would record it along.
RESOLVING_PATHS = {
    'sold': (EventType.READY, EventType.SOLD),
    'failed': (EventType.FAILED,),
    'culled': (EventType.CULLED,),
    'lost': (EventType.LOST,),
    'donated': (EventType.DONATED,),
    'harvested': (EventType.HARVEST_FINISHED,),
    'discarded': (EventType.READY, EventType.SOLD, EventType.RETURNED_DISCARDED),
}


class GrowthProjectionTestCase(TestCase):
    """One tray fill on one bench, under the rules a nursery is seeded with."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = self.workspace.Mode.NURSERY
        self.workspace.timezone = 'Pacific/Auckland'
        self.workspace.save()
        self.bench = make_location(workspace=self.workspace)
        self.fill = make_seed_tray_cell_planting(quantity=20)
        self.stage = make_growth_stage(workspace=self.workspace, target_days=14)

    def plant(self):
        """Return one more seedling from the same fill, standing on the bench."""
        return make_plant_at_location(
            self.bench, workspace=self.workspace, cell_planting=self.fill,
        )

    def observe(self, plants):
        """Record the stage and the ready date both reviews are dated from."""
        record_observation(
            self.workspace, None, plant_ids=[plant.pk for plant in plants],
            stage=self.stage, expected_ready=date(2026, 3, 20),
            occurred_at=OBSERVED_AT,
        )

    def tasks(self, code):
        """Return what one rule projects now."""
        return [
            task for task in projected_tasks(self.workspace)
            if task.rule.code == code
        ]

    def targeted_plants(self, task):
        """Return the plant ids one task names."""
        return {
            link.target.pk for link in task.targets
            if link.url.startswith('/plantings/plants/')
        }


class ResolvedPlantGrowthProjectionTests(GrowthProjectionTestCase):
    """A resolved plant stops asking for reviews; its siblings do not."""

    def test_a_resolved_plant_raises_no_review_beside_a_growing_one(self):
        """Each way of resolving a plant takes it out of the queue."""
        growing = self.plant()
        resolved = {outcome: self.plant() for outcome in RESOLVING_PATHS}
        self.observe([growing, *resolved.values()])
        for outcome, plant in resolved.items():
            for event_type in RESOLVING_PATHS[outcome]:
                record_lifecycle_event(plant, None, OutcomeRequest(event_type))

        for code in ('stage-review', 'ready-review'):
            with self.subTest(rule=code):
                tasks = self.tasks(code)
                self.assertEqual(len(tasks), 1)
                self.assertEqual(self.targeted_plants(tasks[0]), {growing.pk})
                self.assertEqual(tasks[0].source_snapshot['target_count'], 1)

    def test_a_withdrawn_germination_raises_no_review(self):
        """A plant that never came up cannot need its stage reviewed."""
        growing = self.plant()
        imagined = self.plant()
        record_germination_event(imagined, None)
        withdraw_germination(imagined, None, 'Entered against the wrong tray.')
        # An observation blocks a withdrawal, so a withdrawn plant can only be
        # observed afterwards -- which nothing refuses yet (task 155). However
        # that lands, the queue must not raise work for a plant nobody has.
        self.observe([growing, imagined])

        tasks = self.tasks('stage-review')

        self.assertEqual(len(tasks), 1)
        self.assertEqual(self.targeted_plants(tasks[0]), {growing.pk})

    def test_a_retained_plant_keeps_its_review(self):
        """Retention resolves a plant without taking it off the bench."""
        growing = self.plant()
        retained = self.plant()
        self.observe([growing, retained])
        record_lifecycle_event(retained, None, OutcomeRequest(EventType.RETAINED))

        tasks = self.tasks('stage-review')

        self.assertEqual(len(tasks), 1)
        self.assertEqual(self.targeted_plants(tasks[0]), {growing.pk, retained.pk})

    def test_a_calendar_round_skips_plants_that_are_gone(self):
        """Calendar care reads the same candidates and keeps the same answer."""
        WorkTaskRule.objects.filter(workspace=self.workspace).delete()
        make_work_rule(
            workspace=self.workspace, code='watering',
            task_type=WorkTaskType.WATERING,
        )
        growing = self.plant()
        culled = self.plant()
        record_lifecycle_event(culled, None, OutcomeRequest(EventType.CULLED))

        tasks = self.tasks('watering')

        self.assertEqual(len(tasks), 1)
        self.assertEqual(
            {link.target.pk for link in tasks[0].targets}, {growing.pk},
        )


class AcknowledgedReviewTests(GrowthProjectionTestCase):
    """Work somebody took up keeps its snapshot when its plant is sold (task 16)."""

    def test_selling_the_plant_leaves_the_acknowledged_review_standing(self):
        """Acknowledged work keeps the snapshot its operator acted on."""
        plant = self.plant()
        self.observe([plant])
        projection = self.tasks('stage-review')[0]
        task = acknowledge_projection(self.workspace, None, projection.key)

        for event_type in (EventType.READY, EventType.SOLD):
            record_lifecycle_event(plant, None, OutcomeRequest(event_type))

        task.refresh_from_db()
        self.assertEqual(task.status, WorkTask.Status.OPEN)
        self.assertEqual(task.source_snapshot['target_count'], 1)
        self.assertTrue(task.links.filter(label=f'Plant {plant.pk}').exists())
        self.assertEqual(self.tasks('stage-review'), [])
