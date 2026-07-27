"""
Tests for supplies
"""
from tests.api import RESTContractTestCase


class SupplierAPITests(RESTContractTestCase):
    """Tests for the supplier REST resource."""

    LIST_URLS = ('/supplies/supplier/',)

    def test_list_route_requires_authentication(self):
        """Anonymous requests cannot list suppliers."""
        self.assert_authentication_required(self.LIST_URLS)

    def test_list_route_returns_a_list(self):
        """Authenticated supplier collections use the common list contract."""
        self.assert_list_contract(self.LIST_URLS)

    def test_supplier_round_trip(self):
        """An authenticated supplier write round-trips through the API."""
        self.assert_create_retrieve(
            '/supplies/supplier/',
            {
                'name': 'Local Seed Company',
                'website': 'https://seeds.example.com',
                'notes': 'Open-pollinated varieties',
            },
        )
