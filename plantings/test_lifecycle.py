"""Tests for the append-only plant lifecycle services."""
# pylint: disable=duplicate-code
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import (
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    skipUnlessDBFeature,
)
from django.utils import timezone

from tests.factories import (
    make_garden_square,
    make_plant_lifecycle_event,
    make_specific_plant,
    make_specific_plant_location,
)
from workspaces.models import Workspace

from .lifecycle import (
    ALLOWED_FROM,
    EventType,
    FINAL_STATES,
    LifecycleState,
    OutcomeRequest,
    STATE_AFTER,
    availability_intervals,
    lifecycle_summaries,
    plant_lifecycle_summary,
    record_bulk_outcome,
    record_germination_event,
    record_lifecycle_event,
    record_transplant_event,
    reverse_lifecycle_event,
    states_without_exits,
    with_lifecycle_state,
)
from .models import PlantLifecycleEvent, SpecificPlant, SpecificPlantLocation


#: Facts that resolve a plant, paired with the state each one derives to.
FINAL_OUTCOMES = (
    (EventType.RETAINED, LifecycleState.RETAINED),
    (EventType.DONATED, LifecycleState.DONATED),
    (EventType.FAILED, LifecycleState.FAILED),
    (EventType.LOST, LifecycleState.LOST),
    (EventType.CULLED, LifecycleState.CULLED),
    (EventType.HARVEST_FINISHED, LifecycleState.HARVESTED),
)


