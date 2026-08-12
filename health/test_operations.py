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
from plantings.lifecycle import EventType, OutcomeRequest, record_lifecycle_event
from plantings.models import CohortOperation, PlantCohort
from plantings.register import RegisterFilters, register_queryset
from tests.factories import (
    make_inventory_item,
    make_location,
    make_specific_plant,
    make_stock_lot,
)
from workspaces.models import Workspace, get_current_workspace

from .availability import is_quarantined
from .models import HealthObservation, HealthObservationType, QuarantineAction
from .operations import (
    act_on_quarantine,
    link_treatment,
    quarantine_observation,
    record_follow_up,
)
from .services import preview_observation, record_observation


class HealthOperationTests(TestCase):
    """Health constraints compose with lifecycle and cohort services."""

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
