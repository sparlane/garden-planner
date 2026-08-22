"""The basis-by-order-shape matrix task 117's verification section asks for.

Six order shapes — prepaid, part-paid, invoiced but unpaid, fulfilled and
paid, returned without a refund, and refunded — across the payments, invoice
and hybrid bases. Every case asserts the exact date GST falls due and the
exact amount, because a return is wrong if either is off by one.

These are pure: no database, no fixtures. That is the reason there can be
eighteen of them.
"""

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from .recognition import (
    BASES,
    HYBRID,
    INVOICE,
    PAYMENTS,
    STANDARD,
    SUPPLY,
    SUPPLY_CREDIT,
    ZERO_RATED,
    FulfillmentFact,
    LineFact,
    OrderFacts,
    PaymentFact,
    RefundFact,
    RefundPortion,
    line_tax,
    order_recognition,
)


RATE = Decimal('15.0000')
MARCH = date(2026, 3, 10)
APRIL = date(2026, 4, 20)
MAY = date(2026, 5, 5)


def standard_line(line_id, gross):
    """A standard-rated line whose entered price includes GST."""
    return LineFact(line_id=line_id, tax_rate=RATE, tax_code=STANDARD, gross_incl_tax=Decimal(gross))


def order(lines, **facts):
    """Assemble one order's facts around its lines."""
    return OrderFacts(order_id=1, currency_code='NZD', lines=tuple(lines), **facts)


def totals(recognition):
    """Return the gross and tax a recognition brings to account, net of credits."""
    gross = sum((event.gross for event in recognition.supply_events), Decimal('0.0000'))
    tax = sum((event.tax for event in recognition.supply_events), Decimal('0.0000'))
    credit_gross = sum((event.gross for event in recognition.credit_events), Decimal('0.0000'))
    credit_tax = sum((event.tax for event in recognition.credit_events), Decimal('0.0000'))
    return gross - credit_gross, tax - credit_tax


def dates_of(recognition, kind=SUPPLY):
    """Return the dates GST fell due, as a set, for one kind of event."""
    return {event.supply_date for event in recognition.events if event.kind == kind}


class LineTaxTests(SimpleTestCase):
    """A rate of zero cannot tell a zero-rated supply from an exempt one."""

    def test_a_standard_line_carries_the_gst_inside_its_price(self):
        """15% of a 115.00 inclusive price is 15.00, not 17.25."""
        self.assertEqual(line_tax(Decimal('115.0000'), RATE, STANDARD), Decimal('15.0000'))

    def test_a_zero_rated_line_carries_no_tax(self):
        """An export is taxable at zero, so it counts as turnover but adds no tax."""
        self.assertEqual(line_tax(Decimal('115.0000'), Decimal('0'), ZERO_RATED), Decimal('0.0000'))

    def test_the_code_decides_rather_than_the_rate(self):
        """An exempt line carrying a stray rate must still produce no output tax."""
        self.assertEqual(line_tax(Decimal('115.0000'), RATE, 'exempt'), Decimal('0.0000'))

    def test_taxable_is_the_residual_so_a_row_always_balances(self):
        """Deriving taxable the other way leaves rows that do not sum to gross."""
        recognition = order_recognition(
            order([standard_line(1, '100.0000')], payments=(PaymentFact(1, MARCH, Decimal('100.0000')),)),
            PAYMENTS,
        )
        for event in recognition.events:
            self.assertEqual(event.taxable + event.tax, event.gross)


class UnknownBasisTests(SimpleTestCase):
    """A typo in a basis must not silently recognise nothing."""

    def test_an_unknown_basis_is_refused(self):
        """Defaulting to one of the three would produce a plausible wrong return."""
        with self.assertRaises(ValueError):
            order_recognition(order([standard_line(1, '100.0000')]), 'accrual')


