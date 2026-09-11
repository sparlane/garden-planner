"""Correcting a usage setting, which is a catalog record held by its code.

A growth stage and a plant grade are wrong in the same three ways a crop is,
and take the same corrections, so most of what holds here is already covered
by the shared catalog tests. What is tested here is the one field they carry
that a crop does not, and everything that follows from it: a stable code is
written once, a code already taken is the record somebody is looking for
rather than a resemblance to warn about, and a wrong one is corrected by
merging into the record carrying the right one.
"""

# Test names state their behavior; repeating it in method docstrings adds noise.
# pylint: disable=missing-function-docstring

from plantings.growth import record_observation
from plantings.models import GrowthStage, PlantGrade
from tests.api import RESTContractTestCase
from tests.factories import (
    make_growth_stage,
    make_nursery_workspace,
    make_plant_grade,
    make_specific_plant,
)

STAGES = '/plantings/growth-stages/'
GRADES = '/plantings/plant-grades/'


class CodedSettingTestCase(RESTContractTestCase):
    """One nursery workspace and the settings it was seeded with."""

    def setUp(self):
        super().setUp()
        self.workspace = make_nursery_workspace()
        self.rooted = GrowthStage.objects.get(workspace=self.workspace, code='rooted')
        self.standard = PlantGrade.objects.get(
            workspace=self.workspace, code='standard',
        )