class LifecycleStateDerivationTests(TestCase):
    """Current state is replayed from the recorded facts."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='deriver')
        self.plant = make_specific_plant()
        record_germination_event(self.plant, self.user)

    def test_a_germinated_plant_is_growing_and_not_sellable(self):
        """Germination starts a plant active but not yet offerable."""
        summary = plant_lifecycle_summary(self.plant)
        self.assertEqual(summary.state, LifecycleState.GROWING)
        self.assertFalse(summary.sellable)
        self.assertIsNone(summary.final_outcome)
        self.assertIsNone(summary.final_outcome_at)

    def test_a_plant_with_no_history_at_all_is_growing(self):
        """A plant predating any recorded fact is not treated as resolved."""
        summary = plant_lifecycle_summary(make_specific_plant())
        self.assertEqual(summary.state, LifecycleState.GROWING)
        self.assertIsNone(summary.final_outcome)

    def test_ready_makes_a_plant_available_and_sellable(self):
        """A ready plant is the one state that may be offered to somebody."""
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))
        summary = plant_lifecycle_summary(self.plant)
        self.assertEqual(summary.state, LifecycleState.AVAILABLE)
        self.assertTrue(summary.sellable)

    def test_transplanting_records_a_fact_without_changing_state(self):
        """Where a plant lives is separate from its commercial disposition."""
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))
        record_transplant_event(self.plant, self.user, timezone.now())
        summary = plant_lifecycle_summary(self.plant)
        self.assertEqual(summary.state, LifecycleState.AVAILABLE)

    def test_commerce_events_sell_and_reopen_a_plant_explicitly(self):
        """A returned plant is sellable only for the available outcome."""
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.SOLD))
        self.assertEqual(plant_lifecycle_summary(self.plant).state, LifecycleState.SOLD)
        record_lifecycle_event(
            self.plant,
            self.user,
            OutcomeRequest(EventType.RETURNED_AVAILABLE),
        )
        self.assertTrue(plant_lifecycle_summary(self.plant).sellable)

    def test_quarantined_and_discarded_returns_are_not_sellable(self):
        """Every return outcome has a distinct auditable lifecycle state."""
        for event_type, expected in (
                (EventType.RETURNED_QUARANTINED, LifecycleState.QUARANTINED),
                (EventType.RETURNED_DISCARDED, LifecycleState.DISCARDED)):
            with self.subTest(event_type=event_type):
                plant = make_specific_plant()
                record_germination_event(plant, self.user)
                record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.READY))
                record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.SOLD))
                record_lifecycle_event(plant, self.user, OutcomeRequest(event_type))
                summary = plant_lifecycle_summary(plant)
                self.assertEqual(summary.state, expected)
                self.assertFalse(summary.sellable)

    def test_each_final_outcome_derives_its_state_and_metadata(self):
        """Every resolving fact reports itself as the final outcome."""
        for event_type, expected_state in FINAL_OUTCOMES:
            with self.subTest(event_type=event_type):
                plant = make_specific_plant()
                record_germination_event(plant, self.user)
                occurred_at = timezone.now()
                record_lifecycle_event(
                    plant,
                    self.user,
                    OutcomeRequest(event_type, occurred_at=occurred_at),
                )
                summary = plant_lifecycle_summary(plant)
                self.assertEqual(summary.state, expected_state)
                self.assertEqual(summary.final_outcome, event_type)
                self.assertEqual(summary.final_outcome_at, occurred_at)
                self.assertFalse(summary.sellable)


class LifecycleStateAnnotationTests(TestCase):
    """The database annotation answers exactly what the replay answers.

    The register filters, counts, sorts, and pages by lifecycle state, which
    the Python replay cannot do. Both derivations must therefore agree on every
    shape of history, or a screen would disagree with the plant it is showing.
    """

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='annotator')

    def germinated_plant(self, occurred_at):
        """Create one plant whose germination is already on file."""
        plant = make_specific_plant(germinated=occurred_at)
        record_germination_event(plant, self.user)
        return plant

    def population(self):
        """Build one plant per derived state, plus the awkward histories."""
        start = timezone.now() - timedelta(days=30)
        plants = {'unrecorded': make_specific_plant(germinated=start)}
        plants['growing'] = self.germinated_plant(start)
        plants['available'] = self.germinated_plant(start)
        record_lifecycle_event(
            plants['available'],
            self.user,
            OutcomeRequest(EventType.READY, occurred_at=start + timedelta(days=1)),
        )
        for name, (event_type, _) in zip(
            ('retained', 'donated', 'failed', 'lost', 'culled', 'harvested'),
            FINAL_OUTCOMES,
        ):
            plant = self.germinated_plant(start)
            make_specific_plant_location(specific_plant=plant, started=start)
            record_lifecycle_event(
                plant,
                self.user,
                OutcomeRequest(event_type, occurred_at=start + timedelta(days=2)),
            )
            plants[name] = plant

        for name, returned_event in (
                ('sold', None),
                ('quarantined', EventType.RETURNED_QUARANTINED),
                ('discarded', EventType.RETURNED_DISCARDED)):
            plant = self.germinated_plant(start)
            record_lifecycle_event(
                plant, self.user,
                OutcomeRequest(EventType.READY, occurred_at=start + timedelta(days=1)),
            )
            record_lifecycle_event(
                plant, self.user,
                OutcomeRequest(EventType.SOLD, occurred_at=start + timedelta(days=2)),
            )
            if returned_event:
                record_lifecycle_event(
                    plant, self.user,
                    OutcomeRequest(returned_event, occurred_at=start + timedelta(days=3)),
                )
            plants[name] = plant

        plants['held_back'] = self.germinated_plant(start)
        record_lifecycle_event(
            plants['held_back'],
            self.user,
            OutcomeRequest(EventType.READY, occurred_at=start + timedelta(days=1)),
        )
        record_lifecycle_event(
            plants['held_back'],
            self.user,
            OutcomeRequest(
                EventType.HELD_BACK,
                occurred_at=start + timedelta(days=2),
                reason='Gone leggy in the heat.',
            ),
        )

        plants['offered_again'] = self.germinated_plant(start)
        for day, event_type, reason in (
                (1, EventType.READY, ''),
                (2, EventType.HELD_BACK, 'Gone leggy in the heat.'),
                (3, EventType.READY, '')):
            record_lifecycle_event(
                plants['offered_again'],
                self.user,
                OutcomeRequest(
                    event_type, occurred_at=start + timedelta(days=day), reason=reason,
                ),
            )

        plants['retention_ended'] = self.germinated_plant(start)
        record_lifecycle_event(
            plants['retention_ended'],
            self.user,
            OutcomeRequest(EventType.RETAINED, occurred_at=start + timedelta(days=1)),
        )
        record_lifecycle_event(
            plants['retention_ended'],
            self.user,
            OutcomeRequest(
                EventType.RETENTION_ENDED,
                occurred_at=start + timedelta(days=2),
                reason='Back into sale stock.',
            ),
        )

        plants['transplanted'] = self.germinated_plant(start)
        record_lifecycle_event(
            plants['transplanted'],
            self.user,
            OutcomeRequest(EventType.READY, occurred_at=start + timedelta(days=1)),
        )
        record_transplant_event(
            plants['transplanted'],
            self.user,
            start + timedelta(days=2),
        )

        plants['corrected'] = self.germinated_plant(start)
        record_lifecycle_event(
            plants['corrected'],
            self.user,
            OutcomeRequest(EventType.READY, occurred_at=start + timedelta(days=1)),
        )
        mistake = record_lifecycle_event(
            plants['corrected'],
            self.user,
            OutcomeRequest(EventType.FAILED, occurred_at=start + timedelta(days=2)),
        )
        reverse_lifecycle_event(
            mistake,
            self.user,
            'Recorded against the wrong plant.',
            occurred_at=start + timedelta(days=3),
        )
        return plants

    def annotated(self, plants):
        """Return the annotated summary of every plant, keyed by primary key."""
        queryset = with_lifecycle_state(
            SpecificPlant.objects.filter(pk__in=[plant.pk for plant in plants]),
        )
        return {
            row.pk: (
                row.lifecycle_state,
                row.sellable,
                row.final_outcome,
                row.final_outcome_at,
                row.last_state_at,
                row.first_ready_at,
            )
            for row in queryset
        }

    def test_the_annotation_matches_the_replay_for_every_history(self):
        """One vocabulary, two derivations, no drift between them."""
        plants = self.population()
        annotated = self.annotated(plants.values())
        replayed = lifecycle_summaries([plant.pk for plant in plants.values()])
        for name, plant in plants.items():
            with self.subTest(history=name):
                summary = replayed[plant.pk]
                intervals = availability_intervals(list(plant.lifecycle_events.all()))
                self.assertEqual(
                    annotated[plant.pk],
                    (
                        summary.state,
                        summary.sellable,
                        summary.final_outcome,
                        summary.final_outcome_at,
                        summary.state_since,
                        intervals[0].started if intervals else None,
                    ),
                )

    def test_the_population_exercises_every_derived_state(self):
        """A new state cannot be added without being compared here."""
        plants = self.population()
        derived = {state for state, *_ in self.annotated(plants.values()).values()}
        self.assertEqual(derived, {state.value for state in LifecycleState})

    def test_a_correction_returns_the_annotation_to_the_surviving_facts(self):
        """A reversed outcome stops counting the moment it is corrected."""
        plants = self.population()
        plant = plants['corrected']
        ready_at = plant.lifecycle_events.get(event_type=EventType.READY).occurred_at
        annotated = self.annotated([plant])[plant.pk]
        self.assertEqual(
            annotated,
            (LifecycleState.AVAILABLE, True, None, None, ready_at, ready_at),
        )


class LifecycleTransitionTests(TestCase):
    """Only the permitted transitions are accepted."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='transitioner')
        self.plant = make_specific_plant()
        record_germination_event(self.plant, self.user)

    def test_germination_cannot_be_recorded_twice(self):
        """A plant already has history, so it cannot germinate again."""
        with self.assertRaises(ValidationError):
            record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.GERMINATED))

    def test_ready_is_rejected_from_a_resolved_plant(self):
        """A failed plant cannot become available again without a correction."""
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.FAILED))
        with self.assertRaises(ValidationError) as caught:
            record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))
        self.assertIn('event_type', caught.exception.message_dict)

    def test_every_final_outcome_is_rejected_from_a_resolved_plant(self):
        """A resolved plant cannot pick up a second competing outcome."""
        for event_type, _ in FINAL_OUTCOMES:
            with self.subTest(event_type=event_type):
                plant = make_specific_plant()
                record_germination_event(plant, self.user)
                record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.CULLED))
                with self.assertRaises(ValidationError):
                    record_lifecycle_event(plant, self.user, OutcomeRequest(event_type))

    def test_a_retained_plant_may_still_fail_or_be_harvested(self):
        """Retention ends availability without ending biological growth."""
        for event_type in (EventType.FAILED, EventType.HARVEST_FINISHED):
            with self.subTest(event_type=event_type):
                plant = make_specific_plant()
                record_germination_event(plant, self.user)
                record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.RETAINED))
                record_lifecycle_event(plant, self.user, OutcomeRequest(event_type))
                self.assertEqual(
                    plant_lifecycle_summary(plant).final_outcome,
                    event_type,
                )

    def test_a_rejection_names_the_state_it_came_from(self):
        """The message reads as a sentence for a vowel-initial state too."""
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))
        with self.assertRaises(ValidationError) as caught:
            record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))
        self.assertEqual(
            caught.exception.message_dict['event_type'],
            ['An available plant cannot be recorded as ready for sale or use.'],
        )

    def test_a_retained_plant_cannot_be_marked_ready(self):
        """Retention is final for availability."""
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.RETAINED))
        with self.assertRaises(ValidationError):
            record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))

    def test_a_resolved_plant_cannot_be_transplanted(self):
        """A culled plant is not moved anywhere else."""
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.CULLED))
        with self.assertRaises(ValidationError):
            record_transplant_event(self.plant, self.user, timezone.now())

    def test_events_cannot_be_recorded_out_of_order(self):
        """History is append-only in time as well as in storage."""
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))
        with self.assertRaises(ValidationError) as caught:
            record_lifecycle_event(
                self.plant,
                self.user,
                OutcomeRequest(
                    EventType.FAILED,
                    occurred_at=timezone.now() - timedelta(days=7),
                ),
            )
        self.assertIn('occurred_at', caught.exception.message_dict)


