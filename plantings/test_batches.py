"""Tests for the production batch lifecycle services."""
# pylint: disable=duplicate-code
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone as datetime_timezone

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature

from tests.factories import (
    make_batch_for_packet,
    make_garden_square_sowing,
    make_seed_packet,
    make_seed_tray_cell_planting,
    make_seed_tray_planting,
    make_specific_plant,
    make_specific_plant_location,
)
from workspaces.models import Workspace, get_current_workspace

from .batches import (
    BatchRequest,
    activate_batch,
    batch_final_outcome_count,
    batch_lifecycle_counts,
    batch_plants_with_active_location,
    batch_seeds_sown,
    batch_unresolved_plant_ids,
    cancel_batch,
    complete_batch,
    create_and_activate_batch,
    create_batch,
    finalize_batch_output,
    reopen_batch,
    validate_batch_for_sowing,
)
from .lifecycle import (
    EventType,
    OutcomeRequest,
    record_lifecycle_event,
    reverse_lifecycle_event,
)
from .models import ProductionBatch


class BatchLifecycleTests(TestCase):
    """Batch statuses only change through explicit, audited operations."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='batch-operator')
        self.workspace = get_current_workspace()
        self.packet = make_seed_packet()

    def _planned_batch(self, **overrides):
        """Create one planned batch owned by the current workspace."""
        values = {
            'code': 'BATCH-1',
            'variety': self.packet.seeds.plant_variety,
            'notes': 'Spring sowing',
        }
        values.update(overrides)
        return create_batch(self.workspace, self.user, BatchRequest(**values))

    def _closed_direct_sow_batch(self):
        """Return an active batch whose single direct sowing is closed."""
        batch = make_batch_for_packet(self.packet)
        make_garden_square_sowing(
            seeds_used=self.packet,
            batch=batch,
            removed=True,
        )
        return batch

    def _statuses(self, batch):
        """Return the recorded transition history as status pairs."""
        return [
            (transition.previous_status, transition.new_status)
            for transition in batch.transitions.all()
        ]

    def test_created_batch_is_planned_and_records_its_creation(self):
        """A standalone batch starts planned with an opening transition."""
        batch = self._planned_batch()

        self.assertEqual(batch.status, ProductionBatch.Status.PLANNED)
        self.assertIsNone(batch.actual_start)
        self.assertEqual(batch.created_by, self.user)
        self.assertEqual(self._statuses(batch), [('', ProductionBatch.Status.PLANNED)])

    def test_activation_records_a_supplied_or_current_start(self):
        """Activating stamps the supplied actual start when one is given."""
        supplied = datetime(2026, 3, 1, 9, 0, tzinfo=datetime_timezone.utc)

        batch = activate_batch(self._planned_batch(), self.user, actual_start=supplied)

        self.assertEqual(batch.status, ProductionBatch.Status.ACTIVE)
        self.assertEqual(batch.actual_start, supplied)
        self.assertEqual(
            self._statuses(batch),
            [
                ('', ProductionBatch.Status.PLANNED),
                (ProductionBatch.Status.PLANNED, ProductionBatch.Status.ACTIVE),
            ],
        )

    def test_activation_defaults_to_now_and_rejects_repeat_activation(self):
        """Only a planned batch can be activated, and only once."""
        batch = activate_batch(self._planned_batch(), self.user)

        self.assertIsNotNone(batch.actual_start)
        with self.assertRaisesMessage(ValidationError, 'cannot be activated'):
            activate_batch(batch, self.user)

    def test_inline_creation_activates_atomically(self):
        """An inline batch is created and started in one operation."""
        planted = datetime(2026, 4, 2, 7, 30, tzinfo=datetime_timezone.utc)

        batch = create_and_activate_batch(
            self.workspace,
            self.user,
            BatchRequest(code='INLINE-1', variety=self.packet.seeds.plant_variety),
            actual_start=planted,
        )

        self.assertEqual(batch.status, ProductionBatch.Status.ACTIVE)
        self.assertEqual(batch.actual_start, planted)
        self.assertEqual(batch.transitions.count(), 2)

    def test_output_finalization_requires_a_closed_sowing(self):
        """Output cannot be finalized while any sowing activity is open."""
        batch = make_batch_for_packet(self.packet)
        with self.assertRaisesMessage(ValidationError, 'at least one sowing'):
            finalize_batch_output(batch, self.user)

        sowing = make_garden_square_sowing(seeds_used=self.packet, batch=batch)
        with self.assertRaisesMessage(ValidationError, 'Close every sowing activity'):
            finalize_batch_output(batch, self.user)

        sowing.removed = True
        sowing.save(update_fields=['removed'])
        finalized = finalize_batch_output(batch, self.user)

        self.assertEqual(finalized.status, ProductionBatch.Status.OUTPUT_FINALIZED)
        self.assertIsNotNone(finalized.output_finalized_at)

    def test_direct_sow_batches_complete_after_output_finalization(self):
        """A batch with no individual outputs completes cleanly."""
        batch = finalize_batch_output(self._closed_direct_sow_batch(), self.user)

        completed = complete_batch(batch, self.user)

        self.assertEqual(completed.status, ProductionBatch.Status.COMPLETED)
        self.assertIsNotNone(completed.completed_at)

    def test_completion_reports_observed_plants_as_unmet_conditions(self):
        """Observed plants block completion until their outcomes exist."""
        batch = make_batch_for_packet(self.packet)
        sowing = make_seed_tray_planting(
            seeds_used=self.packet,
            batch=batch,
            removed=True,
        )
        cell_planting = make_seed_tray_cell_planting(seed_tray_planting=sowing)
        plants = [
            make_specific_plant(cell_planting=cell_planting),
            make_specific_plant(cell_planting=cell_planting),
        ]
        finalize_batch_output(batch, self.user)

        with self.assertRaises(ValidationError) as caught:
            complete_batch(batch, self.user)

        message = caught.exception.message_dict['detail'][0]
        self.assertIn('2 observed plants', message)
        for plant in plants:
            self.assertIn(str(plant.pk), message)
        batch.refresh_from_db()
        self.assertEqual(batch.status, ProductionBatch.Status.OUTPUT_FINALIZED)

    def test_ended_locations_are_not_treated_as_dispositions(self):
        """A plant whose location ended still has no final outcome."""
        batch = make_batch_for_packet(self.packet)
        sowing = make_seed_tray_planting(
            seeds_used=self.packet,
            batch=batch,
            removed=True,
        )
        cell_planting = make_seed_tray_cell_planting(seed_tray_planting=sowing)
        plant = make_specific_plant(cell_planting=cell_planting)
        plant.locations.create(
            location_type='seed_tray_cell',
            seed_tray_cell=cell_planting.cell,
            started=datetime(2026, 4, 1, 8, 0, tzinfo=datetime_timezone.utc),
            ended=datetime(2026, 5, 1, 8, 0, tzinfo=datetime_timezone.utc),
        )
        finalize_batch_output(batch, self.user)

        with self.assertRaisesMessage(ValidationError, '1 observed plants'):
            complete_batch(batch, self.user)

    def test_completion_requires_output_finalization_first(self):
        """An active batch cannot skip straight to completion."""
        with self.assertRaisesMessage(ValidationError, 'cannot be completed'):
            complete_batch(self._closed_direct_sow_batch(), self.user)

    def test_planned_batches_cancel_directly_with_a_reason(self):
        """Cancelling a planned batch needs only an audit reason."""
        batch = self._planned_batch()

        with self.assertRaisesMessage(ValidationError, 'A reason is required.'):
            cancel_batch(batch, self.user, '  ')

        cancelled = cancel_batch(batch, self.user, 'Seed order fell through')

        self.assertEqual(cancelled.status, ProductionBatch.Status.CANCELLED)
        self.assertIsNotNone(cancelled.cancelled_at)
        self.assertEqual(
            cancelled.transitions.last().reason,
            'Seed order fell through',
        )

    def test_active_cancellation_requires_closed_sowings_and_no_plants(self):
        """A batch that produced plants records an outcome instead of vanishing."""
        batch = make_batch_for_packet(self.packet)
        sowing = make_seed_tray_planting(seeds_used=self.packet, batch=batch)

        with self.assertRaisesMessage(ValidationError, 'Close every sowing activity'):
            cancel_batch(batch, self.user, 'Nothing came up')

        sowing.removed = True
        sowing.save(update_fields=['removed'])
        cell_planting = make_seed_tray_cell_planting(seed_tray_planting=sowing)
        make_specific_plant(cell_planting=cell_planting)

        with self.assertRaisesMessage(ValidationError, 'cannot be cancelled'):
            cancel_batch(batch, self.user, 'Nothing came up')

    def test_zero_germination_batches_may_cancel_once_closed(self):
        """A closed sowing with no germination is a legitimate cancellation."""
        cancelled = cancel_batch(
            self._closed_direct_sow_batch(),
            self.user,
            'Zero germination',
        )

        self.assertEqual(cancelled.status, ProductionBatch.Status.CANCELLED)

    def test_reopening_steps_back_and_clears_superseded_timestamps(self):
        """Each reopen is one audited correction that retains its history."""
        batch = complete_batch(
            finalize_batch_output(self._closed_direct_sow_batch(), self.user),
            self.user,
        )

        with self.assertRaisesMessage(ValidationError, 'A reason is required.'):
            reopen_batch(batch, self.user, '')

        reopened = reopen_batch(batch, self.user, 'Completed by mistake')
        self.assertEqual(reopened.status, ProductionBatch.Status.OUTPUT_FINALIZED)
        self.assertIsNone(reopened.completed_at)
        self.assertIsNotNone(reopened.output_finalized_at)

        reopened = reopen_batch(reopened, self.user, 'More seedlings expected')
        self.assertEqual(reopened.status, ProductionBatch.Status.ACTIVE)
        self.assertIsNone(reopened.output_finalized_at)
        self.assertIsNotNone(reopened.actual_start)
        self.assertEqual(reopened.transitions.count(), 5)

    def test_reopening_a_cancelled_batch_restores_its_started_state(self):
        """A cancelled batch returns to active when it had already started."""
        started = reopen_batch(
            cancel_batch(self._closed_direct_sow_batch(), self.user, 'Mistake'),
            self.user,
            'Cancelled the wrong batch',
        )
        self.assertEqual(started.status, ProductionBatch.Status.ACTIVE)
        self.assertIsNone(started.cancelled_at)

        never_started = reopen_batch(
            cancel_batch(self._planned_batch(code='BATCH-2'), self.user, 'Mistake'),
            self.user,
            'Cancelled the wrong batch',
        )
        self.assertEqual(never_started.status, ProductionBatch.Status.PLANNED)
        self.assertIsNone(never_started.actual_start)

    def test_active_batches_cannot_be_reopened(self):
        """Reopening is a correction, not a generic status edit."""
        with self.assertRaisesMessage(ValidationError, 'cannot be reopened'):
            reopen_batch(make_batch_for_packet(self.packet), self.user, 'No reason')

    def test_seeds_sown_totals_every_attached_sowing(self):
        """The sown total spans direct-sow and tray sowings alike."""
        batch = make_batch_for_packet(self.packet)
        make_garden_square_sowing(seeds_used=self.packet, batch=batch, quantity=3)
        make_seed_tray_planting(seeds_used=self.packet, batch=batch, quantity=4)

        self.assertEqual(batch_seeds_sown(batch), 7)

    def test_plants_without_any_location_are_not_counted_as_housed(self):
        """Only an open interval means a plant currently occupies a place."""
        batch = make_batch_for_packet(self.packet)
        sowing = make_seed_tray_planting(seeds_used=self.packet, batch=batch)
        cell_planting = make_seed_tray_cell_planting(seed_tray_planting=sowing)
        housed = make_specific_plant(cell_planting=cell_planting)
        make_specific_plant_location(specific_plant=housed)
        ended = make_specific_plant(cell_planting=cell_planting)
        make_specific_plant_location(
            specific_plant=ended,
            started=datetime(2026, 4, 1, 8, 0, tzinfo=datetime_timezone.utc),
            ended=datetime(2026, 5, 1, 8, 0, tzinfo=datetime_timezone.utc),
        )
        make_specific_plant(cell_planting=cell_planting)

        housed_plants = batch_plants_with_active_location(batch)

        self.assertEqual([plant.pk for plant in housed_plants], [housed.pk])
        self.assertEqual(len(batch_unresolved_plant_ids(batch)), 3)

    def test_sowings_only_attach_to_a_compatible_active_batch(self):
        """Workspace, status, and variety must all agree before attaching."""
        planned = self._planned_batch(code='BATCH-3')
        with self.assertRaisesMessage(ValidationError, 'only join an active batch'):
            validate_batch_for_sowing(planned, self.packet, self.workspace)

        other_variety_batch = make_batch_for_packet(make_seed_packet())
        with self.assertRaisesMessage(ValidationError, 'different plant variety'):
            validate_batch_for_sowing(
                other_variety_batch,
                self.packet,
                self.workspace,
            )

        validate_batch_for_sowing(
            make_batch_for_packet(self.packet),
            self.packet,
            self.workspace,
        )


class BatchPlantResolutionTests(TestCase):
    """Batch completion follows the plants' recorded lifecycle outcomes."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='resolver')
        self.packet = make_seed_packet()

    def _finalized_batch_with_plants(self, count):
        """Return an output-finalized batch and the plants it raised."""
        batch = make_batch_for_packet(self.packet)
        sowing = make_seed_tray_planting(
            seeds_used=self.packet,
            batch=batch,
            removed=True,
        )
        cell_planting = make_seed_tray_cell_planting(seed_tray_planting=sowing)
        plants = [
            make_specific_plant(cell_planting=cell_planting)
            for _ in range(count)
        ]
        finalize_batch_output(batch, self.user)
        return batch, plants

    def test_recorded_outcomes_resolve_plants_and_unblock_completion(self):
        """Every recorded final outcome clears one plant from the blockers."""
        batch, (failed, retained) = self._finalized_batch_with_plants(2)

        record_lifecycle_event(failed, self.user, OutcomeRequest(EventType.FAILED))
        self.assertEqual(batch_unresolved_plant_ids(batch), [retained.pk])
        self.assertEqual(batch_final_outcome_count(batch), 1)

        record_lifecycle_event(retained, self.user, OutcomeRequest(EventType.RETAINED))
        self.assertEqual(batch_unresolved_plant_ids(batch), [])
        self.assertEqual(batch_final_outcome_count(batch), 2)
        self.assertEqual(
            batch_lifecycle_counts(batch),
            {
                'growing': 0,
                'available': 0,
                'retained': 1,
                'donated': 0,
                'failed': 1,
                'culled': 0,
                'harvested': 0,
            },
        )

        completed = complete_batch(batch, self.user)
        self.assertEqual(completed.status, ProductionBatch.Status.COMPLETED)

    def test_a_reversed_outcome_blocks_completion_again(self):
        """Correcting a mistaken failure returns the plant to the blockers."""
        batch, (plant,) = self._finalized_batch_with_plants(1)

        failure = record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.FAILED))
        self.assertEqual(batch_unresolved_plant_ids(batch), [])

        reverse_lifecycle_event(failure, self.user, 'Recorded against the wrong plant.')
        self.assertEqual(batch_unresolved_plant_ids(batch), [plant.pk])
        with self.assertRaisesMessage(ValidationError, '1 observed plants'):
            complete_batch(batch, self.user)


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentBatchTransitionTests(TransactionTestCase):
    """A locked batch admits only one lifecycle transition at a time."""

    def _post_teardown(self):
        """Restore migration seed data removed by transactional test flushing."""
        super()._post_teardown()
        if not Workspace.objects.filter(pk=settings.CURRENT_WORKSPACE_ID).exists():
            Workspace.objects.create(
                pk=settings.CURRENT_WORKSPACE_ID,
                name='My Garden',
            )

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='batch-racer')
        packet = make_seed_packet()
        batch = make_batch_for_packet(packet)
        make_garden_square_sowing(seeds_used=packet, batch=batch, removed=True)
        self.batch_pk = batch.pk

    def _finalize_output(self):
        """Attempt output finalization from an independent connection."""
        close_old_connections()
        batch = ProductionBatch.objects.get(pk=self.batch_pk)
        user = get_user_model().objects.get(pk=self.user.pk)
        try:
            finalize_batch_output(batch, user)
        except ValidationError:
            result = 'rejected'
        else:
            result = 'finalized'
        close_old_connections()
        return result

    def test_only_one_concurrent_finalization_succeeds(self):
        """The loser is rejected instead of appending a second transition."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(
                future.result()
                for future in [
                    pool.submit(self._finalize_output),
                    pool.submit(self._finalize_output),
                ]
            )

        self.assertEqual(results, ['finalized', 'rejected'])
        batch = ProductionBatch.objects.get(pk=self.batch_pk)
        self.assertEqual(batch.status, ProductionBatch.Status.OUTPUT_FINALIZED)
        self.assertEqual(
            batch.transitions.filter(
                new_status=ProductionBatch.Status.OUTPUT_FINALIZED,
            ).count(),
            1,
        )
