"""Warning that the catalog already means what somebody is typing."""

from common.duplicates import normalized
from tests.api import RESTContractTestCase
from tests.factories import (
    make_plant,
    make_plant_family,
    make_plant_variety,
    make_seed_tray_model,
    make_seeds,
    make_supplier,
)


class NormalizedNameTests(RESTContractTestCase):
    """The form two catalog names are compared in."""

    def test_case_punctuation_and_spacing_do_not_distinguish_a_name(self):
        """They are how one name gets typed twice, not how two names differ."""
        for written in ('Money-Maker', 'money   maker', 'MONEY MAKER', 'Money.Maker'):
            with self.subTest(written=written):
                self.assertEqual(normalized(written), 'money maker')

    def test_a_trailing_plural_does_not_distinguish_a_name(self):
        """Half a catalog is filed in the plural and half is not."""
        self.assertEqual(normalized('Chives'), normalized('Chive'))
        self.assertEqual(normalized('Broad Beans'), normalized('Broad Bean'))

    def test_a_short_word_keeps_its_s(self):
        """Stripping one from `as` or `is` would leave nothing to compare."""
        self.assertEqual(normalized('As'), 'as')


class CatalogDuplicateTestCase(RESTContractTestCase):
    """One crop hierarchy, asked what it already holds."""

    def setUp(self):
        super().setUp()
        self.family = make_plant_family(name='Nightshades')
        self.plant = make_plant(family=self.family, name='Tomato')
        self.roma = make_plant_variety(plant=self.plant, name='Roma')

    def check(self, collection, name, **scope):
        """Ask a collection what already means this name."""
        response = self.client.get(f'{collection}duplicates/', {'name': name, **scope})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def labels(self, data):
        """Return the records the warning named."""
        return [entry['label'] for entry in data['candidates']]


class DuplicateWarningTests(CatalogDuplicateTestCase):
    """A warning names what is already there and refuses nothing."""

    def test_the_same_name_typed_again_is_reported(self):
        """The commonest duplicate is somebody not knowing it exists."""
        data = self.check('/plants/variety/', 'Roma', plant=self.plant.pk)

        self.assertEqual(self.labels(data), ['Roma'])
        self.assertEqual(data['candidates'][0]['reason'], 'the same name')

    def test_a_typo_of_an_existing_name_is_reported(self):
        """This is the duplicate a merge exists to clean up afterwards."""
        make_plant(family=self.family, name='Tomatoe')

        data = self.check(
            '/plants/plant/', 'Tomato', family=self.family.pk, exclude=self.plant.pk,
        )

        self.assertEqual(self.labels(data), ['Tomatoe'])
        self.assertEqual(data['candidates'][0]['reason'], 'nearly the same name')

    def test_the_same_name_is_reported_ahead_of_a_near_one(self):
        """An exact match is the one an operator most likely meant."""
        make_plant_variety(plant=self.plant, name='Romar')

        data = self.check('/plants/variety/', 'Roma', plant=self.plant.pk)

        self.assertEqual(self.labels(data), ['Roma', 'Romar'])

    def test_a_short_name_has_to_match_exactly(self):
        """At three letters a single edit is usually a different crop."""
        make_plant(family=self.family, name='Pea')

        data = self.check('/plants/plant/', 'Tea', family=self.family.pk)

        self.assertEqual(self.labels(data), [])

    def test_an_unrelated_name_is_not_reported(self):
        """A warning nobody can act on is noise on every keystroke."""
        data = self.check('/plants/variety/', 'Black Krim', plant=self.plant.pk)

        self.assertEqual(self.labels(data), [])

    def test_a_blank_name_reports_nothing(self):
        """An empty form has not proposed anything to be a duplicate of."""
        data = self.check('/plants/variety/', '', plant=self.plant.pk)

        self.assertEqual(data['candidates'], [])

    def test_a_warning_creates_nothing(self):
        """It is the question, asked before anything is saved."""
        self.check('/plants/variety/', 'Roma', plant=self.plant.pk)

        self.assertEqual(
            self.client.get('/plants/variety/').data['count'], 1,
        )


class DuplicateScopeTests(CatalogDuplicateTestCase):
    """A duplicate is only a duplicate under the same parents."""

    def test_the_same_name_under_another_parent_is_not_a_duplicate(self):
        """Two records are interchangeable only where a merge would allow it."""
        pepper = make_plant(family=self.family, name='Pepper')
        make_plant_variety(plant=pepper, name='Cherry')

        data = self.check('/plants/variety/', 'Cherry', plant=self.plant.pk)

        self.assertEqual(self.labels(data), [])

    def test_the_parent_has_to_be_named(self):
        """Without it the answer would come from another crop entirely."""
        response = self.client.get('/plants/variety/duplicates/', {'name': 'Roma'})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['plant'], ['Name the plant to check against.'])

    def test_a_record_is_not_a_duplicate_of_itself(self):
        """Renaming an entry has to be able to leave the name alone."""
        data = self.check(
            '/plants/variety/', 'Roma', plant=self.plant.pk, exclude=self.roma.pk,
        )

        self.assertEqual(self.labels(data), [])

    def test_a_parentless_catalog_needs_no_scope(self):
        """Suppliers and families hang off nothing but the workspace."""
        make_supplier(name='Kings Seeds')

        data = self.check('/supplies/supplier/', 'Kings Seed')

        self.assertEqual(self.labels(data), ['Kings Seeds'])


