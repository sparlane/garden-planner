"""Correcting a usage setting, which is a catalog record held by its code.

A growth stage, a plant grade, a health observation type and a health
diagnosis are wrong in the same three ways a crop is, and take the same
corrections, so most of what holds here is already covered by the shared
catalog tests. What is tested here is what a setting carries that a crop does
not.

That is the stable code, and everything that follows from it: it is written
once, a code already taken is the record somebody is looking for rather than a
resemblance to warn about, and a wrong one is corrected by merging into the
record carrying the right one.

A diagnosis carries one thing more. Its category files it the way a plant
files a variety, without being a record anybody can retire, so the rules that
ask what makes two records interchangeable read it where they read a parent: a
cross-category merge is refused as a reclassification, and the duplicate
warning is asked within one category or not at all.

A care rule is the same record with two of those groups rather than one: the
kind of work it makes and the fact it counts from are together what every task
under it was projected as, so both are read where a diagnosis reads its
category.
"""

# Test names state their behavior; repeating it in method docstrings adds noise.
# pylint: disable=missing-function-docstring

from health.models import HealthDiagnosis, HealthObservationType
from health.services import preview_observation
from health.services import record_observation as record_health_observation
from plantings.growth import record_observation
from plantings.models import GrowthStage, PlantGrade
from tests.api import RESTContractTestCase
from tests.factories import (
    make_growth_stage,
    make_health_diagnosis,
    make_health_observation_type,
    make_nursery_workspace,
    make_plant_grade,
    make_specific_plant,
    make_work_rule,
)
from work.models import WorkTaskRule, WorkTaskType

STAGES = '/plantings/growth-stages/'
GRADES = '/plantings/plant-grades/'
TYPES = '/health/observation-types/'
DIAGNOSES = '/health/diagnoses/'
RULES = '/work/rules/'


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


class GroupedSettingTestCase(RESTContractTestCase):
    """The health catalogs, which are usage settings with a group above them."""

    def setUp(self):
        super().setUp()
        self.workspace = make_nursery_workspace()
        self.pest_signs = HealthObservationType.objects.get(
            workspace=self.workspace, code='pest-signs',
        )
        self.unknown_pest = HealthDiagnosis.objects.get(
            workspace=self.workspace, code='unknown-pest',
        )
        self.unknown_disease = HealthDiagnosis.objects.get(
            workspace=self.workspace, code='unknown-disease',
        )

    def observe(self, plant, **values):
        """Record one observation over a single plant, reviewed as it stands."""
        scopes = [{'type': 'plant', 'id': plant.pk}]
        reviewed = preview_observation(self.workspace, scopes)
        return record_health_observation(
            self.workspace, self.user, scopes=scopes,
            reviewed_digest=reviewed['digest'], severity='low', **values,
        )


