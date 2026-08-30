"""Production and traceability reports retain exact source identities."""

# Test method names carry the contract.
# pylint: disable=missing-function-docstring

from uuid import uuid4

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from plantings.cohorts import change_cohort, observe_cohort, promote_cohort
from plantings.lifecycle import EventType, OutcomeRequest, record_lifecycle_event
from plantings.models import CohortOperation
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

    def test_loss_totals_by_cause_span_a_batch_promoted_partway(self):
        """One batch's anonymous and identified halves answer in one vocabulary."""
        plant = make_specific_plant(workspace=self.workspace)
        batch = plant.batch
        cohort, _observed = observe_cohort(
            self.workspace, self.user, batch=batch, quantity=10,
            idempotency_key=uuid4(),
        )
        promoted, _promotion = promote_cohort(
            self.workspace, self.user, cohort_id=cohort.pk,
            expected_revision=cohort.revision, quantity=2,
            idempotency_key=uuid4(), reason='Label the best two.',
        )
        record_lifecycle_event(
            promoted[0], self.user,
            OutcomeRequest(EventType.CULLED, reason='Off type.'),
        )
        record_lifecycle_event(
            promoted[1], self.user,
            OutcomeRequest(EventType.LOST, reason='Not on the bench.'),
        )
        cohort.refresh_from_db()
        cohort, _loss = change_cohort(
            self.workspace, self.user, cohort_id=cohort.pk,
            expected_revision=cohort.revision,
            action=CohortOperation.Action.LOSS,
            loss_cause=CohortOperation.LossCause.CULLED,
            idempotency_key=uuid4(), reason='Same grading call.', quantity=3,
        )
        cohort.refresh_from_db()
        change_cohort(
            self.workspace, self.user, cohort_id=cohort.pk,
            expected_revision=cohort.revision,
            action=CohortOperation.Action.LOSS,
            loss_cause=CohortOperation.LossCause.FAILED,
            idempotency_key=uuid4(), reason='Damped off.', quantity=1,
        )

        response = self.client.get('/reports/production-batches/', {'batch': batch.pk})
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data['results'][0]
        self.assertEqual(row['loss_by_cause'], {
            'failed': 1, 'lost': 1, 'culled': 4, 'donated': 0, 'unspecified': 0,
        })
        self.assertEqual(row['loss_quantity'], 6)
        self.assertEqual(
            response.data['totals']['loss_by_cause'], row['loss_by_cause'],
        )
        self.assertEqual(response.data['totals']['loss_quantity'], 6)
        self.assertNotIn(
            'unspecified_loss_cause',
            [flag['code'] for flag in response.data['data_quality']],
        )

    def test_losses_recorded_before_the_cause_existed_are_counted_apart(self):
        """A backfilled loss reads as unspecified rather than as a finding."""
        plant = make_specific_plant(workspace=self.workspace)
        cohort, _observed = observe_cohort(
            self.workspace, self.user, batch=plant.batch, quantity=6,
            idempotency_key=uuid4(),
        )
        _changed, operation = change_cohort(
            self.workspace, self.user, cohort_id=cohort.pk,
            expected_revision=cohort.revision,
            action=CohortOperation.Action.LOSS,
            loss_cause=CohortOperation.LossCause.FAILED,
            idempotency_key=uuid4(), reason='Gone.', quantity=2,
        )
        CohortOperation.objects.filter(pk=operation.pk).update(
            loss_cause=CohortOperation.LossCause.UNSPECIFIED,
        )

        response = self.client.get('/reports/production-batches/', {
            'batch': plant.batch_id,
        })
        row = response.data['results'][0]
        self.assertEqual(row['loss_by_cause']['unspecified'], 2)
        self.assertEqual(row['loss_by_cause']['failed'], 0)
        flags = {flag['code']: flag for flag in response.data['data_quality']}
        self.assertIn('unspecified_loss_cause', flags)
        self.assertEqual(flags['unspecified_loss_cause']['count'], 2)

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
