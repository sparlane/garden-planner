"""The assumption-variance report, its filters, and its CSV export.

The report exists so a planning figure and the crop it sized are read side by
side. Every assertion here is about that comparison surviving into the payload
an operator or a spreadsheet actually reads — including the parts that say how
much evidence is behind it.
"""

# Test method names carry the contract.
# pylint: disable=missing-function-docstring

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from plantings.germination import close_germination
from plantings.models import CohortOperation
from tests.factories import (
    make_plant_variety,
    make_planning_assumption,
    make_planning_stage_assumption,
    make_production_batch,
    make_seed_packet,
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_cell_planting,
    make_seed_tray_planting,
    make_seeds,
    make_specific_plant,
)
from workspaces.models import get_current_workspace


class AssumptionVarianceReportTests(APITestCase):
    """One assumption of 0.85 against ten seeds that produced five plants."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = self.workspace.Mode.NURSERY
        self.workspace.assumption_minimum_samples = 1
        self.workspace.save(update_fields=['mode', 'assumption_minimum_samples'])
        self.user = get_user_model().objects.create_user(username='assumption-reader')
        self.client.force_authenticate(self.user)
        self.variety = make_plant_variety(workspace=self.workspace)
        self.assumption = make_planning_assumption(variety=self.variety)
        make_planning_stage_assumption(assumption=self.assumption)

    def sow(self, *, observed=5, close=True):
        packet = make_seed_packet(seeds=make_seeds(
            plant_variety=self.variety, workspace=self.workspace,
        ))
        planted = timezone.make_aware(timezone.datetime(2026, 2, 1, 9, 0))
        tray = make_seed_tray(workspace=self.workspace)
        sowing = make_seed_tray_planting(
            workspace=self.workspace, seeds_used=packet, seed_tray=tray, quantity=10,
            planted=planted,
            batch=make_production_batch(
                workspace=self.workspace, variety=self.variety, actual_start=planted,
            ),
        )
        allocation = make_seed_tray_cell_planting(
            seed_tray_planting=sowing, cell=make_seed_tray_cell(tray=tray), quantity=10,
        )
        for _index in range(observed):
            make_specific_plant(workspace=self.workspace, cell_planting=allocation)
        if close:
            close_germination(
                sowing, self.user, loss_cause=CohortOperation.LossCause.FAILED,
                reason='The window has passed.',
            )
        return sowing

    def test_the_report_sets_the_observed_rate_beside_the_assumed_one(self):
        self.sow()

        response = self.client.get('/reports/assumption-variance/')

        self.assertEqual(response.status_code, 200, response.data)
        row = response.data['results'][0]
        self.assertEqual(row['assumption_id'], self.assumption.pk)
        self.assertEqual(row['assumed_germination_rate'], '0.850000')
        self.assertEqual(row['observed_germination_rate'], '0.500000')
        self.assertEqual(row['germination_variance'], '-0.350000')
        self.assertEqual(row['batches'], 1)
        self.assertTrue(row['diverged'])

    def test_a_divergence_is_published_as_a_data_quality_finding(self):
        self.sow()

        response = self.client.get('/reports/assumption-variance/')

        codes = [entry['code'] for entry in response.data['data_quality']]
        self.assertIn('diverged_assumption', codes)
        self.assertEqual(response.data['totals']['diverged_assumptions'], 1)

    def test_a_thin_sample_is_published_rather_than_flagged(self):
        self.workspace.assumption_minimum_samples = 5
        self.workspace.save(update_fields=['assumption_minimum_samples'])
        self.sow()

        response = self.client.get('/reports/assumption-variance/')

        codes = [entry['code'] for entry in response.data['data_quality']]
        self.assertIn('thin_assumption_sample', codes)
        self.assertNotIn('diverged_assumption', codes)
        self.assertFalse(response.data['results'][0]['sample_sufficient'])

    def test_an_open_sowing_is_reported_as_left_out_of_the_rate(self):
        self.sow(close=False)

        response = self.client.get('/reports/assumption-variance/')

        row = response.data['results'][0]
        self.assertIsNone(row['observed_germination_rate'])
        self.assertEqual(row['germination_open_sowings'], 1)
        codes = [entry['code'] for entry in response.data['data_quality']]
        self.assertIn('provisional_germination_rate', codes)

    def test_the_diverged_filter_narrows_to_the_assumptions_worth_revising(self):
        self.sow()
        make_planning_assumption(variety=make_plant_variety(workspace=self.workspace))

        every = self.client.get('/reports/assumption-variance/')
        flagged = self.client.get('/reports/assumption-variance/?diverged=true')

        self.assertEqual(every.data['count'], 2)
        self.assertEqual(flagged.data['count'], 1)
        self.assertEqual(flagged.data['results'][0]['assumption_id'], self.assumption.pk)

    def test_an_unknown_filter_is_rejected_rather_than_silently_widening_it(self):
        response = self.client.get('/reports/assumption-variance/?varietty=1')

        self.assertEqual(response.status_code, 400)
        self.assertIn('varietty', response.data)

    def test_the_export_carries_the_same_columns_as_the_screen(self):
        self.sow()

        response = self.client.get('/reports/assumption-variance/export/')

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('assumption-variance', body)
        self.assertIn('observed_germination_rate', body)
        self.assertIn('0.500000', body)
        self.assertEqual(
            response['Content-Disposition'],
            'attachment; filename="assumption-variance-v1.csv"',
        )
