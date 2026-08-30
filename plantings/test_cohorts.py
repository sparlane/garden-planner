"""Cohort quantity, lineage, promotion, and REST contract tests."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from tests.api import RESTContractTestCase
from tests.factories import make_location, make_production_batch, make_seed_tray_planting
from workspaces.models import Workspace

from .cohorts import change_cohort, merge_cohorts, observe_cohort, promote_cohort, split_cohort
from .models import (
    CohortEvent,
    CohortOperation,
    GrowthStage,
    PlantCohort,
    PlantGrade,
    PlantLifecycleEvent,
)


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


class CohortLossCauseTests(TestCase):
    """A cohort loss says why in the same vocabulary the plant events use."""

    def setUp(self):
        """Build one cohort large enough to lose stock four separate ways."""
        self.workspace = Workspace.objects.get()
        self.batch = make_production_batch()
        self.cohort, _operation = observe_cohort(
            self.workspace, None, batch=self.batch, quantity=20,
            idempotency_key=uuid4(),
        )

    def lose(self, cause, quantity=1, **overrides):
        """Take one loss for a stated cause off the current revision."""
        self.cohort.refresh_from_db()
        values = {
            'cohort_id': self.cohort.pk,
            'expected_revision': self.cohort.revision,
            'action': CohortOperation.Action.LOSS,
            'loss_cause': cause,
            'idempotency_key': uuid4(),
            'reason': 'Counted off the bench.',
            'quantity': quantity,
        }
        values.update(overrides)
        return change_cohort(self.workspace, None, **values)

    def test_every_plant_loss_event_has_a_cohort_cause_that_records_it(self):
        """The four ways a plant is lost are the four a cohort can be."""
        for cause in CohortOperation.LossCause:
            if cause == CohortOperation.LossCause.UNSPECIFIED:
                continue
            with self.subTest(cause=cause):
                _cohort, operation = self.lose(cause, quantity=2)
                self.assertEqual(operation.loss_cause, cause)
        self.cohort.refresh_from_db()
        self.assertEqual(self.cohort.quantity, 12)
        self.assertEqual(
            sorted(
                CohortOperation.objects.filter(
                    action=CohortOperation.Action.LOSS,
                ).values_list('loss_cause', flat=True)
            ),
            ['culled', 'donated', 'failed', 'lost'],
        )

    def test_a_loss_without_a_cause_is_refused(self):
        """Free text alone is what this field exists to stop being the record."""
        with self.assertRaisesMessage(ValidationError, 'A loss needs a recorded cause.'):
            self.lose(None)
        self.cohort.refresh_from_db()
        self.assertEqual(self.cohort.quantity, 20)

    def test_unspecified_describes_history_and_not_a_new_loss(self):
        """The backfill value is storable but never recordable."""
        with self.assertRaisesMessage(ValidationError, 'Unspecified only describes'):
            self.lose(CohortOperation.LossCause.UNSPECIFIED)
        self.cohort.refresh_from_db()
        self.assertEqual(self.cohort.quantity, 20)

    def test_only_a_loss_carries_a_cause(self):
        """A cause on a move would be a second, unreadable meaning for it."""
        self.cohort.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, 'Only a loss carries a cause.'):
            change_cohort(
                self.workspace, None, cohort_id=self.cohort.pk,
                expected_revision=self.cohort.revision,
                action=CohortOperation.Action.READY,
                loss_cause=CohortOperation.LossCause.FAILED,
                idempotency_key=uuid4(),
            )

    def test_a_replay_carrying_a_different_cause_is_different_work(self):
        """The stored cause is part of the fact the idempotency key stands for."""
        _cohort, operation = self.lose(CohortOperation.LossCause.FAILED, quantity=3)
        replayed, same = self.lose(
            CohortOperation.LossCause.FAILED, quantity=3,
            idempotency_key=operation.idempotency_key,
            expected_revision=operation.payload['expected_revision'],
        )
        self.assertEqual(same.pk, operation.pk)
        self.assertEqual(replayed.quantity, 17)
        with self.assertRaisesMessage(ValidationError, 'already used for different work'):
            self.lose(
                CohortOperation.LossCause.CULLED, quantity=3,
                idempotency_key=operation.idempotency_key,
                expected_revision=operation.payload['expected_revision'],
            )


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

    def test_loss_cause_reaches_the_history_the_filter_and_the_report(self):
        """One structured cause serves the detail history and the register filter."""
        observed = self.client.post('/plantings/cohorts/observe/', {
            'batch': self.batch.pk,
            'quantity': 10,
            'location': self.location.pk,
            'idempotency_key': str(uuid4()),
        }, format='json')
        cohort_id = observed.data['pk']

        rejected = self.client.post(f'/plantings/cohorts/{cohort_id}/loss/', {
            'expected_revision': observed.data['revision'],
            'quantity': 2,
            'reason': 'Slugs.',
            'idempotency_key': str(uuid4()),
        }, format='json')
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertIn('loss_cause', rejected.data)

        lost = self.client.post(f'/plantings/cohorts/{cohort_id}/loss/', {
            'expected_revision': observed.data['revision'],
            'quantity': 2,
            'loss_cause': 'failed',
            'reason': 'Slugs.',
            'idempotency_key': str(uuid4()),
        }, format='json')
        self.assertEqual(lost.status_code, 200, lost.data)
        self.assertEqual(lost.data['quantity'], 8)
        history = {event['action']: event['loss_cause'] for event in lost.data['events']}
        self.assertEqual(history, {'observe': '', 'loss': 'failed'})

        matched = self.client.get('/plantings/cohorts/', {'loss_cause': 'failed'})
        self.assertEqual([row['pk'] for row in matched.data['results']], [cohort_id])
        self.assertEqual(matched.data['cohort_totals']['quantity'], 8)
        missed = self.client.get('/plantings/cohorts/', {'loss_cause': 'culled'})
        self.assertEqual(missed.data['results'], [])

    def test_unspecified_is_refused_through_the_action_endpoint(self):
        """The history-only value is not on offer to a screen either."""
        observed = self.client.post('/plantings/cohorts/observe/', {
            'batch': self.batch.pk,
            'quantity': 5,
            'idempotency_key': str(uuid4()),
        }, format='json')
        response = self.client.post(f'/plantings/cohorts/{observed.data["pk"]}/loss/', {
            'expected_revision': observed.data['revision'],
            'quantity': 1,
            'loss_cause': 'unspecified',
            'reason': 'Historic.',
            'idempotency_key': str(uuid4()),
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('loss_cause', response.data)

    def test_current_growth_facts_are_filterable_after_catalog_deactivation(self):
        """Historical catalog rows remain useful after operators retire a choice."""
        observed = self.client.post('/plantings/cohorts/observe/', {
            'batch': self.batch.pk,
            'quantity': 6,
            'location': self.location.pk,
            'idempotency_key': str(uuid4()),
        }, format='json')
        stage = GrowthStage.objects.get(workspace=self.workspace, code='rooted')
        grade = PlantGrade.objects.get(workspace=self.workspace, code='premium')
        stage.target_days = 1
        stage.save()
        growth = self.client.post('/plantings/nursery-observations/', {
            'cohort': observed.data['pk'],
            'stage': stage.pk,
            'grade': grade.pk,
            'expected_ready': timezone.localdate().isoformat(),
            'occurred_at': (timezone.now() - timedelta(days=2)).isoformat(),
        }, format='json')
        self.assertEqual(growth.status_code, 201, growth.data)
        stage.active = False
        stage.save()

        listed = self.client.get('/plantings/cohorts/', {
            'stage': stage.pk,
            'grade': grade.pk,
            'stage_overdue': 'true',
            'expected_ready_to': timezone.localdate().isoformat(),
        })
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(len(listed.data['results']), 1)
        self.assertEqual(listed.data['results'][0]['stage_name'], stage.name)
        self.assertEqual(listed.data['results'][0]['grade_name'], grade.name)


@skipUnlessDBFeature('has_select_for_update')
class CohortConcurrencyTests(TransactionTestCase):
    """A stale writer cannot spend the same anonymous quantity twice."""

    reset_sequences = True

    def _post_teardown(self):
        """Restore migration seed data removed by transactional test flushing."""
        super()._post_teardown()
        Workspace.objects.get_or_create(
            pk=settings.CURRENT_WORKSPACE_ID,
            defaults={'name': 'My Garden'},
        )

    def setUp(self):
        """Create one positive cohort whose initial revision both writers read."""
        self.workspace = Workspace.objects.get()
        batch = make_production_batch()
        self.cohort, _operation = observe_cohort(
            self.workspace,
            None,
            batch=batch,
            quantity=10,
            idempotency_key=uuid4(),
        )

    def split(self):
        """Attempt one concurrent split on an isolated database connection."""
        close_old_connections()
        try:
            split_cohort(
                self.workspace,
                None,
                cohort_id=self.cohort.pk,
                expected_revision=self.cohort.revision,
                quantity=6,
                idempotency_key=uuid4(),
                reason='Concurrent split.',
            )
            return True
        except ValidationError:
            return False
        finally:
            close_old_connections()

    def test_only_one_racing_split_uses_the_loaded_revision(self):
        """Row locking admits one writer and makes the other revision stale."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: self.split(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.cohort.refresh_from_db()
        self.assertEqual(self.cohort.quantity, 4)
        self.assertEqual(sum(PlantCohort.objects.values_list('quantity', flat=True)), 10)
