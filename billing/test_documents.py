"""Issuing a taxable supply document, and everything that stops one twice.

These are change 2, change 3 and change 5 of task 118: what a document states,
the five order shapes it has to cope with, and the information each value band
requires before one may be issued at all.
"""

# pylint: disable=duplicate-code

from datetime import date, datetime, timezone as datetime_timezone
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import TestCase

from .documents import invoiceable, issue_supply_document
from .models import SupplyDocument, SupplyDocumentCoverage
from .test_fixtures import DocumentScenarioMixin


class IssuingTests(DocumentScenarioMixin, TestCase):
    """What one issued document says, and what it refuses to say."""

    def setUp(self):
        """Register for GST and confirm a two-plant order to invoice."""
        super().setUp()
        self.registration = self.register_for_gst()
        self.plants = self.ready_plants(2)
        self.customer = self.make_customer()
        self.order, self.line, self.allocations = self.confirmed_order(
            self.plants, customer=self.customer,
        )

    def issue(self, positions=(1, 2), **overrides):
        """Issue one document covering the positions given."""
        values = {
            'operation_key': uuid4(),
            'lines': [{'order_line': self.line, 'positions': list(positions)}],
            'issued_on': date(2026, 5, 4),
        }
        values.update(overrides)
        return issue_supply_document(self.order, self.user, **values)

    def test_a_document_snapshots_the_seller_in_force_on_its_own_date(self):
        """Renaming the business later must not restate a document handed over."""
        document = self.issue()

        self.assertEqual(document.document_number, 'INV-000001')
        self.assertEqual(document.seller_legal_name, 'Kowhai Growers Limited')
        self.assertEqual(document.seller_trading_name, 'Kowhai Nursery')
        self.assertEqual(document.seller_gst_number, '049091850')
        self.assertEqual(document.seller_registration_id, self.registration.pk)
        self.assertTrue(document.taxable_supply)

        self.workspace.legal_name = 'Kowhai Growers (2026) Limited'
        self.workspace.save()
        document.refresh_from_db()
        self.assertEqual(document.seller_legal_name, 'Kowhai Growers Limited')

    def test_a_document_totals_exactly_the_positions_it_covers(self):
        """Nothing is apportioned, so a document adds back up to its order."""
        document = self.issue()

        self.assertEqual(document.total_incl_tax, self.order.total_incl_tax)
        self.assertEqual(document.subtotal_ex_tax, Decimal('20.0000'))
        self.assertEqual(document.tax_total, Decimal('3.0000'))
        self.assertEqual(document.total_incl_tax, Decimal('23.0000'))
        line = document.lines.get()
        self.assertEqual(line.quantity, 2)
        self.assertEqual(line.total_incl_tax, Decimal('23.0000'))

    def test_the_value_band_is_recorded_as_the_document_was_issued(self):
        """A $23 supply is in the low band and needs no buyer identified."""
        document = self.issue()
        self.assertEqual(document.tier, 'low')

    def test_a_retried_operation_key_returns_the_first_document(self):
        """A dropped connection must not issue a customer two invoices."""
        key = uuid4()
        first = self.issue(operation_key=key)
        second = self.issue(operation_key=key)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(SupplyDocument.objects.count(), 1)

    def test_one_operation_key_cannot_mean_two_different_documents(self):
        """Reusing a key for other work is refused rather than answered wrongly."""
        key = uuid4()
        self.issue(positions=(1,), operation_key=key)
        with self.assertRaises(ValidationError) as caught:
            self.issue(positions=(2,), operation_key=key)
        self.assertIn('operation_key', caught.exception.message_dict)

    def test_an_item_cannot_be_invoiced_twice(self):
        """The whole point of covering positions rather than amounts."""
        self.issue(positions=(1,))
        with self.assertRaises(ValidationError) as caught:
            self.issue(positions=(1,))
        self.assertIn('lines', caught.exception.message_dict)

    def test_a_partial_invoice_leaves_the_rest_invoiceable(self):
        """A backorder invoices what shipped and keeps the rest available."""
        self.issue(positions=(1,))
        remaining = invoiceable(self.order)[0]

        self.assertEqual(remaining['invoiced_positions'], [1])
        self.assertEqual([row['position'] for row in remaining['positions']], [2])

        second = self.issue(positions=(2,))
        self.assertEqual(second.document_number, 'INV-000002')
        self.assertEqual(second.total_incl_tax, Decimal('11.5000'))
        self.assertEqual(invoiceable(self.order)[0]['positions'], [])

    def test_invoicing_before_dispatch_records_no_fulfillment(self):
        """An invoice ahead of delivery is a real document with nothing shipped."""
        document = self.issue()
        coverage = SupplyDocumentCoverage.objects.filter(document_line__document=document)
        self.assertEqual(coverage.count(), 2)
        self.assertTrue(all(row.fulfillment_line_id is None for row in coverage))

    def test_invoicing_after_dispatch_links_the_exact_items_delivered(self):
        """A document issued after delivery points at the plants that left."""
        fulfillment = self.fulfill(self.order, self.allocations)
        document = self.issue()
        covered = {
            row.commercial_position: row.fulfillment_line_id
            for row in SupplyDocumentCoverage.objects.filter(document_line__document=document)
        }
        dispatched = {line.commercial_position: line.pk for line in fulfillment.lines.all()}
        self.assertEqual(covered, dispatched)

    def test_a_returned_item_is_not_offered_for_invoicing(self):
        """Something given back before it was ever invoiced is not a supply."""
        fulfillment = self.fulfill(self.order, self.allocations)
        from sales.commerce import post_return  # pylint: disable=import-outside-toplevel

        post_return(
            self.order, self.user,
            operation_key=uuid4(),
            items=[{
                'fulfillment_line': fulfillment.lines.order_by('pk').first(),
                'outcome': 'available',
                'destination': self.store,
            }],
            reason='Wrong variety sent',
        )
        offered = [row['position'] for row in invoiceable(self.order)[0]['positions']]
        self.assertEqual(offered, [2])

    def test_a_deposit_shows_as_money_paid_rather_than_as_a_line(self):
        """The deposit is already a payment; a line for it would double the value."""
        self.pay(self.order, '10.0000', date(2026, 5, 1))
        document = self.issue()

        self.assertEqual(document.lines.count(), 1)
        self.assertEqual(document.paid_to_date, Decimal('10.0000'))
        self.assertEqual(document.previously_invoiced, Decimal('0.0000'))
        self.assertEqual(document.balance_due, Decimal('13.0000'))
        self.assertEqual(document.overpaid_at_issue, Decimal('0.0000'))

    def test_a_later_invoice_counts_what_the_earlier_one_already_billed(self):
        """Two documents on one order state one running balance between them."""
        self.pay(self.order, '11.5000', date(2026, 5, 1))
        first = self.issue(positions=(1,))
        second = self.issue(positions=(2,))

        self.assertEqual(first.previously_invoiced, Decimal('0.0000'))
        self.assertEqual(first.balance_due, Decimal('0.0000'))
        self.assertEqual(second.previously_invoiced, Decimal('11.5000'))
        self.assertEqual(second.paid_to_date, Decimal('11.5000'))
        self.assertEqual(second.balance_due, Decimal('11.5000'))

    def test_an_overpayment_is_reported_rather_than_discounting_the_supply(self):
        """Money beyond the value of a supply is not a reduction in it."""
        self.pay(self.order, '30.0000', date(2026, 5, 1))
        document = self.issue()

        self.assertEqual(document.total_incl_tax, Decimal('23.0000'))
        self.assertEqual(document.balance_due, Decimal('0.0000'))
        self.assertEqual(document.overpaid_at_issue, Decimal('7.0000'))

    def test_money_received_after_the_document_date_is_not_on_it(self):
        """A snapshot dated May cannot know about June's cheque."""
        self.pay(self.order, '23.0000', date(2026, 6, 1))
        document = self.issue(issued_on=date(2026, 5, 4))
        self.assertEqual(document.paid_to_date, Decimal('0.0000'))

    def test_a_refund_before_the_document_date_reduces_what_was_paid(self):
        """Paid to date is cash net of what went back, not cash in."""
        payment = self.pay(self.order, '23.0000', date(2026, 5, 1))
        fulfillment = self.fulfill(self.order, self.allocations)
        from sales.commerce import post_refund  # pylint: disable=import-outside-toplevel

        post_refund(
            self.order, self.user,
            operation_key=uuid4(),
            payment=payment,
            fulfillment_lines=list(fulfillment.lines.all()),
            amount=Decimal('5.0000'),
            reason='Goodwill',
            refunded_at=datetime(2026, 5, 2, 3, 0, tzinfo=datetime_timezone.utc),
        )
        document = self.issue()
        self.assertEqual(document.paid_to_date, Decimal('18.0000'))


