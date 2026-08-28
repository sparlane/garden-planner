"""Tests for quarantine, treatment, follow-up, and availability commands."""

# Test names describe behavior directly.
# pylint: disable=missing-function-docstring,duplicate-code

from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from applications.models import InputApplicationTarget
from applications.services import (
    ApplicationRequest,
    LineRequest,
    TargetRequest,
    create_application_draft,
    post_application,
)
from inventory.units import UnitCode
from plantings.cohorts import change_cohort
from plantings.lifecycle import (
    EventType,
    LifecycleState,
    OutcomeRequest,
    plant_lifecycle_summary,
    record_lifecycle_event,
)
from plantings.models import CohortOperation, PlantCohort, SpecificPlantLocation
from plantings.register import RegisterFilters, register_queryset
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
        from work.models import WorkTask  # pylint: disable=import-outside-toplevel

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
        self.assertEqual(
            action.results.get().cohort_operation.action, CohortOperation.Action.LOSS,
        )
        self.assertFalse(case_is_active(case))
        self.assertFalse(is_quarantined(cohort))

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
