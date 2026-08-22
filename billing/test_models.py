"""What the document tables refuse, independently of any service.

The services in `documents` are where a document is normally built, and they
check far more than this. These tests go around them on purpose: a management
command, a data migration or a future caller can write these rows directly, and
the guarantees an issued document rests on — that it is never edited, never
deleted, and never claims GST nobody was registered to charge — have to hold
for those writers too.
"""

# pylint: disable=duplicate-code

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from workspaces.models import Workspace

from .models import (
    SUPPLY_SERIES,
    DocumentNumberSequence,
    SupplyCorrection,
    SupplyDocument,
    SupplyDocumentLine,
)
from .test_fixtures import DocumentScenarioMixin


class DocumentTableTests(DocumentScenarioMixin, TestCase):
    """Guarantees the tables hold whoever writes to them."""

    def setUp(self):
        """Build one confirmed order every document here is issued against."""
        super().setUp()
        self.registration = self.register_for_gst()
        self.plants = self.ready_plants(2)
        self.order, self.line, self.allocations = self.confirmed_order(self.plants)

    def build_document(self, **overrides):
        """Build one unsaved document, bypassing the service that normally would."""
        values = {
            'workspace': self.workspace,
            'order': self.order,
            'document_number': f'INV-{uuid4().hex[:6]}',
            'issued_on': date(2026, 5, 4),
            'taxable_supply': True,
            'tier': 'low',
            'currency_code': 'NZD',
            'subtotal_ex_tax': Decimal('20.0000'),
            'tax_total': Decimal('3.0000'),
            'total_incl_tax': Decimal('23.0000'),
            'previously_invoiced': Decimal('0.0000'),
            'paid_to_date': Decimal('0.0000'),
            'balance_due': Decimal('23.0000'),
            'overpaid_at_issue': Decimal('0.0000'),
            'seller_legal_name': 'Kowhai Growers Limited',
            'seller_gst_number': '049091850',
            'seller_registration': self.registration,
            'operation_key': uuid4(),
            'request_fingerprint': 'x' * 64,
        }
        values.update(overrides)
        return SupplyDocument(**values)

    def make_document(self, **overrides):
        """Write one document straight to the table, bypassing the service."""
        document = self.build_document(**overrides)
        document.save()
        return document

    def assert_refused_by_the_database(self, **overrides):
        """Assert the table itself refuses a row, not only `save`.

        `bulk_create` is how a row gets in without `full_clean` running, so it
        is what a data migration or a future bulk writer would hit. Using it
        here is the difference between testing the constraint and testing the
        model method that happens to check the same thing first.
        """
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SupplyDocument.objects.bulk_create([self.build_document(**overrides)])

    def test_an_issued_document_can_never_be_edited(self):
        """A document is evidence of what was handed over, not a working note."""
        document = self.make_document()
        document.notes = 'Second thoughts'
        with self.assertRaises(ValidationError):
            document.save()
        with self.assertRaises(ValidationError):
            document.delete()
        self.assertEqual(SupplyDocument.objects.get(pk=document.pk).notes, '')

    def test_a_correction_can_never_be_edited_either(self):
        """The record of a change is as durable as the thing it changed."""
        document = self.make_document()
        correction = SupplyCorrection.objects.create(
            workspace=self.workspace,
            document=document,
            document_number='CRN-000001',
            correction_type=SupplyCorrection.CorrectionType.CREDIT,
            reason_code=SupplyCorrection.Reason.RETURN,
            reason='Two plants came back',
            corrected_on=date(2026, 5, 6),
            currency_code='NZD',
            subtotal_ex_tax=Decimal('20.0000'),
            tax_total=Decimal('3.0000'),
            total_incl_tax=Decimal('23.0000'),
            seller_legal_name='Kowhai Growers Limited',
            operation_key=uuid4(),
            request_fingerprint='y' * 64,
        )
        correction.reason = 'Actually one plant'
        with self.assertRaises(ValidationError):
            correction.save()
        with self.assertRaises(ValidationError):
            correction.delete()

    def test_an_unregistered_seller_cannot_be_recorded_charging_gst(self):
        """The database says it too, so a bulk write cannot get past the service."""
        self.assert_refused_by_the_database(
            taxable_supply=False,
            seller_gst_number='',
            seller_registration=None,
        )

    def test_an_unregistered_seller_may_issue_a_document_with_no_gst(self):
        """A nursery below the threshold still sells things and still invoices."""
        document = self.make_document(
            taxable_supply=False,
            seller_gst_number='',
            seller_registration=None,
            tax_total=Decimal('0.0000'),
            subtotal_ex_tax=Decimal('20.0000'),
            total_incl_tax=Decimal('20.0000'),
            balance_due=Decimal('20.0000'),
        )
        self.assertFalse(document.taxable_supply)

    def test_a_document_number_is_unique_within_the_workspace(self):
        """A duplicate number would make two documents one identifier."""
        self.make_document(document_number='INV-000001')
        self.assert_refused_by_the_database(document_number='INV-000001')

    def test_one_operation_key_produces_one_document(self):
        """The retry guard is a constraint, not only a service check."""
        key = uuid4()
        self.make_document(operation_key=key)
        self.assert_refused_by_the_database(operation_key=key)

    def test_an_unknown_value_band_is_refused(self):
        """A tier nothing can interpret would make the required elements unknowable."""
        self.assert_refused_by_the_database(tier='enormous')

    def test_one_order_line_appears_at_most_once_on_a_document(self):
        """Two rows for one order line would be two answers to one question."""
        document = self.make_document()
        values = {
            'document': document,
            'order_line': self.line,
            'description': 'Hebe "Wiri Charm" 2L',
            'quantity': 1,
            'unit_price': Decimal('10.0000'),
            'tax_rate': Decimal('15.0000'),
            'tax_treatment': 'standard',
            'gross_ex_tax': Decimal('10.0000'),
            'discount_ex_tax': Decimal('0.0000'),
            'subtotal_ex_tax': Decimal('10.0000'),
            'tax_total': Decimal('1.5000'),
            'total_incl_tax': Decimal('11.5000'),
        }
        SupplyDocumentLine.objects.create(**values)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SupplyDocumentLine.objects.create(**values)

    def test_a_document_for_another_workspace_is_refused_by_name(self):
        """Cross-workspace leakage is a field error, not an opaque failure."""
        other = Workspace.objects.create(name='Another nursery')
        document = SupplyDocument(
            workspace=other,
            order=self.order,
            document_number='INV-999999',
            issued_on=date(2026, 5, 4),
            taxable_supply=False,
            tier='low',
            currency_code='NZD',
            subtotal_ex_tax=Decimal('0.0000'),
            tax_total=Decimal('0.0000'),
            total_incl_tax=Decimal('0.0000'),
            previously_invoiced=Decimal('0.0000'),
            paid_to_date=Decimal('0.0000'),
            balance_due=Decimal('0.0000'),
            overpaid_at_issue=Decimal('0.0000'),
            seller_legal_name='Elsewhere Limited',
            operation_key=uuid4(),
            request_fingerprint='z' * 64,
        )
        with self.assertRaises(ValidationError) as caught:
            document.save()
        self.assertIn('order', caught.exception.message_dict)


class NumberSequenceTests(DocumentScenarioMixin, TestCase):
    """Each series counts on its own so a gap in one is a real question."""

    def test_one_sequence_exists_per_workspace_and_series(self):
        """Interleaving three document kinds through one counter would gap all three."""
        DocumentNumberSequence.objects.create(
            workspace=self.workspace, series=SUPPLY_SERIES,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DocumentNumberSequence.objects.create(
                    workspace=self.workspace, series=SUPPLY_SERIES,
                )