class RefusalTests(DocumentScenarioMixin, TestCase):
    """A document that would be defective is refused, saying what is absent."""

    def setUp(self):
        """Start registered, with a customer, and take things away per test."""
        super().setUp()
        self.register_for_gst()

    def order_worth(self, unit_price, count=2, customer=None):
        """Confirm an order whose value lands in a chosen band."""
        plants = self.ready_plants(count)
        return self.confirmed_order(plants, customer=customer, unit_price=unit_price)

    def issue(self, order, line, **overrides):
        """Issue every position of one line."""
        values = {
            'operation_key': uuid4(),
            'lines': [{'order_line': line, 'positions': list(range(1, line.quantity + 1))}],
            'issued_on': date(2026, 5, 4),
        }
        values.update(overrides)
        return issue_supply_document(order, self.user, **values)

    def test_a_large_supply_without_a_buyer_is_refused_by_name(self):
        """Over $1,000 the recipient has to be identified, and it says so."""
        order, line, _allocations = self.order_worth('900.0000')
        with self.assertRaises(ValidationError) as caught:
            self.issue(order, line)
        self.assertEqual(
            set(caught.exception.message_dict),
            {'buyer_name', 'buyer_identification'},
        )

    def test_the_same_supply_under_a_thousand_is_issued_anonymously(self):
        """A market stall does not ask a stranger for a postal address."""
        order, line, _allocations = self.order_worth('400.0000')
        document = self.issue(order, line)
        self.assertEqual(document.tier, 'standard')
        self.assertEqual(document.buyer_name, '')

    def test_a_large_supply_to_a_known_customer_is_identified_from_the_record(self):
        """The buyer block comes off the customer without being retyped."""
        order, line, _allocations = self.order_worth('900.0000', customer=self.make_customer())
        document = self.issue(order, line)
        self.assertEqual(document.tier, 'full')
        self.assertEqual(document.buyer_name, 'Riverbend Landscapes')
        self.assertIn('Quarry Road', document.buyer_address)

    def test_a_one_off_buyer_can_be_named_without_a_customer_record(self):
        """Nobody should have to create a customer to invoice one wholesale load."""
        order, line, _allocations = self.order_worth('900.0000')
        document = self.issue(order, line, buyer={
            'buyer_name': 'Te Awa Plantings',
            'buyer_identifier': '9429041234567',
        })
        self.assertEqual(document.buyer_name, 'Te Awa Plantings')
        self.assertEqual(document.buyer_identification, '9429041234567')

    def test_a_workspace_with_no_legal_name_cannot_issue_anything(self):
        """The workspace name is not silently substituted for the entity's."""
        self.workspace.legal_name = ''
        self.workspace.save()
        order, line, _allocations = self.order_worth('10.0000')
        with self.assertRaises(ValidationError) as caught:
            self.issue(order, line)
        self.assertIn('seller_legal_name', caught.exception.message_dict)

    def test_a_document_cannot_predate_its_own_order(self):
        """Backdating past the order would file a supply before it was agreed."""
        order, line, _allocations = self.order_worth('10.0000')
        with self.assertRaises(ValidationError) as caught:
            self.issue(order, line, issued_on=date(2025, 12, 31))
        self.assertIn('issued_on', caught.exception.message_dict)

    def test_a_quote_cannot_be_invoiced(self):
        """A quote is an offer, not a supply."""
        from sales.services import create_order  # pylint: disable=import-outside-toplevel
        from sales.models import SalesOrder  # pylint: disable=import-outside-toplevel

        quote = create_order(self.workspace, self.user, status=SalesOrder.Status.QUOTE)
        with self.assertRaises(ValidationError) as caught:
            issue_supply_document(
                quote, self.user, operation_key=uuid4(), lines=[],
            )
        self.assertIn('order', caught.exception.message_dict)

    def test_selecting_nothing_is_refused(self):
        """An empty document describes no supply and would fail its own band anyway."""
        order, line, _allocations = self.order_worth('10.0000')
        del line
        with self.assertRaises(ValidationError) as caught:
            issue_supply_document(order, self.user, operation_key=uuid4(), lines=[])
        self.assertIn('lines', caught.exception.message_dict)