class PrepaidOrderTests(SimpleTestCase):
    """Money first, plants later. Every basis recognises at the payment."""

    def facts(self):
        """A customer pays in March for plants delivered in April."""
        return order(
            [standard_line(1, '115.0000')],
            payments=(PaymentFact(payment_id=7, paid_on=MARCH, gross=Decimal('115.0000')),),
            fulfillments=(FulfillmentFact(9, APRIL, {1: Decimal('115.0000')}),),
        )

    def test_every_basis_recognises_at_the_payment(self):
        """Payment is the earlier trigger, so the invoice basis agrees with payments."""
        for basis in BASES:
            with self.subTest(basis=basis):
                recognition = order_recognition(self.facts(), basis)
                self.assertEqual(dates_of(recognition), {MARCH})
                self.assertEqual(totals(recognition), (Decimal('115.0000'), Decimal('15.0000')))

    def test_the_delivery_adds_nothing(self):
        """Recognising the delivery again would double the return."""
        recognition = order_recognition(self.facts(), INVOICE)
        self.assertEqual(len(recognition.supply_events), 1)
        self.assertEqual(recognition.unrecognised_gross, Decimal('0.0000'))


class PartiallyPaidOrderTests(SimpleTestCase):
    """A deposit, a delivery, then the balance — the case bases disagree on."""

    def facts(self):
        """40.00 down in March, delivered in April, 75.00 balance in May."""
        return order(
            [standard_line(1, '115.0000')],
            payments=(
                PaymentFact(7, MARCH, Decimal('40.0000')),
                PaymentFact(8, MAY, Decimal('75.0000')),
            ),
            fulfillments=(FulfillmentFact(9, APRIL, {1: Decimal('115.0000')}),),
        )

    def test_payments_basis_recognises_at_each_payment(self):
        """Two returns carry this order, and neither is the delivery period."""
        recognition = order_recognition(self.facts(), PAYMENTS)
        self.assertEqual(dates_of(recognition), {MARCH, MAY})
        self.assertEqual(totals(recognition), (Decimal('115.0000'), Decimal('15.0000')))

    def test_invoice_basis_recognises_the_deposit_then_the_balance_on_delivery(self):
        """The delivery is the earlier trigger for everything not already prepaid."""
        recognition = order_recognition(self.facts(), INVOICE)
        self.assertEqual(dates_of(recognition), {MARCH, APRIL})
        by_date = {event.supply_date: event.gross for event in recognition.supply_events}
        self.assertEqual(by_date[MARCH], Decimal('40.0000'))
        self.assertEqual(by_date[APRIL], Decimal('75.0000'))

    def test_hybrid_matches_invoice_for_output_tax(self):
        """Hybrid differs only on input tax, which this module does not decide."""
        self.assertEqual(
            [event.supply_date for event in order_recognition(self.facts(), HYBRID).supply_events],
            [event.supply_date for event in order_recognition(self.facts(), INVOICE).supply_events],
        )

    def test_the_may_payment_adds_nothing_under_the_invoice_basis(self):
        """The delivery already brought the balance to account in April."""
        recognition = order_recognition(self.facts(), INVOICE)
        self.assertNotIn(MAY, dates_of(recognition))


class InvoicedButUnpaidOrderTests(SimpleTestCase):
    """Delivered, never paid. This is what the payments basis exists for."""

    def facts(self):
        """Plants delivered in April against an invoice nobody has settled."""
        return order(
            [standard_line(1, '115.0000')],
            fulfillments=(FulfillmentFact(9, APRIL, {1: Decimal('115.0000')}),),
        )

    def test_payments_basis_recognises_nothing(self):
        """No money has been received, so no GST is payable yet."""
        recognition = order_recognition(self.facts(), PAYMENTS)
        self.assertEqual(recognition.supply_events, ())
        self.assertEqual(recognition.unrecognised_gross, Decimal('115.0000'))

    def test_invoice_basis_recognises_the_whole_supply_on_delivery(self):
        """GST falls due whether or not the customer ever pays."""
        recognition = order_recognition(self.facts(), INVOICE)
        self.assertEqual(dates_of(recognition), {APRIL})
        self.assertEqual(totals(recognition), (Decimal('115.0000'), Decimal('15.0000')))

    def test_the_fulfillment_is_flagged_as_an_invoice_date_proxy(self):
        """Task 118 has to find exactly these events to supersede them."""
        recognition = order_recognition(self.facts(), INVOICE)
        self.assertTrue(all(event.proxy for event in recognition.supply_events))
        self.assertEqual(
            {event.time_of_supply_source for event in recognition.supply_events},
            {'fulfillment'},
        )

    def test_a_payment_trigger_is_never_a_proxy(self):
        """A payment date is a fact; only the invoice date is being stood in for."""
        facts = order(
            [standard_line(1, '115.0000')],
            payments=(PaymentFact(7, MARCH, Decimal('115.0000')),),
        )
        recognition = order_recognition(facts, INVOICE)
        self.assertFalse(any(event.proxy for event in recognition.supply_events))


