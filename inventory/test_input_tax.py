"""Every input-tax evidence warning fires on its own condition and no other."""

import ast
from datetime import date
from pathlib import Path

from tax.models import GstRegistration
from tax.services import record_registration

from . import input_tax
from .models import StockReceiptLine
from .test_ledger_rest import LedgerRestFixture


def module_warning_codes():
    """Return every code `input_tax` can emit, read from its own source.

    A declared table that drifts from the module is worse than no table: it
    reports full coverage of a set that has grown underneath it. The codes are
    literals passed to one private constructor, so the module can be asked
    directly rather than trusted.
    """
    tree = ast.parse(Path(input_tax.__file__).read_text(encoding='utf-8'))
    return {
        node.args[0].value
        for node in ast.walk(tree)
        if _is_warning_call(node)
    }


def _is_warning_call(node):
    """Return whether one syntax node constructs a warning from a literal code."""
    if not isinstance(node, ast.Call) or not node.args:
        return False
    if not isinstance(node.func, ast.Name) or node.func.id != '_warning':
        return False
    return isinstance(node.args[0], ast.Constant)


class InputTaxWarningTests(LedgerRestFixture):
    """A claim carries exactly the warnings its own evidence has earned."""

    # Each entry names the one thing removed from a fully evidenced claim, as
    # receipt-level and line-level overrides, and the single code that answers.
    breakages = {
        'workspace_not_registered': ({'invoice_date': '2025-06-01'}, {}),
        'purchase_evidence_missing': (
            {
                'source_document_number': '',
                'evidence_reference': '',
                'evidence_url': '',
            },
            {},
        ),
        'invoice_date_missing': ({'invoice_date': None}, {}),
        'unknown_purchase_tax_treatment': (
            {},
            {'tax_treatment': 'unknown', 'tax_rate': '0.0000'},
        ),
        'supplier_registration_unsupported': (
            {'supplier_gst_status': 'unknown'},
            {},
        ),
        'supplier_gst_number_missing': ({'supplier_gst_number': ''}, {}),
        'customs_evidence_missing': ({}, {'input_tax_source': 'customs'}),
        'second_hand_supplier_status_missing': (
            {},
            {'input_tax_source': 'second_hand'},
        ),
        'second_hand_seller_details_missing': (
            {
                'supplier_gst_status': 'unregistered',
                'supplier_name_snapshot': '',
                'supplier_address_snapshot': '',
            },
            {'input_tax_source': 'second_hand'},
        ),
    }

    def setUp(self):
        super().setUp()
        record_registration(
            self.workspace,
            self.user,
            registered=True,
            basis=GstRegistration.Basis.INVOICE,
            effective_from=date(2026, 1, 1),
            filing_frequency=GstRegistration.Frequency.TWO_MONTHLY,
            period_anchor_month=1,
            gst_number='123456785',
        )

    def evidenced_payload(self, receipt=None, line=None):
        """Return a claim nothing is missing from, less any named omission."""
        payload = self.receipt_payload()
        payload.update({
            'invoice_date': '2026-08-01',
            'source_document_type': 'taxable_supply',
            'source_document_number': 'TSI-4471',
            'evidence_reference': 'SCAN-4471',
            'supplier_name_snapshot': 'API Supplier Limited',
            'supplier_address_snapshot': '9 Nursery Road, Levin',
            'supplier_gst_status': 'registered',
            'supplier_gst_number': '987654321',
        })
        payload['lines'][0].update({
            'claim_input_tax': True,
            'claimable_percentage': '100.0000',
        })
        payload.update(receipt or {})
        payload['lines'][0].update(line or {})
        return payload

    def warnings_for(self, **overrides):
        """Return the warning codes one draft receipt reports."""
        response = self.client.post(
            self.receipt_url,
            self.evidenced_payload(**overrides),
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return {warning['code'] for warning in response.data['tax_warnings']}

    def test_the_declared_breakages_are_the_warnings_the_module_can_emit(self):
        """A code added without a case here would otherwise never be exercised."""
        self.assertEqual(
            set(self.breakages) | {'legacy_unverified_claim'},
            module_warning_codes(),
        )

    def test_a_fully_evidenced_claim_warns_about_nothing(self):
        """A warning on a complete claim is what teaches operators to ignore them."""
        self.assertEqual(self.warnings_for(), set())

    def test_each_missing_piece_of_evidence_raises_only_its_own_warning(self):
        """One gap, one message: the operator is told what to go and fix."""
        for code, (receipt, line) in self.breakages.items():
            with self.subTest(code=code):
                self.assertEqual(
                    self.warnings_for(receipt=receipt, line=line),
                    {code},
                )

    def test_a_line_claiming_nothing_is_never_warned_about(self):
        """Evidence is only owed for tax somebody is actually claiming back."""
        codes = self.warnings_for(
            receipt={
                'invoice_date': None,
                'source_document_number': '',
                'evidence_reference': '',
                'evidence_url': '',
                'supplier_gst_status': 'unknown',
                'supplier_gst_number': '',
            },
            line={'claim_input_tax': False, 'claimable_percentage': '0.0000'},
        )
        self.assertEqual(codes, set())

    def test_a_migrated_claim_says_so_until_somebody_verifies_it(self):
        """The former receipt-wide flag is a claim nobody made line by line."""
        created = self.client.post(
            self.receipt_url,
            self.evidenced_payload(),
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        StockReceiptLine.objects.filter(
            receipt_id=created.data['pk'],
        ).update(legacy_tax_classification=True)

        reread = self.client.get(f"{self.receipt_url}{created.data['pk']}/")
        self.assertEqual(reread.status_code, 200, reread.data)
        warnings = reread.data['tax_warnings']
        self.assertEqual(
            {warning['code'] for warning in warnings},
            {'legacy_unverified_claim'},
        )
        self.assertEqual(
            warnings[0]['line_id'],
            created.data['lines'][0]['pk'],
        )

    def test_a_line_level_warning_names_the_line_and_a_receipt_one_does_not(self):
        """The editor needs to know which row a message belongs against."""
        response = self.client.post(
            self.receipt_url,
            self.evidenced_payload(
                receipt={'invoice_date': None},
                line={'input_tax_source': 'customs'},
            ),
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        by_code = {
            warning['code']: warning['line_id']
            for warning in response.data['tax_warnings']
        }
        self.assertIsNone(by_code['invoice_date_missing'])
        self.assertEqual(
            by_code['customs_evidence_missing'],
            response.data['lines'][0]['pk'],
        )
