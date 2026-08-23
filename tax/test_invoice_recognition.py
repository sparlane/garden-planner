"""The GST period ledger against real documents rather than the stand-in.

`test_recognition` proves the rules; this proves the reading. An invoice, a
credit note and a return with no money attached all have to reach the period
report through `facts`, and the fulfillment-date proxy has to stop being used
the moment a document exists to supersede it.
"""

# pylint: disable=duplicate-code

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from django.test import TestCase

from billing.documents import full_credit, issue_correction, issue_supply_document
from billing.models import SupplyCorrection
from billing.test_fixtures import DocumentScenarioMixin
from sales.commerce import post_return

from .entries import derive_entries
from .facts import order_facts
from .recognition import INVOICE, SUPPLY, SUPPLY_CREDIT, order_recognition


class InvoiceDrivenRecognitionTests(DocumentScenarioMixin, TestCase):
    """What a period owes, once a document says when the supply happened."""

    #: A dispatch instant in June, so it is unambiguously a different month
    #: from the May invoice date and the July correction date.
    dispatched_at = datetime(2026, 6, 10, 9, 0, tzinfo=timezone.utc)

    def setUp(self):
        """Register, confirm a two-plant order, and dispatch it in June."""
        super().setUp()
        self.register_for_gst()
        self.plants = self.ready_plants(2, ready_at=datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc))
        self.order, self.line, self.allocations = self.confirmed_order(self.plants)
        self.fulfillment = self.fulfill(
            self.order, self.allocations, fulfilled_at=self.dispatched_at,
        )

    def invoice(self, issued_on=date(2026, 5, 4), positions=(1, 2)):
        """Issue one document over the positions given."""
        return issue_supply_document(
            self.order, self.user,
            operation_key=uuid4(),
            lines=[{'order_line': self.line, 'positions': list(positions)}],
            issued_on=issued_on,
        )

    def recognition(self):
        """Recognise this order under the invoice basis, straight from the database."""
        return order_recognition(order_facts(self.order), INVOICE)

    def test_without_a_document_the_dispatch_date_still_stands_in(self):
        """The behaviour task 117 shipped, and the baseline this supersedes."""
        recognition = self.recognition()
        event = recognition.supply_events[0]

        self.assertEqual(event.supply_date, date(2026, 6, 10))
        self.assertTrue(event.proxy)
        self.assertEqual(recognition.proxy_gross, Decimal('23.0000'))

    def test_issuing_a_document_moves_the_supply_to_its_own_date(self):
        """May's return carries it now, and nothing is marked proxy."""
        self.invoice()
        recognition = self.recognition()

        self.assertEqual({event.supply_date for event in recognition.supply_events}, {date(2026, 5, 4)})
        self.assertEqual(recognition.proxy_gross, Decimal('0.0000'))
        self.assertEqual(recognition.recognised_gross, Decimal('23.0000'))

    def test_a_partly_invoiced_order_mixes_a_real_date_with_the_stand_in(self):
        """Half documented is half documented; the report says which half."""
        self.invoice(positions=(1,))
        recognition = self.recognition()
        by_source = {event.source_type: event for event in recognition.supply_events}

        self.assertEqual(by_source['supply_document'].supply_date, date(2026, 5, 4))
        self.assertFalse(by_source['supply_document'].proxy)
        self.assertEqual(by_source['fulfillment'].supply_date, date(2026, 6, 10))
        self.assertTrue(by_source['fulfillment'].proxy)
        self.assertEqual(recognition.recognised_gross, Decimal('23.0000'))

    def test_a_credit_note_credits_in_the_period_it_was_issued(self):
        """The adjustment belongs to July, not back in May."""
        document = self.invoice()
        full_credit(
            document, self.user,
            operation_key=uuid4(),
            reason_code=SupplyCorrection.Reason.CANCELLATION,
            reason='Order cancelled after invoicing',
            corrected_on=date(2026, 7, 1),
        )
        recognition = self.recognition()

        self.assertEqual({event.supply_date for event in recognition.credit_events}, {date(2026, 7, 1)})
        self.assertEqual(recognition.recognised_gross, Decimal('0.0000'))

    def test_a_return_with_no_money_is_settled_by_its_credit_note(self):
        """Task 117 could only report this as outstanding work; now it is accounted for."""
        document = self.invoice()
        returned = post_return(
            self.order, self.user,
            operation_key=uuid4(),
            items=[{
                'fulfillment_line': self.fulfillment.lines.order_by('pk').first(),
                'outcome': 'available',
                'destination': self.store,
            }],
            reason='Root rot found on arrival',
        )
        self.assertEqual(self.recognition().uncredited_return_ids, (returned.pk,))

        issue_correction(
            document, self.user,
            operation_key=uuid4(),
            correction_type=SupplyCorrection.CorrectionType.CREDIT,
            reason_code=SupplyCorrection.Reason.RETURN,
            reason='Credit for the plant returned',
            lines=[{'document_line': document.lines.get(), 'amount': Decimal('11.5000'), 'quantity': 1}],
            corrected_on=date(2026, 7, 1),
            sales_return=returned,
        )
        recognition = self.recognition()

        self.assertEqual(recognition.uncredited_return_ids, ())
        self.assertEqual(recognition.recognised_gross, Decimal('11.5000'))

    def test_the_period_entries_report_the_document_as_the_source(self):
        """The drill-down names the invoice, which is what makes it reconcilable."""
        document = self.invoice()
        entries = derive_entries(self.workspace, date(2026, 5, 1), date(2026, 6, 30))
        supplies = [entry for entry in entries if entry.kind == SUPPLY]

        self.assertEqual({entry.source_type for entry in supplies}, {'supply_document'})
        self.assertEqual({entry.source_id for entry in supplies}, {document.pk})
        self.assertEqual({entry.period_label for entry in supplies}, {'2026-05-01..2026-06-30'})
        self.assertFalse(any(entry.proxy for entry in supplies))

    def test_a_credit_note_appears_in_the_period_it_falls_in(self):
        """A May invoice and a July credit are two periods, as they should be."""
        document = self.invoice()
        full_credit(
            document, self.user,
            operation_key=uuid4(),
            reason_code=SupplyCorrection.Reason.CANCELLATION,
            reason='Cancelled',
            corrected_on=date(2026, 7, 1),
        )
        entries = derive_entries(self.workspace, date(2026, 5, 1), date(2026, 8, 31))
        credits_ = [entry for entry in entries if entry.kind == SUPPLY_CREDIT]

        self.assertEqual([entry.period_label for entry in credits_], ['2026-07-01..2026-08-31'])
        self.assertEqual(sum(entry.tax for entry in credits_), Decimal('3.0000'))
