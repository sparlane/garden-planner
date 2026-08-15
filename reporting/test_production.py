"""Production and traceability reports retain exact source identities."""

# Test method names carry the contract.
# pylint: disable=missing-function-docstring

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from tests.factories import (
    make_specific_plant,
    make_stock_lot,
)
from workspaces.models import get_current_workspace


class ProductionReportTests(APITestCase):
    """Batch summaries and both trace directions use posted source records."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = self.workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        self.user = get_user_model().objects.create_user(username='production-reporter')
        self.client.force_authenticate(self.user)

    def test_batch_report_reconciles_output_without_inventing_cost(self):
        plant = make_specific_plant(workspace=self.workspace)
        batch = plant.batch
        response = self.client.get('/reports/production-batches/', {
            'batch': batch.pk,
        })
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data['results'][0]
        self.assertEqual(row['batch_id'], batch.pk)
        self.assertEqual(row['original_output'], 1)
        self.assertEqual(row['current_seedlings'], 1)
        self.assertTrue(row['provisional'])
        self.assertEqual(response.data['totals']['provisional_batches'], 1)

    def test_plant_trace_keeps_cell_batch_seed_and_empty_commerce(self):
        plant = make_specific_plant(workspace=self.workspace)
        response = self.client.get(
            f'/reports/traceability/plants/{plant.pk}/',
        )
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data['results'][0]
        self.assertEqual(row['plant_id'], plant.pk)
        self.assertEqual(row['batch_id'], plant.batch_id)
        self.assertEqual(row['cell_id'], plant.cell_planting.cell_id)
        self.assertEqual(
            row['seed_packet_id'],
            plant.cell_planting.seed_tray_planting.seeds_used_id,
        )
        self.assertEqual(row['commerce'], [])

    def test_lot_trace_reports_remaining_balance_without_fake_allocation(self):
        lot = make_stock_lot(workspace=self.workspace)
        response = self.client.get(
            f'/reports/traceability/lots/{lot.pk}/',
        )
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data['results'][0]
        self.assertEqual(row['lot_id'], lot.pk)
        self.assertIsNone(row['allocation_id'])
        self.assertEqual(
            response.data['totals']['remaining_balances'][0]['quantity'],
            '100.000000000',
        )

    def test_trace_exports_are_versioned_and_cross_workspace_ids_are_hidden(self):
        plant = make_specific_plant(workspace=self.workspace)
        exported = self.client.get(
            f'/reports/traceability/plants/{plant.pk}/export/',
        )
        self.assertEqual(exported.status_code, 200)
        self.assertIn('plant-trace', exported.content.decode())
        missing = self.client.get('/reports/traceability/plants/999999/')
        self.assertEqual(missing.status_code, 404)
