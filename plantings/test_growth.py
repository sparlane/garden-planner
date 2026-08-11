"""Tests for Nursery growth catalogs and append-only observations."""

# Test names state their behavior; repeating it in method docstrings adds noise.
# pylint: disable=missing-function-docstring

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from inventory.models import InventoryItem
from inventory.units import UnitCode
from tests.api import RESTContractTestCase
from tests.factories import make_specific_plant
from workspaces.models import Workspace, get_current_workspace

from .growth import correct_observation, current_growth, record_observation
from .models import GrowthStage, NurseryObservation, PlantGrade


class GrowthObservationTests(TestCase):
    """Current facts replay independently without rewriting old rows."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.plant = make_specific_plant()
        self.stage = GrowthStage.objects.get(workspace=self.workspace, code='rooted')
        self.grade = PlantGrade.objects.get(workspace=self.workspace, code='standard')
        self.container = InventoryItem.objects.create(
            workspace=self.workspace,
            name='P9 pot',
            category=InventoryItem.Category.POT_CONTAINER,
            base_unit=UnitCode.EACH,
            container_size_label='P9',
            container_volume_ml=500,
            container_footprint_m2='0.008100',
        )

    def test_records_snapshots_and_projects_each_fact(self):
        observation = record_observation(
            self.workspace, None,
            plant_ids=[self.plant.pk],
            stage=self.stage,
            container_item=self.container,
            container_count=1,
            expected_ready=timezone.localdate() + timedelta(days=7),
        )
        self.container.name = 'Renamed pot'
        self.container.save()
        current = current_growth(self.plant)
        self.assertEqual(current['stage'], self.stage)
        self.assertEqual(current['container_name'], 'P9 pot')
        self.assertEqual(current['container_count'], 1)
        self.assertEqual(observation.container_size_label, 'P9')

    def test_whole_observation_correction_falls_back_per_field(self):
        old = record_observation(
            self.workspace, None, plant_ids=[self.plant.pk],
            stage=self.stage, grade=self.grade,
        )
        earlier_grade = PlantGrade.objects.get(workspace=self.workspace, code='premium')
        record_observation(
            self.workspace, None, plant_ids=[self.plant.pk],
            grade=earlier_grade, occurred_at=old.occurred_at - timedelta(days=1),
        )
        replacement = correct_observation(
            self.workspace, None, observation_id=old.pk,
            notes='The combined reading was entered against the wrong plants.',
        )
        current = current_growth(self.plant)
        self.assertIsNone(current['stage'])
        self.assertEqual(current['grade'], earlier_grade)
        self.assertEqual(replacement.corrects, old)
        self.assertEqual(NurseryObservation.objects.count(), 3)
        with self.assertRaises(ValidationError):
            correct_observation(
                self.workspace, None, observation_id=old.pk,
                notes='A second correction is not allowed.',
            )


class GrowthRestTests(RESTContractTestCase):
    """Nursery routes scope catalogs and write observations through services."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()

    def test_catalogs_and_observation_contract(self):
        stage = self.client.get('/plantings/growth-stages/').data[0]
        self.assertIn('target_days', stage)
        plant = make_specific_plant()
        response = self.client.post('/plantings/nursery-observations/', {
            'plants': [plant.pk],
            'stage': stage['pk'],
            'height_cm': '12.500',
            'photo_url': 'https://example.test/plant-photo.jpg',
            'notes': 'Rooted evenly.',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        history = self.client.get(
            f'/plantings/nursery-observations/?plant={plant.pk}',
        )
        self.assertEqual(len(history.data), 1)
        self.assertEqual(history.data[0]['stage_name'], stage['name'])
        self.assertEqual(history.data[0]['photo_url'], 'https://example.test/plant-photo.jpg')

    def test_catalog_codes_are_stable_after_creation(self):
        response = self.client.post('/plantings/growth-stages/', {
            'code': 'acclimating', 'name': 'Acclimating', 'display_order': 8,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        changed = self.client.patch(
            f"/plantings/growth-stages/{response.data['pk']}/",
            {'code': 'renamed'}, format='json',
        )
        self.assertEqual(changed.status_code, 400)
