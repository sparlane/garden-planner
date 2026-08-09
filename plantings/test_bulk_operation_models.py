"""Tests for the shared audit records behind bulk plant work."""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from tests.factories import make_specific_plant

from .lifecycle import EventType, OutcomeRequest, record_germination_event, record_lifecycle_event
from .models import BulkPlantOperation, BulkPlantOperationResult


class BulkPlantOperationModelTests(TestCase):
    """An operation groups immutable per-plant results without replacing them."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='bulk-auditor')
        self.plant = make_specific_plant()
        record_germination_event(self.plant, self.user)
        self.operation = BulkPlantOperation.objects.create(
            workspace=self.plant.workspace,
            idempotency_key=uuid4(),
            request_digest='a' * 64,
            action=BulkPlantOperation.Action.READY,
            atomicity=BulkPlantOperation.Atomicity.ALL_OR_NOTHING,
            occurred_at=timezone.now(),
            created_by=self.user,
            selection_source={'mode': 'ids'},
            action_payload={},
        )

    def test_an_operation_and_its_results_are_immutable(self):
        """A completed audit cannot be rewritten or removed."""
        event = record_lifecycle_event(
            self.plant,
            self.user,
            OutcomeRequest(EventType.READY),
        )
        result = BulkPlantOperationResult.objects.create(
            workspace=self.plant.workspace,
            operation=self.operation,
            plant=self.plant,
            status=BulkPlantOperationResult.Status.APPLIED,
            lifecycle_event=event,
        )

        self.operation.reason = 'Rewritten'
        with self.assertRaises(ValidationError):
            self.operation.save()
        with self.assertRaises(ValidationError):
            self.operation.delete()
        result.errors = ['Rewritten']
        with self.assertRaises(ValidationError):
            result.save()
        with self.assertRaises(ValidationError):
            result.delete()

    def test_an_idempotency_key_is_unique_inside_a_workspace(self):
        """One request identity cannot describe two completed operations."""
        with self.assertRaises(ValidationError):
            BulkPlantOperation.objects.create(
                workspace=self.operation.workspace,
                idempotency_key=self.operation.idempotency_key,
                request_digest='b' * 64,
                action=BulkPlantOperation.Action.CULL,
                atomicity=BulkPlantOperation.Atomicity.ELIGIBLE_ONLY,
                occurred_at=timezone.now(),
            )

    def test_a_result_cannot_point_at_another_plants_event(self):
        """The shared audit never substitutes for per-plant traceability."""
        other = make_specific_plant()
        record_germination_event(other, self.user)
        event = record_lifecycle_event(
            other,
            self.user,
            OutcomeRequest(EventType.READY),
        )
        with self.assertRaises(ValidationError) as caught:
            BulkPlantOperationResult.objects.create(
                workspace=self.plant.workspace,
                operation=self.operation,
                plant=self.plant,
                status=BulkPlantOperationResult.Status.APPLIED,
                lifecycle_event=event,
            )
        self.assertIn('lifecycle_event', caught.exception.message_dict)