class QuarantinedTransitionTests(TestCase):
    """A returned quarantined plant can be assessed and resolved."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='assessor')

    def quarantined_plant(self):
        """Return one plant a customer returned into quarantine."""
        plant = make_specific_plant()
        record_germination_event(plant, self.user)
        record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.READY))
        record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.SOLD))
        record_lifecycle_event(
            plant, self.user, OutcomeRequest(EventType.RETURNED_QUARANTINED),
        )
        return plant

    def test_releasing_a_recovered_plant_makes_it_available_again(self):
        """Recovery is a fact of its own, not a denial of the quarantine."""
        plant = self.quarantined_plant()
        record_lifecycle_event(
            plant, self.user, OutcomeRequest(EventType.RELEASED_AVAILABLE),
        )
        summary = plant_lifecycle_summary(plant)
        self.assertEqual(summary.state, LifecycleState.AVAILABLE)
        self.assertTrue(summary.sellable)
        self.assertIsNone(summary.final_outcome)
        self.assertEqual(
            list(
                plant.lifecycle_events
                .filter(event_type=EventType.RETURNED_QUARANTINED)
                .values_list('reversal_of', flat=True)
            ),
            [None],
        )

    def test_a_plant_that_does_not_recover_can_be_resolved(self):
        """Quarantine ends in an outcome without denying it happened."""
        for event_type, expected in (
                (EventType.CULLED, LifecycleState.CULLED),
                (EventType.FAILED, LifecycleState.FAILED),
                (EventType.LOST, LifecycleState.LOST),
                (EventType.RETAINED, LifecycleState.RETAINED)):
            with self.subTest(event_type=event_type):
                plant = self.quarantined_plant()
                record_lifecycle_event(plant, self.user, OutcomeRequest(event_type))
                summary = plant_lifecycle_summary(plant)
                self.assertEqual(summary.state, expected)
                self.assertEqual(summary.final_outcome, event_type)

    def test_a_quarantined_plant_cannot_be_cleared_except_by_release(self):
        """Handing on an uncleared plant is what quarantine exists to stop.

        `ready` is refused with them because release is the fact that says a
        quarantined plant is offerable again.
        """
        for event_type in (
                EventType.DONATED,
                EventType.HARVEST_FINISHED,
                EventType.SOLD,
                EventType.READY):
            with self.subTest(event_type=event_type):
                plant = self.quarantined_plant()
                with self.assertRaises(ValidationError) as caught:
                    record_lifecycle_event(plant, self.user, OutcomeRequest(event_type))
                self.assertIn('event_type', caught.exception.message_dict)

    def test_a_quarantined_plant_cannot_be_planted_out(self):
        """Planting out spreads whatever the quarantine is holding back."""
        plant = self.quarantined_plant()
        with self.assertRaises(ValidationError):
            record_transplant_event(plant, self.user, timezone.now())

    def test_release_is_rejected_from_every_other_state(self):
        """Release names a quarantine; nothing else has one to end."""
        plant = make_specific_plant()
        record_germination_event(plant, self.user)
        for prior in (None, EventType.READY, EventType.SOLD):
            with self.subTest(prior=prior):
                if prior is not None:
                    record_lifecycle_event(plant, self.user, OutcomeRequest(prior))
                with self.assertRaises(ValidationError) as caught:
                    record_lifecycle_event(
                        plant, self.user, OutcomeRequest(EventType.RELEASED_AVAILABLE),
                    )
                self.assertIn('event_type', caught.exception.message_dict)

    def test_a_released_plant_can_be_sold_again(self):
        """Release restores the plant to ordinary saleable stock."""
        plant = self.quarantined_plant()
        record_lifecycle_event(
            plant, self.user, OutcomeRequest(EventType.RELEASED_AVAILABLE),
        )
        record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.SOLD))
        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.SOLD)


class LifecycleReachabilityTests(SimpleTestCase):
    """No state may be added without a way out of it."""

    def test_every_non_final_state_accepts_at_least_one_fact(self):
        """A plant is never stuck somewhere only a correction can undo."""
        self.assertEqual(states_without_exits(), set())

    def test_a_state_added_without_a_transition_is_reported(self):
        """The invariant catches the omission rather than assuming care."""
        state_after = {**STATE_AFTER, EventType.CORRECTED: LifecycleState.QUARANTINED}
        allowed_from = {
            event_type: {
                state for state in sources if state != LifecycleState.QUARANTINED
            }
            for event_type, sources in ALLOWED_FROM.items()
        }
        self.assertEqual(
            states_without_exits(
                state_after=state_after,
                allowed_from=allowed_from,
                final_states=FINAL_STATES,
            ),
            {LifecycleState.QUARANTINED},
        )


class LifecycleLocationClosureTests(TestCase):
    """Outcomes close a physical location only where they should."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='closer')
        self.plant = make_specific_plant()
        record_germination_event(self.plant, self.user)
        self.location = make_specific_plant_location(specific_plant=self.plant)

    def _active_location(self):
        """Return the plant's open location, if it still has one."""
        return SpecificPlantLocation.objects.filter(
            specific_plant=self.plant,
            ended__isnull=True,
        ).first()

    def test_departing_and_ending_outcomes_close_the_location(self):
        """Leaving the operation or dying ends the plant's occupancy."""
        closing = (
            EventType.DONATED,
            EventType.FAILED,
            EventType.CULLED,
            EventType.HARVEST_FINISHED,
        )
        for event_type in closing:
            with self.subTest(event_type=event_type):
                plant = make_specific_plant()
                record_germination_event(plant, self.user)
                location = make_specific_plant_location(specific_plant=plant)
                occurred_at = timezone.now()
                record_lifecycle_event(
                    plant,
                    self.user,
                    OutcomeRequest(event_type, occurred_at=occurred_at),
                )
                location.refresh_from_db()
                self.assertEqual(location.ended, occurred_at)

    def test_a_retained_plant_keeps_its_location(self):
        """Retention removes a plant from sale, not from the ground."""
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.RETAINED))
        self.location.refresh_from_db()
        self.assertIsNone(self.location.ended)
        self.assertIsNotNone(self._active_location())

    def test_marking_a_plant_ready_keeps_its_location(self):
        """Readiness is a commercial fact, not a physical one."""
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))
        self.location.refresh_from_db()
        self.assertIsNone(self.location.ended)

    def test_an_outcome_cannot_predate_the_current_location(self):
        """A closure that ends before it started is refused outright."""
        self.location.started = timezone.now()
        self.location.save(update_fields=['started'])
        with self.assertRaises(ValidationError):
            record_lifecycle_event(
                self.plant,
                self.user,
                OutcomeRequest(
                    EventType.FAILED,
                    occurred_at=self.location.started - timedelta(hours=1),
                ),
            )
        self.location.refresh_from_db()
        self.assertIsNone(self.location.ended)

    def test_a_rejected_outcome_leaves_the_location_untouched(self):
        """An invalid transition never half-closes a location."""
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.RETAINED))
        with self.assertRaises(ValidationError):
            record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))
        self.location.refresh_from_db()
        self.assertIsNone(self.location.ended)

    def test_an_outcome_without_a_location_is_still_recorded(self):
        """A plant with no tracked location can still be resolved."""
        plant = make_specific_plant()
        record_germination_event(plant, self.user)
        record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.FAILED))
        self.assertEqual(
            plant_lifecycle_summary(plant).final_outcome,
            EventType.FAILED,
        )


