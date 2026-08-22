"""Correcting an issued document without ever touching it.

Task 118's verification asks two things of this file. Every kind of correction
— a return, a discount, a wrong rate, a cancellation, a partial credit — has to
produce exactly one traceable record; and no reversal anywhere may mutate a
document that has already been issued.
"""

# One correction scenario needs the whole chain behind it — workspace, order,
# line, allocations, fulfillment, document and document line — because a
# correction is only meaningful against a supply that actually happened.
# pylint: disable=duplicate-code,too-many-instance-attributes

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import TestCase

from sales.commerce import post_refund, post_return, reverse_refund, reverse_return

from .documents import (
    document_state,
    full_credit,
    invoiceable,
    issue_correction,
    issue_supply_document,
    net_credited,
)
from .models import SupplyCorrection, SupplyDocument
from .test_fixtures import DocumentScenarioMixin


class CorrectionScenario(DocumentScenarioMixin, TestCase):
    """One dispatched, invoiced order with the correction helpers on top."""

    def setUp(self):
        """Confirm, dispatch and invoice a two-plant order."""
        super().setUp()
        self.register_for_gst()
        self.plants = self.ready_plants(2)
        self.customer = self.make_customer()
        self.order, self.line, self.allocations = self.confirmed_order(
            self.plants, customer=self.customer,
        )
        self.fulfillment = self.fulfill(self.order, self.allocations)
        self.document = issue_supply_document(
            self.order, self.user,
            operation_key=uuid4(),
            lines=[{'order_line': self.line, 'positions': [1, 2]}],
            issued_on=date(2026, 5, 4),
        )
        self.document_line = self.document.lines.get()

    def credit(self, amount, reason_code=SupplyCorrection.Reason.PARTIAL_CREDIT, **overrides):
        """Issue one credit against the single document line."""
        values = {
            'operation_key': uuid4(),
            'correction_type': SupplyCorrection.CorrectionType.CREDIT,
            'reason_code': reason_code,
            'reason': 'Recorded by a test',
            'lines': [{'document_line': self.document_line, 'amount': Decimal(amount)}],
            'corrected_on': date(2026, 5, 6),
        }
        values.update(overrides)
        return issue_correction(self.document, self.user, **values)

    def snapshot(self):
        """Read every stored field of the document, to prove none of them move."""
        return SupplyDocument.objects.filter(pk=self.document.pk).values().get()