class UnregisteredSellerTests(DocumentScenarioMixin, TestCase):
    """A nursery below the threshold issues receipts, not tax invoices."""

    def test_an_unregistered_workspace_issues_a_document_with_no_gst(self):
        """Trading before registration is real, and needs a record of its own."""
        plants = self.ready_plants(1)
        order, line, _allocations = self.confirmed_order(plants, tax_rate='0')
        document = issue_supply_document(
            order, self.user,
            operation_key=uuid4(),
            lines=[{'order_line': line, 'positions': [1]}],
            issued_on=date(2026, 5, 4),
        )

        self.assertFalse(document.taxable_supply)
        self.assertEqual(document.seller_gst_number, '')
        self.assertIsNone(document.seller_registration_id)
        self.assertEqual(document.tax_total, Decimal('0.0000'))

    def test_an_unregistered_workspace_cannot_issue_a_document_charging_gst(self):
        """Charging tax nobody was registered to charge is refused outright."""
        plants = self.ready_plants(1)
        order, line, _allocations = self.confirmed_order(plants)
        with self.assertRaises(ValidationError) as caught:
            issue_supply_document(
                order, self.user,
                operation_key=uuid4(),
                lines=[{'order_line': line, 'positions': [1]}],
                issued_on=date(2026, 5, 4),
            )
        self.assertIn('taxable_supply', caught.exception.message_dict)
