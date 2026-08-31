"""REST contract for nursery production planning."""

# Test names state the behavior more clearly than repeated method docstrings.
# pylint: disable=missing-function-docstring

from datetime import date
from decimal import Decimal

from tests.api import RESTContractTestCase
from tests.factories import make_location, make_plant_variety
from workspaces.models import Workspace

from .models import (
    GrowthStage,
    NurseryPlanningAssumption,
    NurseryPlanningStageAssumption,
    NurseryProductionPlan,
    ProductionBatch,
)


class NurseryPlanningRESTTests(RESTContractTestCase):
    """Plans expose reviewed calculation and approval actions in nursery mode."""

    def setUp(self):
        super().setUp()
        self.variety = make_plant_variety()
        self.workspace = self.variety.workspace
        Workspace.objects.filter(pk=self.workspace.pk).update(mode=Workspace.Mode.NURSERY)
        self.workspace.refresh_from_db()
        self.location = make_location(
            workspace=self.workspace,
            capacity_basis='plants',
            capacity_value=Decimal('1000'),
        )
        stage = GrowthStage.objects.create(
            workspace=self.workspace, code='finish', name='Finish', display_order=1,
        )
        assumption = NurseryPlanningAssumption.objects.create(
            workspace=self.workspace,
            variety=self.variety,
            effective_from=date(2026, 1, 1),
            germination_rate=Decimal('0.8'),
            seeds_per_cluster=1,
            tray_density=50,
        )
        NurseryPlanningStageAssumption.objects.create(
            assumption=assumption,
            stage=stage,
            sequence=1,
            lead_days=10,
            loss_rate=Decimal('0.2'),
            location=self.location,
        )

    def test_plan_workflow_calculates_approves_revises_and_reports_variance(self):
        created = self.client.post('/plantings/production-plans/', {
            'code': 'AUTUMN',
            'direction': NurseryProductionPlan.Direction.BACKWARD,
            'notes': 'Retail availability',
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        plan_id = created.data['pk']
        demand = self.client.post('/plantings/production-plan-demand/', {
            'plan': plan_id,
            'variety': self.variety.pk,
            'target_quantity': 80,
            'ready_from': '2026-05-20',
            'ready_until': '2026-05-22',
            'source': 'confirmed_order',
            'priority': 30,
            'order_reference': 'ORDER-9',
            'source_line_reference': 'ORDER-9-L1',
        }, format='json')
        self.assertEqual(demand.status_code, 201, demand.data)

        calculated = self.client.post(f'/plantings/production-plans/{plan_id}/calculate/')
        self.assertEqual(calculated.status_code, 200, calculated.data)
        requirement = calculated.data['demand_lines'][0]['requirement']
        self.assertEqual(requirement['required_clusters'], 125)
        self.assertEqual(requirement['required_trays'], 3)
        self.assertEqual(requirement['sowing_date'], '2026-05-10')

        approved = self.client.post(f'/plantings/production-plans/{plan_id}/approve/')
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data['status'], NurseryProductionPlan.Status.APPROVED)
        batch = ProductionBatch.objects.get(planning_requirement__demand__plan_id=plan_id)
        self.assertEqual(batch.status, ProductionBatch.Status.PLANNED)

        rejected = self.client.patch(
            f'/plantings/production-plans/{plan_id}/', {'notes': 'silent rewrite'},
            format='json',
        )
        self.assertEqual(rejected.status_code, 400)
        rejected = self.client.patch(
            f'/plantings/production-plan-demand/{demand.data["pk"]}/',
            {'target_quantity': 1}, format='json',
        )
        self.assertEqual(rejected.status_code, 400)

        variance = self.client.get(f'/plantings/production-plans/{plan_id}/variance/')
        self.assertEqual(variance.status_code, 200)
        self.assertEqual(variance.data[0]['planned_seeds'], 125)
        self.assertEqual(variance.data[0]['actual_seeds'], 0)

        revision = self.client.post(f'/plantings/production-plans/{plan_id}/revise/')
        self.assertEqual(revision.status_code, 201, revision.data)
        self.assertEqual(revision.data['version'], 2)
        self.assertEqual(revision.data['supersedes'], plan_id)
        self.assertEqual(revision.data['demand_lines'][0]['target_quantity'], 80)

    def test_routes_require_authentication_and_nursery_profile(self):
        urls = [
            '/plantings/planning-assumptions/',
            '/plantings/planning-stage-assumptions/',
            '/plantings/planning-input-assumptions/',
            '/plantings/production-plan-demand/',
            '/plantings/production-plans/',
        ]
        self.assert_authentication_required(urls)
        self.assert_paginated_list_contract(urls)

        Workspace.objects.filter(pk=self.workspace.pk).update(mode=Workspace.Mode.GARDEN)
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
