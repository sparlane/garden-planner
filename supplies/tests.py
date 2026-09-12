"""
Tests for supplies
"""
from supplies.defaults import ensure_default_supplier
from supplies.models import Supplier
from tests.api import RESTContractTestCase
from tests.factories import make_confirmed_invoice, make_supplier
from workspaces.models import get_current_workspace


class SupplierAPITests(RESTContractTestCase):
    """Tests for the supplier REST resource."""

    LIST_URLS = ('/supplies/supplier/',)

    def test_list_route_requires_authentication(self):
        """Anonymous requests cannot list suppliers."""
        self.assert_authentication_required(self.LIST_URLS)

    def test_list_route_returns_a_list(self):
        """Authenticated supplier collections use the common list contract."""
        self.assert_paginated_list_contract(self.LIST_URLS)

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


class SupplierCorrectionTests(RESTContractTestCase):
    """A supplier is corrected in place, whatever has been posted against it."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.supplier = make_supplier(
            name='Kings Sedes',
            address='Katikati',
            gst_status='registered',
            gst_number='049091850',
        )

    def correct(self, **changes):
        """Ask for a supplier to be corrected the way the screen does."""
        return self.client.patch(
            f'/supplies/supplier/{self.supplier.pk}/', changes, format='json',
        )

    def test_a_typo_is_corrected_in_place(self):
        """Nothing about a supplier is frozen, so no replacement is owed."""
        response = self.correct(name='Kings Seeds', notes='Katikati, Bay of Plenty')

        self.assertEqual(response.status_code, 200, response.data)
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.name, 'Kings Seeds')
        self.assertEqual(self.supplier.notes, 'Katikati, Bay of Plenty')

    def test_a_posted_document_keeps_the_identity_it_was_issued_with(self):
        """A correction fixes the catalog, not what a document already says."""
        invoice = make_confirmed_invoice(self.workspace, self.user, self.supplier)

        response = self.correct(
            name='Kings Seeds', address='1 Seed Lane', gst_status='unknown', gst_number='',
        )

        self.assertEqual(response.status_code, 200, response.data)
        invoice.refresh_from_db()
        self.assertEqual(invoice.supplier_name_snapshot, 'Kings Sedes')
        self.assertEqual(invoice.supplier_address_snapshot, 'Katikati')

    def test_the_tax_identity_is_corrected_as_one_change(self):
        """A supplier that stops being registered stops carrying a number."""
        response = self.correct(gst_status='unregistered', gst_number='')

        self.assertEqual(response.status_code, 200, response.data)
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.gst_status, 'unregistered')
        self.assertEqual(self.supplier.gst_number, '')

    def test_dropping_the_registration_on_its_own_is_refused(self):
        """The number the record still holds is what the status is judged
        against, which is why the screen sends both fields together."""
        response = self.correct(gst_status='unregistered')

        self.assertEqual(response.status_code, 400)
        self.assertIn('gst_number', response.data)

    def test_registering_without_a_number_is_refused(self):
        """Evidence of a taxable supply needs the supplier's GST number."""
        self.supplier.gst_status = 'unknown'
        self.supplier.gst_number = ''
        self.supplier.save()

        response = self.correct(gst_status='registered')

        self.assertEqual(response.status_code, 400)
        self.assertIn('gst_number', response.data)

    def test_a_corrected_gst_number_is_normalized(self):
        """A number typed with its dashes reads as the one tax validates."""
        response = self.correct(gst_number='49-091-850')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['gst_number'], '049091850')

    def test_the_stand_in_supplier_can_be_renamed(self):
        """It may not be merged away or retired, because an unnamed purchase
        still has to land somewhere, but a garden may say what it holds."""
        self.supplier = ensure_default_supplier(self.workspace)

        response = self.correct(name='Gifts and swaps')

        self.assertEqual(response.status_code, 200, response.data)
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.name, 'Gifts and swaps')
        self.assertTrue(self.supplier.is_system_default)

    def test_a_correction_cannot_claim_the_stand_in_flag(self):
        """The fallback is the system's own row, not one anybody elects."""
        response = self.correct(is_system_default=True)

        self.assertEqual(response.status_code, 200, response.data)
        self.supplier.refresh_from_db()
        self.assertFalse(self.supplier.is_system_default)
