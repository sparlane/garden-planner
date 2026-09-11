"""Finding a record in a catalog that has grown past reading straight through."""

from tests.api import RESTContractTestCase
from tests.factories import (
    make_plant,
    make_plant_family,
    make_plant_variety,
    make_seed_tray_model,
    make_seeds,
    make_supplier,
)


class CatalogSearchTestCase(RESTContractTestCase):
    """One crop hierarchy, asked to find a record in itself."""

    def setUp(self):
        super().setUp()
        self.nightshades = make_plant_family(name='Nightshades')
        self.tomato = make_plant(family=self.nightshades, name='Tomato')
        self.roma = make_plant_variety(plant=self.tomato, name='Roma')
        self.maker = make_plant_variety(plant=self.tomato, name='Money-Maker')
        self.legumes = make_plant_family(name='Legumes')
        self.pea = make_plant(family=self.legumes, name='Pea')
        self.onward = make_plant_variety(plant=self.pea, name='Onward')

    def found(self, collection, query):
        """Return the names a search turned up, in the order it returned them."""
        return self.listed_names(collection, search=query)


class NameMatchingTests(CatalogSearchTestCase):
    """What counts as answering to a word somebody typed."""

    def test_a_word_is_matched_by_the_start_of_a_name(self):
        """A catalog is read by the starts of its words."""
        self.assertEqual(self.found('/plants/variety/', 'rom'), ['Roma'])

    def test_a_word_inside_a_name_is_not_a_match(self):
        """Matching the middles too would answer with most of the catalog."""
        self.assertEqual(self.found('/plants/variety/', 'oma'), [])

    def test_punctuation_and_case_do_not_hide_a_name(self):
        """They are how one name gets typed twice, not how two names differ."""
        self.assertEqual(self.found('/plants/variety/', 'money maker'), ['Money-Maker'])

    def test_every_word_of_the_query_has_to_match(self):
        """Two words are how somebody narrows an answer, not widens it."""
        self.assertEqual(self.found('/plants/variety/', 'mon mak'), ['Money-Maker'])
        self.assertEqual(self.found('/plants/variety/', 'mon roma'), [])

    def test_a_mistyped_query_still_finds_the_name(self):
        """This is the search a cleanup starts with."""
        self.assertEqual(self.found('/plants/plant/', 'Tomatoe'), ['Tomato'])

    def test_a_blank_search_narrows_nothing(self):
        """An empty box is not a filter that matches nothing."""
        self.assertCountEqual(
            self.found('/plants/family/', ''), ['Nightshades', 'Legumes'],
        )

    def test_an_unmatched_query_finds_nothing(self):
        """A search that cannot find it should say so rather than guess."""
        self.assertEqual(self.found('/plants/variety/', 'Black Krim'), [])


class CatalogHierarchySearchTests(CatalogSearchTestCase):
    """One query sent to three collections still reads as a tree."""

    def test_a_variety_keeps_the_crop_and_family_it_hangs_off(self):
        """A match nobody can see the parents of is a broken screen."""
        self.assertEqual(self.found('/plants/variety/', 'Roma'), ['Roma'])
        self.assertEqual(self.found('/plants/plant/', 'Roma'), ['Tomato'])
        self.assertEqual(self.found('/plants/family/', 'Roma'), ['Nightshades'])

    def test_a_family_keeps_everything_filed_under_it(self):
        """Searching the top of a branch is how somebody reads that branch."""
        self.assertEqual(self.found('/plants/family/', 'Nightshades'), ['Nightshades'])
        self.assertEqual(self.found('/plants/plant/', 'Nightshades'), ['Tomato'])
        self.assertEqual(
            self.found('/plants/variety/', 'Nightshades'), ['Roma', 'Money-Maker'],
        )

    def test_a_crop_keeps_its_family_and_its_varieties(self):
        """The middle of a branch reaches both ways."""
        self.assertEqual(self.found('/plants/family/', 'Tomato'), ['Nightshades'])
        self.assertEqual(
            self.found('/plants/variety/', 'Tomato'), ['Roma', 'Money-Maker'],
        )

    def test_another_branch_is_left_out(self):
        """Narrowing that keeps everything has not narrowed anything."""
        self.assertEqual(self.found('/plants/variety/', 'Legumes'), ['Onward'])