class FulfilledAndPaidOrderTests(SimpleTestCase):
    """Delivered in April, paid in May — the bases part company by a period."""

    def facts(self):
        """The ordinary trade sale: goods first, payment on terms."""
        return order(
            [standard_line(1, '115.0000')],
            fulfillments=(FulfillmentFact(9, APRIL, {1: Decimal('115.0000')}),),
            payments=(PaymentFact(7, MAY, Decimal('115.0000')),),
        )

    def test_payments_basis_recognises_in_may(self):
        """The money arrived in May, so that is the return it belongs to."""
        self.assertEqual(dates_of(order_recognition(self.facts(), PAYMENTS)), {MAY})

    def test_invoice_basis_recognises_in_april(self):
        """The delivery is the earlier trigger, a whole period sooner."""
        self.assertEqual(dates_of(order_recognition(self.facts(), INVOICE)), {APRIL})

    def test_both_bases_recognise_the_same_total(self):
        """A change of basis moves when GST falls due, never how much."""
        self.assertEqual(
            totals(order_recognition(self.facts(), PAYMENTS)),
            totals(order_recognition(self.facts(), INVOICE)),
        )


class ReturnedWithoutRefundTests(SimpleTestCase):
    """A return moves plants, not money, so no GST adjustment is due."""

    def facts(self):
        """Delivered, paid, and physically returned with no money moved back."""
        return order(
            [standard_line(1, '115.0000')],
            fulfillments=(FulfillmentFact(9, APRIL, {1: Decimal('115.0000')}),),
            payments=(PaymentFact(7, APRIL, Decimal('115.0000')),),
            unrefunded_return_ids=(4,),
        )

    def test_no_basis_credits_anything(self):
        """The consideration has not changed, so neither has the GST on it."""
        for basis in BASES:
            with self.subTest(basis=basis):
                recognition = order_recognition(self.facts(), basis)
                self.assertEqual(recognition.credit_events, ())
                self.assertEqual(totals(recognition), (Decimal('115.0000'), Decimal('15.0000')))

    def test_the_outstanding_credit_note_is_reported(self):
        """Somebody still owes a credit note; silence would hide the work."""
        for basis in BASES:
            with self.subTest(basis=basis):
                recognition = order_recognition(self.facts(), basis)
                self.assertEqual(recognition.unrefunded_return_ids, (4,))


