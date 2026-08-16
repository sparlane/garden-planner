"""Tests for the health data migrations run at deployment time."""

# pylint: disable=duplicate-code

from datetime import timedelta
from importlib import import_module
from uuid import uuid4

from django.apps import apps as django_apps
from django.test import TestCase
from django.utils import timezone

from plantings.lifecycle import (
    EventType,
    LifecycleState,
    OutcomeRequest,
    plant_lifecycle_summary,
    record_lifecycle_event,
)
from plantings.models import PlantLifecycleEvent
from tests.factories import make_specific_plant
from workspaces.models import Workspace, get_current_workspace

from .models import (
    HealthObservation,
    HealthObservationType,
    QuarantineAction,
    QuarantineActionResult,
)
from .operations import quarantine_observation
from .services import preview_observation, record_observation


class ReleasedQuarantineBackfillTests(TestCase):
    """Plants a closed release left behind get the fact that released them."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.observation_type = HealthObservationType.objects.get(
            workspace=self.workspace, code='pest-signs',
        )
        migration = import_module(
            'health.migrations.0004_backfill_released_quarantine'
        )
        self.backfill = migration.backfill_released_quarantine

    def returned_case(self, plant):
        """Open a case over one plant a customer returned into quarantine."""
        scopes = [{'type': 'plant', 'id': plant.pk}]
        preview = preview_observation(self.workspace, scopes)
        observation = record_observation(
            self.workspace, None, scopes=scopes,
            reviewed_digest=preview['digest'],
            observation_type=self.observation_type,
            severity=HealthObservation.Severity.HIGH,
            notes='Returned as diseased.',
        )
        return quarantine_observation(
            self.workspace, None, observation,
            idempotency_key=uuid4(), reason='Returned as diseased.',
        )[0]

    def returned_plant(self):
        """Return one plant whose last recorded fact is a quarantined return."""
        plant = make_specific_plant(workspace=self.workspace)
        for event_type in (
                EventType.READY, EventType.SOLD, EventType.RETURNED_QUARANTINED):
            record_lifecycle_event(plant, None, OutcomeRequest(event_type))
        return plant

    def strand(self, plant, when=None):
        """Close a case the way releasing used to, recording no lifecycle fact."""
        return QuarantineAction.objects.create(
            workspace=self.workspace, case=self.returned_case(plant),
            idempotency_key=uuid4(), action=QuarantineAction.Action.RELEASE,
            occurred_at=when or timezone.now(), reason='Recovered in isolation.',
        )

    def test_a_stranded_plant_is_released_by_the_action_that_closed_its_case(self):
        """The operator already decided; only the consequence went unrecorded."""
        plant = self.returned_plant()
        action = self.strand(plant)
        self.backfill(django_apps, None)
        summary = plant_lifecycle_summary(plant)
        self.assertEqual(summary.state, LifecycleState.AVAILABLE)
        self.assertTrue(summary.sellable)
        event = plant.lifecycle_events.get(event_type=EventType.RELEASED_AVAILABLE)
        self.assertEqual(event.occurred_at, action.occurred_at)
        self.assertEqual(event.reason, action.reason)
        self.assertEqual(event.reference, f'quarantine-action:{action.pk}')
        self.assertEqual(event.batch, plant.batch)
        result = QuarantineActionResult.objects.get(action=action)
        self.assertEqual(result.plant, plant)
        self.assertEqual(result.lifecycle_event, event)

    def test_a_plant_whose_case_is_still_open_is_left_alone(self):
        """Nothing was lost there: the health workflow now resolves it."""
        plant = self.returned_plant()
        self.returned_case(plant)
        self.backfill(django_apps, None)
        self.assertEqual(
            plant_lifecycle_summary(plant).state, LifecycleState.QUARANTINED,
        )
        self.assertFalse(
            plant.lifecycle_events
            .filter(event_type=EventType.RELEASED_AVAILABLE)
            .exists()
        )

    def test_a_plant_that_was_never_quarantined_is_untouched(self):
        """The backfill reads the returns, not every plant on the bench."""
        plant = make_specific_plant(workspace=self.workspace)
        record_lifecycle_event(plant, None, OutcomeRequest(EventType.READY))
        self.backfill(django_apps, None)
        self.assertEqual(
            list(plant.lifecycle_events.values_list('event_type', flat=True)),
            [EventType.READY],
        )

    def test_a_history_the_release_cannot_join_blocks_the_deployment(self):
        """An append out of order would be a worse lie than the missing fact."""
        plant = self.returned_plant()
        action = self.strand(plant)
        PlantLifecycleEvent.objects.create(
            workspace=self.workspace, plant=plant, batch=plant.batch,
            event_type=EventType.TRANSPLANTED,
            occurred_at=action.occurred_at + timedelta(days=1),
        )
        with self.assertRaisesMessage(
            RuntimeError, f'stranded SpecificPlant IDs: [{plant.pk}]',
        ):
            self.backfill(django_apps, None)
        self.assertFalse(
            plant.lifecycle_events
            .filter(event_type=EventType.RELEASED_AVAILABLE)
            .exists()
        )

    def test_the_reported_ids_are_bounded(self):
        """A wholesale failure names enough rows to act on, not every row."""
        migration = import_module(
            'health.migrations.0004_backfill_released_quarantine'
        )
        self.assertEqual(
            migration.describe_rows(range(30), 30),
            f'{list(range(20))} (first 20 of 30)',
        )
