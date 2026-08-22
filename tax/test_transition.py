"""Verification 7: what a change of accounting basis leaves outstanding.

Moving onto the invoice rule means supplies already made but not yet paid for
have to be brought into account in one go; moving off it means output tax
already returned on unpaid debtors comes back out. The adjustment is computed
from the same recognition rules as everything else, with the clock stopped at
the change date, rather than from a second implementation of them.
"""

# pylint: disable=duplicate-code

from datetime import date, datetime, timezone as utc_timezone
from decimal import Decimal
from uuid import uuid4

from sales.models import (
    Fulfillment,
    FulfillmentLine,
    Payment,
    SalesOrder,
    SalesOrderAllocation,
    SalesOrderLine,
)
from sales.services import create_order
from tests.api import RESTContractTestCase
from tests.factories import make_specific_plant
from workspaces.models import Workspace, get_current_workspace

from .models import GstRegistration
from .services import record_registration
from .transition import basis_transition, basis_transitions, outstanding_debtors


class BasisTransitionTestCase(RESTContractTestCase):
    """A Nursery whose orders can be delivered and paid on chosen dates."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.currency_code = 'NZD'
        self.workspace.timezone = 'Pacific/Auckland'
        self.workspace.default_tax_rate = Decimal('15')
        self.workspace.sales_prices_include_tax = False
        self.workspace.save()
        self.delivered = 0

    def order_with(self, ex_tax='100', paid_on=None, delivered_on=date(2026, 5, 1)):
        """Deliver an order, optionally paid on a date.

        A merely confirmed order is not a debtor: nothing has been supplied and
        nothing invoiced, so the invoice basis has accounted for none of it
        either. It takes a delivery to make one.
        """
        order = create_order(self.workspace, self.user, status=SalesOrder.Status.DRAFT)
        plant = make_specific_plant(workspace=self.workspace)
        line = SalesOrderLine.objects.create(
            order=order,
            line_type=SalesOrderLine.LineType.SEEDLING,
            variety=plant.batch.variety,
            description='Seedlings',
            quantity=1,
            unit_price=Decimal(ex_tax),
            tax_rate=Decimal('15'),
            discount_type=SalesOrderLine.DiscountType.NONE,
        )
        SalesOrder.objects.filter(pk=order.pk).update(status=SalesOrder.Status.CONFIRMED)
        order.refresh_from_db()
        allocation = SalesOrderAllocation.objects.create(line=line, plant=plant)
        self.delivered += 1
        fulfillment = Fulfillment.objects.create(
            workspace=self.workspace, order=order,
            fulfillment_number=f'FUL-{self.delivered:06d}',
            fulfilled_at=datetime(
                delivered_on.year, delivered_on.month, delivered_on.day, 2, 0,
                tzinfo=utc_timezone.utc,
            ),
            operation_key=uuid4(), request_fingerprint='transition-test',
        )
        FulfillmentLine.objects.create(
            fulfillment=fulfillment, allocation=allocation, commercial_position=1,
            gross_ex_tax=line.gross_ex_tax, discount_ex_tax=line.discount_ex_tax,
            subtotal_ex_tax=line.subtotal_ex_tax, tax_total=line.tax_total,
            total_incl_tax=line.total_incl_tax, tax_treatment=line.tax_treatment,
            currency_code='NZD',
        )
        if paid_on is not None:
            Payment.objects.create(
                workspace=self.workspace, order=order, paid_on=paid_on,
                amount=line.total_incl_tax, currency_code='NZD', method='cash',
                operation_key=uuid4(), request_fingerprint='transition-test',
            )
        return order

    def register(self, basis, effective_from, **overrides):
        """Record an arrangement on a basis from a date."""
        values = {
            'registered': True,
            'effective_from': effective_from,
            'gst_number': '123456785',
            'basis': basis,
            'filing_frequency': GstRegistration.Frequency.TWO_MONTHLY,
            'period_anchor_month': 4,
        }
        values.update(overrides)
        return record_registration(self.workspace, self.user, **values)


class OutstandingDebtorTests(BasisTransitionTestCase):
    """A debtor is a supply made that the money has not caught up with."""

    def test_an_unpaid_order_is_a_debtor(self):
        """Nothing was received, so the payments basis has accounted for none of it."""
        order = self.order_with('100')
        debtors = outstanding_debtors(self.workspace, date(2026, 6, 30))
        self.assertEqual([portion.order_id for portion in debtors], [order.pk])
        self.assertEqual(debtors[0].gross, Decimal('115.0000'))
        self.assertEqual(debtors[0].tax, Decimal('15.0000'))

    def test_an_undelivered_order_is_not_a_debtor(self):
        """Nothing has been supplied or invoiced, so neither basis has accounted for it."""
        create_order(self.workspace, self.user, status=SalesOrder.Status.DRAFT)
        self.assertEqual(outstanding_debtors(self.workspace, date(2026, 6, 30)), ())

    def test_a_paid_order_is_not_a_debtor(self):
        """Both bases have accounted for it, so there is nothing to bring in."""
        self.order_with('100', paid_on=date(2026, 5, 1))
        self.assertEqual(outstanding_debtors(self.workspace, date(2026, 6, 30)), ())

    def test_a_payment_after_the_cutoff_leaves_a_debtor(self):
        """On the change date the money had not arrived, whatever happened later."""
        self.order_with('100', paid_on=date(2026, 8, 1))
        debtors = outstanding_debtors(self.workspace, date(2026, 6, 30))
        self.assertEqual(len(debtors), 1)
        self.assertEqual(debtors[0].gross, Decimal('115.0000'))

    def test_a_part_paid_order_is_a_debtor_for_its_balance(self):
        """Only the part the money has not reached needs bringing in."""
        order = self.order_with('100')
        Payment.objects.create(
            workspace=self.workspace, order=order, paid_on=date(2026, 5, 1),
            amount=Decimal('46.0000'), currency_code='NZD', method='cash',
            operation_key=uuid4(), request_fingerprint='transition-test',
        )
        debtors = outstanding_debtors(self.workspace, date(2026, 6, 30))
        self.assertEqual(debtors[0].gross, Decimal('69.0000'))
        self.assertEqual(debtors[0].tax, Decimal('9.0000'))


class DirectionTests(BasisTransitionTestCase):
    """Which way the adjustment goes follows from the rule each basis uses."""

    def test_payments_to_invoice_is_a_debit_adjustment(self):
        """Supplies already made have to be brought into account in one go."""
        self.order_with('100')
        transition = basis_transition(self.workspace, 'payments', 'invoice', date(2026, 7, 1))
        self.assertEqual(transition.direction, 'debit')
        self.assertEqual(transition.adjustment_tax['NZD'], Decimal('15.0000'))
        self.assertTrue(transition.required)

    def test_invoice_to_payments_is_a_credit_adjustment(self):
        """Output tax already returned on unpaid debtors comes back out."""
        self.order_with('100')
        transition = basis_transition(self.workspace, 'invoice', 'payments', date(2026, 7, 1))
        self.assertEqual(transition.direction, 'credit')
        self.assertEqual(transition.adjustment_tax['NZD'], Decimal('15.0000'))

    def test_hybrid_to_invoice_needs_no_output_adjustment(self):
        """Both use the invoice rule for output tax, so nothing has moved."""
        self.order_with('100')
        transition = basis_transition(self.workspace, 'hybrid', 'invoice', date(2026, 7, 1))
        self.assertEqual(transition.direction, 'none')
        self.assertFalse(transition.required)

    def test_payments_to_hybrid_needs_the_full_adjustment(self):
        """Hybrid moves output tax onto the invoice rule, so the debtors come in."""
        self.order_with('100')
        transition = basis_transition(self.workspace, 'payments', 'hybrid', date(2026, 7, 1))
        self.assertEqual(transition.direction, 'debit')
        self.assertEqual(transition.adjustment_tax['NZD'], Decimal('15.0000'))

    def test_a_change_with_no_debtors_needs_nothing(self):
        """The direction stands, but there is no adjustment to make."""
        self.order_with('100', paid_on=date(2026, 5, 1))
        transition = basis_transition(self.workspace, 'payments', 'invoice', date(2026, 7, 1))
        self.assertEqual(transition.direction, 'debit')
        self.assertFalse(transition.required)

    def test_the_creditors_side_is_reported_unavailable(self):
        """No supplier payment date exists anywhere, so it is stated, not guessed."""
        self.order_with('100')
        transition = basis_transition(self.workspace, 'payments', 'invoice', date(2026, 7, 1))
        self.assertIsNone(transition.creditors_tax)
        self.assertFalse(transition.complete)


class RecordedTransitionTests(BasisTransitionTestCase):
    """Transitions are read off the recorded history, not asked for separately."""

    def test_a_recorded_basis_change_produces_a_transition(self):
        """The arrangement history is the only place a change is stated."""
        self.register(GstRegistration.Basis.PAYMENTS, date(2026, 1, 1))
        self.order_with('100')
        self.register(GstRegistration.Basis.INVOICE, date(2026, 7, 1))
        transitions = basis_transitions(self.workspace)
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0].change_date, date(2026, 7, 1))
        self.assertEqual(transitions[0].direction, 'debit')

    def test_a_frequency_change_is_not_a_basis_change(self):
        """It moves the period boundaries and accounts for nothing differently."""
        self.register(GstRegistration.Basis.PAYMENTS, date(2026, 1, 1))
        self.register(
            GstRegistration.Basis.PAYMENTS, date(2026, 7, 1),
            filing_frequency=GstRegistration.Frequency.MONTHLY,
        )
        self.assertEqual(basis_transitions(self.workspace), [])

    def test_a_change_across_a_deregistration_is_not_a_transition(self):
        """Registering again is a new arrangement, not a change to the old one."""
        self.register(GstRegistration.Basis.PAYMENTS, date(2026, 1, 1))
        record_registration(
            self.workspace, self.user, registered=False, effective_from=date(2026, 7, 1),
        )
        self.register(GstRegistration.Basis.INVOICE, date(2027, 1, 1))
        self.assertEqual(basis_transitions(self.workspace), [])


class BasisTransitionRestTests(BasisTransitionTestCase):
    """The operator has to be able to see the work the change leaves them."""

    URL = '/tax/gst/basis-transitions/'

    def test_authentication_is_required(self):
        """Tax adjustments are not public information."""
        self.assert_authentication_required([self.URL])

    def test_the_route_reports_each_change(self):
        """Change 5 asks for the work to be exposed rather than done silently."""
        self.register(GstRegistration.Basis.PAYMENTS, date(2026, 1, 1))
        self.order_with('100')
        self.register(GstRegistration.Basis.INVOICE, date(2026, 7, 1))
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        entry = response.data[0]
        self.assertEqual(entry['direction'], 'debit')
        self.assertEqual(entry['adjustment_tax']['NZD'], '15.0000')
        self.assertIsNone(entry['creditors_tax'])
        self.assertFalse(entry['complete'])

    def test_garden_mode_is_refused(self):
        """A bookmarked URL must be refused by the server, not only the menu."""
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.save()
        self.assertEqual(self.client.get(self.URL).status_code, 403)
