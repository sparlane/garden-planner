"""Reviewing garden work links facts without creating or changing those facts."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from tests.factories import make_garden_planting, make_plant_variety, make_production_batch, make_work_rule
from workspaces.models import Workspace, get_current_workspace

from .models import WorkTask, WorkTaskRule, WorkTaskType
from .projections import projected_tasks


class GardenActionTests(APITestCase):
    """The UI's picker and repeat rules stay scoped and auditable."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.timezone = 'Pacific/Auckland'
        self.workspace.save()
        self.client.force_authenticate(get_user_model().objects.create_user(username='reviewer'))
        self.crop = make_garden_planting()
        local = ZoneInfo(self.workspace.timezone)
        self.start = datetime(2026, 9, 26, 9, tzinfo=local)
        self.end = datetime(2026, 9, 27, 10, tzinfo=local)

    def create(self, **extra):
        """Schedule a review whose first window crosses the DST transition."""
        return self.client.post('/work/tasks/', {
            'title': 'Review carrots', 'task_type': 'feeding',
            'due_start': self.start.isoformat(), 'due_end': self.end.isoformat(),
            **extra,
        }, format='json')

    def act(self, task, action, **extra):
        """Exercise the same explicit action endpoint as the dialog."""
        return self.client.post(f'/work/tasks/{task}/act/', {'action': action, **extra}, format='json')

    def test_picker_is_scoped_and_linked_completion_is_repeatable(self):
        """Reopening a review does not duplicate its result or change the crop."""
        foreign = Workspace.objects.create(name='Another household')
        make_garden_planting(workspace=foreign, batch=make_production_batch(workspace=foreign, variety=make_plant_variety(workspace=foreign)))
        options = self.client.get('/work/tasks/result-options/?kind=planting')
        self.assertEqual(options.status_code, 200, options.data)
        self.assertEqual(options.data['count'], 1)
        result = options.data['results'][0]
        self.assertEqual(result['object_id'], self.crop.pk)
        task = self.create().data['pk']
        for attempt in range(2):
            response = self.act(task, 'complete', results=[result], reason='Checked growth')
            self.assertEqual(response.status_code, 200, response.data)
            links = [link for link in response.data['links'] if link['role'] == 'result']
            self.assertEqual(len(links), 1)
            if attempt == 0:
                self.assertEqual(self.act(task, 'reopen', reason='Check again').status_code, 200)
        self.crop.refresh_from_db()
        self.assertIsNone(self.crop.finished_on)
        self.assertEqual(self.crop.quantity, 1)
        self.assertEqual(response.data['history'][-1]['changes']['results'][0]['object_id'], self.crop.pk)

    def test_missing_or_foreign_result_rolls_back_completion(self):
        """A rejected result leaves both the task and its audit history open."""
        foreign = Workspace.objects.create(name='Other')
        crop = make_garden_planting(workspace=foreign, batch=make_production_batch(workspace=foreign, variety=make_plant_variety(workspace=foreign)))
        task = self.create().data['pk']
        response = self.act(task, 'complete', results=[{
            'app_label': 'plantings', 'model': 'gardenplanting', 'object_id': crop.pk,
        }])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WorkTask.objects.get(pk=task).status, WorkTask.Status.OPEN)
        self.assertEqual(WorkTask.objects.get(pk=task).history.count(), 1)

    def test_repeated_windows_keep_both_local_times_and_bounded_keys(self):
        """Repeats spanning DST keep the promised wall times across a long series."""
        response = self.create(recurrence={'frequency': 'daily', 'interval': 1})
        self.assertEqual(response.status_code, 201, response.data)
        task = WorkTask.objects.get(pk=response.data['pk'])
        local = ZoneInfo(self.workspace.timezone)
        for _ in range(12):
            self.assertEqual(self.act(task.pk, 'complete').status_code, 200)
            task = task.following_occurrences.get()
            self.assertLess(len(task.key), 255)
            self.assertEqual(task.due_start.astimezone(local).hour, 9)
            self.assertEqual(task.due_end.astimezone(local).hour, 10)
            self.assertEqual(task.due_end.astimezone(local).date() - task.due_start.astimezone(local).date(), timedelta(days=1))

    def test_invalid_repeat_and_snoozing_closed_work_are_rejected(self):
        """Malformed repeat input cannot break the next completion."""
        for recurrence in ([], ['daily'], {'frequency': 'daily', 'interval': 0},
                           {'frequency': 'weekly', 'weekdays': 'monday'},
                           {'frequency': 'daily', 'interval': True}):
            response = self.create(recurrence=recurrence)
            self.assertEqual(response.status_code, 400, response.data)
        task = self.create().data['pk']
        self.assertEqual(self.act(task, 'complete').status_code, 200)
        response = self.act(task, 'snooze', until='2099-01-01T09:00:00Z')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WorkTask.objects.get(pk=task).status, WorkTask.Status.COMPLETED)

    def test_recurring_care_includes_aggregate_and_unknown_origin(self):
        """Routine care does not require a known sowing date or individual ids."""
        rule = make_work_rule(task_type=WorkTaskType.FEEDING, trigger=WorkTaskRule.Trigger.CALENDAR,
                              frequency=WorkTaskRule.Frequency.DAILY)
        tasks = [task for task in projected_tasks(self.workspace) if task.rule == rule]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].targets[0].target, self.crop)

    def test_weekly_care_respects_the_interval_and_selected_weekdays(self):
        """An off week keeps the last review rather than inventing a new one."""
        rule = make_work_rule(task_type=WorkTaskType.FEEDING, trigger=WorkTaskRule.Trigger.CALENDAR,
                              frequency=WorkTaskRule.Frequency.WEEKLY, interval=2, weekdays=[0, 2])
        WorkTaskRule.objects.filter(pk=rule.pk).update(created=datetime(2026, 9, 21, 9, tzinfo=ZoneInfo(self.workspace.timezone)))
        for today, expected in ((date(2026, 9, 30), '2026-09-23'), (date(2026, 10, 5), '2026-10-05')):
            task = next(task for task in projected_tasks(self.workspace, today=today) if task.rule == rule)
            self.assertEqual(task.source_snapshot['anchor_start'], expected)