class RefundedOrderTests(SimpleTestCase):
    """A refund is the credit adjustment, read off the refund's own lines."""

    def facts(self, refund_gross='115.0000', refund_tax='15.0000'):
        """Delivered and paid in April, refunded in May."""
        return order(
            [standard_line(1, '115.0000')],
            fulfillments=(FulfillmentFact(9, APRIL, {1: Decimal('115.0000')}),),
            payments=(PaymentFact(7, APRIL, Decimal('115.0000')),),
            refunds=(RefundFact(
                refund_id=3,
                refunded_on=MAY,
                portions=(RefundPortion(1, Decimal(refund_gross), Decimal(refund_tax)),),
            ),),
        )

    def test_a_full_refund_credits_the_whole_supply(self):
        """The order nets to nothing, in both value and tax, under every basis."""
        for basis in BASES:
            with self.subTest(basis=basis):
                recognition = order_recognition(self.facts(), basis)
                self.assertEqual(totals(recognition), (Decimal('0.0000'), Decimal('0.0000')))

    def test_the_credit_falls_in_the_refund_period_not_the_supply_period(self):
        """April's return stands; May's return carries the correction."""
        recognition = order_recognition(self.facts(), INVOICE)
        self.assertEqual(dates_of(recognition, SUPPLY), {APRIL})
        self.assertEqual(dates_of(recognition, SUPPLY_CREDIT), {MAY})

    def test_a_partial_refund_credits_only_what_was_refunded(self):
        """The refund's own split is trusted; re-deriving it would drift."""
        recognition = order_recognition(
            self.facts(refund_gross='46.0000', refund_tax='6.0000'), INVOICE,
        )
        self.assertEqual(totals(recognition), (Decimal('69.0000'), Decimal('9.0000')))

    def test_a_credit_cannot_exceed_the_supply_it_credits(self):
        """Otherwise a refund would create a GST claim out of nothing."""
        recognition = order_recognition(
            self.facts(refund_gross='200.0000', refund_tax='26.0870'), INVOICE,
        )
        self.assertEqual(totals(recognition), (Decimal('0.0000'), Decimal('0.0000')))
        self.assertEqual(recognition.over_credited, Decimal('85.0000'))

    def test_a_refund_under_the_payments_basis_credits_the_payment(self):
        """Money went back out, so the payments basis credits it too."""
        recognition = order_recognition(self.facts(), PAYMENTS)
        self.assertEqual(dates_of(recognition, SUPPLY_CREDIT), {MAY})


class MixedRateApportionmentTests(SimpleTestCase):
    """A partial payment has no line linkage, so the split has to be derived."""

    def facts(self):
        """One standard-rated line and one zero-rated line, part paid."""
        return order(
            [
                standard_line(1, '115.0000'),
                LineFact(line_id=2, tax_rate=Decimal('0'), tax_code=ZERO_RATED, gross_incl_tax=Decimal('85.0000')),
            ],
            payments=(PaymentFact(7, MARCH, Decimal('100.0000')),),
        )

    def test_the_payment_is_split_by_remaining_value(self):
        """115 and 85 of a 200 order take 57.50 and 42.50 of a 100 payment."""
        recognition = order_recognition(self.facts(), PAYMENTS)
        by_line = {event.line_id: event.gross for event in recognition.supply_events}
        self.assertEqual(by_line, {1: Decimal('57.5000'), 2: Decimal('42.5000')})

    def test_the_shares_sum_back_to_the_payment_exactly(self):
        """A remainder dropped here would understate the return by cents."""
        recognition = order_recognition(self.facts(), PAYMENTS)
        self.assertEqual(
            sum(event.gross for event in recognition.supply_events), Decimal('100.0000'),
        )

    def test_only_the_standard_rated_share_carries_tax(self):
        """15/115 of 57.50 is 7.50; the zero-rated share adds nothing."""
        recognition = order_recognition(self.facts(), PAYMENTS)
        by_line = {event.line_id: event.tax for event in recognition.supply_events}
        self.assertEqual(by_line, {1: Decimal('7.5000'), 2: Decimal('0.0000')})

    def test_an_indivisible_payment_still_sums_back_exactly(self):
        """Three equal lines and a penny that will not divide by three."""
        facts = order(
            [standard_line(index, '100.0000') for index in (1, 2, 3)],
            payments=(PaymentFact(7, MARCH, Decimal('0.0001')),),
        )
        recognition = order_recognition(facts, PAYMENTS)
        self.assertEqual(
            sum(event.gross for event in recognition.supply_events), Decimal('0.0001'),
        )


