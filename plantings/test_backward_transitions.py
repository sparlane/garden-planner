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
    plant_lifecycle_summary,
    record_germination_event,
    record_lifecycle_event,
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