class ObservationTypeCorrectionTests(GroupedSettingTestCase):
    """An observation type is corrected the way any coded setting is."""

    def test_a_retired_type_is_no_longer_offered(self):
        observed = make_specific_plant()
        scopes = [{'type': 'plant', 'id': observed.pk}]
        reviewed = preview_observation(self.workspace, scopes)
        self.client.patch(f'{TYPES}{self.pest_signs.pk}/', {'active': False}, format='json')

        response = self.client.post('/health/observations/', {
            'scopes': scopes, 'reviewed_digest': reviewed['digest'],
            'observation_type': self.pest_signs.pk, 'severity': 'low',
        }, format='json')

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('observation_type', response.data)

    def test_the_collection_still_names_a_retired_type(self):
        """It is what an inspection recorded last season points at."""
        self.client.patch(f'{TYPES}{self.pest_signs.pk}/', {'active': False}, format='json')

        self.assertIn(self.pest_signs.name, self.listed_names(TYPES))
        self.assertNotIn(self.pest_signs.name, self.listed_names(TYPES, active='true'))

    def test_a_merge_moves_the_observations_and_retires_the_duplicate(self):
        duplicate = make_health_observation_type(code='pest-sign', name='Pest sign')
        observation = self.observe(
            make_specific_plant(), observation_type=duplicate,
        )

        response = self.client.post(
            f'{TYPES}{duplicate.pk}/merge/', {'into': self.pest_signs.pk}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        observation.refresh_from_db()
        duplicate.refresh_from_db()
        self.assertEqual(observation.observation_type_id, self.pest_signs.pk)
        self.assertEqual(duplicate.merged_into_id, self.pest_signs.pk)
        self.assertFalse(duplicate.active)

    def test_a_resembling_name_already_in_the_catalog_is_reported(self):
        response = self.client.get(f'{TYPES}duplicates/', {'name': 'Pest sign'})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn(
            str(self.pest_signs), [entry['label'] for entry in response.data['candidates']],
        )

    def test_a_type_is_found_by_the_start_of_its_name(self):
        self.assertIn(self.pest_signs.name, self.listed_names(TYPES, search='pest'))

    def test_an_observation_is_still_correctable_under_a_retired_type(self):
        """Correcting is the only way to fix one, and it restates what it said."""
        observation = self.observe(make_specific_plant(), observation_type=self.pest_signs)
        self.client.patch(f'{TYPES}{self.pest_signs.pk}/', {'active': False}, format='json')

        response = self.client.post(f'/health/observations/{observation.pk}/correct/', {
            'observation_type': self.pest_signs.pk, 'severity': 'high',
            'correction_reason': 'Closer inspection changed the assessment.',
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)


class DiagnosisGroupingTests(GroupedSettingTestCase):
    """A category files a diagnosis without being a record anybody can retire."""

    def test_two_diagnoses_in_one_category_merge(self):
        duplicate = make_health_diagnosis(
            code='unidentified-pest', name='Unidentified pest',
            category=HealthDiagnosis.Category.PEST,
        )

        response = self.client.post(
            f'{DIAGNOSES}{duplicate.pk}/merge/',
            {'into': self.unknown_pest.pk}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.merged_into_id, self.unknown_pest.pk)

    def test_a_merge_across_categories_is_refused_as_a_reclassification(self):
        """Every observation of a pest would otherwise become one of a disease."""
        response = self.client.post(
            f'{DIAGNOSES}{self.unknown_pest.pk}/merge/',
            {'into': self.unknown_disease.pk}, format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('category', response.data['into'][0])
        self.assertIn('Disease', response.data['into'][0])
        self.unknown_pest.refresh_from_db()
        self.assertIsNone(self.unknown_pest.merged_into_id)

    def test_the_preview_names_the_reclassification_before_anything_moves(self):
        response = self.client.get(
            f'{DIAGNOSES}{self.unknown_pest.pk}/merge/',
            {'into': self.unknown_disease.pk},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['blockers'])

    def test_the_category_a_diagnosis_is_filed_under_stays_editable(self):
        """It is a field on the record, not the identity other records hold."""
        response = self.client.patch(
            f'{DIAGNOSES}{self.unknown_pest.pk}/',
            {'category': HealthDiagnosis.Category.DISEASE}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.unknown_pest.refresh_from_db()
        self.assertEqual(self.unknown_pest.category, HealthDiagnosis.Category.DISEASE)


class DiagnosisDuplicateWarningTests(GroupedSettingTestCase):
    """The warning is asked within the category, because the merge would be."""

    def test_a_resembling_name_in_the_same_category_is_reported(self):
        response = self.client.get(f'{DIAGNOSES}duplicates/', {
            'name': 'Unknown pests', 'category': HealthDiagnosis.Category.PEST,
        })

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn(
            str(self.unknown_pest),
            [entry['label'] for entry in response.data['candidates']],
        )

    def test_a_resembling_name_in_another_category_is_not(self):
        """Naming it would offer a merge that is then refused."""
        response = self.client.get(f'{DIAGNOSES}duplicates/', {
            'name': 'Unknown pests', 'category': HealthDiagnosis.Category.DISEASE,
        })

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['candidates'], [])

    def test_the_category_has_to_be_named_to_check_against(self):
        response = self.client.get(f'{DIAGNOSES}duplicates/', {'name': 'Unknown pests'})

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('category', response.data)


class DiagnosisSearchTests(GroupedSettingTestCase):
    """A diagnosis is found by its own name and by the group above it."""

    def test_a_diagnosis_is_found_by_the_start_of_its_name(self):
        self.assertIn(self.unknown_pest.name, self.listed_names(DIAGNOSES, search='unkn'))

    def test_a_diagnosis_is_found_by_the_category_it_is_filed_under(self):
        """Somebody cleaning up the pests starts by asking for the pests."""
        found = self.listed_names(DIAGNOSES, search='pest')

        self.assertIn(self.unknown_pest.name, found)
        self.assertNotIn(self.unknown_disease.name, found)


class CareRuleTestCase(RESTContractTestCase):
    """The care rules a workspace runs by, which are coded settings too."""

    def setUp(self):
        super().setUp()
        self.workspace = make_nursery_workspace()
        self.checks = WorkTaskRule.objects.get(
            workspace=self.workspace, code='germination-check',
        )

    def watering_rule(self, **overrides):
        """Create one calendar watering rule, which is what somebody types."""
        return make_work_rule(workspace=self.workspace, **overrides)


class CareRuleCorrectionTests(CareRuleTestCase):
    """A rule is held by its code the way every other usage setting is."""

    def test_a_stable_code_cannot_be_rewritten(self):
        """The seeded defaults are re-found by code every time a workspace saves."""
        response = self.client.patch(
            f'{RULES}{self.checks.pk}/', {'code': 'germination-checks'}, format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('merge', response.data['code'][0].lower())
        self.checks.refresh_from_db()
        self.assertEqual(self.checks.code, 'germination-check')

    def test_a_name_typed_wrong_is_corrected_in_place(self):
        response = self.client.patch(
            f'{RULES}{self.checks.pk}/', {'name': 'Germination checks'}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.checks.refresh_from_db()
        self.assertEqual(self.checks.name, 'Germination checks')

    def test_a_code_already_taken_names_the_rule_holding_it(self):
        """It is exactly the rule being looked for, not a resemblance."""
        response = self.client.post(RULES, {
            'code': 'Germination-Check', 'name': 'Germination checks',
            'task_type': WorkTaskType.WATERING,
            'trigger': WorkTaskRule.Trigger.CALENDAR,
            'frequency': WorkTaskRule.Frequency.DAILY,
        }, format='json')

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(str(self.checks), response.data['code'][0])
        self.assertIn('Merge into it', response.data['code'][0])

    def test_what_a_rule_projects_stays_editable(self):
        """Refiling one is the deliberate edit a merge refuses to make."""
        response = self.client.patch(
            f'{RULES}{self.checks.pk}/',
            {'task_type': WorkTaskType.HEALTH_INSPECTION}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.checks.refresh_from_db()
        self.assertEqual(self.checks.task_type, WorkTaskType.HEALTH_INSPECTION)


class CareRuleRetirementTests(CareRuleTestCase):
    """Retiring is what disabling a rule always did, said in the shared words."""

    def test_a_retired_rule_stops_projecting_work(self):
        response = self.client.patch(
            f'{RULES}{self.checks.pk}/', {'active': False}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.checks.refresh_from_db()
        self.assertFalse(self.checks.active)

    def test_the_collection_still_names_a_retired_rule(self):
        """It is what the tasks acknowledged under it point at."""
        self.client.patch(f'{RULES}{self.checks.pk}/', {'active': False}, format='json')

        self.assertIn(self.checks.name, self.listed_names(RULES))
        self.assertNotIn(self.checks.name, self.listed_names(RULES, active='true'))

    def test_a_retired_rule_restores(self):
        self.client.patch(f'{RULES}{self.checks.pk}/', {'active': False}, format='json')

        response = self.client.patch(
            f'{RULES}{self.checks.pk}/', {'active': True}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn(self.checks.name, self.listed_names(RULES, active='true'))

    def test_a_rule_is_never_deleted(self):
        response = self.client.delete(f'{RULES}{self.checks.pk}/')

        self.assertEqual(response.status_code, 405)


class CareRuleMergeTests(CareRuleTestCase):
    """Two rules are interchangeable only while they project the same work."""

    def setUp(self):
        super().setUp()
        self.watering = self.watering_rule(code='watering', name='Watering')
        self.duplicate = self.watering_rule(code='watering-again', name='Waterings')

    def test_a_merge_retires_the_duplicate_pointing_at_where_it_went(self):
        response = self.client.post(
            f'{RULES}{self.duplicate.pk}/merge/', {'into': self.watering.pk},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.duplicate.refresh_from_db()
        self.assertFalse(self.duplicate.active)
        self.assertEqual(self.duplicate.merged_into_id, self.watering.pk)

    def test_a_merge_into_another_kind_of_work_is_refused(self):
        """Every task under it was acknowledged as the work the rule makes."""
        feeding = self.watering_rule(
            code='feeding', name='Feeding', task_type=WorkTaskType.FEEDING,
        )

        response = self.client.get(
            f'{RULES}{self.duplicate.pk}/merge/', {'into': feeding.pk},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['blockers'])

    def test_a_merge_across_the_anchor_it_counts_from_is_refused(self):
        response = self.client.get(
            f'{RULES}{self.duplicate.pk}/merge/', {'into': self.checks.pk},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['blockers'])


class CareRuleDuplicateWarningTests(CareRuleTestCase):
    """The merge question, asked while the second rule is still half typed."""

    def setUp(self):
        super().setUp()
        self.watering = self.watering_rule(code='watering', name='Watering')

    def test_a_resembling_name_making_the_same_work_is_reported(self):
        response = self.client.get(f'{RULES}duplicates/', {
            'name': 'Waterings', 'task_type': WorkTaskType.WATERING,
            'trigger': WorkTaskRule.Trigger.CALENDAR,
        })

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn(
            str(self.watering),
            [entry['label'] for entry in response.data['candidates']],
        )

    def test_a_resembling_name_making_other_work_is_not(self):
        """Naming it would offer a merge that is then refused."""
        response = self.client.get(f'{RULES}duplicates/', {
            'name': 'Waterings', 'task_type': WorkTaskType.FEEDING,
            'trigger': WorkTaskRule.Trigger.CALENDAR,
        })

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['candidates'], [])

    def test_both_halves_of_what_files_a_rule_have_to_be_named(self):
        response = self.client.get(f'{RULES}duplicates/', {'name': 'Waterings'})

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('task_type', response.data)


class CareRuleSearchTests(CareRuleTestCase):
    """A rule is found by what an operator was shown, either half of it."""

    def test_a_rule_is_found_by_the_start_of_its_name(self):
        self.assertIn(self.checks.name, self.listed_names(RULES, search='germ'))

    def test_a_rule_is_found_by_its_code(self):
        """Somebody who met it in a seeded default met the code, not the name."""
        rule = self.watering_rule(code='bench-water', name='Propagation round')

        self.assertIn(rule.name, self.listed_names(RULES, search='bench'))

    def test_a_search_that_matches_nothing_narrows_to_nothing(self):
        self.assertEqual(self.listed_names(RULES, search='hydroponics'), [])
