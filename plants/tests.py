"""
Tests for plants
"""
from tests.api import RESTContractTestCase


class PlantRESTContractTests(RESTContractTestCase):
    """Smoke tests for plant taxonomy REST resources."""

    LIST_URLS = (
        '/plants/family/',
        '/plants/plant/',
        '/plants/variety/',
    )

    def test_list_routes_require_authentication(self):
        """Anonymous requests cannot list plant resources."""
        self.assert_authentication_required(self.LIST_URLS)

    def test_list_routes_return_lists(self):
        """Authenticated plant collections use the common list contract."""
        self.assert_list_contract(self.LIST_URLS)

    def test_resources_round_trip(self):
        """Family, plant, and variety fields survive create and retrieve."""
        family = self.assert_create_retrieve(
            '/plants/family/',
            {
                'name': 'Apiaceae',
                'notes': 'Carrot family',
            },
        )
        plant = self.assert_create_retrieve(
            '/plants/plant/',
            {
                'family': family['pk'],
                'name': 'Carrot',
                'notes': 'Root vegetable',
                'spacing': 5,
                'inter_row_spacing': 20,
                'plants_per_square_foot': 16,
                'germination_days_min': 7,
                'germination_days_max': 21,
                'maturity_days_min': 60,
                'maturity_days_max': 80,
                'maturity_basis': 'transplanting',
            },
        )
        variety = self.assert_create_retrieve(
            '/plants/variety/',
            {
                'plant': plant['pk'],
                'name': 'Nantes',
                'notes': 'Early variety',
                'spacing': 4,
                'inter_row_spacing': 18,
                'plants_per_square_foot': 16,
                'germination_days_min': 7,
                'germination_days_max': 18,
                'maturity_days_min': 55,
                'maturity_days_max': 70,
            },
        )
        self.assertIsNone(variety['maturity_basis'])
        self.assertEqual(variety['effective_maturity_basis'], 'transplanting')

    def test_resources_can_be_edited_and_reassigned(self):
        """Catalog corrections may update details and hierarchy relationships."""
        first_family = self.client.post(
            '/plants/family/', {'name': 'First'}, format='json',
        ).data
        second_family = self.client.post(
            '/plants/family/', {'name': 'Second'}, format='json',
        ).data
        first_plant = self.client.post(
            '/plants/plant/',
            {'family': first_family['pk'], 'name': 'Tomato'},
            format='json',
        ).data
        second_plant = self.client.post(
            '/plants/plant/',
            {'family': second_family['pk'], 'name': 'Pepper'},
            format='json',
        ).data
        variety = self.client.post(
            '/plants/variety/',
            {'plant': first_plant['pk'], 'name': 'Roma'},
            format='json',
        ).data

        family_response = self.client.patch(
            f"/plants/family/{first_family['pk']}/",
            {'name': 'Nightshades', 'notes': 'Corrected'},
            format='json',
        )
        plant_response = self.client.patch(
            f"/plants/plant/{first_plant['pk']}/",
            {
                'family': second_family['pk'],
                'notes': 'Moved after correction',
                'maturity_days_min': 60,
                'maturity_basis': 'transplanting',
            },
            format='json',
        )
        variety_response = self.client.patch(
            f"/plants/variety/{variety['pk']}/",
            {
                'plant': second_plant['pk'],
                'spacing': 450,
                'maturity_basis': 'seed',
            },
            format='json',
        )

        self.assertEqual(family_response.status_code, 200, family_response.data)
        self.assertEqual(family_response.data['name'], 'Nightshades')
        self.assertEqual(plant_response.status_code, 200, plant_response.data)
        self.assertEqual(plant_response.data['family'], second_family['pk'])
        self.assertEqual(plant_response.data['maturity_basis'], 'transplanting')
        self.assertEqual(variety_response.status_code, 200, variety_response.data)
        self.assertEqual(variety_response.data['plant'], second_plant['pk'])
        self.assertEqual(variety_response.data['effective_maturity_basis'], 'seed')

    def test_variety_maturity_basis_can_return_to_inherited_default(self):
        """A null override follows later changes to the parent plant default."""
        family = self.client.post(
            '/plants/family/', {'name': 'Family'}, format='json',
        ).data
        plant = self.client.post(
            '/plants/plant/',
            {
                'family': family['pk'],
                'name': 'Plant',
                'maturity_basis': 'transplanting',
            },
            format='json',
        ).data
        variety = self.client.post(
            '/plants/variety/',
            {
                'plant': plant['pk'],
                'name': 'Variety',
                'maturity_basis': 'seed',
            },
            format='json',
        ).data

        response = self.client.patch(
            f"/plants/variety/{variety['pk']}/",
            {'maturity_basis': None},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['maturity_basis'])
        self.assertEqual(response.data['effective_maturity_basis'], 'transplanting')
