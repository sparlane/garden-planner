"""Direct-sown aggregate lifecycle contracts."""

# Test names state their contracts; helper and setup docstrings add no signal.
# pylint: disable=missing-function-docstring

from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APITestCase

from tests.factories import make_garden_planting, make_garden_square, make_location
from workspaces.models import Workspace, get_current_workspace

from .direct_sown import direct_sown_summary, record_direct_sown_event
from .models import DirectSownCropEvent, GardenPlanting, SpecificPlantLocation


class DirectSownLifecycleTests(TestCase):
    """Event replay retains every stage without allowing impossible counts."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.user = get_user_model().objects.create_user(username='direct-lifecycle')
        self.planting = make_garden_planting(
            workspace=self.workspace, source=GardenPlanting.Source.DIRECT_SEED,
            tracking=GardenPlanting.Tracking.AGGREGATE, quantity=20,
            recorded_on=date(2026, 9, 1),
        )

    def record(self, event_type, quantity, **overrides):
        return record_direct_sown_event(
            self.planting, self.user, event_type, date(2026, 9, 2), quantity,
            **overrides,
        )

    def test_partial_emergence_repeated_thinning_and_losses_replay(self):
        self.record('emerged', 8, count_quality='exact')
        self.record('emerged', 4, count_quality='estimated')
        self.record('failed_germination', 8, notes='Seeds did not emerge')
        self.record('thinned', 3, notes='Spaced seedlings')
        self.record('pest_loss', 2, notes='Slugs')

        summary = direct_sown_summary(self.planting)

        self.assertEqual(summary['seeds_sown'], 20)
        self.assertEqual(summary['emerged_plants'], 12)
        self.assertEqual(summary['loss_quantity'], 13)
        self.assertEqual(summary['current_plants'], 7)
        self.assertEqual(summary['count_quality'], 'estimated')

    def test_unknown_emergence_preserves_unknown_current_count(self):
        self.record('emerged', None, count_quality='unknown')

        summary = direct_sown_summary(self.planting)

        self.assertIsNone(summary['emerged_plants'])
        self.assertIsNone(summary['current_plants'])
        self.assertEqual(summary['state'], 'unknown')

    def test_over_removal_is_rejected_without_an_event(self):
        self.record('emerged', 3, count_quality='exact')

        with self.assertRaisesMessage(ValidationError, 'negative'):
            self.record('thinned', 4, notes='Too many')

        self.assertEqual(self.planting.direct_sown_events.count(), 1)


class DirectSownApiTests(APITestCase):
    """The garden register exposes audited actions and separate totals."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='direct-api')
        self.client.force_authenticate(self.user)
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.save(update_fields=['mode'])
        self.planting = make_garden_planting(
            workspace=self.workspace, source=GardenPlanting.Source.DIRECT_SEED,
            quantity=10, recorded_on=date(2026, 9, 1),
        )
        self.url = f'/plantings/garden-register/aggregate-{self.planting.pk}/'

    def post(self, action, payload):
        response = self.client.post(f'{self.url}{action}/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_observe_move_individualize_and_reverse(self):
        emerged = self.post('direct-event', {
            'event_type': 'emerged', 'quantity': 6, 'count_quality': 'exact',
            'occurred_on': '2026-09-02', 'notes': 'First flush',
        })
        destination = make_location(workspace=self.workspace)
        self.post('move-crop', {
            'location': destination.pk, 'occurred_on': '2026-09-03',
            'notes': 'Transplanted to growing bed',
        })
        promoted = self.post('individualize', {
            'quantity': 2, 'names': ['Keeper one', 'Keeper two'],
            'occurred_on': '2026-09-04', 'notes': 'Perennial keepers',
        })

        detail = self.client.get(self.url)
        self.assertEqual(detail.status_code, 200, detail.data)
        lifecycle = detail.data['direct_sown_lifecycle']
        self.assertEqual(lifecycle['seeds_sown'], 10)
        self.assertEqual(lifecycle['emerged_plants'], 6)
        self.assertEqual(lifecycle['current_plants'], 4)
        self.assertEqual(lifecycle['individualized'], 2)
        self.assertEqual(lifecycle['location'], destination.pk)
        self.assertEqual(len(promoted['plants']), 2)
        self.assertEqual(
            set(SpecificPlantLocation.objects.filter(
                specific_plant_id__in=promoted['plants'], ended__isnull=True,
            ).values_list('location_id', flat=True)),
            {destination.pk},
        )

        self.post('reverse-direct-event', {
            'event': emerged['pk'], 'occurred_on': '2026-09-05',
            'notes': 'Observation belonged to another patch',
        })
        self.assertIsNone(self.client.get(self.url).data['direct_sown_lifecycle']['current_plants'])

    def test_retained_count_reconciles_without_silent_editing(self):
        self.post('direct-event', {
            'event_type': 'emerged', 'quantity': 7, 'count_quality': 'estimated',
            'notes': '',
        })
        retained = self.post('direct-event', {
            'event_type': 'retained', 'quantity': 5, 'count_quality': 'exact',
            'notes': 'Counted after thinning',
        })

        self.assertEqual(retained['quantity_delta'], -2)
        self.assertEqual(self.client.get(self.url).data['direct_sown_lifecycle']['current_plants'], 5)

    def test_other_workspace_destination_is_rejected(self):
        other = Workspace.objects.create(name='Other', mode=Workspace.Mode.GARDEN)
        destination = make_garden_square(workspace=other)

        response = self.client.post(
            f'{self.url}move-crop/',
            {'garden_square': destination.pk, 'notes': 'Not ours'}, format='json',
        )

        self.assertEqual(response.status_code, 400)

    def test_non_direct_aggregate_cannot_use_lifecycle(self):
        other = make_garden_planting(workspace=self.workspace)

        response = self.client.post(
            f'/plantings/garden-register/aggregate-{other.pk}/direct-event/',
            {'event_type': 'emerged', 'quantity': 1, 'count_quality': 'exact'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)

    def test_event_rows_are_immutable(self):
        event = DirectSownCropEvent.objects.create(
            workspace=self.workspace, planting=self.planting, event_type='emerged',
            occurred_on=date(2026, 9, 2), quantity=1, quantity_delta=1,
            count_quality='exact',
        )
        event.notes = 'Changed'
        with self.assertRaisesMessage(ValidationError, 'immutable'):
            event.save()
