"""Tests for quarantine, treatment, follow-up, and availability commands."""

# Test names describe behavior directly.
# pylint: disable=missing-function-docstring,duplicate-code

from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from work.models import WorkTask
from applications.models import InputApplicationTarget
from applications.services import (
    ApplicationRequest,
    LineRequest,
    TargetRequest,
    create_application_draft,
    post_application,
)
from inventory.ledger import IndividualizationRequest, individualize_lot_units
from inventory.units import UnitCode
from plantings.cohorts import change_cohort, correct_cohort_loss
from plantings.counted_fills import plant_counted_fill
from plantings.movement import move_specific_plant
from plantings.lifecycle import (
    EventType,
    LifecycleState,
    OutcomeRequest,
    plant_lifecycle_summary,
    record_germination_event,
    record_lifecycle_event,
)
from plantings.withdrawal import withdraw_germination
from plantings.models import CohortOperation, PlantCohort, SpecificPlantLocation
from plantings.register import RegisterFilters, register_queryset
from seedtrays.container_fills import open_counted_fill, open_numbered_fill
from seedtrays.models import SeedTrayGeneration
from tests.factories import (
    make_inventory_item,
    make_location,
    make_specific_plant,
    make_specific_plant_location,
    make_stock_lot,
)
from workspaces.models import Workspace, get_current_workspace

from .availability import case_is_active, is_quarantined
from .models import HealthObservation, HealthObservationType, QuarantineAction
from .operations import (
    act_on_quarantine,
    link_treatment,
    quarantine_observation,
    record_follow_up,
)
from .reports import health_report
from .services import preview_observation, record_observation