class LifecycleCorrectionTests(TestCase):
    """Corrections append a reversal instead of rewriting history."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='corrector')
        self.plant = make_specific_plant()
        record_germination_event(self.plant, self.user)
        self.location = make_specific_plant_location(specific_plant=self.plant)

    def test_reversing_a_failure_reopens_the_plant_and_keeps_the_fact(self):
        """The mistaken event stays visible while the state recovers."""
        failure = record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.FAILED))
        correction = reverse_lifecycle_event(failure, self.user, 'Wrong plant.')

        self.assertEqual(correction.reversal_of_id, failure.pk)
        self.assertTrue(
            PlantLifecycleEvent.objects.filter(pk=failure.pk).exists(),
        )
        summary = plant_lifecycle_summary(self.plant)
        self.assertEqual(summary.state, LifecycleState.GROWING)
        self.assertIsNone(summary.final_outcome)

    def test_a_replacement_location_can_follow_a_reversed_failure(self):
        """The closed location stays closed; a new interval is appended."""
        failure = record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.FAILED))
        reverse_lifecycle_event(failure, self.user, 'Wrong plant.')
        self.location.refresh_from_db()
        self.assertIsNotNone(self.location.ended)

        replacement = SpecificPlantLocation.objects.create(
            specific_plant=self.plant,
            location_type=SpecificPlantLocation.GARDEN_SQUARE,
            garden_square=make_garden_square(),
            started=self.location.ended,
        )
        self.assertIsNone(replacement.ended)

    def test_a_reversed_plant_accepts_a_valid_replacement_outcome(self):
        """After a correction the plant can be resolved properly."""
        failure = record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.FAILED))
        reverse_lifecycle_event(failure, self.user, 'Wrong plant.')
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.CULLED))
        self.assertEqual(
            plant_lifecycle_summary(self.plant).final_outcome,
            EventType.CULLED,
        )

    def test_a_correction_requires_a_reason(self):
        """Audited corrections always say why they were needed."""
        failure = record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.FAILED))
        with self.assertRaises(ValidationError) as caught:
            reverse_lifecycle_event(failure, self.user, '   ')
        self.assertIn('reason', caught.exception.message_dict)

    def test_an_event_cannot_be_corrected_twice(self):
        """One fact has at most one reversal."""
        failure = record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.FAILED))
        reverse_lifecycle_event(failure, self.user, 'Wrong plant.')
        with self.assertRaises(ValidationError):
            reverse_lifecycle_event(failure, self.user, 'Again.')

    def test_a_correction_cannot_itself_be_corrected(self):
        """Corrections are undone by recording the truth, not by nesting."""
        failure = record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.FAILED))
        correction = reverse_lifecycle_event(failure, self.user, 'Wrong plant.')
        with self.assertRaises(ValidationError):
            reverse_lifecycle_event(correction, self.user, 'Undo the undo.')

    def test_germination_cannot_be_reversed(self):
        """The fact that created the plant is not correctable."""
        germination = PlantLifecycleEvent.objects.get(
            plant=self.plant,
            event_type=EventType.GERMINATED,
        )
        with self.assertRaises(ValidationError):
            reverse_lifecycle_event(germination, self.user, 'Never happened.')


class LifecycleImmutabilityTests(TestCase):
    """Recorded facts are never overwritten or deleted."""

    def setUp(self):
        super().setUp()
        self.event = make_plant_lifecycle_event()

    def test_an_event_cannot_be_saved_again(self):
        """Editing a fact is refused by the model itself."""
        self.event.reason = 'Rewritten.'
        with self.assertRaises(ValidationError):
            self.event.save()

    def test_an_event_cannot_be_deleted(self):
        """Deleting a fact is refused by the model itself."""
        with self.assertRaises(ValidationError):
            self.event.delete()

    def test_an_event_must_match_its_plant_batch(self):
        """The denormalised batch cannot disagree with the plant's own."""
        other_plant = make_specific_plant()
        with self.assertRaises(ValidationError) as caught:
            make_plant_lifecycle_event(
                plant=other_plant,
                batch=self.event.batch,
            )
        self.assertIn('batch', caught.exception.message_dict)


