"""Opening a fill of a tray, and confirming a migrated one."""
# pylint: disable=duplicate-code

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from tests.factories import make_seed_tray, make_seed_tray_generation

from .generations import (
    open_generation,
    open_generation_for,
    require_open_generation,
    review_generation,
)
from .models import SeedTrayGeneration, SeedTrayGenerationEvent


class OpenGenerationTests(TestCase):
    """Filling a tray is an explicit act with a record behind it."""

    def setUp(self):
        self.tray = make_seed_tray()
        self.user = get_user_model().objects.create_user('filler', password='x')

    def test_the_first_fill_is_numbered_from_one(self):
        """A tray's fills are numbered so its history reads in order."""
        generation = open_generation(self.tray, self.user)

        self.assertEqual(generation.sequence, 1)
        self.assertEqual(generation.code, f'TRAY-{self.tray.pk}-1')
        self.assertEqual(generation.status, SeedTrayGeneration.Status.OPEN)
        self.assertEqual(generation.origin, SeedTrayGeneration.Origin.OPERATOR)
        self.assertEqual(generation.review_state, SeedTrayGeneration.ReviewState.NONE)
        self.assertEqual(generation.created_by, self.user)

    def test_opening_records_why_the_fill_exists(self):
        """The history starts at the moment the tray was filled."""
        generation = open_generation(self.tray, self.user)

        event = generation.events.get()
        self.assertEqual(event.event_type, SeedTrayGenerationEvent.EventType.OPENED)
        self.assertEqual(event.occurred_at, generation.opened_at)
        self.assertEqual(event.created_by, self.user)

    def test_a_tray_cannot_be_filled_twice_over(self):
        """The second fill would inherit the first one's seedlings and media."""
        existing = open_generation(self.tray, self.user)

        with self.assertRaises(ValidationError) as caught:
            open_generation(self.tray, self.user)

        self.assertIn(existing.code, ' '.join(caught.exception.messages))
        self.assertEqual(SeedTrayGeneration.objects.filter(tray=self.tray).count(), 1)

    def test_refilling_a_cleaned_tray_takes_the_next_number(self):
        """Reuse is the point; the numbering keeps the cycles apart."""
        first = open_generation(self.tray, self.user)
        SeedTrayGeneration.objects.filter(pk=first.pk).update(
            status=SeedTrayGeneration.Status.CLOSED,
            closed_at=timezone.now(),
        )

        second = open_generation(self.tray, self.user)

        self.assertEqual(second.sequence, 2)
        self.assertEqual(second.code, f'TRAY-{self.tray.pk}-2')

    def test_an_empty_tray_reports_no_open_fill(self):
        """Nothing pretends a tray is filled when it is not."""
        self.assertIsNone(open_generation_for(self.tray))

        with self.assertRaises(ValidationError) as caught:
            require_open_generation(self.tray)

        self.assertIn('no open generation', ' '.join(caught.exception.messages))

    def test_the_error_field_follows_the_caller(self):
        """An application reports this under its own targets field."""
        with self.assertRaises(ValidationError) as caught:
            require_open_generation(self.tray, field='targets')

        self.assertIn('targets', caught.exception.message_dict)


class ReviewGenerationTests(TestCase):
    """A migrated fill stays flagged until somebody confirms it."""

    def setUp(self):
        self.user = get_user_model().objects.create_user('reviewer', password='x')
        self.generation = make_seed_tray_generation(
            origin=SeedTrayGeneration.Origin.LEGACY,
            review_state=SeedTrayGeneration.ReviewState.NEEDS_REVIEW,
        )

    def test_reviewing_clears_the_flag_and_records_who_said_so(self):
        """The confirmation is an operator's statement, so it is kept."""
        generation = review_generation(
            self.generation,
            self.user,
            'Checked against the sowing notebook; one fill.',
        )

        self.assertEqual(generation.review_state, SeedTrayGeneration.ReviewState.NONE)
        event = generation.events.get(
            event_type=SeedTrayGenerationEvent.EventType.REVIEWED,
        )
        self.assertEqual(event.created_by, self.user)
        self.assertIn('sowing notebook', event.reason)

    def test_a_review_needs_a_reason(self):
        """An unexplained confirmation is indistinguishable from a guess."""
        with self.assertRaises(ValidationError) as caught:
            review_generation(self.generation, self.user, '   ')

        self.assertIn('reason', caught.exception.message_dict)
        self.generation.refresh_from_db()
        self.assertEqual(
            self.generation.review_state,
            SeedTrayGeneration.ReviewState.NEEDS_REVIEW,
        )

    def test_a_reviewed_generation_cannot_be_reviewed_again(self):
        """Repeating it would append a second confirmation of nothing."""
        review_generation(self.generation, self.user, 'Confirmed.')

        with self.assertRaises(ValidationError) as caught:
            review_generation(self.generation, self.user, 'Confirmed again.')

        self.assertIn('review_state', caught.exception.message_dict)
