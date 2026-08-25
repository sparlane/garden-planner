"""
Tests for supplies
"""
from supplies.models import Supplier
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

    def test_registered_supplier_number_is_normalized(self):
        """Supplier evidence uses the same validated GST identity as tax."""
        response = self.client.post('/supplies/supplier/', {
            'name': 'Registered Seed Company',
            'address': '1 Seed Lane',
            'gst_status': 'registered',
            'gst_number': '49-091-850',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['gst_number'], '049091850')
        self.assertEqual(
            Supplier.objects.get(pk=response.data['pk']).gst_number,
            '049091850',
        )

    def test_unregistered_supplier_cannot_carry_a_gst_number(self):
        """Contradictory supplier evidence is rejected as a field error."""
        response = self.client.post('/supplies/supplier/', {
            'name': 'Private seller',
            'gst_status': 'unregistered',
            'gst_number': '049091850',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('gst_number', response.data)