class SupersededDuplicateTests(CatalogDuplicateTestCase):
    """A name that was corrected away is the one worth warning about most."""

    def test_a_merged_duplicate_says_where_the_catalog_went(self):
        """Retyping it recreates exactly the duplicate somebody cleaned up."""
        duplicate = make_plant_variety(plant=self.plant, name='Romar')
        merged = self.client.post(
            f'/plants/variety/{duplicate.pk}/merge/',
            {'into': self.roma.pk},
            format='json',
        )
        self.assertEqual(merged.status_code, 200, merged.data)

        data = self.check('/plants/variety/', 'Romar', plant=self.plant.pk)

        entry = next(row for row in data['candidates'] if row['label'] == 'Romar')
        self.assertFalse(entry['active'])
        self.assertEqual(
            entry['handoff'], {'relation': 'merged_into', 'pk': self.roma.pk, 'label': 'Roma'},
        )

    def test_a_retired_name_is_still_taken(self):
        """It stays readable everywhere it is recorded, so it still means that."""
        retired = make_plant_variety(plant=self.plant, name='Black Krim')
        self.client.patch(
            f'/plants/variety/{retired.pk}/', {'active': False}, format='json',
        )

        data = self.check('/plants/variety/', 'Black Krim', plant=self.plant.pk)

        self.assertEqual(self.labels(data), ['Black Krim'])
        self.assertFalse(data['candidates'][0]['active'])
        self.assertIsNone(data['candidates'][0]['handoff'])


class TrayModelDuplicateTests(RESTContractTestCase):
    """The one catalog name the database also holds to."""

    def test_a_tray_model_is_checked_on_its_identifier(self):
        """It is named by an identifier rather than a name."""
        make_seed_tray_model(identifier='72-cell propagator')

        response = self.client.get(
            '/seedtrays/seedtraymodels/duplicates/',
            {'identifier': '72 cell propagator'},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [entry['label'] for entry in response.data['candidates']],
            ['72-cell propagator'],
        )
        self.assertEqual(response.data['candidates'][0]['reason'], 'the same name')


class SeedCatalogDuplicateTests(RESTContractTestCase):
    """The catalog with no name typed on it, and no merge behind it."""

    def setUp(self):
        super().setUp()
        self.supplier = make_supplier(name='Kings Seeds')
        self.roma = make_plant_variety(name='Roma')

    def check(self, **params):
        """Ask the seed catalog what it already holds under these parents."""
        response = self.client.get('/seeds/seeds/duplicates/', params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_holding_one_suppliers_variety_twice_is_the_duplicate(self):
        """There is nothing else an entry could resemble."""
        held = make_seeds(supplier=self.supplier, plant_variety=self.roma)

        data = self.check(supplier=self.supplier.pk, plant_variety=self.roma.pk)

        self.assertEqual([entry['pk'] for entry in data['candidates']], [held.pk])
        self.assertEqual(
            data['candidates'][0]['reason'], 'the same supplier and variety',
        )

    def test_the_answer_reports_nothing_that_was_compared(self):
        """No name was typed, so there is no normalized form of one."""
        data = self.check(supplier=self.supplier.pk, plant_variety=self.roma.pk)

        self.assertEqual(list(data), ['candidates'])

    def test_the_same_variety_from_another_supplier_is_not_a_duplicate(self):
        """Two suppliers' Roma are two packets and two inventory items."""
        make_seeds(supplier=make_supplier(name='Egmont Seeds'), plant_variety=self.roma)

        data = self.check(supplier=self.supplier.pk, plant_variety=self.roma.pk)

        self.assertEqual(data['candidates'], [])

    def test_another_variety_from_the_same_supplier_is_not_a_duplicate(self):
        """A supplier's catalog is most of what the warning has to ignore."""
        make_seeds(
            supplier=self.supplier, plant_variety=make_plant_variety(name='Black Krim'),
        )

        data = self.check(supplier=self.supplier.pk, plant_variety=self.roma.pk)

        self.assertEqual(data['candidates'], [])

    def test_both_parents_have_to_be_named(self):
        """Half the key answers about entries holding some other variety."""
        response = self.client.get(
            '/seeds/seeds/duplicates/', {'supplier': self.supplier.pk},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['plant_variety'],
            ['Name the plant_variety to check against.'],
        )

    def test_an_entry_is_not_a_duplicate_of_itself(self):
        """Correcting an entry has to be able to leave its identity alone."""
        held = make_seeds(supplier=self.supplier, plant_variety=self.roma)

        data = self.check(
            supplier=self.supplier.pk, plant_variety=self.roma.pk, exclude=held.pk,
        )

        self.assertEqual(data['candidates'], [])

    def test_a_superseded_entry_says_where_the_catalog_went(self):
        """It is retired, so nothing offers it, and it is still that seed."""
        created = self.client.post(
            '/seeds/seeds/',
            {'supplier': self.supplier.pk, 'plant_variety': self.roma.pk},
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        held = created.data
        other = make_supplier(name='Egmont Seeds')
        replaced = self.client.post(
            f'/seeds/seeds/{held["pk"]}/replace/', {'supplier': other.pk}, format='json',
        )
        self.assertEqual(replaced.status_code, 200, replaced.data)

        data = self.check(supplier=self.supplier.pk, plant_variety=self.roma.pk)

        entry = data['candidates'][0]
        self.assertEqual(entry['pk'], held['pk'])
        self.assertFalse(entry['active'])
        self.assertEqual(entry['handoff']['relation'], 'replaced_by')
        self.assertEqual(
            entry['handoff']['pk'], replaced.data['replacement']['pk'],
        )

    def test_a_warning_creates_nothing(self):
        """It is the question, asked before anything is saved."""
        self.check(supplier=self.supplier.pk, plant_variety=self.roma.pk)

        self.assertEqual(self.client.get('/seeds/seeds/').data['count'], 0)