class OverpaymentTests(SimpleTestCase):
    """Cash beyond the order's value is not consideration for any supply."""

    def test_the_excess_is_reported_rather_than_recognised(self):
        """Recognising it would create output tax on a supply nobody made."""
        facts = order(
            [standard_line(1, '115.0000')],
            payments=(PaymentFact(7, MARCH, Decimal('150.0000')),),
        )
        recognition = order_recognition(facts, PAYMENTS)
        self.assertEqual(totals(recognition), (Decimal('115.0000'), Decimal('15.0000')))
        self.assertEqual(recognition.unmatched_overpayment, Decimal('35.0000'))

    def test_a_payment_against_a_fully_recognised_order_is_wholly_unmatched(self):
        """The second payment has nothing left to be consideration for."""
        facts = order(
            [standard_line(1, '115.0000')],
            payments=(
                PaymentFact(7, MARCH, Decimal('115.0000')),
                PaymentFact(8, MAY, Decimal('50.0000')),
            ),
        )
        recognition = order_recognition(facts, PAYMENTS)
        self.assertEqual(recognition.unmatched_overpayment, Decimal('50.0000'))


class SameDayOrderingTests(SimpleTestCase):
    """A deposit and a same-day delivery must split the same way every run."""

    def test_the_payment_is_attributed_before_the_fulfillment(self):
        """An attribution that changed between runs cannot be reconciled."""
        facts = order(
            [standard_line(1, '115.0000')],
            payments=(PaymentFact(7, APRIL, Decimal('40.0000')),),
            fulfillments=(FulfillmentFact(9, APRIL, {1: Decimal('115.0000')}),),
        )
        recognition = order_recognition(facts, INVOICE)
        by_source = {event.source_type: event.gross for event in recognition.supply_events}
        self.assertEqual(by_source, {'payment': Decimal('40.0000'), 'fulfillment': Decimal('75.0000')})


class AsAtTests(SimpleTestCase):
    """Stopping the clock is what makes a basis-change adjustment computable."""

    def facts(self):
        """Delivered in April, paid in May."""
        return order(
            [standard_line(1, '115.0000')],
            fulfillments=(FulfillmentFact(9, APRIL, {1: Decimal('115.0000')}),),
            payments=(PaymentFact(7, MAY, Decimal('115.0000')),),
        )

    def test_a_cutoff_excludes_later_triggers(self):
        """At the end of April the invoice basis has recognised it; payments has not."""
        cutoff = date(2026, 4, 30)
        invoice = order_recognition(self.facts(), INVOICE, as_at=cutoff)
        payments = order_recognition(self.facts(), PAYMENTS, as_at=cutoff)
        self.assertEqual(invoice.recognised_gross, Decimal('115.0000'))
        self.assertEqual(payments.recognised_gross, Decimal('0.0000'))

    def test_the_difference_is_the_debtor_a_basis_change_adjusts_for(self):
        """That gap is exactly what a payments-to-invoice transition brings in."""
        cutoff = date(2026, 4, 30)
        invoice = order_recognition(self.facts(), INVOICE, as_at=cutoff)
        payments = order_recognition(self.facts(), PAYMENTS, as_at=cutoff)
        self.assertEqual(
            invoice.recognised_gross - payments.recognised_gross, Decimal('115.0000'),
        )

    def test_a_cutoff_excludes_a_later_refund(self):
        """A credit dated after the change belongs to the new basis, not the old."""
        facts = order(
            [standard_line(1, '115.0000')],
            fulfillments=(FulfillmentFact(9, APRIL, {1: Decimal('115.0000')}),),
            refunds=(RefundFact(3, MAY, (RefundPortion(1, Decimal('115.0000'), Decimal('15.0000')),)),),
        )
        recognition = order_recognition(facts, INVOICE, as_at=date(2026, 4, 30))
        self.assertEqual(recognition.credit_events, ())


class RestatedConstantTests(SimpleTestCase):
    """The strings restated here must not drift from the model's own choices.

    `recognition` stays free of the model layer so it can be tested without a
    database. That is worth a test rather than a comment: a fourth basis added
    to the model and not here would silently recognise nothing.
    """

    def test_the_bases_match_the_registration_model(self):
        """A basis the model offers and this module rejects would raise at report time."""
        from .models import GstRegistration  # pylint: disable=import-outside-toplevel
        self.assertEqual(set(BASES), set(GstRegistration.Basis.values))
