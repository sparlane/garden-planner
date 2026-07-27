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
            },
        )
        self.assert_create_retrieve(
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