class CorrectionKindTests(CorrectionScenario):
    """Each situation the task names produces one traceable record."""

    def test_a_return_credits_through_one_record_linked_to_the_return(self):
        """The credit note points at the return, so the two are one story."""
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
        correction = self.credit(
            '11.5000',
            reason_code=SupplyCorrection.Reason.RETURN,
            sales_return=returned,
            lines=[{'document_line': self.document_line, 'amount': Decimal('11.5000'), 'quantity': 1}],
        )

        self.assertEqual(SupplyCorrection.objects.count(), 1)
        self.assertEqual(correction.document_number, 'CRN-000001')
        self.assertEqual(correction.sales_return_id, returned.pk)
        self.assertEqual(correction.reason_code, SupplyCorrection.Reason.RETURN)
        self.assertEqual(correction.lines.get().quantity, 1)
        self.assertEqual(correction.total_incl_tax, Decimal('11.5000'))

    def test_a_refund_credits_through_one_record_linked_to_the_refund(self):
        """Money going back and the document saying so are one traceable pair."""
        payment = self.pay(self.order, '23.0000', date(2026, 5, 2))
        refund = post_refund(
            self.order, self.user,
            operation_key=uuid4(),
            payment=payment,
            fulfillment_lines=list(self.fulfillment.lines.all()),
            amount=Decimal('5.0000'),
            reason='Damaged in transit',
        )
        correction = self.credit(
            '5.0000',
            reason_code=SupplyCorrection.Reason.DISCOUNT,
            refund=refund,
        )

        self.assertEqual(SupplyCorrection.objects.count(), 1)
        self.assertEqual(correction.refund_id, refund.pk)
        self.assertEqual(correction.correction_type, SupplyCorrection.CorrectionType.CREDIT)

    def test_a_discount_credits_value_without_any_quantity(self):
        """A price adjustment on goods nobody gave back has no volume."""
        correction = self.credit('3.0000', reason_code=SupplyCorrection.Reason.DISCOUNT)

        self.assertIsNone(correction.lines.get().quantity)
        self.assertEqual(correction.total_incl_tax, Decimal('3.0000'))

    def test_a_cancellation_credits_the_whole_document_in_one_record(self):
        """One record, not one per line, and the document is left alone."""
        correction = full_credit(
            self.document, self.user,
            operation_key=uuid4(),
            reason_code=SupplyCorrection.Reason.CANCELLATION,
            reason='Order cancelled before delivery',
            corrected_on=date(2026, 5, 6),
        )

        self.assertEqual(SupplyCorrection.objects.count(), 1)
        self.assertEqual(correction.total_incl_tax, self.document.total_incl_tax)
        self.assertEqual(document_state(self.document)['status'], 'credited')

    def test_a_wrong_rate_is_corrected_by_crediting_and_re_issuing(self):
        """A fully credited line frees its items, so a corrected document can replace it."""
        full_credit(
            self.document, self.user,
            operation_key=uuid4(),
            reason_code=SupplyCorrection.Reason.WRONG_RATE,
            reason='Issued against the wrong GST treatment',
            corrected_on=date(2026, 5, 6),
        )
        offered = [row['position'] for row in invoiceable(self.order)[0]['positions']]
        self.assertEqual(offered, [1, 2])

        replacement = issue_supply_document(
            self.order, self.user,
            operation_key=uuid4(),
            lines=[{'order_line': self.line, 'positions': [1, 2]}],
            issued_on=date(2026, 5, 6),
        )
        self.assertEqual(replacement.document_number, 'INV-000002')
        self.assertEqual(replacement.total_incl_tax, Decimal('23.0000'))
        self.assertEqual(SupplyDocument.objects.count(), 2)

    def test_a_partial_credit_leaves_the_rest_of_the_document_standing(self):
        """Half credited is a state of its own, not credited and not untouched."""
        self.credit('8.0000')
        state = document_state(self.document)

        self.assertEqual(state['status'], 'part_credited')
        self.assertEqual(state['credited_total'], Decimal('8.0000'))
        self.assertEqual(state['net_total_incl_tax'], Decimal('15.0000'))
        self.assertEqual([row['position'] for row in invoiceable(self.order)[0]['positions']], [])

    def test_a_credit_splits_its_tax_the_way_the_line_it_credits_was_split(self):
        """A credit lands in the same box of a return as the supply it reverses."""
        correction = self.credit('11.5000')
        line = correction.lines.get()

        self.assertEqual(line.subtotal_ex_tax, Decimal('10.0000'))
        self.assertEqual(line.tax_total, Decimal('1.5000'))
        self.assertEqual(line.tax_treatment, self.document_line.tax_treatment)


