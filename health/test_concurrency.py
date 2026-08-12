"""Concurrency proofs for health command idempotency.

These tests require real row locks. SQLite skips them; PostgreSQL CI exercises
the same-key race that can otherwise create two quarantine cases.
"""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from django.conf import settings
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from tests.factories import make_specific_plant
from workspaces.models import Workspace, get_current_workspace

from .models import HealthObservation, HealthObservationType, QuarantineAction, QuarantineCase
from .operations import quarantine_observation
from .services import preview_observation, record_observation


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentQuarantineTests(TransactionTestCase):
    """Retries serialize on the observation before creating an action."""

    def _post_teardown(self):
        """Restore the singleton workspace removed by transactional flushing."""
        super()._post_teardown()
        if not Workspace.objects.filter(pk=settings.CURRENT_WORKSPACE_ID).exists():
            Workspace.objects.create(pk=settings.CURRENT_WORKSPACE_ID, name='My Garden')

    def setUp(self):
        super().setUp()
        workspace = get_current_workspace()
        workspace.mode = Workspace.Mode.NURSERY
        workspace.save()
        observation_type = HealthObservationType.objects.get(
            workspace=workspace, code='pest-signs',
        )
        plant = make_specific_plant(workspace=workspace)
        scopes = [{'type': 'plant', 'id': plant.pk}]
        preview = preview_observation(workspace, scopes)
        observation = record_observation(
            workspace, None, scopes=scopes,
            reviewed_digest=preview['digest'],
            observation_type=observation_type,
            severity=HealthObservation.Severity.HIGH,
        )
        self.observation_pk = observation.pk
        self.key = uuid4()

    def _quarantine(self):
        close_old_connections()
        workspace = get_current_workspace()
        observation = HealthObservation.objects.get(pk=self.observation_pk)
        case, action = quarantine_observation(
            workspace, None, observation, idempotency_key=self.key,
            reason='Concurrent reviewed quarantine.',
        )
        result = case.pk, action.pk
        close_old_connections()
        return result

    def test_same_key_creates_one_case_and_one_action(self):
        """Both callers receive the one committed command result."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result() for future in [
                pool.submit(self._quarantine),
                pool.submit(self._quarantine),
            ]]
        self.assertEqual(results[0], results[1])
        self.assertEqual(QuarantineCase.objects.count(), 1)
        self.assertEqual(QuarantineAction.objects.count(), 1)
