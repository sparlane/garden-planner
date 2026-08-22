"""The REST contract for issuing, reading and printing documents.

The frontend has no test runner of its own, so what the screens rely on is
pinned here: the payload the printable view renders, the shape the issue form
posts, and the refusals it has to show against a field.
"""

# pylint: disable=duplicate-code

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.test import TestCase

from tests.api import RESTContractTestCase
from workspaces.models import Workspace

from .documents import issue_supply_document
from .models import SupplyCorrection
from .test_fixtures import DocumentScenarioMixin


DOCUMENTS_URL = '/billing/supply-documents/'


class DocumentRESTTests(DocumentScenarioMixin, RESTContractTestCase):
    """Issuing and reading documents over the API."""

    def setUp(self):
        """Register, confirm a two-plant order, and dispatch it."""
        super().setUp()
        self.register_for_gst()
        self.plants = self.ready_plants(2)
        self.customer = self.make_customer()
        self.order, self.line, self.allocations = self.confirmed_order(
            self.plants, customer=self.customer,
        )

    def issue(self, positions=(1, 2), **overrides):
        """Post one document and return the response."""
        payload = {
            'operation_key': str(uuid4()),
            'order': self.order.pk,
            'lines': [{'order_line': self.line.pk, 'positions': list(positions)}],
            'issued_on': '2026-05-04',
        }
        payload.update(overrides)
        return self.client.post(DOCUMENTS_URL, payload, format='json')

    def test_authentication_is_required(self):
        """An anonymous caller reads nobody's invoices."""
        self.assert_authentication_required([DOCUMENTS_URL])

    def test_issuing_and_reading_one_document(self):
        """The register lists what was issued, and the detail carries its lines."""
        response = self.issue()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['document_number'], 'INV-000001')
        self.assertEqual(response.data['total_incl_tax'], '23.0000')
        self.assertEqual(response.data['state']['status'], 'issued')

        detail = self.client.get(f"{DOCUMENTS_URL}{response.data['pk']}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data['order_number'], self.order.order_number)
        self.assertEqual(len(detail.data['lines']), 1)
        self.assertEqual(
            [row['commercial_position'] for row in detail.data['lines'][0]['coverage']],
            [1, 2],
        )

        listed = self.client.get(DOCUMENTS_URL)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([row['pk'] for row in listed.data], [response.data['pk']])

    def test_the_register_can_be_narrowed_to_one_order(self):
        """An order screen asks for its own documents and gets only those."""
        self.issue(positions=(1,))
        matching = self.client.get(f'{DOCUMENTS_URL}?order={self.order.pk}')
        self.assertEqual(len(matching.data), 1)
        other = self.client.get(f'{DOCUMENTS_URL}?order={self.order.pk + 1000}')
        self.assertEqual(other.data, [])

    def test_a_document_cannot_be_edited_or_deleted_over_the_api(self):
        """The surface offers no way to rewrite evidence."""
        created = self.issue()
        url = f"{DOCUMENTS_URL}{created.data['pk']}/"
        for method in (self.client.patch, self.client.put):
            with self.subTest(method=method.__name__):
                self.assertEqual(method(url, {'notes': 'no'}, format='json').status_code, 405)
        self.assertEqual(self.client.delete(url).status_code, 405)

    def test_a_defective_document_is_refused_against_its_own_fields(self):
        """A form can show the message beside the control it belongs to."""
        big_plants = self.ready_plants(2)
        order, line, _allocations = self.confirmed_order(big_plants, unit_price='900.0000')
        response = self.client.post(DOCUMENTS_URL, {
            'operation_key': str(uuid4()),
            'order': order.pk,
            'lines': [{'order_line': line.pk, 'positions': [1, 2]}],
            'issued_on': '2026-05-04',
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.data), {'buyer_name', 'buyer_identification'})

    def test_a_one_off_buyer_can_be_supplied_with_the_request(self):
        """Invoicing a wholesale load should not require inventing a customer."""
        big_plants = self.ready_plants(2)
        order, line, _allocations = self.confirmed_order(big_plants, unit_price='900.0000')
        response = self.client.post(DOCUMENTS_URL, {
            'operation_key': str(uuid4()),
            'order': order.pk,
            'lines': [{'order_line': line.pk, 'positions': [1, 2]}],
            'issued_on': '2026-05-04',
            'buyer': {'buyer_name': 'Te Awa Plantings', 'buyer_identifier': '9429041234567'},
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['buyer_name'], 'Te Awa Plantings')
        self.assertEqual(response.data['tier'], 'full')

    def test_the_invoiceable_route_says_what_is_left_and_what_shipped(self):
        """The issue form is built from this, so its shape is a contract."""
        self.fulfill(self.order, self.allocations[:1])
        self.issue(positions=(2,))

        response = self.client.get(f'/billing/invoiceable/{self.order.pk}/')
        self.assertEqual(response.status_code, 200)
        line = response.data['lines'][0]
        self.assertEqual(line['invoiced_positions'], [2])
        self.assertEqual(line['positions'], [
            {'position': 1, 'dispatched': True, 'total_incl_tax': '11.5000'},
        ])

    def test_the_printable_payload_carries_both_parties_and_the_checklist(self):
        """Change 5 has to be visible to a reader, not only enforced at issue."""
        created = self.issue()
        response = self.client.get(f"{DOCUMENTS_URL}{created.data['pk']}/print/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['title'], 'Tax invoice')
        self.assertEqual(response.data['seller']['gst_number'], '049091850')
        self.assertEqual(response.data['seller']['legal_name'], 'Kowhai Growers Limited')
        self.assertEqual(response.data['buyer']['name'], 'Riverbend Landscapes')
        self.assertEqual(response.data['tier_label'], '$200 or less')
        self.assertTrue(response.data['required_information'])
        self.assertTrue(all(row['satisfied'] for row in response.data['required_information']))
        self.assertEqual(response.data['totals']['balance_due'], '23.0000')


class UnregisteredSellerRESTTests(DocumentScenarioMixin, RESTContractTestCase):
    """A workspace below the threshold issues receipts, and they say so."""

    def test_the_printed_document_is_a_receipt_not_a_tax_invoice(self):
        """Calling a receipt a tax invoice would be a false record."""
        plants = self.ready_plants(1)
        order, line, _allocations = self.confirmed_order(plants, tax_rate='0')
        document = issue_supply_document(
            order, self.user,
            operation_key=uuid4(),
            lines=[{'order_line': line, 'positions': [1]}],
            issued_on=date(2026, 5, 4),
        )
        response = self.client.get(f'{DOCUMENTS_URL}{document.pk}/print/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['title'], 'Sales receipt')
        self.assertFalse(response.data['taxable_supply'])
        self.assertEqual(response.data['seller']['gst_number'], '')
        codes = {row['code'] for row in response.data['required_information']}
        self.assertNotIn('seller_gst_number', codes)
        self.assertIn('seller_name', codes)


class CorrectionRESTTests(DocumentScenarioMixin, RESTContractTestCase):
    """Issuing corrections over the API."""

    def setUp(self):
        """Issue one document to correct."""
        super().setUp()
        self.register_for_gst()
        self.plants = self.ready_plants(2)
        self.order, self.line, self.allocations = self.confirmed_order(self.plants)
        self.document = issue_supply_document(
            self.order, self.user,
            operation_key=uuid4(),
            lines=[{'order_line': self.line, 'positions': [1, 2]}],
            issued_on=date(2026, 5, 4),
        )
        self.url = f'{DOCUMENTS_URL}{self.document.pk}/corrections/'

    def test_a_partial_credit_is_issued_and_listed(self):
        """One record, readable straight back from the document it corrects."""
        response = self.client.post(self.url, {
            'operation_key': str(uuid4()),
            'correction_type': 'credit',
            'reason_code': 'discount',
            'reason': 'Agreed price adjustment',
            'lines': [{'document_line': self.document.lines.get().pk, 'amount': '5.0000'}],
            'corrected_on': '2026-05-06',
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['document_number'], 'CRN-000001')
        self.assertEqual(response.data['total_incl_tax'], '5.0000')

        listed = self.client.get(self.url)
        self.assertEqual([row['pk'] for row in listed.data], [response.data['pk']])
        detail = self.client.get(f'{DOCUMENTS_URL}{self.document.pk}/')
        self.assertEqual(detail.data['state']['status'], 'part_credited')
        self.assertEqual(detail.data['state']['net_total_incl_tax'], '18.0000')

    def test_a_full_credit_needs_no_line_selection(self):
        """Cancelling is one button, and it credits whatever is left."""
        response = self.client.post(self.url, {
            'operation_key': str(uuid4()),
            'correction_type': 'credit',
            'reason_code': 'cancellation',
            'reason': 'Order cancelled',
            'full': True,
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['total_incl_tax'], '23.0000')
        self.assertEqual(SupplyCorrection.objects.count(), 1)

    def test_naming_lines_and_asking_for_a_full_credit_is_refused(self):
        """Two answers to one question is a mistake worth failing on."""
        response = self.client.post(self.url, {
            'operation_key': str(uuid4()),
            'correction_type': 'credit',
            'reason_code': 'cancellation',
            'reason': 'Order cancelled',
            'full': True,
            'lines': [{'document_line': self.document.lines.get().pk, 'amount': '5.0000'}],
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_a_full_debit_is_refused(self):
        """A debit reverses a credit; there is no whole document to debit."""
        response = self.client.post(self.url, {
            'operation_key': str(uuid4()),
            'correction_type': 'debit',
            'reason_code': 'other',
            'reason': 'Nope',
            'full': True,
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_an_over_credit_is_refused_with_a_field_error(self):
        """The screen can put the message against the amount that caused it."""
        response = self.client.post(self.url, {
            'operation_key': str(uuid4()),
            'correction_type': 'credit',
            'reason_code': 'discount',
            'reason': 'Too much',
            'lines': [{'document_line': self.document.lines.get().pk, 'amount': '99.0000'}],
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('lines', response.data)


class GardenProfileTests(DocumentScenarioMixin, RESTContractTestCase):
    """A Garden workspace has no nursery to invoice for."""

    def setUp(self):
        """Present the workspace as a Garden."""
        super().setUp()
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.save()

    def test_the_document_routes_are_refused_in_the_garden_profile(self):
        """Enforced on the server, so a bookmarked URL stays honest."""
        response = self.client.get(DOCUMENTS_URL)
        self.assertEqual(response.status_code, 403)


class MoneyPrecisionTests(DocumentScenarioMixin, TestCase):
    """A document's own arithmetic has to close, not nearly close."""

    def test_three_items_at_an_awkward_price_still_total_the_order(self):
        """Positions are split exactly, so a document never drifts from its order."""
        self.register_for_gst()
        plants = self.ready_plants(3)
        order, line, _allocations = self.confirmed_order(plants, unit_price='10.0100')
        document = issue_supply_document(
            order, self.user,
            operation_key=uuid4(),
            lines=[{'order_line': line, 'positions': [1, 2, 3]}],
            issued_on=date(2026, 5, 4),
        )

        self.assertEqual(document.total_incl_tax, order.total_incl_tax)
        self.assertEqual(
            document.subtotal_ex_tax + document.tax_total,
            document.total_incl_tax,
        )
        self.assertEqual(document.total_incl_tax, Decimal('34.5345'))
