"""Germination rate is reported with whether it is a finished figure.

The report exists because a rate that can still rise is not a result. Every
assertion here is about that distinction surviving into the payload an operator
or an export actually reads.
"""

# Test method names carry the contract.
# pylint: disable=missing-function-docstring

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from plantings.germination import close_germination
from plantings.models import CohortOperation
from tests.factories import (
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_cell_planting,
    make_seed_tray_planting,
    make_specific_plant,
)
from workspaces.models import get_current_workspace


LossCause = CohortOperation.LossCause


class GerminationReportTests(APITestCase):
    """One sowing of ten seeds, three of which came up."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = self.workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        self.user = get_user_model().objects.create_user(username='germination-reporter')
        self.client.force_authenticate(self.user)
        self.tray = make_seed_tray(workspace=self.workspace)
        self.cell = make_seed_tray_cell(tray=self.tray)
        self.sowing = make_seed_tray_planting(
            workspace=self.workspace, seed_tray=self.tray, quantity=10,
        )
        self.allocation = make_seed_tray_cell_planting(
            seed_tray_planting=self.sowing, cell=self.cell, quantity=10,
        )
        for _index in range(3):
            make_specific_plant(
                workspace=self.workspace, cell_planting=self.allocation,
            )

    def close(self):
        return close_germination(
            self.sowing, self.user, loss_cause=LossCause.FAILED,
            reason='The window has passed.',
        )

    def test_an_open_sowing_reports_a_provisional_rate(self):
        response = self.client.get('/reports/germination/')
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data['results'][0]
        self.assertEqual(row['sowing_id'], self.sowing.pk)
        self.assertEqual(row['observed_count'], 3)
        self.assertEqual(row['germination_rate'], '0.300000')
        self.assertTrue(row['provisional'])
        self.assertIsNone(response.data['totals']['final_germination_rate'])

    def test_the_provisional_rate_is_published_as_a_data_quality_warning(self):
        response = self.client.get('/reports/germination/')
        codes = [entry['code'] for entry in response.data['data_quality']]
        self.assertIn('provisional_germination_rate', codes)

    def test_a_closed_sowing_reports_a_final_rate_and_its_remainder(self):
        self.close()
        response = self.client.get('/reports/germination/')
        row = response.data['results'][0]
        self.assertFalse(row['provisional'])
        self.assertEqual(row['ungerminated'], 7)
        self.assertEqual(row['loss_cause'], LossCause.FAILED)
        totals = response.data['totals']
        self.assertEqual(totals['final_germination_rate'], '0.300000')
        self.assertEqual(totals['closed_sowings'], 1)

    def test_the_variety_total_carries_the_sample_size_behind_it(self):
        self.close()
        response = self.client.get('/reports/germination/')
        by_variety = response.data['totals']['by_variety']
        self.assertEqual(len(by_variety), 1)
        self.assertEqual(by_variety[0]['sowings'], 1)
        self.assertEqual(by_variety[0]['closed_sowings'], 1)
        self.assertEqual(by_variety[0]['final_germination_rate'], '0.300000')

    def test_the_provisional_filter_narrows_to_the_unfinished_sowings(self):
        self.close()
        response = self.client.get('/reports/germination/', {'provisional': 'true'})
        self.assertEqual(response.data['results'], [])

    def test_the_export_renders_the_same_report_as_csv(self):
        response = self.client.get('/reports/germination/export/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('germination_rate', response.content.decode())

    def test_the_batch_report_marks_its_germination_rate_provisional(self):
        response = self.client.get('/reports/production-batches/', {
            'batch': self.sowing.batch_id,
        })
        row = response.data['results'][0]
        self.assertTrue(row['germination_provisional'])
        self.assertEqual(row['germination_sown'], 10)
        self.assertEqual(row['germination_observed'], 3)
        self.assertEqual(row['germination_rate'], '0.300000')

    def test_the_batch_report_stops_marking_it_provisional_once_closed(self):
        self.close()
        response = self.client.get('/reports/production-batches/', {
            'batch': self.sowing.batch_id,
        })
        row = response.data['results'][0]
        self.assertFalse(row['germination_provisional'])
        self.assertEqual(row['germination_ungerminated'], 7)

    def test_ungerminated_seed_stays_out_of_the_plant_loss_totals(self):
        self.close()
        response = self.client.get('/reports/production-batches/', {
            'batch': self.sowing.batch_id,
        })
        row = response.data['results'][0]
        self.assertEqual(row['loss_quantity'], 0)
        self.assertEqual(row['germination_ungerminated'], 7)
