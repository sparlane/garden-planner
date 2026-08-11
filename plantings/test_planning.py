"""Production planning calculations, versioning, approval, and variance."""

# Test names state the behavior more clearly than repeated method docstrings.
# pylint: disable=missing-function-docstring

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from tests.factories import make_location, make_plant_variety

from .models import (
    GrowthStage,
    NurseryPlanDemand,
    NurseryPlanIssue,
    NurseryPlanningAssumption,
    NurseryPlanningStageAssumption,
    NurseryProductionPlan,
    ProductionBatch,
)
from .planning import approve_plan, calculate_plan, revise_plan


class NurseryPlanningTests(TestCase):
    """A plan snapshots explicit assumptions without posting production facts."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='planner')
        self.variety = make_plant_variety()
        self.workspace = self.variety.workspace
        self.location = make_location(
            workspace=self.workspace,
            capacity_basis='plants',
            capacity_value=Decimal('500'),
        )
        self.germination = GrowthStage.objects.create(
            workspace=self.workspace, code='germination', name='Germination', display_order=10,
        )
        self.finishing = GrowthStage.objects.create(
            workspace=self.workspace, code='finishing', name='Finishing', display_order=20,
        )
        self.assumption = NurseryPlanningAssumption.objects.create(
            workspace=self.workspace,
            variety=self.variety,
            effective_from=date(2026, 1, 1),
            germination_rate=Decimal('0.9'),
            seeds_per_cluster=2,
            tray_density=72,
        )
        NurseryPlanningStageAssumption.objects.create(
            assumption=self.assumption,
            stage=self.germination,
            sequence=1,
            lead_days=7,
            loss_rate=Decimal('0.1'),
            location=self.location,
        )
        NurseryPlanningStageAssumption.objects.create(
            assumption=self.assumption,
            stage=self.finishing,
            sequence=2,
            lead_days=14,
            loss_rate=Decimal('0.2'),
            location=self.location,
        )

    def _plan(self, direction='backward', sowing_date=None):
        plan = NurseryProductionPlan.objects.create(
            workspace=self.workspace,
            code='SPRING-2026',
            direction=direction,
            sowing_date=sowing_date,
            created_by=self.user,
        )
        NurseryPlanDemand.objects.create(
            plan=plan,
            variety=self.variety,
            target_quantity=72,
            ready_from=date(2026, 4, 30),
            ready_until=date(2026, 5, 2),
            source=NurseryPlanDemand.Source.FORECAST,
            source_line_reference='forecast-1',
        )
        return plan

    def test_backward_calculation_compounds_losses_and_snapshots_dates(self):
        plan = self._plan()

        requirement = calculate_plan(plan).get()

        self.assertEqual(requirement.required_clusters, 112)
        self.assertEqual(requirement.required_seeds, 224)
        self.assertEqual(requirement.required_trays, 2)
        self.assertEqual(requirement.sowing_date, date(2026, 4, 9))
        self.assertEqual(requirement.expected_ready_from, date(2026, 4, 30))
        self.assertEqual(
            list(requirement.milestones.values_list(
                'planned_date', 'input_quantity', 'expected_output',
            )),
            [
                (date(2026, 4, 9), 100, 90),
                (date(2026, 4, 16), 90, 72),
            ],
        )
        self.assertEqual(requirement.assumption_snapshot['germination_rate'], '0.900000')
        self.assertTrue(plan.issues.filter(kind=NurseryPlanIssue.Kind.SEED).exists())
        self.assertTrue(plan.issues.filter(kind=NurseryPlanIssue.Kind.TRAY).exists())

    def test_forward_calculation_starts_at_explicit_sowing_date(self):
        plan = self._plan(
            direction=NurseryProductionPlan.Direction.FORWARD,
            sowing_date=date(2026, 3, 1),
        )

        requirement = calculate_plan(plan).get()

        self.assertEqual(requirement.sowing_date, date(2026, 3, 1))
        self.assertEqual(requirement.expected_ready_from, date(2026, 3, 22))
        self.assertEqual(requirement.expected_ready_until, date(2026, 3, 24))

    def test_distinct_sources_are_not_deduplicated(self):
        plan = self._plan()
        NurseryPlanDemand.objects.create(
            plan=plan,
            variety=self.variety,
            target_quantity=20,
            ready_from=date(2026, 4, 30),
            ready_until=date(2026, 4, 30),
            source=NurseryPlanDemand.Source.CONFIRMED_ORDER,
            order_reference='ORDER-1',
            source_line_reference='line-1',
        )

        self.assertEqual(calculate_plan(plan).count(), 2)
        self.assertEqual(plan.demand_lines.count(), 2)

    def test_demand_lines_share_capacity_and_stock_availability(self):
        self.location.capacity_value = Decimal('150')
        self.location.save()
        plan = self._plan()
        NurseryPlanDemand.objects.create(
            plan=plan,
            variety=self.variety,
            target_quantity=72,
            ready_from=date(2026, 4, 30),
            ready_until=date(2026, 5, 2),
            source=NurseryPlanDemand.Source.MANUAL,
        )

        calculate_plan(plan)

        capacity = plan.issues.filter(kind=NurseryPlanIssue.Kind.CAPACITY).first()
        seed = plan.issues.filter(kind=NurseryPlanIssue.Kind.SEED).last()
        self.assertEqual(capacity.required_quantity, Decimal('100'))
        self.assertEqual(capacity.available_quantity, Decimal('50'))
        self.assertEqual(seed.required_quantity, Decimal('448'))

    def test_approval_creates_planned_batches_without_sowings_or_stock(self):
        plan = self._plan()
        requirement = calculate_plan(plan).get()

        approved = approve_plan(plan, self.user)
        requirement.refresh_from_db()

        self.assertEqual(approved.status, NurseryProductionPlan.Status.APPROVED)
        self.assertEqual(requirement.batch.status, ProductionBatch.Status.PLANNED)
        self.assertEqual(requirement.batch.planned_start, requirement.sowing_date)
        self.assertFalse(requirement.batch.seedtrayplanting_sowings.exists())
        with self.assertRaisesMessage(ValidationError, 'Approved plans are immutable'):
            approved.save()
        with self.assertRaisesMessage(ValidationError, 'Approved plan demand is immutable'):
            requirement.demand.save()

    def test_revision_retains_approved_snapshot_and_clones_demand(self):
        plan = self._plan()
        original = calculate_plan(plan).get()
        approve_plan(plan, self.user)

        revision = revise_plan(plan, self.user)
        revision.demand_lines.update(target_quantity=100)
        recalculated = calculate_plan(revision).get()
        original.refresh_from_db()

        self.assertEqual(revision.version, 2)
        self.assertEqual(revision.supersedes, plan)
        self.assertEqual(recalculated.expected_finished, 100)
        self.assertEqual(original.expected_finished, 72)

    def test_missing_effective_assumption_is_an_explicit_issue(self):
        NurseryPlanningAssumption.objects.filter(pk=self.assumption.pk).update(
            effective_from=date(2027, 1, 1),
        )
        plan = self._plan()

        self.assertFalse(calculate_plan(plan).exists())
        issue = plan.issues.get()
        self.assertEqual(issue.kind, NurseryPlanIssue.Kind.ASSUMPTION)