class StableCodeTests(CodedSettingTestCase):
    """The code is what everything else holds the setting by."""

    def test_a_stable_code_cannot_be_rewritten(self):
        response = self.client.patch(
            f'{STAGES}{self.rooted.pk}/', {'code': 'struck-cutting'}, format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('merge', response.data['code'][0].lower())
        self.rooted.refresh_from_db()
        self.assertEqual(self.rooted.code, 'rooted')

    def test_resending_the_code_being_shown_is_not_a_change(self):
        """A code is stored normalized, so re-sending it is not an edit."""
        response = self.client.patch(
            f'{STAGES}{self.rooted.pk}/',
            {'code': 'ROOTED ', 'name': 'Rooted cutting'},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.rooted.refresh_from_db()
        self.assertEqual(self.rooted.name, 'Rooted cutting')

    def test_everything_but_the_code_stays_editable(self):
        response = self.client.patch(
            f'{STAGES}{self.rooted.pk}/',
            {'name': 'Rooted cutting', 'target_days': 28},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.rooted.refresh_from_db()
        self.assertEqual((self.rooted.name, self.rooted.target_days), ('Rooted cutting', 28))

    def test_a_code_already_taken_names_the_setting_holding_it(self):
        """It is not a near-duplicate; it is the record being looked for."""
        response = self.client.post(
            STAGES, {'code': 'Rooted', 'name': 'Rooted again'}, format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(str(self.rooted), response.data['code'][0])
        self.assertIn('Merge into it', response.data['code'][0])

    def test_a_code_is_still_required(self):
        response = self.client.post(STAGES, {'code': '  ', 'name': 'Nameless'}, format='json')

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('code', response.data)


class SettingRetirementTests(CodedSettingTestCase):
    """A retired setting leaves the selectors and stays in the history."""

    def test_a_retired_setting_is_no_longer_offered(self):
        observed = make_specific_plant()
        self.client.patch(f'{STAGES}{self.rooted.pk}/', {'active': False}, format='json')

        response = self.client.post('/plantings/nursery-observations/', {
            'plants': [observed.pk], 'stage': self.rooted.pk,
        }, format='json')

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('stage', response.data)

    def test_the_collection_still_names_a_retired_setting(self):
        """It is what an observation recorded last season points at."""
        self.client.patch(f'{STAGES}{self.rooted.pk}/', {'active': False}, format='json')

        self.assertIn(self.rooted.name, self.listed_names(STAGES))
        self.assertNotIn(self.rooted.name, self.listed_names(STAGES, active='true'))

    def test_a_retired_setting_restores(self):
        self.client.patch(f'{STAGES}{self.rooted.pk}/', {'active': False}, format='json')

        response = self.client.patch(
            f'{STAGES}{self.rooted.pk}/', {'active': True}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn(self.rooted.name, self.listed_names(STAGES, active='true'))

    def test_a_grade_retires_the_same_way(self):
        response = self.client.patch(
            f'{GRADES}{self.standard.pk}/', {'active': False}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn(self.standard.name, self.listed_names(GRADES, active='true'))


class SettingMergeTests(CodedSettingTestCase):
    """The correction a frozen code leaves is the merge."""

    def setUp(self):
        super().setUp()
        self.duplicate = make_growth_stage(code='rooted-cutting', name='Rooted cuttings')

    def test_a_merge_moves_the_observations_and_retires_the_duplicate(self):
        observed = make_specific_plant()
        observation = record_observation(
            self.workspace, self.user, plant_ids=[observed.pk], stage=self.duplicate,
        )

        response = self.client.post(
            f'{STAGES}{self.duplicate.pk}/merge/', {'into': self.rooted.pk}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        observation.refresh_from_db()
        self.duplicate.refresh_from_db()
        self.assertEqual(observation.stage_id, self.rooted.pk)
        self.assertFalse(self.duplicate.active)
        self.assertEqual(self.duplicate.merged_into_id, self.rooted.pk)

    def test_a_preview_names_what_would_move_and_moves_nothing(self):
        observed = make_specific_plant()
        observation = record_observation(
            self.workspace, self.user, plant_ids=[observed.pk], stage=self.duplicate,
        )

        response = self.client.get(
            f'{STAGES}{self.duplicate.pk}/merge/', {'into': self.rooted.pk},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['blockers'], [])
        self.assertIn(
            'plantings.nurseryobservation.stage',
            [reference['relation'] for reference in response.data['references']],
        )
        observation.refresh_from_db()
        self.assertEqual(observation.stage_id, self.duplicate.pk)

    def test_the_code_the_duplicate_held_is_kept_rather_than_freed(self):
        """Anything still holding it has to keep resolving to the trail."""
        self.client.post(
            f'{STAGES}{self.duplicate.pk}/merge/', {'into': self.rooted.pk}, format='json',
        )

        response = self.client.post(
            STAGES, {'code': 'rooted-cutting', 'name': 'Rooted cuttings'}, format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('code', response.data)

    def test_a_grade_merges_the_same_way(self):
        duplicate = make_plant_grade(code='std', name='Standards')

        response = self.client.post(
            f'{GRADES}{duplicate.pk}/merge/', {'into': self.standard.pk}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.merged_into_id, self.standard.pk)


class SettingDuplicateWarningTests(CodedSettingTestCase):
    """The merge question, asked while the second setting is half typed."""

    def test_a_resembling_name_already_in_the_catalog_is_reported(self):
        response = self.client.get(f'{STAGES}duplicates/', {'name': 'Rooted'})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn(
            str(self.rooted), [entry['label'] for entry in response.data['candidates']],
        )

    def test_a_setting_merged_away_still_warns_and_says_where_it_went(self):
        duplicate = make_growth_stage(code='rooted-cutting', name='Rooted cuttings')
        self.client.post(
            f'{STAGES}{duplicate.pk}/merge/', {'into': self.rooted.pk}, format='json',
        )

        response = self.client.get(f'{STAGES}duplicates/', {'name': 'Rooted cuttings'})

        candidates = {entry['label']: entry for entry in response.data['candidates']}
        self.assertIn(str(duplicate), candidates)
        self.assertEqual(
            candidates[str(duplicate)]['handoff']['label'], str(self.rooted),
        )


class SettingSearchTests(CodedSettingTestCase):
    """A setting is found by what an operator was shown, either half of it."""

    def test_a_setting_is_found_by_the_start_of_its_name(self):
        self.assertIn(self.rooted.name, self.listed_names(STAGES, search='roo'))

    def test_a_setting_is_found_by_its_code(self):
        """Somebody who met it in a seeded default met the code, not the name."""
        stage = make_growth_stage(code='plugs', name='Cell trays')

        self.assertIn(stage.name, self.listed_names(STAGES, search='plug'))

    def test_a_search_that_matches_nothing_narrows_to_nothing(self):
        self.assertEqual(self.listed_names(STAGES, search='hydroponics'), [])
