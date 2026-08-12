"""Tests for reviewed, immutable nursery health evidence."""

# Test names describe behavior directly.
# pylint: disable=missing-function-docstring

from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from plantings.models import PlantCohort, SpecificPlantLocation
from tests.api import RESTContractTestCase
from tests.factories import (
    make_seed_tray_cell_planting,
    make_seed_tray,
    make_seed_tray_generation,
    make_seed_tray_planting,
    make_location,
    make_specific_plant,
    make_specific_plant_location,
)
from workspaces.models import Workspace, get_current_workspace

from .models import HealthDiagnosis, HealthObservation, HealthObservationType
from .services import correct_observation, preview_observation, record_observation


class HealthObservationServiceTests(TestCase):
    """The recorded affected set is concrete and cannot be rewritten."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.observation_type = HealthObservationType.objects.get(
            workspace=self.workspace, code='pest-signs',
        )
        self.diagnosis = HealthDiagnosis.objects.get(
            workspace=self.workspace, code='unknown-pest',
        )

    def test_batch_scope_deduplicates_plants_and_freezes_whole_cohort(self):
        plant = make_specific_plant(workspace=self.workspace)
        batch = plant.batch
        cohort = PlantCohort.objects.create(
            workspace=self.workspace, batch=batch, quantity=4,
        )
        preview = preview_observation(self.workspace, [
            {'type': 'batch', 'id': batch.pk},
            {'type': 'plant', 'id': plant.pk},
        ])
        self.assertEqual(preview['plants'], [plant.pk])
        self.assertEqual(preview['cohorts'], [{'cohort': cohort.pk, 'quantity': 4}])
        self.assertEqual(preview['affected_count'], 5)
        observation = record_observation(
            self.workspace, None,
            scopes=[
                {'type': 'batch', 'id': batch.pk},
                {'type': 'plant', 'id': plant.pk},
            ],
            reviewed_digest=preview['digest'],
            observation_type=self.observation_type,
            severity=HealthObservation.Severity.MODERATE,
            diagnoses=[(self.diagnosis, 'suspected')],
            evidence=[{
                'url': 'https://example.test/aphids.jpg',
                'label': 'Leaf underside',
            }],
            notes='Several insects under new growth.',
        )
        cohort.quantity = 2
        cohort.save()
        self.assertCountEqual(
            list(observation.affected_stock.values_list('cohort_id', 'quantity')),
            [(cohort.pk, 4), (None, 1)],
        )
        self.assertEqual(observation.diagnoses.get().certainty, 'suspected')
        self.assertEqual(observation.evidence_links.get().label, 'Leaf underside')

    def test_every_supported_scope_resolves_the_same_concrete_stock(self):
        tray = make_seed_tray(workspace=self.workspace)
        generation = make_seed_tray_generation(tray=tray)
        planting = make_seed_tray_planting(
            seed_tray=tray, generation=generation, workspace=self.workspace,
        )
        allocation = make_seed_tray_cell_planting(seed_tray_planting=planting)
        plant = make_specific_plant(
            cell_planting=allocation, workspace=self.workspace,
        )
        make_specific_plant_location(specific_plant=plant)
        location = tray.inventory_unit.current_location
        cohort = PlantCohort.objects.create(
            workspace=self.workspace, batch=plant.batch,
            source_sowing=planting, location=location, quantity=3,
        )
        scopes = {
            'plant': (plant.pk, [plant.pk], []),
            'cohort': (cohort.pk, [], [{'cohort': cohort.pk, 'quantity': 3}]),
            'tray': (tray.pk, [plant.pk], [{'cohort': cohort.pk, 'quantity': 3}]),
            'generation': (
                generation.pk, [plant.pk],
                [{'cohort': cohort.pk, 'quantity': 3}],
            ),
            'batch': (
                plant.batch_id, [plant.pk],
                [{'cohort': cohort.pk, 'quantity': 3}],
            ),
            'location': (
                location.pk, [plant.pk],
                [{'cohort': cohort.pk, 'quantity': 3}],
            ),
        }
        for scope_type, (scope_id, plants, cohorts) in scopes.items():
            with self.subTest(scope_type=scope_type):
                preview = preview_observation(
                    self.workspace, [{'type': scope_type, 'id': scope_id}],
                )
                self.assertEqual(preview['plants'], plants)
                self.assertEqual(preview['cohorts'], cohorts)

    def test_confirmation_rejects_changed_reviewed_set(self):
        planting = make_seed_tray_planting(workspace=self.workspace)
        batch = planting.batch
        preview = preview_observation(
            self.workspace, [{'type': 'batch', 'id': batch.pk}],
        )
        allocation = make_seed_tray_cell_planting(seed_tray_planting=planting)
        make_specific_plant(cell_planting=allocation, workspace=self.workspace)
        with self.assertRaisesMessage(ValidationError, 'review it again'):
            record_observation(
                self.workspace, None,
                scopes=[{'type': 'batch', 'id': batch.pk}],
                reviewed_digest=preview['digest'],
                observation_type=self.observation_type,
                severity=HealthObservation.Severity.LOW,
            )
        self.assertFalse(HealthObservation.objects.exists())

    def test_correction_retains_original_scope_and_affected_set(self):
        plant = make_specific_plant(workspace=self.workspace)
        scopes = [{'type': 'plant', 'id': plant.pk}]
        preview = preview_observation(self.workspace, scopes)
        original = record_observation(
            self.workspace, None, scopes=scopes,
            reviewed_digest=preview['digest'], observation_type=self.observation_type,
            severity=HealthObservation.Severity.HIGH,
        )
        replacement = correct_observation(
            self.workspace, None, original,
            observation_type=self.observation_type,
            severity=HealthObservation.Severity.LOW,
            correction_reason='Severity was selected incorrectly.',
        )
        self.assertEqual(replacement.corrects, original)
        self.assertEqual(replacement.affected_stock.get().plant, plant)
        with self.assertRaisesMessage(ValidationError, 'already been corrected'):
            correct_observation(
                self.workspace, None, original,
                observation_type=self.observation_type,
                severity=HealthObservation.Severity.LOW,
                correction_reason='Another correction.',
            )
        with self.assertRaisesMessage(ValidationError, 'immutable'):
            original.save()


class HealthObservationRestTests(RESTContractTestCase):
    """Nursery health endpoints expose reviewed evidence without data leakage."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()

    def test_preview_create_list_and_correct_contract(self):
        plant = make_specific_plant(workspace=self.workspace)
        scope = [{'type': 'plant', 'id': plant.pk}]
        preview = self.client.post(
            '/health/observations/preview/', {'scopes': scope}, format='json',
        )
        self.assertEqual(preview.status_code, 200, preview.data)
        observation_type = HealthObservationType.objects.get(
            workspace=self.workspace, code='pest-signs',
        )
        diagnosis = HealthDiagnosis.objects.get(
            workspace=self.workspace, code='unknown-pest',
        )
        created = self.client.post('/health/observations/', {
            'scopes': scope,
            'reviewed_digest': preview.data['digest'],
            'observation_type': observation_type.pk,
            'severity': 'moderate',
            'diagnoses': [{
                'diagnosis': diagnosis.pk, 'certainty': 'suspected',
            }],
            'evidence': [{
                'url': 'https://example.test/evidence.jpg', 'label': 'Leaf',
            }],
            'notes': 'Review confirmed.',
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['affected_count'], 1)
        history = self.client.get(f'/health/observations/?plant={plant.pk}')
        self.assertEqual(len(history.data), 1)
        corrected = self.client.post(
            f"/health/observations/{created.data['pk']}/correct/", {
                'observation_type': observation_type.pk,
                'severity': 'low',
                'correction_reason': 'Closer inspection changed the assessment.',
            }, format='json',
        )
        self.assertEqual(corrected.status_code, 201, corrected.data)
        history = self.client.get(f'/health/observations/?plant={plant.pk}')
        self.assertEqual([row['pk'] for row in history.data], [corrected.data['pk']])

    def test_garden_workspace_is_rejected(self):
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.save()
        response = self.client.get('/health/observation-types/')
        self.assertEqual(response.status_code, 403)

    def test_active_alerts_surface_in_location_and_task_views(self):
        location = make_location(workspace=self.workspace)
        plant = make_specific_plant(workspace=self.workspace)
        make_specific_plant_location(
            specific_plant=plant,
            location_type=SpecificPlantLocation.LOCATION,
            seed_tray_cell=None,
            location=location,
        )
        scopes = [{'type': 'plant', 'id': plant.pk}]
        preview = self.client.post(
            '/health/observations/preview/', {'scopes': scopes}, format='json',
        )
        observation_type = HealthObservationType.objects.get(
            workspace=self.workspace, code='pest-signs',
        )
        observation = self.client.post('/health/observations/', {
            'scopes': scopes,
            'reviewed_digest': preview.data['digest'],
            'observation_type': observation_type.pk,
            'severity': 'high',
            'follow_up_due_at': timezone.now().isoformat(),
        }, format='json')
        constrained = self.client.post(
            f"/health/observations/{observation.data['pk']}/quarantine/",
            {
                'idempotency_key': str(uuid4()),
                'reason': 'Keep away from healthy plants.',
            },
            format='json',
        )
        self.assertEqual(constrained.status_code, 201, constrained.data)
        occupancy = self.client.get(f'/locations/{location.pk}/occupancy/')
        self.assertEqual(occupancy.data['active_health_alerts'], 1)
        queue = self.client.get('/work/tasks/')
        health_task = next(
            row for row in queue.data
            if row['source_snapshot'].get('health_observation') == observation.data['pk']
        )
        plant_link = next(
            row for row in health_task['links']
            if row['target_type'] == 'specificplant'
        )
        self.assertEqual(plant_link['active_health_alerts'], 1)