class HealthOperationTestCase(TestCase):
    """A nursery workspace with observation and quarantine helpers."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.observation_type = HealthObservationType.objects.get(
            workspace=self.workspace, code='pest-signs',
        )

    def observe(self, target_type, target):
        scopes = [{'type': target_type, 'id': target.pk}]
        preview = preview_observation(self.workspace, scopes)
        return record_observation(
            self.workspace, None, scopes=scopes,
            reviewed_digest=preview['digest'],
            observation_type=self.observation_type,
            severity=HealthObservation.Severity.HIGH,
            notes='Evidence confirmed.',
        )

    def quarantine(self, observation):
        return quarantine_observation(
            self.workspace, None, observation,
            idempotency_key=uuid4(), reason='Prevent spread while reviewed.',
        )[0]

    def returned_plant(self):
        """Return one plant a customer returned into quarantine."""
        plant = make_specific_plant(workspace=self.workspace)
        for event_type in (
                EventType.READY, EventType.SOLD, EventType.RETURNED_QUARANTINED):
            record_lifecycle_event(
                plant, None, OutcomeRequest(event_type, reason='Commerce.'),
            )
        return plant


class HealthOperationTests(HealthOperationTestCase):
    """Health constraints compose with lifecycle and cohort services."""

    def test_quarantine_changes_register_availability_without_lifecycle_change(self):
        plant = make_specific_plant(workspace=self.workspace)
        record_lifecycle_event(
            plant, None, OutcomeRequest(EventType.READY, reason='Ready for sale.'),
        )
        before = register_queryset(self.workspace, RegisterFilters()).get(pk=plant.pk)
        self.assertTrue(before.sellable)
        case = self.quarantine(self.observe('plant', plant))
        during = register_queryset(self.workspace, RegisterFilters()).get(pk=plant.pk)
        self.assertTrue(during.quarantined)
        self.assertFalse(during.sellable)
        self.assertEqual(during.lifecycle_state, 'available')
        act_on_quarantine(
            self.workspace, None, case,
            action_name=QuarantineAction.Action.RELEASE,
            idempotency_key=uuid4(), reason='Inspection found no remaining issue.',
        )
        after = register_queryset(self.workspace, RegisterFilters()).get(pk=plant.pk)
        self.assertFalse(after.quarantined)
        self.assertTrue(after.sellable)

    def test_releasing_an_overlay_quarantine_records_no_lifecycle_fact(self):
        plant = make_specific_plant(workspace=self.workspace)
        record_lifecycle_event(
            plant, None, OutcomeRequest(EventType.READY, reason='Ready for sale.'),
        )
        case = self.quarantine(self.observe('plant', plant))
        action = act_on_quarantine(
            self.workspace, None, case,
            action_name=QuarantineAction.Action.RELEASE,
            idempotency_key=uuid4(), reason='Inspection found no remaining issue.',
        )
        self.assertEqual(
            list(plant.lifecycle_events.values_list('event_type', flat=True)),
            [EventType.READY],
        )
        self.assertFalse(action.results.exists())

    def test_overlapping_cases_must_each_be_released(self):
        plant = make_specific_plant(workspace=self.workspace)
        observation = self.observe('plant', plant)
        first = self.quarantine(observation)
        second = self.quarantine(observation)
        act_on_quarantine(
            self.workspace, None, first,
            action_name='release', idempotency_key=uuid4(), reason='First issue resolved.',
        )
        self.assertTrue(is_quarantined(plant))
        act_on_quarantine(
            self.workspace, None, second,
            action_name='release', idempotency_key=uuid4(), reason='Second issue resolved.',
        )
        self.assertFalse(is_quarantined(plant))

    def test_releasing_a_returned_plant_records_its_recovery(self):
        plant = self.returned_plant()
        case = self.quarantine(self.observe('plant', plant))
        action = act_on_quarantine(
            self.workspace, None, case,
            action_name=QuarantineAction.Action.RELEASE,
            idempotency_key=uuid4(), reason='Reassessed as healthy stock.',
        )
        row = register_queryset(self.workspace, RegisterFilters()).get(pk=plant.pk)
        self.assertEqual(row.lifecycle_state, LifecycleState.AVAILABLE)
        self.assertTrue(row.sellable)
        self.assertFalse(row.quarantined)
        result = action.results.get()
        self.assertEqual(result.plant, plant)
        self.assertEqual(
            result.lifecycle_event.event_type, EventType.RELEASED_AVAILABLE,
        )
        self.assertEqual(
            result.lifecycle_event.reference, f'quarantine-action:{action.pk}',
        )
        self.assertEqual(result.lifecycle_event.reason, 'Reassessed as healthy stock.')

    def test_a_release_keeps_the_quarantine_it_resolved_on_the_record(self):
        plant = self.returned_plant()
        case = self.quarantine(self.observe('plant', plant))
        act_on_quarantine(
            self.workspace, None, case,
            action_name=QuarantineAction.Action.RELEASE,
            idempotency_key=uuid4(), reason='Reassessed as healthy stock.',
        )
        self.assertEqual(
            list(
                plant.lifecycle_events.order_by('occurred_at', 'pk')
                .values_list('event_type', flat=True)
            ),
            [
                EventType.READY,
                EventType.SOLD,
                EventType.RETURNED_QUARANTINED,
                EventType.RELEASED_AVAILABLE,
            ],
        )

    def test_culling_a_returned_plant_resolves_it_and_closes_its_location(self):
        plant = self.returned_plant()
        bench = make_location(workspace=self.workspace, location_type='quarantine')
        make_specific_plant_location(
            specific_plant=plant,
            location_type=SpecificPlantLocation.LOCATION,
            seed_tray_cell=None,
            location=bench,
        )
        case = self.quarantine(self.observe('plant', plant))
        action = act_on_quarantine(
            self.workspace, None, case,
            action_name=QuarantineAction.Action.CULL,
            idempotency_key=uuid4(), reason='Disease confirmed on reassessment.',
        )
        summary = plant_lifecycle_summary(plant)
        self.assertEqual(summary.state, LifecycleState.CULLED)
        self.assertEqual(summary.final_outcome, EventType.CULLED)
        self.assertFalse(
            SpecificPlantLocation.objects
            .filter(specific_plant=plant, ended__isnull=True)
            .exists()
        )
        result = action.results.get()
        self.assertEqual(result.lifecycle_event.event_type, EventType.CULLED)

    def test_a_release_resolves_only_the_members_quarantine_held(self):
        returned = self.returned_plant()
        live = make_specific_plant(workspace=self.workspace)
        record_lifecycle_event(live, None, OutcomeRequest(EventType.READY))
        scopes = [
            {'type': 'plant', 'id': returned.pk},
            {'type': 'plant', 'id': live.pk},
        ]
        preview = preview_observation(self.workspace, scopes)
        observation = record_observation(
            self.workspace, None, scopes=scopes,
            reviewed_digest=preview['digest'],
            observation_type=self.observation_type,
            severity=HealthObservation.Severity.HIGH,
            notes='Both benches inspected.',
        )
        action = act_on_quarantine(
            self.workspace, None, self.quarantine(observation),
            action_name=QuarantineAction.Action.RELEASE,
            idempotency_key=uuid4(), reason='Nothing found on reassessment.',
        )
        self.assertEqual([result.plant for result in action.results.all()], [returned])
        self.assertEqual(
            plant_lifecycle_summary(live).state, LifecycleState.AVAILABLE,
        )
        self.assertEqual(
            list(live.lifecycle_events.values_list('event_type', flat=True)),
            [EventType.READY],
        )

    def test_quarantine_move_preserves_physical_location_history(self):
        plant = make_specific_plant(workspace=self.workspace)
        source = make_location(workspace=self.workspace)
        original = make_specific_plant_location(
            specific_plant=plant,
            location_type=SpecificPlantLocation.LOCATION,
            seed_tray_cell=None,
            location=source,
        )
        destination = make_location(
            workspace=self.workspace, location_type='quarantine',
        )
        _case, action = quarantine_observation(
            self.workspace, None, self.observe('plant', plant),
            idempotency_key=uuid4(), reason='Move away from healthy stock.',
            destination=destination,
        )
        original.refresh_from_db()
        current = SpecificPlantLocation.objects.get(
            specific_plant=plant, ended__isnull=True,
        )
        self.assertIsNotNone(original.ended)
        self.assertEqual(current.location, destination)
        self.assertEqual(action.results.get().plant_location, current)

    def test_quarantined_cohort_requires_release_before_structural_change(self):
        plant = make_specific_plant(workspace=self.workspace)
        cohort = PlantCohort.objects.create(
            workspace=self.workspace, batch=plant.batch, quantity=4,
        )
        self.quarantine(self.observe('cohort', cohort))
        with self.assertRaisesMessage(ValidationError, 'Release this cohort'):
            change_cohort(
                self.workspace, None, cohort_id=cohort.pk,
                expected_revision=cohort.revision,
                action=CohortOperation.Action.LOSS,
                loss_cause=CohortOperation.LossCause.FAILED,
                idempotency_key=uuid4(), reason='Attempted partial loss.', quantity=1,
            )
        cohort.refresh_from_db()
        self.assertEqual(cohort.quantity, 4)

    def test_treatment_application_and_follow_up_are_each_linked_once(self):
        plant = make_specific_plant(workspace=self.workspace)
        observation = self.observe('plant', plant)
        location = make_location(workspace=self.workspace)
        item = make_inventory_item(workspace=self.workspace)
        lot = make_stock_lot(item=item, location=location, quantity='5')
        application = create_application_draft(
            self.workspace, None,
            ApplicationRequest(
                applied_at=timezone.now(), source_location=location,
                batch=plant.batch,
                lines=(LineRequest(
                    item=item, lot=lot, applied_quantity=Decimal('1'),
                    unit_code=UnitCode.LITRE,
                    targets=(TargetRequest(
                        InputApplicationTarget.TargetType.SPECIFIC_PLANT, plant,
                    ),),
                ),),
            ),
        )
        application, _movements = post_application(application, None)
        treatment = link_treatment(
            self.workspace, None, observation, application,
            follow_up_due_at=timezone.now(),
        )
        with self.assertRaisesMessage(ValidationError, 'already linked'):
            link_treatment(self.workspace, None, observation, application)
        follow_up = record_follow_up(
            self.workspace, None, observation, treatment=treatment,
            result='improving', effectiveness='partial',
        )
        with self.assertRaisesMessage(ValidationError, 'already been recorded'):
            record_follow_up(
                self.workspace, None, observation, treatment=treatment,
                result='resolved', effectiveness='effective',
            )
        replacement = record_follow_up(
            self.workspace, None, observation, corrects=follow_up,
            result='resolved', effectiveness='effective',
            correction_reason='The result was transcribed incorrectly.',
        )
        self.assertEqual(replacement.corrects, follow_up)

    def test_report_traces_issue_to_batch_variety_and_seed_supplier(self):
        plant = make_specific_plant(workspace=self.workspace)
        observation = self.observe('plant', plant)
        report = health_report(self.workspace, {'severity': 'high'})
        self.assertEqual(report['summary']['observations'], 1)
        row = report['results'][0]
        self.assertEqual(row['observation'], observation.pk)
        self.assertEqual(row['batches'][0]['batch'], plant.batch_id)
        self.assertEqual(
            row['seed_sources'][0]['supplier'],
            plant.cell_planting.seed_tray_planting.seeds_used.seeds.supplier_id,
        )


class QuarantineCaseLifecycleTests(HealthOperationTestCase):
    """A case is opened, worked, and closed exactly once, whatever it holds.

    The existing tests reach a state and stop there. These follow each case to
    the end, for plants and for cohorts, and assert what a closed case will and
    will not accept afterwards.
    """

    CLOSING = (QuarantineAction.Action.RELEASE, QuarantineAction.Action.CULL)
    ACTIONS = (
        QuarantineAction.Action.RELEASE,
        QuarantineAction.Action.ESCALATE,
        QuarantineAction.Action.CULL,
    )

    def act(self, case, action_name, reason='Reviewed on the bench.', **values):
        return act_on_quarantine(
            self.workspace, None, case, action_name=action_name,
            idempotency_key=uuid4(), reason=reason, **values,
        )

    def open_case_for_plant(self):
        """Quarantine one returned plant, which holds a lifecycle state."""
        plant = self.returned_plant()
        return self.quarantine(self.observe('plant', plant)), plant

    def open_case_for_cohort(self, quantity=4):
        """Quarantine one whole counted cohort."""
        plant = make_specific_plant(workspace=self.workspace)
        cohort = PlantCohort.objects.create(
            workspace=self.workspace, batch=plant.batch, quantity=quantity,
        )
        return self.quarantine(self.observe('cohort', cohort)), cohort

    def test_escalating_raises_a_linked_task_and_leaves_the_case_open(self):
        """Escalation asks for attention; it does not resolve anything."""

        case, plant = self.open_case_for_plant()
        action = self.act(
            case, QuarantineAction.Action.ESCALATE,
            reason='Second opinion needed before a decision.',
        )
        task = WorkTask.objects.get(
            source_snapshot__quarantine_action=action.pk,
        )
        self.assertEqual(task.priority, 100)
        self.assertEqual(task.source_snapshot['quarantine_case'], case.pk)
        self.assertTrue(case_is_active(case))
        self.assertTrue(is_quarantined(plant))
        self.assertEqual(
            plant_lifecycle_summary(plant).state, LifecycleState.QUARANTINED,
        )

    def test_a_case_can_be_escalated_more_than_once_before_it_closes(self):
        """Two people may each ask for attention on the same case."""
        case, _plant = self.open_case_for_plant()
        first = self.act(case, QuarantineAction.Action.ESCALATE, reason='Unsure.')
        second = self.act(case, QuarantineAction.Action.ESCALATE, reason='Still unsure.')
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(
            case.actions.filter(
                action=QuarantineAction.Action.ESCALATE,
            ).count(), 2,
        )
        self.assertTrue(case_is_active(case))

    def test_an_escalated_plant_case_still_closes_either_way(self):
        """Escalation is not a dead end for the stock it is raised over."""
        for closing, state in (
                (QuarantineAction.Action.RELEASE, LifecycleState.AVAILABLE),
                (QuarantineAction.Action.CULL, LifecycleState.CULLED)):
            with self.subTest(closing=closing):
                case, plant = self.open_case_for_plant()
                self.act(case, QuarantineAction.Action.ESCALATE, reason='Unsure.')
                self.act(case, closing, reason='Decision made after review.')
                self.assertFalse(case_is_active(case))
                self.assertFalse(is_quarantined(plant))
                self.assertEqual(plant_lifecycle_summary(plant).state, state)

    def test_a_closed_case_accepts_no_further_action(self):
        """Nothing may be decided twice about the same reviewed stock."""
        for closing in self.CLOSING:
            for attempted in self.ACTIONS:
                with self.subTest(closed_by=closing, attempted=attempted):
                    case, _plant = self.open_case_for_plant()
                    self.act(case, closing, reason='Decision made after review.')
                    with self.assertRaisesMessage(
                            ValidationError, 'This quarantine case is already closed.'):
                        self.act(case, attempted, reason='Changed my mind.')

    def test_every_action_states_why_it_was_taken(self):
        """A decision about live stock is never recorded without a reason."""
        case, _plant = self.open_case_for_plant()
        for action_name in self.ACTIONS:
            for reason in ('', '   '):
                with self.subTest(action=action_name, reason=repr(reason)):
                    with self.assertRaises(ValidationError) as caught:
                        self.act(case, action_name, reason=reason)
                    self.assertIn('reason', caught.exception.message_dict)
        self.assertTrue(case_is_active(case))

    def test_one_idempotency_key_records_one_action(self):
        """A retried request returns the action it already recorded."""
        case, _plant = self.open_case_for_plant()
        key = uuid4()
        values = {
            'action_name': QuarantineAction.Action.ESCALATE,
            'idempotency_key': key, 'reason': 'Second opinion needed.',
        }
        first = act_on_quarantine(self.workspace, None, case, **values)
        second = act_on_quarantine(self.workspace, None, case, **values)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(case.actions.filter(idempotency_key=key).count(), 1)
        with self.assertRaisesMessage(
                ValidationError, 'That key was used for different work.'):
            act_on_quarantine(
                self.workspace, None, case,
                action_name=QuarantineAction.Action.CULL,
                idempotency_key=key, reason='Second opinion needed.',
            )

    def test_a_release_must_take_stock_out_of_quarantine(self):
        """Releasing into a quarantine bench would resolve nothing."""
        case, _plant = self.open_case_for_plant()
        quarantine_bench = make_location(
            workspace=self.workspace, location_type='quarantine',
        )
        with self.assertRaisesMessage(
                ValidationError, 'Released stock must leave quarantine.'):
            self.act(
                case, QuarantineAction.Action.RELEASE,
                reason='Recovered.', destination=quarantine_bench,
            )
        self.assertTrue(case_is_active(case))
        bench = make_location(workspace=self.workspace)
        self.act(
            case, QuarantineAction.Action.RELEASE,
            reason='Recovered.', destination=bench,
        )
        self.assertFalse(case_is_active(case))

    def test_a_released_cohort_becomes_changeable_again(self):
        """Release is what lifts the structural lock quarantine imposes."""
        case, cohort = self.open_case_for_cohort()
        self.act(case, QuarantineAction.Action.RELEASE, reason='Nothing found.')
        self.assertFalse(case_is_active(case))
        self.assertFalse(is_quarantined(cohort))
        cohort.refresh_from_db()
        changed, _operation = change_cohort(
            self.workspace, None, cohort_id=cohort.pk,
            expected_revision=cohort.revision,
            action=CohortOperation.Action.LOSS,
            loss_cause=CohortOperation.LossCause.FAILED,
            idempotency_key=uuid4(), reason='Ordinary loss.', quantity=1,
        )
        self.assertEqual(changed.quantity, 3)

    def test_a_culled_cohort_is_written_down_in_full_and_closes_its_case(self):
        """Culling a cohort removes the counted stock it stood for."""
        case, cohort = self.open_case_for_cohort()
        action = self.act(
            case, QuarantineAction.Action.CULL, reason='Disease confirmed.',
        )
        cohort.refresh_from_db()
        self.assertEqual(cohort.quantity, 0)
        operation = action.results.get().cohort_operation
        self.assertEqual(operation.action, CohortOperation.Action.LOSS)
        self.assertEqual(operation.loss_cause, CohortOperation.LossCause.CULLED)
        self.assertFalse(case_is_active(case))
        self.assertFalse(is_quarantined(cohort))

    def test_a_cull_is_corrected_through_its_case_not_the_cohort(self):
        """The case still records the destruction, so the loss stays."""
        case, cohort = self.open_case_for_cohort()
        action = self.act(
            case, QuarantineAction.Action.CULL, reason='Disease confirmed.',
        )
        operation = action.results.get().cohort_operation
        with self.assertRaises(ValidationError):
            correct_cohort_loss(
                self.workspace, None, operation_id=operation.pk,
                idempotency_key=uuid4(), reason='Culled the wrong bench.',
            )
        cohort.refresh_from_db()
        self.assertEqual(cohort.quantity, 0)

    def test_an_escalated_cohort_case_still_closes(self):
        """A cohort escalation is no more a dead end than a plant one."""
        case, cohort = self.open_case_for_cohort()
        self.act(case, QuarantineAction.Action.ESCALATE, reason='Unsure.')
        self.assertTrue(case_is_active(case))
        self.act(case, QuarantineAction.Action.CULL, reason='Disease confirmed.')
        cohort.refresh_from_db()
        self.assertEqual(cohort.quantity, 0)
        self.assertFalse(case_is_active(case))

    def test_an_open_case_always_admits_a_closing_action(self):
        """No action a case accepts can leave it with nothing left to do.

        Task 91's invariant, applied to the case rather than the plant: every
        action reachable from an open case is either itself closing, or leaves
        the case open and still able to close.
        """
        openers = (self.open_case_for_plant, self.open_case_for_cohort)
        for opener in openers:
            for action_name in self.ACTIONS:
                with self.subTest(opener=opener.__name__, action=action_name):
                    case, _member = opener()
                    self.act(case, action_name, reason='Reviewed on the bench.')
                    if action_name in self.CLOSING:
                        self.assertFalse(case_is_active(case))
                        continue
                    self.assertTrue(case_is_active(case))
                    self.act(
                        case, QuarantineAction.Action.RELEASE,
                        reason='Closed after escalation.',
                    )
                    self.assertFalse(case_is_active(case))


class ReleaseDestinationTests(HealthOperationTestCase):
    """Task 130: closing a case says where the stock it held went.

    `released_available` leaves a plant where the quarantine put it and the
    closed case stops refusing sales, so a release that names no destination
    leaves saleable stock standing on the quarantine bench. The destination is
    owed exactly when there is a bench to come off.
    """

    def act(self, case, reason='Inspection found nothing.', **values):
        return act_on_quarantine(
            self.workspace, None, case,
            action_name=QuarantineAction.Action.RELEASE,
            idempotency_key=uuid4(), reason=reason, **values,
        )

    def quarantine_bench(self):
        return make_location(
            workspace=self.workspace, location_type='quarantine',
        )

    def offered_plant(self, bench=None):
        """One plant on offer, standing on a bench of its own."""
        plant = make_specific_plant(workspace=self.workspace)
        record_lifecycle_event(
            plant, None, OutcomeRequest(EventType.READY, reason='Ready for sale.'),
        )
        make_specific_plant_location(
            specific_plant=plant,
            location_type=SpecificPlantLocation.LOCATION,
            seed_tray_cell=None,
            location=bench or make_location(workspace=self.workspace),
        )
        return plant

    def case_on_the_quarantine_bench(self, target_type, target):
        """Open one case that moves its stock onto a quarantine bench."""
        case, _action = quarantine_observation(
            self.workspace, None, self.observe(target_type, target),
            idempotency_key=uuid4(), reason='Keep it away from healthy stock.',
            destination=self.quarantine_bench(),
        )
        return case

    def standing_at(self, plant):
        return SpecificPlantLocation.objects.get(
            specific_plant=plant, ended__isnull=True,
        ).location

    def test_a_release_from_a_quarantine_bench_needs_a_destination(self):
        """Verification 1: the case may not close over stranded stock."""
        plant = self.offered_plant()
        case = self.case_on_the_quarantine_bench('plant', plant)
        placed_at = self.standing_at(plant)

        with self.assertRaisesMessage(
                ValidationError,
                f'Released stock would stay in quarantine: Plant {plant.pk}.'):
            self.act(case)

        self.assertTrue(case_is_active(case))
        self.assertFalse(
            case.actions.filter(action=QuarantineAction.Action.RELEASE).exists(),
        )
        self.assertEqual(self.standing_at(plant), placed_at)
        self.assertTrue(is_quarantined(plant))

    def test_a_released_plant_stands_where_the_release_sends_it(self):
        """Verification 3: the move ends the quarantine placement, not the release."""
        plant = self.offered_plant()
        case = self.case_on_the_quarantine_bench('plant', plant)
        quarantine_placement = SpecificPlantLocation.objects.get(
            specific_plant=plant, ended__isnull=True,
        )
        bench = make_location(workspace=self.workspace, name='Sales bench')

        action = self.act(case, destination=bench)

        quarantine_placement.refresh_from_db()
        self.assertEqual(quarantine_placement.ended, action.occurred_at)
        self.assertEqual(self.standing_at(plant), bench)
        row = register_queryset(self.workspace, RegisterFilters()).get(pk=plant.pk)
        self.assertEqual(row.standing_at, bench.pk)
        self.assertTrue(row.sellable)
        self.assertFalse(row.quarantined)

    def test_a_release_that_moves_no_stock_needs_no_destination(self):
        """An overlay case left the plant where it was, so nothing is owed."""
        bench = make_location(workspace=self.workspace, name='Growing-on bench')
        plant = self.offered_plant(bench=bench)
        case = self.quarantine(self.observe('plant', plant))

        self.act(case)

        self.assertFalse(case_is_active(case))
        self.assertEqual(self.standing_at(plant), bench)
        row = register_queryset(self.workspace, RegisterFilters()).get(pk=plant.pk)
        self.assertTrue(row.sellable)

    def test_a_cohort_block_on_a_quarantine_bench_needs_a_destination(self):
        """A block is counted stock in a place, so it is asked the same thing."""
        plant = make_specific_plant(workspace=self.workspace)
        cohort = PlantCohort.objects.create(
            workspace=self.workspace, batch=plant.batch, quantity=4,
        )
        case = self.case_on_the_quarantine_bench('cohort', cohort)

        with self.assertRaisesMessage(
                ValidationError,
                f'Released stock would stay in quarantine: Cohort {cohort.pk}.'):
            self.act(case)

        self.assertTrue(case_is_active(case))
        bench = make_location(workspace=self.workspace, name='Block bench')
        self.act(case, destination=bench)
        cohort.refresh_from_db()
        self.assertFalse(case_is_active(case))
        self.assertEqual(cohort.location_id, bench.pk)
        self.assertEqual(cohort.quantity, 4)

    def pot_lot(self, bench, quantity='4'):
        """Empty pots standing where a fill can claim them."""
        return make_stock_lot(
            item=make_inventory_item(
                category='pot_container', base_unit='each', tracking_mode='mixed',
            ),
            location=bench, quantity=quantity, base_unit_cost=Decimal('3'),
        )

    def potted_plant(self, bench):
        """One plant in a counted pot fill, as a quarantined return leaves it."""
        fill = open_counted_fill(
            self.workspace, None, self.pot_lot(bench), bench, 1, returned=True,
        )
        plant = make_specific_plant(workspace=self.workspace)
        record_lifecycle_event(
            plant, None, OutcomeRequest(EventType.READY, reason='Ready for sale.'),
        )
        plant_counted_fill(self.workspace, None, fill, [plant.pk])
        return plant, fill

    def numbered_pot_plant(self, bench):
        """One plant in a numbered pot, which carries its own placement."""
        lot = self.pot_lot(bench, quantity='1')
        unit, = individualize_lot_units(
            self.workspace, None, IndividualizationRequest(lot, bench, 1),
        )
        open_numbered_fill(self.workspace, None, unit, returned=True)
        plant = make_specific_plant(workspace=self.workspace)
        record_lifecycle_event(
            plant, None, OutcomeRequest(EventType.READY, reason='Ready for sale.'),
        )
        move_specific_plant(plant, {
            'location_type': SpecificPlantLocation.CONTAINER_UNIT,
            'container_unit': unit,
        }, user=None)
        return plant, unit

    def test_a_release_will_not_take_a_plant_out_of_its_pot(self):
        """A counted fill cannot be carried, so the release says so instead.

        Moving the plant would make its placement a plain bench one, leave the
        fill open at the quarantine location with nobody in it, and freeze its
        shares on a departure nobody recorded.
        """
        bench = self.quarantine_bench()
        plant, fill = self.potted_plant(bench)
        case = self.quarantine(self.observe('plant', plant))

        with self.assertRaisesMessage(
                ValidationError,
                f'A counted pot fill cannot leave the location it was opened at: '
                f'Plant {plant.pk} in pot fill {fill.pk}.'):
            self.act(case)
        with self.assertRaisesMessage(
                ValidationError, 'Take the plant out of its pot'):
            self.act(case, destination=make_location(workspace=self.workspace))

        self.assertTrue(case_is_active(case))
        placement = SpecificPlantLocation.objects.get(
            specific_plant=plant, ended__isnull=True,
        )
        self.assertEqual(placement.container_fill_id, fill.pk)
        self.assertEqual(placement.location_id, bench.pk)
        fill.refresh_from_db()
        self.assertEqual(fill.status, SeedTrayGeneration.Status.OPEN)

    def test_a_plant_taken_out_of_its_pot_then_releases_on_its_own(self):
        """The refusal points somewhere real: the departure is the operator's."""
        plant, _fill = self.potted_plant(self.quarantine_bench())
        case = self.quarantine(self.observe('plant', plant))
        bench = make_location(workspace=self.workspace, name='Sales bench')
        move_specific_plant(plant, {
            'location_type': SpecificPlantLocation.LOCATION, 'location': bench,
        }, user=None)

        self.act(case)

        self.assertFalse(case_is_active(case))
        self.assertEqual(self.standing_at(plant), bench)

    def test_a_numbered_pot_leaves_quarantine_carrying_its_plant(self):
        """A pot holds its own placement, so the pot is what moves."""
        plant, unit = self.numbered_pot_plant(self.quarantine_bench())
        case = self.quarantine(self.observe('plant', plant))

        with self.assertRaisesMessage(
                ValidationError,
                f'Released stock would stay in quarantine: Plant {plant.pk}.'):
            self.act(case)

        bench = make_location(workspace=self.workspace, name='Sales bench')
        action = self.act(case, destination=bench)

        unit.refresh_from_db()
        self.assertEqual(unit.current_location_id, bench.pk)
        placement = SpecificPlantLocation.objects.get(
            specific_plant=plant, ended__isnull=True,
        )
        self.assertEqual(placement.location_type, SpecificPlantLocation.CONTAINER_UNIT)
        self.assertEqual(placement.container_unit_id, unit.pk)
        self.assertIsNotNone(action.results.get().stock_movement)

    def test_a_release_names_every_member_left_in_quarantine(self):
        """One refusal lists the whole reviewed set, as the member checks do."""
        plant = self.offered_plant()
        cohort = PlantCohort.objects.create(
            workspace=self.workspace, batch=plant.batch, quantity=4,
        )
        scopes = [
            {'type': 'plant', 'id': plant.pk},
            {'type': 'cohort', 'id': cohort.pk},
        ]
        preview = preview_observation(self.workspace, scopes)
        observation = record_observation(
            self.workspace, None, scopes=scopes,
            reviewed_digest=preview['digest'],
            observation_type=self.observation_type,
            severity=HealthObservation.Severity.HIGH,
            notes='Both the bench and the block inspected.',
        )
        case, _action = quarantine_observation(
            self.workspace, None, observation,
            idempotency_key=uuid4(), reason='Keep it away from healthy stock.',
            destination=self.quarantine_bench(),
        )

        with self.assertRaisesMessage(
                ValidationError,
                f'Plant {plant.pk}, Cohort {cohort.pk}. '
                'Name the location it is going to.'):
            self.act(case)

        self.assertTrue(case_is_active(case))


