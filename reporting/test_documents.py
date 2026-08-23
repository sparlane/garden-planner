"""The register of issued documents, and the export of it.

Change 6 asks for exportable records with stable identifiers and audit
history. What that means in practice is checked here: a correction is a row of
its own with its own number and date, the audit columns say who issued what,
and the CSV carries every column whether or not a given row fills it in.
"""

# pylint: disable=duplicate-code

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from billing.documents import full_credit, issue_correction, issue_supply_document
from billing.models import SupplyCorrection
from billing.test_fixtures import DocumentScenarioMixin
from sales.commerce import post_refund
from tests.api import RESTContractTestCase


REGISTER_URL = '/reports/supply-documents/'
EXPORT_URL = '/reports/supply-documents/export/'


class SupplyDocumentRegisterTests(DocumentScenarioMixin, RESTContractTestCase):
    """One row per document and per correction, filtered and exported."""

    def setUp(self):
        """Register, invoice a dispatched two-plant order, and credit part of it."""
        super().setUp()
        self.register_for_gst()
        plants = self.ready_plants(2, ready_at=datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc))
        self.order, self.line, self.allocations = self.confirmed_order(plants)
        self.document = issue_supply_document(
            self.order, self.user,
            operation_key=uuid4(),
            lines=[{'order_line': self.line, 'positions': [1, 2]}],
            issued_on=date(2026, 5, 4),
        )

    def register(self, **params):
        """Fetch the register over a range covering the whole scenario."""
        query = {'date_from': '2026-01-01', 'date_to': '2026-12-31'}
        query.update(params)
        response = self.client.get(REGISTER_URL, query)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def credit(self, amount, **overrides):
        """Issue one credit note against the document."""
        values = {
            'operation_key': uuid4(),
            'correction_type': SupplyCorrection.CorrectionType.CREDIT,
            'reason_code': SupplyCorrection.Reason.DISCOUNT,
            'reason': 'Agreed price adjustment',
            'lines': [{'document_line': self.document.lines.get(), 'amount': Decimal(amount)}],
            'corrected_on': date(2026, 6, 2),
        }
        values.update(overrides)
        return issue_correction(self.document, self.user, **values)

    def test_authentication_is_required(self):
        """A customer register is not public information."""
        self.assert_authentication_required([REGISTER_URL])

    def test_a_document_carries_its_identity_parties_and_audit_columns(self):
        """The stable identifier and the audit history change 6 asks for."""
        row = self.register()['results'][0]

        self.assertEqual(row['document_number'], 'INV-000001')
        self.assertEqual(row['document_kind'], 'supply')
        self.assertEqual(row['document_date'], '2026-05-04')
        self.assertEqual(row['order_number'], self.order.order_number)
        self.assertEqual(row['seller_gst_number'], '049091850')
        self.assertEqual(row['total_incl_tax'], '23.0000')
        self.assertEqual(row['status'], 'issued')
        self.assertEqual(row['missing_information'], '')
        self.assertEqual(row['issued_by'], self.user.pk)
        self.assertIsNotNone(row['issued_at'])

    def test_a_correction_is_a_row_of_its_own_naming_what_it_corrects(self):
        """A credit note is a document, not a column on the invoice."""
        self.credit('5.0000')
        rows = {row['document_number']: row for row in self.register()['results']}

        self.assertEqual(set(rows), {'INV-000001', 'CRN-000001'})
        credit = rows['CRN-000001']
        self.assertEqual(credit['document_kind'], 'credit')
        self.assertEqual(credit['corrects'], 'INV-000001')
        self.assertEqual(credit['document_date'], '2026-06-02')
        self.assertEqual(credit['reason_code'], 'discount')
        self.assertEqual(credit['total_incl_tax'], '5.0000')
        self.assertEqual(rows['INV-000001']['net_total_incl_tax'], '18.0000')

    def test_rows_are_dated_by_their_own_document_not_by_the_supply(self):
        """A range covering only June sees the credit note and not the invoice."""
        self.credit('5.0000')
        numbers = [
            row['document_number'] for row in
            self.register(date_from='2026-06-01', date_to='2026-06-30')['results']
        ]
        self.assertEqual(numbers, ['CRN-000001'])

    def test_the_register_can_be_narrowed_to_one_kind(self):
        """An accountant asking for the credit notes gets only those."""
        self.credit('5.0000')
        numbers = [row['document_number'] for row in self.register(kind='credit')['results']]
        self.assertEqual(numbers, ['CRN-000001'])

    def test_a_credited_document_keeps_its_number_and_says_it_is_credited(self):
        """Identifiers are never reissued, including for a document credited away."""
        full_credit(
            self.document, self.user,
            operation_key=uuid4(),
            reason_code=SupplyCorrection.Reason.CANCELLATION,
            reason='Order cancelled',
            corrected_on=date(2026, 6, 2),
        )
        replacement = issue_supply_document(
            self.order, self.user,
            operation_key=uuid4(),
            lines=[{'order_line': self.line, 'positions': [1, 2]}],
            issued_on=date(2026, 6, 3),
        )
        rows = {row['document_number']: row for row in self.register()['results']}

        self.assertEqual(rows['INV-000001']['status'], 'credited')
        self.assertEqual(rows['INV-000001']['net_total_incl_tax'], '0.0000')
        self.assertEqual(replacement.document_number, 'INV-000002')
        self.assertIn('INV-000002', rows)

    def test_the_totals_stay_inside_one_currency(self):
        """No exchange rate exists, so nothing is consolidated across currencies."""
        self.credit('5.0000')
        totals = self.register()['totals']

        self.assertEqual(totals['documents'], 2)
        self.assertEqual(totals['currencies'], ['NZD'])
        self.assertEqual(totals['by_currency']['NZD']['counts'], {'supply': 1, 'credit': 1})
        self.assertEqual(totals['by_currency']['NZD']['total_incl_tax'], '28.0000')

    def test_a_refund_with_no_credit_note_is_reported_as_outstanding_paperwork(self):
        """The GST adjustment happens either way; the customer's evidence does not."""
        fulfillment = self.fulfill(self.order, self.allocations)
        payment = self.pay(self.order, '23.0000', date(2026, 5, 5))
        post_refund(
            self.order, self.user,
            operation_key=uuid4(),
            payment=payment,
            fulfillment_lines=list(fulfillment.lines.all()),
            amount=Decimal('5.0000'),
            reason='Damaged in transit',
        )
        codes = {finding['code'] for finding in self.register()['data_quality']}
        self.assertIn('refund_without_a_correction', codes)

    def test_issuing_the_credit_note_clears_the_finding(self):
        """The report stops nagging once the paperwork exists."""
        fulfillment = self.fulfill(self.order, self.allocations)
        payment = self.pay(self.order, '23.0000', date(2026, 5, 5))
        refund = post_refund(
            self.order, self.user,
            operation_key=uuid4(),
            payment=payment,
            fulfillment_lines=list(fulfillment.lines.all()),
            amount=Decimal('5.0000'),
            reason='Damaged in transit',
        )
        self.credit('5.0000', reason_code=SupplyCorrection.Reason.RETURN, refund=refund)
        codes = {finding['code'] for finding in self.register()['data_quality']}
        self.assertNotIn('refund_without_a_correction', codes)

    def test_the_export_carries_every_column_for_every_row(self):
        """A correction fills in fewer columns, and the CSV shape must not shift."""
        self.credit('5.0000')
        response = self.client.get(EXPORT_URL, {'date_from': '2026-01-01', 'date_to': '2026-12-31'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        lines = response.content.decode().splitlines()
        header = lines[3].split(',')
        self.assertIn('document_number', header)
        self.assertIn('corrects', header)
        self.assertEqual(len(lines), 6)
        for row in lines[4:]:
            self.assertEqual(len(row.split(',')), len(header))
