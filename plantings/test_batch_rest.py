"""Tests for the production batch REST resources and lifecycle actions."""
# pylint: disable=duplicate-code
from tests.api import RESTContractTestCase
from tests.factories import (
    make_batch_for_packet,
    make_garden_square_sowing,
    make_plant_variety,
    make_seed_packet,
    make_seed_tray_cell_planting,
    make_seed_tray_planting,
    make_specific_plant,
    make_specific_plant_location,
)

from .models import ProductionBatch


class ProductionBatchRESTTests(RESTContractTestCase):
    """The batch API exposes identity, summaries, and explicit actions."""

    url = '/plantings/batches/'

    def setUp(self):
        super().setUp()
        self.packet = make_seed_packet()
        self.variety = self.packet.seeds.plant_variety

    def _create(self, **overrides):
        """Create one batch through the API and return its response data."""
        payload = {
            'code': 'BATCH-1',
            'variety': self.variety.pk,
            'planned_start': '2026-03-01',
            'notes': 'Spring sowing',
        }
        payload.update(overrides)
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def _post_action(self, batch_pk, action, payload=None):
        """Post one lifecycle action and return the response."""
        return self.client.post(
            f'{self.url}{batch_pk}/{action}/',
            payload or {},
            format='json',
        )

    def test_list_route_requires_authentication_and_returns_a_list(self):
        """Batches follow the common authenticated collection contract."""
        self.assert_authentication_required([self.url])
        self.assert_list_contract([self.url])

    def test_create_starts_a_planned_batch_with_its_history(self):
        """A created batch is planned and already carries a transition."""
        created = self._create()

        self.assertEqual(created['status'], ProductionBatch.Status.PLANNED)
        self.assertEqual(created['planned_start'], '2026-03-01')
        self.assertIsNone(created['actual_start'])

        detail = self.client.get(f'{self.url}{created["pk"]}/')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.data['transitions']), 1)
        self.assertEqual(detail.data['transitions'][0]['previous_status'], '')
        self.assertEqual(detail.data['variety_name'], self.variety.name)

    def test_duplicate_codes_are_rejected_inside_one_workspace(self):
        """Batch codes stay unique so they can identify a crop."""
        self._create()

        response = self.client.post(
            self.url,
            {'code': 'BATCH-1', 'variety': self.variety.pk},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('code', response.data)

    def test_generic_updates_edit_only_descriptive_fields(self):
        """Status and lifecycle timestamps are action-controlled."""
        created = self._create()

        response = self.client.patch(
            f'{self.url}{created["pk"]}/',
            {
                'code': 'BATCH-RENAMED',
                'planned_start': '2026-04-01',
                'notes': 'Delayed',
                'status': ProductionBatch.Status.COMPLETED,
                'actual_start': '2026-03-05T08:00:00Z',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 'BATCH-RENAMED')
        self.assertEqual(response.data['planned_start'], '2026-04-01')
        self.assertEqual(response.data['status'], ProductionBatch.Status.PLANNED)
        self.assertIsNone(response.data['actual_start'])

    def test_variety_is_editable_only_while_planned_and_unsown(self):
        """A batch's crop identity locks as soon as anything depends on it."""
        created = self._create()
        replacement = make_plant_variety()

        response = self.client.patch(
            f'{self.url}{created["pk"]}/',
            {'variety': replacement.pk},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['variety'], replacement.pk)

        batch = make_batch_for_packet(self.packet)
        make_garden_square_sowing(seeds_used=self.packet, batch=batch)
        blocked = self.client.patch(
            f'{self.url}{batch.pk}/',
            {'variety': replacement.pk},
            format='json',
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertIn('variety', blocked.data)

    def test_lifecycle_actions_walk_a_direct_sow_batch_to_completion(self):
        """Each action advances the batch and appends its transition."""
        created = self._create()
        activated = self._post_action(
            created['pk'],
            'activate',
            {'actual_start': '2026-03-05T08:00:00Z'},
        )
        self.assertEqual(activated.status_code, 200, activated.data)
        self.assertEqual(activated.data['status'], ProductionBatch.Status.ACTIVE)
        self.assertEqual(activated.data['actual_start'], '2026-03-05T08:00:00Z')

        sowing = make_garden_square_sowing(
            seeds_used=self.packet,
            batch=ProductionBatch.objects.get(pk=created['pk']),
            quantity=6,
            removed=True,
        )

        finalized = self._post_action(created['pk'], 'finalize-output')
        self.assertEqual(finalized.status_code, 200, finalized.data)
        self.assertEqual(
            finalized.data['status'],
            ProductionBatch.Status.OUTPUT_FINALIZED,
        )
        self.assertEqual(finalized.data['seeds_sown'], 6)
        self.assertEqual(finalized.data['sowing_count'], 1)
        self.assertEqual(finalized.data['sowings'][0]['pk'], sowing.pk)

        completed = self._post_action(created['pk'], 'complete')
        self.assertEqual(completed.status_code, 200, completed.data)
        self.assertEqual(completed.data['status'], ProductionBatch.Status.COMPLETED)
        self.assertEqual(len(completed.data['transitions']), 4)

    def test_output_finalization_reports_open_sowings(self):
        """The API explains which sowing activity still has to close."""
        batch = make_batch_for_packet(self.packet)
        sowing = make_garden_square_sowing(seeds_used=self.packet, batch=batch)

        response = self._post_action(batch.pk, 'finalize-output')

        self.assertEqual(response.status_code, 400)
        self.assertIn(str(sowing.pk), response.data['detail'][0])

    def test_completion_reports_observed_plants_until_outcomes_exist(self):
        """Observed plants are reported as unmet conditions, not guessed at."""
        batch = make_batch_for_packet(self.packet)
        sowing = make_seed_tray_planting(
            seeds_used=self.packet,
            batch=batch,
            removed=True,
        )
        cell_planting = make_seed_tray_cell_planting(seed_tray_planting=sowing)
        plant = make_specific_plant(cell_planting=cell_planting)
        self._post_action(batch.pk, 'finalize-output')

        response = self._post_action(batch.pk, 'complete')

        self.assertEqual(response.status_code, 400)
        self.assertIn(str(plant.pk), response.data['detail'][0])

    def test_cancel_and_reopen_require_reasons(self):
        """Audited corrections never happen without a stated reason."""
        created = self._create()

        blank = self._post_action(created['pk'], 'cancel', {'reason': '   '})
        self.assertEqual(blank.status_code, 400)
        self.assertIn('reason', blank.data)

        cancelled = self._post_action(
            created['pk'],
            'cancel',
            {'reason': 'Seed order fell through'},
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(cancelled.data['status'], ProductionBatch.Status.CANCELLED)

        missing = self._post_action(created['pk'], 'reopen', {})
        self.assertEqual(missing.status_code, 400)

        reopened = self._post_action(
            created['pk'],
            'reopen',
            {'reason': 'Cancelled the wrong batch'},
        )
        self.assertEqual(reopened.status_code, 200, reopened.data)
        self.assertEqual(reopened.data['status'], ProductionBatch.Status.PLANNED)
        self.assertIsNone(reopened.data['cancelled_at'])

    def test_invalid_transitions_are_rejected(self):
        """An action a status does not permit leaves the batch untouched."""
        created = self._create()

        response = self._post_action(created['pk'], 'complete')

        self.assertEqual(response.status_code, 400)
        self.assertIn('status', response.data)
        detail = self.client.get(f'{self.url}{created["pk"]}/')
        self.assertEqual(detail.data['status'], ProductionBatch.Status.PLANNED)
        self.assertEqual(len(detail.data['transitions']), 1)

    def test_summary_counts_are_reported_separately(self):
        """Seeds sown, plants observed, active locations, and outcomes differ."""
        batch = make_batch_for_packet(self.packet)
        sowing = make_seed_tray_planting(
            seeds_used=self.packet,
            batch=batch,
            quantity=2,
        )
        cell_planting = make_seed_tray_cell_planting(
            seed_tray_planting=sowing,
            quantity=2,
        )
        housed = make_specific_plant(cell_planting=cell_planting)
        make_specific_plant_location(specific_plant=housed)
        make_specific_plant(cell_planting=cell_planting)
        make_specific_plant(cell_planting=cell_planting)

        response = self.client.get(f'{self.url}{batch.pk}/')

        self.assertEqual(response.data['seeds_sown'], 2)
        self.assertEqual(response.data['plants_observed'], 3)
        self.assertEqual(response.data['plants_with_active_location'], 1)
        self.assertEqual(response.data['final_outcomes'], 0)
        self.assertEqual(len(response.data['unresolved_plants']), 3)
        self.assertEqual(len(response.data['current_locations']), 1)
        self.assertEqual(response.data['sowings'][0]['plants_observed'], 3)
        self.assertEqual(response.data['sowings'][0]['cells'][0]['quantity'], 2)

    def test_batches_are_filterable_by_status_variety_and_repair_state(self):
        """The batch screens can narrow a long list without extra endpoints."""
        active = make_batch_for_packet(self.packet)
        ProductionBatch.objects.filter(pk=active.pk).update(
            repair_state=ProductionBatch.RepairState.NEEDS_REPAIR,
        )
        self._create(code='BATCH-PLANNED')

        by_status = self.client.get(self.url, {'status': 'active'})
        self.assertEqual([row['pk'] for row in by_status.data], [active.pk])

        by_variety = self.client.get(self.url, {'variety': self.variety.pk})
        self.assertEqual(len(by_variety.data), 2)

        needs_repair = self.client.get(self.url, {'needs_repair': 'true'})
        self.assertEqual([row['pk'] for row in needs_repair.data], [active.pk])

        invalid = self.client.get(self.url, {'status': 'growing'})
        self.assertEqual(invalid.status_code, 400)

    def test_batches_cannot_be_deleted(self):
        """Cultivation identity is corrected through actions, not removal."""
        created = self._create()

        response = self.client.delete(f'{self.url}{created["pk"]}/')

        self.assertEqual(response.status_code, 405)
