"""Cohort quantity, lineage, promotion, and REST contract tests."""

from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from tests.api import RESTContractTestCase
from tests.factories import make_location, make_production_batch, make_seed_tray_planting
from workspaces.models import Workspace

from .cohorts import merge_cohorts, observe_cohort, promote_cohort, split_cohort
from .models import CohortEvent, PlantCohort, PlantLifecycleEvent


class CohortServiceTests(TestCase):
    """Structural operations reconcile quantities and retain source history."""

    def setUp(self):
        """Build one batch and tray sowing shared by service scenarios."""
        self.workspace = Workspace.objects.get()
        self.batch = make_production_batch()
        self.sowing = make_seed_tray_planting(batch=self.batch)

    def observe(self, quantity=12):
        """Create a cohort through the public service with a fresh identity."""
        cohort, _operation = observe_cohort(
            self.workspace,
            None,
            batch=self.batch,
            source_sowing=self.sowing,
            quantity=quantity,
            idempotency_key=uuid4(),
        )
        return cohort

    def test_split_and_merge_reconcile_and_preserve_lineage(self):
        """A split followed by a compatible merge neither invents nor loses stock."""
        source = self.observe()
        child, _split = split_cohort(
            self.workspace,
            None,
            cohort_id=source.pk,
            expected_revision=source.revision,
            quantity=5,
            idempotency_key=uuid4(),
            reason='Separate one sales block.',
        )
        source.refresh_from_db()
        self.assertEqual((source.quantity, child.quantity), (7, 5))
        self.assertEqual(list(child.events.get().source_cohorts.all()), [source])

        merged, _merge = merge_cohorts(
            self.workspace,
            None,
            target_id=source.pk,
            source_ids=[child.pk],
            revisions={str(source.pk): source.revision, str(child.pk): child.revision},
            idempotency_key=uuid4(),
            reason='Blocks are homogeneous again.',
        )
        child.refresh_from_db()
        self.assertEqual(merged.quantity, 12)
        self.assertEqual(child.quantity, 0)
        self.assertEqual(child.lifecycle_state, PlantCohort.LifecycleState.DEPLETED)

    def test_stale_revision_is_rejected_without_an_event(self):
        """A stale expected revision leaves both quantity and audit untouched."""
        cohort = self.observe()
        before = CohortEvent.objects.count()
        with self.assertRaises(ValidationError):
            split_cohort(
                self.workspace,
                None,
                cohort_id=cohort.pk,
                expected_revision=cohort.revision + 1,
                quantity=2,
                idempotency_key=uuid4(),
                reason='Stale request.',
            )
        self.assertEqual(CohortEvent.objects.count(), before)

    def test_promotion_creates_individual_ids_without_double_counting(self):
        """Promotion is idempotent and gives each plant the cohort's lineage."""
        cohort = self.observe(quantity=4)
        plants, operation = promote_cohort(
            self.workspace,
            None,
            cohort_id=cohort.pk,
            expected_revision=cohort.revision,
            quantity=3,
            idempotency_key=uuid4(),
            occurred_at=timezone.now(),
            reason='These plants need individual sale labels.',
        )
        cohort.refresh_from_db()
        self.assertEqual(cohort.quantity, 1)
        self.assertEqual(len(plants), 3)
        self.assertTrue(all(plant.promoted_from_cohort_id == cohort.pk for plant in plants))
        self.assertTrue(all(plant.batch_id == cohort.batch_id for plant in plants))
        self.assertEqual(
            PlantLifecycleEvent.objects.filter(plant__in=plants, event_type='germinated').count(),
            3,
        )

        replayed, same_operation = promote_cohort(
            self.workspace,
            None,
            cohort_id=cohort.pk,
            expected_revision=1,
            quantity=3,
            idempotency_key=operation.idempotency_key,
            occurred_at=operation.occurred_at,
            reason='These plants need individual sale labels.',
        )
        self.assertEqual(same_operation.pk, operation.pk)
        self.assertEqual({plant.pk for plant in replayed}, {plant.pk for plant in plants})


class CohortRESTTests(RESTContractTestCase):
    """The register is Nursery-only, scoped, and returns quantity totals."""

    def setUp(self):
        """Enable Nursery routes and build valid batch/location choices."""
        super().setUp()
        self.workspace = Workspace.objects.get()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        self.batch = make_production_batch()
        self.location = make_location()

    def test_observe_list_and_adjust_round_trip(self):
        """Observation, totals, and count reconciliation share one REST contract."""
        response = self.client.post('/plantings/cohorts/observe/', {
            'batch': self.batch.pk,
            'quantity': 9,
            'location': self.location.pk,
            'idempotency_key': str(uuid4()),
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        cohort_id = response.data['pk']

        listed = self.client.get('/plantings/cohorts/')
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data['cohort_totals']['quantity'], 9)
        self.assertEqual(listed.data['results'][0]['quantity'], 9)

        adjusted = self.client.post(f'/plantings/cohorts/{cohort_id}/adjust/', {
            'expected_revision': response.data['revision'],
            'quantity': 8,
            'reason': 'Physical count found one missing plant.',
            'idempotency_key': str(uuid4()),
        }, format='json')
        self.assertEqual(adjusted.status_code, 200, adjusted.data)
        self.assertEqual(adjusted.data['quantity'], 8)
        self.assertEqual(len(adjusted.data['events']), 2)

    def test_combined_availability_counts_promoted_plants_once(self):
        """Moving units to concrete IDs leaves the combined total unchanged."""
        observed = self.client.post('/plantings/cohorts/observe/', {
            'batch': self.batch.pk,
            'quantity': 9,
            'location': self.location.pk,
            'idempotency_key': str(uuid4()),
        }, format='json')
        cohort_id = observed.data['pk']
        ready = self.client.post(f'/plantings/cohorts/{cohort_id}/ready/', {
            'expected_revision': observed.data['revision'],
            'idempotency_key': str(uuid4()),
        }, format='json')
        promoted = self.client.post(f'/plantings/cohorts/{cohort_id}/promote/', {
            'expected_revision': ready.data['revision'],
            'quantity': 2,
            'reason': 'Give selected stock individual labels.',
            'idempotency_key': str(uuid4()),
        }, format='json')
        self.assertEqual(promoted.status_code, 200, promoted.data)

        availability = self.client.get('/plantings/cohorts/availability/', {
            'batch': self.batch.pk,
            'location': self.location.pk,
        })
        self.assertEqual(availability.data, {
            'cohort_quantity': 7,
            'individual_count': 2,
            'combined_total': 9,
        })
        register = self.client.get('/plantings/register/', {'batch': self.batch.pk})
        self.assertEqual(register.status_code, 200, register.data)
        self.assertEqual(register.data['count'], 2)
