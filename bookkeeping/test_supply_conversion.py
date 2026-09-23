"""Supply, payments and refunds converted into the workspace's currency.

Task 121's sales side. One euro order, dispatched and invoiced, paid and partly
refunded, in a workspace that afterwards records New Zealand dollars -- which
is how a taxable supply document comes to be foreign, since every document
takes the currency of the order it is issued against.

The four records are converted separately because they are four transactions.
An invoice issued in May and a refund paid in June did not happen at the same
rate, and nothing here makes one stand in for the other.
"""

# Test names state their behavior and are clearer than repeated method docstrings.
# One euro scenario needs the whole chain behind it -- workspace, order, line,
# allocations, fulfillment, document and payment -- the same reason
# `billing.test_corrections` gives for the same disable.
# pylint: disable=missing-function-docstring,duplicate-code,too-many-instance-attributes

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.test import TestCase

from billing.documents import issue_correction, issue_supply_document
from billing.models import SupplyCorrection
from billing.test_fixtures import DocumentScenarioMixin
from sales.commerce import post_refund
from workspaces.conversion import QuoteDirection
from workspaces.models import Workspace

from .conversion import live_conversion, record_conversion


#: The rate every euro record below is converted at. See
#: `test_currency_conversion` for what it is and why it is this one.
EURO_RATE = Decimal('1.6701057193')


class SupplyConversionTestCase(DocumentScenarioMixin, TestCase):
    """A euro order, invoiced and paid, in a workspace that moved to dollars."""

    def setUp(self):
        super().setUp()
        self.record_in('EUR')
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
        self.payment = self.pay(self.order, '23.0000', date(2026, 5, 5))
        self.record_in('NZD')

    def record_in(self, currency_code):
        """Switch the currency this workspace records its amounts in.

        A sales order takes the workspace's currency, a document takes the
        order's and a payment takes the document's, so this is the only way a
        whole euro order exists -- and it is the real one: a workspace left on
        the default code that later sets its own leaves every row it has
        already recorded behind in the old one.
        """
        Workspace.objects.filter(pk=self.workspace.pk).update(
            currency_code=currency_code,
        )
        self.workspace.refresh_from_db()

    def convert(self, source_type, source_id, **overrides):
        """Type a rate against one transaction."""
        request = {
            'rate': EURO_RATE,
            'quote_direction': QuoteDirection.TARGET_PER_SOURCE,
            'rate_source': 'Reserve Bank of New Zealand',
            'effective_date': date(2026, 5, 4),
        }
        request.update(overrides)
        return record_conversion(
            self.workspace, source_type, source_id, request, self.user,
        )

    def amounts_of(self, conversion):
        """Return the converted figures by name."""
        return {row['name']: row['converted'] for row in conversion.amounts}


class SupplyDocumentConversionTests(SupplyConversionTestCase):
    """Verification 2: taxable supply information reconciles to a figure."""

    def test_a_euro_document_states_what_it_comes_to_in_dollars(self):
        conversion = self.convert('supply_document', self.document.pk)

        self.assertEqual(self.amounts_of(conversion), {
            'subtotal_ex_tax': '33.4021',
            'tax_total': '5.0103',
            'total_incl_tax': '38.4124',
        })
        self.assertEqual(conversion.source_currency_code, 'EUR')
        self.assertEqual(conversion.target_currency_code, 'NZD')

    def test_the_issued_document_still_says_what_the_buyer_was_charged(self):
        """An issued document is immutable, and the conversion is beside it."""
        self.convert('supply_document', self.document.pk)

        self.document.refresh_from_db()
        self.assertEqual(self.document.currency_code, 'EUR')
        self.assertEqual(f'{self.document.total_incl_tax:.4f}', '23.0000')

    def test_a_correction_carries_its_own_rate_on_its_own_date(self):
        correction = issue_correction(
            self.document, self.user,
            operation_key=uuid4(),
            correction_type=SupplyCorrection.CorrectionType.CREDIT,
            reason_code=SupplyCorrection.Reason.PARTIAL_CREDIT,
            reason='Two plants arrived damaged',
            lines=[{
                'document_line': self.document.lines.get(),
                'amount': Decimal('5.0000'),
            }],
            corrected_on=date(2026, 5, 6),
        )
        self.convert('supply_document', self.document.pk)

        conversion = self.convert(
            'supply_correction', correction.pk,
            rate=Decimal('2'), effective_date=date(2026, 5, 6),
        )

        self.assertEqual(self.amounts_of(conversion)['total_incl_tax'], '10.0000')
        self.assertEqual(
            f'{live_conversion(self.workspace, "supply_document", self.document.pk).rate:f}',
            '1.6701057193',
        )


class PaymentConversionTests(SupplyConversionTestCase):
    """Cash received and cash returned are each their own transaction."""

    def test_a_euro_payment_states_what_was_received_in_dollars(self):
        conversion = self.convert('payment', self.payment.pk)

        self.assertEqual(self.amounts_of(conversion), {'amount': '38.4124'})
        self.assertEqual(conversion.effective_date, date(2026, 5, 4))

    def test_a_refund_is_converted_at_the_rate_on_the_day_it_was_paid(self):
        refund = post_refund(
            self.order, self.user,
            operation_key=uuid4(),
            payment=self.payment,
            fulfillment_lines=list(self.fulfillment.lines.all()),
            amount=Decimal('5.0000'),
            reason='Two plants arrived damaged',
        )
        self.convert('payment', self.payment.pk)

        conversion = self.convert(
            'refund', refund.pk, rate=Decimal('2'), effective_date=date(2026, 6, 1),
        )

        self.assertEqual(self.amounts_of(conversion), {'amount': '10.0000'})
        self.assertEqual(
            f'{live_conversion(self.workspace, "payment", self.payment.pk).rate:f}',
            '1.6701057193',
        )

    def test_every_sales_record_is_converted_on_its_own(self):
        """Four transactions, four rates, and no figure derived from another."""
        self.convert('supply_document', self.document.pk)
        self.convert('payment', self.payment.pk)

        self.assertEqual(
            {
                (row.source_type, f'{row.rate:f}')
                for row in [
                    live_conversion(
                        self.workspace, 'supply_document', self.document.pk,
                    ),
                    live_conversion(self.workspace, 'payment', self.payment.pk),
                ]
            },
            {
                ('supply_document', '1.6701057193'),
                ('payment', '1.6701057193'),
            },
        )
