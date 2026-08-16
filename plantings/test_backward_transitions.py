"""Tests for the backward lifecycle facts that are not corrections."""
# pylint: disable=duplicate-code
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from tests.factories import make_specific_plant, make_specific_plant_location

from .lifecycle import (
    EventType,
    LifecycleState,
    OutcomeRequest,
    availability_intervals,
    plant_lifecycle_summary,
    record_germination_event,
    record_lifecycle_event,
    reverse_lifecycle_event,
)


class BackwardTransitionTests(TestCase):
    """A condition that changes backwards is a fact, not a correction."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='grader')
        self.plant = make_specific_plant()
        record_germination_event(self.plant, self.user)

    def held_back_plant(self):
        """Return one plant graded ready and then taken off offer."""
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))
        record_lifecycle_event(
            self.plant,
            self.user,
            OutcomeRequest(EventType.HELD_BACK, reason='Gone leggy in the heat.'),
        )
        return self.plant

    def test_holding_a_plant_back_returns_it_to_production(self):
        """Off offer is not resolved: the plant is growing again."""
        plant = self.held_back_plant()
        summary = plant_lifecycle_summary(plant)
        self.assertEqual(summary.state, LifecycleState.GROWING)
        self.assertFalse(summary.sellable)
        self.assertIsNone(summary.final_outcome)

    def test_holding_a_plant_back_leaves_the_ready_fact_standing(self):
        """The plant was ready; the history keeps saying so."""
        plant = self.held_back_plant()
        ready = plant.lifecycle_events.get(event_type=EventType.READY)
        self.assertIsNone(ready.reversal_of)
        self.assertFalse(hasattr(ready, 'reversal'))

    def test_a_held_back_plant_can_be_offered_again(self):
        """A repeated cycle reads as separate offers, not one fact twice."""
        plant = self.held_back_plant()
        record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.READY))
        self.assertEqual(
            plant_lifecycle_summary(plant).state,
            LifecycleState.AVAILABLE,
        )
        self.assertEqual(
            list(
                plant.lifecycle_events
                .exclude(event_type=EventType.GERMINATED)
                .values_list('event_type', flat=True)
            ),
            [EventType.READY, EventType.HELD_BACK, EventType.READY],
        )

    def test_a_held_back_plant_cannot_be_sold(self):
        """Withdrawn stock is not offerable while it is withdrawn."""
        plant = self.held_back_plant()
        with self.assertRaises(ValidationError) as caught:
            record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.SOLD))
        self.assertIn('event_type', caught.exception.message_dict)

    def test_ending_a_retention_returns_the_plant_to_production(self):
        """A mother plant coming back to stock is growing, not available."""
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.RETAINED))
        record_lifecycle_event(
            self.plant,
            self.user,
            OutcomeRequest(EventType.RETENTION_ENDED, reason='Back into sale stock.'),
        )
        summary = plant_lifecycle_summary(self.plant)
        self.assertEqual(summary.state, LifecycleState.GROWING)
        self.assertFalse(summary.sellable)
        self.assertIsNone(summary.final_outcome)
        self.assertIsNone(summary.final_outcome_at)

    def test_a_plant_leaving_retention_is_graded_before_it_is_sold(self):
        """Retention may never have been preceded by a ready decision."""
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.RETAINED))
        record_lifecycle_event(
            self.plant,
            self.user,
            OutcomeRequest(EventType.RETENTION_ENDED, reason='Back into sale stock.'),
        )
        with self.assertRaises(ValidationError):
            record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.SOLD))
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.SOLD))
        self.assertEqual(
            plant_lifecycle_summary(self.plant).state,
            LifecycleState.SOLD,
        )

    def test_a_backward_fact_requires_a_reason(self):
        """Stock leaving offer unexplained is indistinguishable from a slip."""
        for event_type, prior in (
                (EventType.HELD_BACK, EventType.READY),
                (EventType.RETENTION_ENDED, EventType.RETAINED)):
            with self.subTest(event_type=event_type):
                plant = make_specific_plant()
                record_germination_event(plant, self.user)
                record_lifecycle_event(plant, self.user, OutcomeRequest(prior))
                with self.assertRaises(ValidationError) as caught:
                    record_lifecycle_event(plant, self.user, OutcomeRequest(event_type))
                self.assertEqual(
                    caught.exception.message_dict['reason'],
                    ['A reason is required.'],
                )

    def test_a_blank_reason_does_not_satisfy_the_requirement(self):
        """Whitespace is not an explanation."""
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))
        with self.assertRaises(ValidationError) as caught:
            record_lifecycle_event(
                self.plant, self.user, OutcomeRequest(EventType.HELD_BACK, reason='   '),
            )
        self.assertIn('reason', caught.exception.message_dict)

    def test_a_backward_fact_cannot_predate_the_fact_it_reverses(self):
        """Chronology applies to a backward fact like any other."""
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))
        with self.assertRaises(ValidationError) as caught:
            record_lifecycle_event(
                self.plant,
                self.user,
                OutcomeRequest(
                    EventType.HELD_BACK,
                    occurred_at=timezone.now() - timedelta(days=7),
                    reason='Gone leggy in the heat.',
                ),
            )
        self.assertIn('occurred_at', caught.exception.message_dict)

    def test_holding_back_is_rejected_from_every_other_state(self):
        """Only stock that is on offer can be taken off it."""
        for priors in (
                (),
                (EventType.RETAINED,),
                (EventType.READY, EventType.SOLD),
                (EventType.READY, EventType.SOLD, EventType.RETURNED_QUARANTINED)):
            with self.subTest(priors=priors):
                plant = make_specific_plant()
                record_germination_event(plant, self.user)
                for prior in priors:
                    record_lifecycle_event(plant, self.user, OutcomeRequest(prior))
                with self.assertRaises(ValidationError) as caught:
                    record_lifecycle_event(
                        plant,
                        self.user,
                        OutcomeRequest(EventType.HELD_BACK, reason='Gone leggy.'),
                    )
                self.assertIn('event_type', caught.exception.message_dict)

    def test_ending_a_retention_is_rejected_without_one_to_end(self):
        """The fact names a retention, so there has to be one."""
        for priors in ((), (EventType.READY,)):
            with self.subTest(priors=priors):
                plant = make_specific_plant()
                record_germination_event(plant, self.user)
                for prior in priors:
                    record_lifecycle_event(plant, self.user, OutcomeRequest(prior))
                with self.assertRaises(ValidationError) as caught:
                    record_lifecycle_event(
                        plant,
                        self.user,
                        OutcomeRequest(EventType.RETENTION_ENDED, reason='Back to stock.'),
                    )
                self.assertIn('event_type', caught.exception.message_dict)

    def test_a_backward_fact_leaves_the_plant_where_it_stands(self):
        """Withdrawing stock changes the plan for it, not its bench."""
        location = make_specific_plant_location(specific_plant=self.plant)
        self.held_back_plant()
        location.refresh_from_db()
        self.assertIsNone(location.ended)

    def test_a_rejection_names_the_backward_fact_it_refused(self):
        """The message reads as a sentence for the new vocabulary too."""
        with self.assertRaises(ValidationError) as caught:
            record_lifecycle_event(
                self.plant,
                self.user,
                OutcomeRequest(EventType.HELD_BACK, reason='Gone leggy.'),
            )
        self.assertEqual(
            caught.exception.message_dict['event_type'],
            ['A growing plant cannot be recorded as held back from sale.'],
        )


class AvailabilityIntervalTests(TestCase):
    """A repeated cycle is reported as spans, not as one latest state."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='interval')
        self.start = timezone.now() - timedelta(days=30)
        self.plant = make_specific_plant(germinated=self.start)
        record_germination_event(self.plant, self.user)

    def record(self, event_type, day, reason=''):
        """Record one fact a fixed number of days into the history."""
        return record_lifecycle_event(
            self.plant,
            self.user,
            OutcomeRequest(
                event_type,
                occurred_at=self.start + timedelta(days=day),
                reason=reason,
            ),
        )

    def intervals(self):
        """Return the plant's offered spans as recorded so far."""
        return availability_intervals(list(self.plant.lifecycle_events.all()))

    def test_a_plant_never_offered_has_no_intervals(self):
        """Growing stock has not been on offer at all."""
        self.assertEqual(self.intervals(), [])

    def test_an_offered_plant_has_one_open_interval(self):
        """Stock currently on offer has no end date yet."""
        self.record(EventType.READY, 1)
        intervals = self.intervals()
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0].started, self.start + timedelta(days=1))
        self.assertIsNone(intervals[0].ended)

    def test_holding_back_closes_the_interval_it_ends(self):
        """The plant was ready for exactly that long, and the span says so."""
        self.record(EventType.READY, 1)
        self.record(EventType.HELD_BACK, 5, reason='Gone leggy in the heat.')
        self.assertEqual(
            self.intervals(),
            [(self.start + timedelta(days=1), self.start + timedelta(days=5))],
        )

    def test_a_repeated_cycle_reports_every_offer(self):
        """This is what only the latest state cannot show."""
        self.record(EventType.READY, 1)
        self.record(EventType.HELD_BACK, 5, reason='Gone leggy in the heat.')
        self.record(EventType.READY, 12)
        self.record(EventType.HELD_BACK, 20, reason='Wanted for next season.')
        self.record(EventType.READY, 25)
        intervals = self.intervals()
        self.assertEqual(len(intervals), 3)
        self.assertEqual(
            [interval.ended for interval in intervals],
            [
                self.start + timedelta(days=5),
                self.start + timedelta(days=20),
                None,
            ],
        )

    def test_a_correction_leaves_no_interval_where_holding_back_leaves_one(self):
        """The two mechanisms are told apart by what the history keeps."""
        mistake = self.record(EventType.READY, 1)
        reverse_lifecycle_event(
            mistake,
            self.user,
            'Recorded against the wrong plant.',
            occurred_at=self.start + timedelta(days=2),
        )
        self.assertEqual(self.intervals(), [])

        other = make_specific_plant(germinated=self.start)
        record_germination_event(other, self.user)
        for event_type, day, reason in (
                (EventType.READY, 1, ''),
                (EventType.HELD_BACK, 2, 'Gone leggy in the heat.')):
            record_lifecycle_event(
                other,
                self.user,
                OutcomeRequest(
                    event_type,
                    occurred_at=self.start + timedelta(days=day),
                    reason=reason,
                ),
            )
        self.assertEqual(
            len(availability_intervals(list(other.lifecycle_events.all()))),
            1,
        )

    def test_a_sale_closes_the_interval_and_a_return_opens_another(self):
        """Every fact reaching a sellable state starts a span, not only ready."""
        self.record(EventType.READY, 1)
        self.record(EventType.SOLD, 5)
        self.record(EventType.RETURNED_AVAILABLE, 9)
        intervals = self.intervals()
        self.assertEqual(len(intervals), 2)
        self.assertEqual(intervals[0].ended, self.start + timedelta(days=5))
        self.assertEqual(intervals[1].started, self.start + timedelta(days=9))

    def test_the_summary_reports_when_the_current_state_began(self):
        """A screen can tell stock held back yesterday from stock held in March."""
        self.record(EventType.READY, 1)
        self.record(EventType.HELD_BACK, 5, reason='Gone leggy in the heat.')
        summary = plant_lifecycle_summary(self.plant)
        self.assertEqual(summary.state, LifecycleState.GROWING)
        self.assertEqual(summary.state_since, self.start + timedelta(days=5))
