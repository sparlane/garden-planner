"""Retiring catalog records without taking them out of history."""

from supplies.defaults import ensure_default_supplier
from tests.api import RESTContractTestCase
from tests.factories import (
    make_plant,
    make_plant_family,
    make_plant_variety,
    make_seed_tray_model,
    make_seeds,
    make_supplier,
)
from workspaces.models import get_current_workspace


class CatalogRetirementTests(RESTContractTestCase):
    """A retired record leaves the selectors and stays in the records."""

    def setUp(self):
        super().setUp()
        self.family = make_plant_family(name='Nightshades')
        self.plant = make_plant(family=self.family, name='Tomato')
        self.variety = make_plant_variety(plant=self.plant, name='Roma')

    def retire(self, url, active=False):
        """Ask the API to change one catalog record's activation state."""
        return self.client.patch(url, {'active': active}, format='json')

    def test_retiring_a_family_waits_for_the_plants_under_it(self):
        """Retirement never cascades, so what disappears is always chosen."""
        response = self.retire(f'/plants/family/{self.family.pk}/')

        self.assertEqual(response.status_code, 400)
        self.assertIn('Tomato', response.data['active'][0])

    def test_a_catalog_branch_retires_from_the_bottom_up(self):
        """Each level goes once nothing active is left hanging off it."""
        variety = self.retire(f'/plants/variety/{self.variety.pk}/')
        plant = self.retire(f'/plants/plant/{self.plant.pk}/')
        family = self.retire(f'/plants/family/{self.family.pk}/')

        self.assertEqual(variety.status_code, 200, variety.data)
        self.assertEqual(plant.status_code, 200, plant.data)
        self.assertEqual(family.status_code, 200, family.data)
        self.assertFalse(family.data['active'])

    def test_a_plant_cannot_be_restored_under_a_retired_family(self):
        """A record is offerable only while its whole chain is."""
        self.retire(f'/plants/variety/{self.variety.pk}/')
        self.retire(f'/plants/plant/{self.plant.pk}/')
        self.retire(f'/plants/family/{self.family.pk}/')

        response = self.retire(f'/plants/plant/{self.plant.pk}/', active=True)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['active'], ['Restore Nightshades first.'])

    def test_a_restored_family_lets_its_plants_come_back(self):
        """Restoring from the top down returns the branch to the selectors."""
        self.retire(f'/plants/variety/{self.variety.pk}/')
        self.retire(f'/plants/plant/{self.plant.pk}/')
        self.retire(f'/plants/family/{self.family.pk}/')

        family = self.retire(f'/plants/family/{self.family.pk}/', active=True)
        plant = self.retire(f'/plants/plant/{self.plant.pk}/', active=True)

        self.assertEqual(family.status_code, 200, family.data)
        self.assertEqual(plant.status_code, 200, plant.data)
        self.assertTrue(plant.data['active'])

    def test_a_retired_variety_is_no_longer_offered_as_a_choice(self):
        """A new record cannot be pointed at something taken out of use."""
        supplier = make_supplier(name='Local Seed Company')
        self.retire(f'/plants/variety/{self.variety.pk}/')

        response = self.client.post('/seeds/seeds/', {
            'supplier': supplier.pk,
            'plant_variety': self.variety.pk,
            'supplier_code': 'ROMA-1',
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('plant_variety', response.data)

    def test_a_record_already_on_a_retired_variety_can_still_be_corrected(self):
        """Correcting an old record must not silently repoint it."""
        seeds = make_seeds(plant_variety=self.variety)
        self.retire(f'/seeds/seeds/{seeds.pk}/')
        self.retire(f'/plants/variety/{self.variety.pk}/')

        response = self.client.patch(
            f'/seeds/seeds/{seeds.pk}/',
            {'plant_variety': self.variety.pk, 'notes': 'Discontinued line'},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['plant_variety'], self.variety.pk)

    def test_the_collection_answers_both_questions_it_is_asked(self):
        """Selectors ask for the active records; history reads them all."""
        retired = make_plant(family=self.family, name='Retired crop')
        self.retire(f'/plants/plant/{retired.pk}/')

        everything = self.client.get('/plants/plant/').data
        offerable = self.client.get('/plants/plant/', {'active': 'true'}).data
        withdrawn = self.client.get('/plants/plant/', {'active': 'false'}).data

        self.assertEqual({row['pk'] for row in everything}, {self.plant.pk, retired.pk})
        self.assertEqual({row['pk'] for row in offerable}, {self.plant.pk})
        self.assertEqual({row['pk'] for row in withdrawn}, {retired.pk})

    def test_a_retired_record_still_reads_from_its_own_route(self):
        """Nothing pointing at it loses the name it was recorded under."""
        self.retire(f'/plants/variety/{self.variety.pk}/')

        response = self.client.get(f'/plants/variety/{self.variety.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['name'], 'Roma')
        self.assertFalse(response.data['active'])

    def test_an_unusable_activation_filter_is_refused(self):
        """A mistyped filter is an error rather than a silently full list."""
        response = self.client.get('/plants/plant/', {'active': 'yes'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('active', response.data)


class SupplyCatalogRetirementTests(RESTContractTestCase):
    """Suppliers, seed catalogs, and tray models retire the same way."""

    def test_a_supplier_waits_for_its_seed_catalog_entries(self):
        """A brand stays offerable while something still hangs off it."""
        seeds = make_seeds()

        response = self.client.patch(
            f'/supplies/supplier/{seeds.supplier.pk}/',
            {'active': False},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(str(seeds), response.data['active'][0])

    def test_a_seed_catalog_entry_retires_on_its_own(self):
        """The entry is the leaf of the catalog, so nothing blocks it."""
        seeds = make_seeds()

        response = self.client.patch(
            f'/seeds/seeds/{seeds.pk}/', {'active': False}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['active'])

    def test_the_stand_in_supplier_cannot_be_retired(self):
        """An unnamed Basic Garden purchase still has to land somewhere."""
        supplier = ensure_default_supplier(get_current_workspace())

        response = self.client.patch(
            f'/supplies/supplier/{supplier.pk}/', {'active': False}, format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['active'], ['The stand-in supplier cannot be retired.'],
        )

    def test_a_tray_model_retires_and_comes_back(self):
        """An obsolete tray stops being offered without losing its trays."""
        tray_model = make_seed_tray_model()
        url = f'/seedtrays/seedtraymodels/{tray_model.pk}/'

        retired = self.client.patch(url, {'active': False}, format='json')
        restored = self.client.patch(url, {'active': True}, format='json')

        self.assertEqual(retired.status_code, 200, retired.data)
        self.assertFalse(retired.data['active'])
        self.assertEqual(restored.status_code, 200, restored.data)
        self.assertTrue(restored.data['active'])
