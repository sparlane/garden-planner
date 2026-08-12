"""Tests for live source projections and local-time recurrence."""

from datetime import datetime, time, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo

from django.test import TestCase

from health.models import HealthObservation, HealthObservationType
from health.operations import record_follow_up
from health.services import preview_observation, record_observation

from plantings.models import (
    GardenSquareTransplant,
    ProductionBatch,
    SeedTrayPlanting,
    SpecificPlantLocation,
)
from tests.factories import (
    make_garden_row_sowing,
    make_garden_square,
    make_plant_variety,
    make_seed_packet,
    make_seed_tray_cell_planting,
    make_seed_tray_planting,
    make_seeds,
    make_specific_plant,
    make_specific_plant_location,
)
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

    def test_health_follow_up_projects_until_result_is_recorded(self):
        """An effective result suppresses the live follow-up occurrence."""
        plant = make_specific_plant(workspace=self.workspace)
        scopes = [{'type': 'plant', 'id': plant.pk}]
        preview = preview_observation(self.workspace, scopes)
        observation = record_observation(
            self.workspace, None, scopes=scopes,
            reviewed_digest=preview['digest'],
            observation_type=HealthObservationType.objects.get(
                workspace=self.workspace, code='pest-signs',
            ),
            severity=HealthObservation.Severity.MODERATE,
            follow_up_due_at=datetime(
                2026, 8, 13, 9, tzinfo=datetime_timezone.utc,
            ),
        )
        tasks = [
            row for row in projected_tasks(self.workspace)
            if 'health-observation' in row.key
        ]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].targets[0].target, plant)
        record_follow_up(
            self.workspace, None, observation,
            result='resolved', effectiveness='unknown',
        )
        self.assertFalse(any(
            'health-observation' in row.key
            for row in projected_tasks(self.workspace)
        ))

    def _maturity_rule(self):
        WorkTaskRule.objects.filter(workspace=self.workspace).delete()
        return WorkTaskRule.objects.create(
            workspace=self.workspace,
            code='maturity',
            name='Maturity',
            task_type=WorkTaskType.HARVEST,
            trigger=WorkTaskRule.Trigger.MATURITY,
        )

    def test_seed_based_maturity_projects_from_tray_sowing(self):
        """Seed-based varieties retain their sowing-based maturity reminder."""
        self._maturity_rule()
        variety = make_plant_variety(
            workspace=self.workspace,
            maturity_days_min=10,
            maturity_days_max=12,
            maturity_basis='seed',
        )
        packet = make_seed_packet(
            workspace=self.workspace,
            seeds=make_seeds(workspace=self.workspace, plant_variety=variety),
        )
        sowed_at = datetime(2026, 4, 1, 9, tzinfo=datetime_timezone.utc)
        make_seed_tray_planting(
            workspace=self.workspace,
            seeds_used=packet,
            planted=sowed_at,
        )

        tasks = projected_tasks(self.workspace)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].due_start.date(), (sowed_at + timedelta(days=10)).date())
        self.assertEqual(tasks[0].due_end.date(), (sowed_at + timedelta(days=12)).date())

    def test_direct_sown_row_projects_from_sowing_for_transplant_variety(self):
        """A direct-sown garden row always uses its sowing date."""
        self._maturity_rule()
        variety = make_plant_variety(
            workspace=self.workspace,
            maturity_days_min=10,
            maturity_days_max=12,
            maturity_basis='transplanting',
        )
        packet = make_seed_packet(
            workspace=self.workspace,
            seeds=make_seeds(workspace=self.workspace, plant_variety=variety),
        )
        sowed_at = datetime(2026, 4, 1, 9, tzinfo=datetime_timezone.utc)
        make_garden_row_sowing(
            workspace=self.workspace,
            seeds_used=packet,
            planted=sowed_at,
        )

        tasks = projected_tasks(self.workspace)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].due_start.date(), (sowed_at + timedelta(days=10)).date())

    def test_transplant_maturity_waits_for_active_garden_placement(self):
        """Tray and nursery locations do not masquerade as transplant anchors."""
        self._maturity_rule()
        variety = make_plant_variety(
            workspace=self.workspace,
            maturity_days_min=10,
            maturity_days_max=12,
            maturity_basis='transplanting',
        )
        packet = make_seed_packet(
            workspace=self.workspace,
            seeds=make_seeds(workspace=self.workspace, plant_variety=variety),
        )
        sowing = make_seed_tray_planting(
            workspace=self.workspace,
            seeds_used=packet,
        )
        cell_planting = make_seed_tray_cell_planting(
            seed_tray_planting=sowing,
            quantity=1,
        )
        plant = make_specific_plant(
            workspace=self.workspace,
            cell_planting=cell_planting,
        )
        nursery_location = make_specific_plant_location(
            specific_plant=plant,
            started=datetime(2026, 3, 1, 9, tzinfo=datetime_timezone.utc),
        )

        self.assertEqual(projected_tasks(self.workspace), [])

        transplanted_at = datetime(2026, 4, 1, 9, tzinfo=datetime_timezone.utc)
        nursery_location.ended = transplanted_at
        nursery_location.save(update_fields=['ended'])
        SpecificPlantLocation.objects.create(
            specific_plant=plant,
            location_type=SpecificPlantLocation.GARDEN_SQUARE,
            garden_square=make_garden_square(workspace=self.workspace),
            started=transplanted_at,
        )

        tasks = projected_tasks(self.workspace)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].due_start.date(), (transplanted_at + timedelta(days=10)).date())
        self.assertIn(f'maturity:plant:{plant.pk}', tasks[0].key)

    def test_individual_transplant_suppresses_matching_legacy_projection(self):
        """Mixed migrated representations produce only one maturity reminder."""
        self._maturity_rule()
        variety = make_plant_variety(
            workspace=self.workspace,
            maturity_days_min=10,
            maturity_days_max=12,
            maturity_basis='transplanting',
        )
        packet = make_seed_packet(
            workspace=self.workspace,
            seeds=make_seeds(workspace=self.workspace, plant_variety=variety),
        )
        sowing = make_seed_tray_planting(
            workspace=self.workspace,
            seeds_used=packet,
        )
        square = make_garden_square(workspace=self.workspace)
        GardenSquareTransplant.objects.create(
            workspace=self.workspace,
            original_planting=sowing,
            quantity=1,
            location=square,
        )
        plant = make_specific_plant(
            workspace=self.workspace,
            cell_planting=make_seed_tray_cell_planting(
                seed_tray_planting=sowing,
                quantity=1,
            ),
        )
        SpecificPlantLocation.objects.create(
            specific_plant=plant,
            location_type=SpecificPlantLocation.GARDEN_SQUARE,
            garden_square=square,
        )

        tasks = projected_tasks(self.workspace)

        self.assertEqual(len(tasks), 1)
        self.assertIn(f'maturity:plant:{plant.pk}', tasks[0].key)

    def test_legacy_aggregate_transplant_projects_from_its_recorded_date(self):
        """Unconverted aggregate transplants retain a usable maturity reminder."""
        self._maturity_rule()
        variety = make_plant_variety(
            workspace=self.workspace,
            maturity_days_min=10,
            maturity_days_max=12,
            maturity_basis='transplanting',
        )
        packet = make_seed_packet(
            workspace=self.workspace,
            seeds=make_seeds(workspace=self.workspace, plant_variety=variety),
        )
        sowing = make_seed_tray_planting(
            workspace=self.workspace,
            seeds_used=packet,
        )
        transplanted_at = datetime(2026, 4, 1, 9, tzinfo=datetime_timezone.utc)
        transplant = GardenSquareTransplant.objects.create(
            workspace=self.workspace,
            original_planting=sowing,
            transplanted=transplanted_at,
            quantity=1,
            location=make_garden_square(workspace=self.workspace),
        )

        tasks = projected_tasks(self.workspace)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].due_start.date(), (transplanted_at + timedelta(days=10)).date())
        self.assertIn(f'maturity:transplant:{transplant.pk}', tasks[0].key)