class CorrectionLimitTests(CorrectionScenario):
    """What a correction to one supply cannot honestly say."""

    def test_a_credit_cannot_exceed_what_was_charged(self):
        """Crediting more than was invoiced would invent money."""
        self.credit('20.0000')
        with self.assertRaises(ValidationError) as caught:
            self.credit('5.0000')
        self.assertIn('lines', caught.exception.message_dict)
        self.assertEqual(net_credited(self.document_line), Decimal('20.0000'))

    def test_a_debit_can_only_reverse_a_credit(self):
        """An undercharge needs an order at the right price, not a figure typed here."""
        with self.assertRaises(ValidationError) as caught:
            self.credit(
                '5.0000',
                correction_type=SupplyCorrection.CorrectionType.DEBIT,
                reason_code=SupplyCorrection.Reason.OTHER,
            )
        self.assertIn('lines', caught.exception.message_dict)

    def test_a_debit_that_reverses_a_credit_restores_the_value(self):
        """The one bounded debit there is, and it nets back exactly."""
        self.credit('8.0000')
        self.credit(
            '3.0000',
            correction_type=SupplyCorrection.CorrectionType.DEBIT,
            reason_code=SupplyCorrection.Reason.OTHER,
        )

        self.assertEqual(net_credited(self.document_line), Decimal('5.0000'))
        self.assertEqual(document_state(self.document)['net_total_incl_tax'], Decimal('18.0000'))

    def test_a_correction_cannot_predate_the_document_it_corrects(self):
        """A correction to something not yet issued describes nothing."""
        with self.assertRaises(ValidationError) as caught:
            self.credit('1.0000', corrected_on=date(2026, 5, 1))
        self.assertIn('corrected_on', caught.exception.message_dict)

    def test_a_correction_needs_a_reason(self):
        """Change 4 asks a correction to say why, and a blank box does not."""
        with self.assertRaises(ValidationError) as caught:
            self.credit('1.0000', reason='   ')
        self.assertIn('reason', caught.exception.message_dict)

    def test_a_retried_operation_key_returns_the_first_correction(self):
        """A retry must not credit a customer twice."""
        key = uuid4()
        first = self.credit('4.0000', operation_key=key)
        second = self.credit('4.0000', operation_key=key)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(SupplyCorrection.objects.count(), 1)

    def test_crediting_a_document_already_credited_in_full_is_refused(self):
        """There is nothing left to correct, and saying so beats a nil record."""
        full_credit(
            self.document, self.user,
            operation_key=uuid4(),
            reason_code=SupplyCorrection.Reason.CANCELLATION,
            reason='Cancelled',
            corrected_on=date(2026, 5, 6),
        )
        with self.assertRaises(ValidationError) as caught:
            full_credit(
                self.document, self.user,
                operation_key=uuid4(),
                reason_code=SupplyCorrection.Reason.CANCELLATION,
                reason='Cancelled again',
                corrected_on=date(2026, 5, 7),
            )
        self.assertIn('document', caught.exception.message_dict)

    def test_a_correction_carries_the_gst_number_of_the_supply_it_corrects(self):
        """Re-reading the registration would put this year's number on last year's supply."""
        self.register_for_gst(
            effective_from=date(2026, 5, 5), gst_number='136410132',
        )
        correction = self.credit('2.0000', corrected_on=date(2026, 5, 6))
        self.assertEqual(correction.seller_gst_number, '049091850')


class DocumentImmutabilityTests(CorrectionScenario):
    """No reversal anywhere rewrites a document that was handed over."""

    def test_every_kind_of_correction_leaves_the_document_byte_identical(self):
        """Verification 3, stated as a comparison of every stored column."""
        before = self.snapshot()
        self.credit('4.0000', reason_code=SupplyCorrection.Reason.DISCOUNT)
        self.credit(
            '1.0000',
            correction_type=SupplyCorrection.CorrectionType.DEBIT,
            reason_code=SupplyCorrection.Reason.OTHER,
        )
        full_credit(
            self.document, self.user,
            operation_key=uuid4(),
            reason_code=SupplyCorrection.Reason.CANCELLATION,
            reason='And then cancelled',
            corrected_on=date(2026, 5, 8),
        )
        self.assertEqual(self.snapshot(), before)

    def test_reversing_the_return_behind_a_credit_leaves_both_standing(self):
        """The commerce fact can be undone; the document that evidenced it cannot."""
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
        correction = self.credit(
            '11.5000',
            reason_code=SupplyCorrection.Reason.RETURN,
            sales_return=returned,
        )
        before = self.snapshot()

        reverse_return(returned, self.user, operation_key=uuid4(), reason='Return entered in error')

        self.assertEqual(self.snapshot(), before)
        correction.refresh_from_db()
        self.assertEqual(correction.total_incl_tax, Decimal('11.5000'))
        self.assertEqual(correction.sales_return_id, returned.pk)

    def test_reversing_the_refund_behind_a_credit_leaves_both_standing(self):
        """Same rule on the money side: the evidence outlives the transaction."""
        payment = self.pay(self.order, '23.0000', date(2026, 5, 2))
        refund = post_refund(
            self.order, self.user,
            operation_key=uuid4(),
            payment=payment,
            fulfillment_lines=list(self.fulfillment.lines.all()),
            amount=Decimal('5.0000'),
            reason='Damaged in transit',
        )
        correction = self.credit('5.0000', refund=refund)
        before = self.snapshot()

        reverse_refund(refund, self.user, operation_key=uuid4(), reason='Refund entered in error')

        self.assertEqual(self.snapshot(), before)
        correction.refresh_from_db()
        self.assertEqual(correction.refund_id, refund.pk)