class RetainedStockQuarantineTests(HealthOperationTestCase):
    """Task 126: retained stock is resolved but still here, so it is quarantinable.

    Retaining a plant says no outcome is owed for it, not that it left the
    bench. The health workflow asks the physical question, so a retained plant
    is in an observation's scope and in the case opened over it, while a plant
    that really left is still refused.
    """

    #: The facts that take a plant out of the nursery, each as recorded.
    DEPARTURES = (
        ('sold', (EventType.READY, EventType.SOLD)),
        ('failed', (EventType.FAILED,)),
        ('culled', (EventType.CULLED,)),
        ('lost', (EventType.LOST,)),
        ('donated', (EventType.DONATED,)),
        ('harvested', (EventType.HARVEST_FINISHED,)),
        ('discarded', (EventType.READY, EventType.SOLD, EventType.RETURNED_DISCARDED)),
    )

    def record(self, plant, *event_types):
        for event_type in event_types:
            record_lifecycle_event(
                plant, None, OutcomeRequest(event_type, reason='Recorded on the bench.'),
            )

    def retained_plant(self, **overrides):
        plant = make_specific_plant(workspace=self.workspace, **overrides)
        self.record(plant, EventType.RETAINED)
        return plant

    def test_a_retained_plant_is_quarantined_with_its_case(self):
        """Verification 1: the case opens with the retained plant as a member."""
        plant = self.retained_plant()
        observation = self.observe('plant', plant)
        self.assertEqual(
            list(observation.affected_stock.values_list('plant_id', flat=True)),
            [plant.pk],
        )
        case = self.quarantine(observation)
        self.assertEqual(
            list(case.members.values_list('plant_id', flat=True)), [plant.pk],
        )
        self.assertTrue(is_quarantined(plant))
        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.RETAINED)

    def test_a_batch_scope_takes_retained_stock_and_leaves_departed_stock(self):
        """Retained plants are no longer dropped from a wider scope unseen."""
        growing = make_specific_plant(workspace=self.workspace)
        retained = self.retained_plant(cell_planting=growing.cell_planting)
        culled = make_specific_plant(
            workspace=self.workspace, cell_planting=growing.cell_planting,
        )
        self.record(culled, EventType.CULLED)
        withdrawn = make_specific_plant(
            workspace=self.workspace, cell_planting=growing.cell_planting,
        )
        record_germination_event(withdrawn, None)
        withdraw_germination(withdrawn, None, 'Entered twice.')

        preview = preview_observation(
            self.workspace, [{'type': 'batch', 'id': growing.batch_id}],
        )
        self.assertEqual(preview['plants'], sorted([growing.pk, retained.pk]))

    def test_releasing_a_retained_plant_records_no_lifecycle_fact(self):
        """Verification 2: quarantine was an overlay; the plant stays retained."""
        plant = self.retained_plant()
        action = act_on_quarantine(
            self.workspace, None, self.quarantine(self.observe('plant', plant)),
            action_name=QuarantineAction.Action.RELEASE,
            idempotency_key=uuid4(), reason='Nothing found on reassessment.',
        )
        self.assertFalse(action.results.exists())
        self.assertEqual(
            list(plant.lifecycle_events.values_list('event_type', flat=True)),
            [EventType.RETAINED],
        )
        self.assertFalse(is_quarantined(plant))

    def test_culling_a_retained_plant_resolves_it_and_closes_its_location(self):
        """Verification 3: the cull is a fact, linked to the action that took it."""
        plant = self.retained_plant()
        make_specific_plant_location(
            specific_plant=plant,
            location_type=SpecificPlantLocation.LOCATION,
            seed_tray_cell=None,
            location=make_location(workspace=self.workspace),
        )
        action = act_on_quarantine(
            self.workspace, None, self.quarantine(self.observe('plant', plant)),
            action_name=QuarantineAction.Action.CULL,
            idempotency_key=uuid4(), reason='Disease confirmed on the mother stock.',
        )
        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.CULLED)
        self.assertFalse(
            SpecificPlantLocation.objects
            .filter(specific_plant=plant, ended__isnull=True)
            .exists()
        )
        result = action.results.get()
        self.assertEqual(result.plant, plant)
        self.assertEqual(result.lifecycle_event.event_type, EventType.CULLED)

    def test_a_plant_gone_since_the_observation_is_still_refused(self):
        """Verification 4: the refusal names the plant as gone, not finished."""
        for label, event_types in self.DEPARTURES:
            with self.subTest(state=label):
                plant = make_specific_plant(workspace=self.workspace)
                observation = self.observe('plant', plant)
                self.record(plant, *event_types)
                self.assertEqual(plant_lifecycle_summary(plant).state, label)
                with self.assertRaises(ValidationError) as caught:
                    self.quarantine(observation)
                message = caught.exception.message_dict['plants'][0]
                self.assertIn('no longer holds', message)
                self.assertIn(str(plant.pk), message)