class RetiredSearchTests(CatalogSearchTestCase):
    """Searching is how a retired record is found again."""

    def test_a_retired_record_is_still_findable(self):
        """It is what somebody looking to restore one is looking for."""
        self.client.patch(
            f'/plants/variety/{self.roma.pk}/', {'active': False}, format='json',
        )

        self.assertEqual(self.found('/plants/variety/', 'Roma'), ['Roma'])

    def test_a_search_composes_with_the_activation_filter(self):
        """A selector asking for choices asks for the active ones."""
        self.client.patch(
            f'/plants/variety/{self.roma.pk}/', {'active': False}, format='json',
        )

        response = self.client.get(
            '/plants/variety/', {'search': 'Tomato', 'active': 'true'},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [record['name'] for record in response.data['results']], ['Money-Maker'],
        )


class SupplierSearchTests(RESTContractTestCase):
    """A flat catalog, found by its own name and nothing else."""

    def test_a_supplier_is_found_by_its_name(self):
        """Which is the only name a supplier has."""
        make_supplier(name='Kings Seeds')
        make_supplier(name='Egmont Seeds')

        response = self.client.get('/supplies/supplier/', {'search': 'kings'})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [record['name'] for record in response.data['results']], ['Kings Seeds'],
        )


class SeedCatalogSearchTests(RESTContractTestCase):
    """An entry with no name of its own is found by the names it holds."""

    def setUp(self):
        super().setUp()
        self.supplier = make_supplier(name='Kings Seeds')
        self.variety = make_plant_variety(
            plant=make_plant(family=make_plant_family(name='Nightshades'), name='Tomato'),
            name='Roma',
        )
        self.entry = make_seeds(
            supplier=self.supplier,
            plant_variety=self.variety,
            supplier_code='KS-4412',
        )
        make_seeds(
            supplier=make_supplier(name='Egmont Seeds'),
            plant_variety=make_plant_variety(name='Onward'),
        )

    def found(self, query):
        """Return the entries a search turned up."""
        response = self.client.get('/seeds/seeds/', {'search': query})
        self.assertEqual(response.status_code, 200, response.data)
        return [record['pk'] for record in response.data['results']]

    def test_an_entry_is_found_by_its_supplier(self):
        """Reading one supplier's part of the catalog is the common search."""
        self.assertEqual(self.found('Kings'), [self.entry.pk])

    def test_an_entry_is_found_by_its_variety(self):
        """Which is what somebody about to sow it knows it as."""
        self.assertEqual(self.found('Roma'), [self.entry.pk])

    def test_an_entry_is_found_by_the_crop_above_its_variety(self):
        """A packet is looked for as a tomato before it is a Roma."""
        self.assertEqual(self.found('Tomato'), [self.entry.pk])

    def test_an_entry_is_found_by_the_code_on_the_packet(self):
        """It is what the packet in somebody's hand is printed with."""
        self.assertEqual(self.found('KS-4412'), [self.entry.pk])


class TrayModelSearchTests(RESTContractTestCase):
    """The catalog named by an identifier rather than a name."""

    def test_a_tray_model_is_found_by_its_identifier(self):
        """It is the one name a tray model has."""
        model = make_seed_tray_model(identifier='72-cell propagator')
        make_seed_tray_model(identifier='40-cell propagator')

        response = self.client.get(
            '/seedtrays/seedtraymodels/', {'search': '72 cell'},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [record['pk'] for record in response.data['results']], [model.pk],
        )