class BulkOutcomeTests(TestCase):
    """A selection produces one traceable event per plant."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='bulk')
        self.plants = []
        for _ in range(3):
            plant = make_specific_plant()
            record_germination_event(plant, self.user)
            make_specific_plant_location(specific_plant=plant)
            self.plants.append(plant)

    def test_each_plant_receives_its_own_event(self):
        """The aggregate action stays auditable plant by plant."""
        plant_ids = [plant.pk for plant in self.plants]
        events = record_bulk_outcome(
            plant_ids,
            self.user,
            OutcomeRequest(EventType.CULLED, reason='Frost damage.'),
        )

        self.assertEqual(len(events), len(plant_ids))
        self.assertEqual(
            sorted(event.plant_id for event in events),
            sorted(plant_ids),
        )
        for event in events:
            self.assertEqual(event.event_type, EventType.CULLED)
            self.assertEqual(event.reason, 'Frost damage.')

    def test_each_plant_location_is_closed(self):
        """Bulk outcomes honour the same location rules as single ones."""
        record_bulk_outcome(
            [plant.pk for plant in self.plants],
            self.user,
            OutcomeRequest(EventType.CULLED),
        )
        self.assertFalse(
            SpecificPlantLocation.objects.filter(
                specific_plant__in=self.plants,
                ended__isnull=True,
            ).exists(),
        )

    def test_one_invalid_plant_rejects_the_whole_selection(self):
        """A partial application would leave an unexplainable audit trail."""
        record_lifecycle_event(self.plants[0], self.user, OutcomeRequest(EventType.FAILED))
        with self.assertRaises(ValidationError) as caught:
            record_bulk_outcome(
                [plant.pk for plant in self.plants],
                self.user,
                OutcomeRequest(EventType.CULLED),
            )

        self.assertIn('plants', caught.exception.message_dict)
        self.assertEqual(
            PlantLifecycleEvent.objects.filter(
                plant__in=self.plants[1:],
                event_type=EventType.CULLED,
            ).count(),
            0,
        )

    def test_an_unknown_plant_is_rejected(self):
        """A selection naming a missing plant records nothing."""
        with self.assertRaises(ValidationError):
            record_bulk_outcome([0], self.user, OutcomeRequest(EventType.CULLED))

    def test_an_empty_selection_is_rejected(self):
        """Recording an outcome for nothing is a mistake, not a no-op."""
        with self.assertRaises(ValidationError):
            record_bulk_outcome([], self.user, OutcomeRequest(EventType.CULLED))


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentPlantOutcomeTests(TransactionTestCase):
    """A locked plant admits only one final outcome at a time."""

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
        self.user = get_user_model().objects.create_user(username='plant-racer')
        plant = make_specific_plant()
        record_germination_event(plant, self.user)
        make_specific_plant_location(specific_plant=plant)
        self.plant_pk = plant.pk

    def _record(self, event_type):
        """Attempt one outcome from an independent connection."""
        close_old_connections()
        plant = SpecificPlant.objects.get(pk=self.plant_pk)
        user = get_user_model().objects.get(pk=self.user.pk)
        try:
            record_lifecycle_event(plant, user, OutcomeRequest(event_type))
        except ValidationError:
            result = 'rejected'
        else:
            result = 'recorded'
        close_old_connections()
        return result

    def test_only_one_concurrent_outcome_commits(self):
        """The loser is rejected instead of resolving the plant twice."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(
                future.result()
                for future in [
                    pool.submit(self._record, EventType.FAILED),
                    pool.submit(self._record, EventType.CULLED),
                ]
            )

        self.assertEqual(results, ['recorded', 'rejected'])
        outcomes = PlantLifecycleEvent.objects.filter(
            plant_id=self.plant_pk,
            event_type__in=[EventType.FAILED, EventType.CULLED],
        )
        self.assertEqual(outcomes.count(), 1)
        self.assertEqual(
            SpecificPlantLocation.objects.filter(
                specific_plant_id=self.plant_pk,
                ended__isnull=True,
            ).count(),
            0,
        )

    def test_only_one_concurrent_reservation_of_readiness_commits(self):
        """Racing the same transition twice still appends one fact."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(
                future.result()
                for future in [
                    pool.submit(self._record, EventType.READY),
                    pool.submit(self._record, EventType.READY),
                ]
            )

        self.assertEqual(results, ['recorded', 'rejected'])
        self.assertEqual(
            PlantLifecycleEvent.objects.filter(
                plant_id=self.plant_pk,
                event_type=EventType.READY,
            ).count(),
            1,
        )
