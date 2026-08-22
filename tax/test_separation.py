"""Task 117 change 6: GST and profitability recognition stay visibly separate.

They answer different questions. Profitability asks what a period earned and
recognises on fulfillment; GST asks what a period owes and recognises on the
basis in force at each time of supply. For one order paid in one period and
delivered in the next, the two disagree — and the disagreement is the contract,
not a defect. Asserting both halves in one test is what stops somebody
"fixing" one to match the other.
"""

# pylint: disable=duplicate-code

from datetime import date, datetime, timezone as utc_timezone
from decimal import Decimal
from uuid import uuid4

from reporting.commerce import profitability_report
from reporting.gst import gst_period_report
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


class RecognitionSeparationTests(RESTContractTestCase):
    """One order, paid in March and delivered in April, seen from both sides."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.currency_code = 'NZD'
        self.workspace.timezone = 'Pacific/Auckland'
        self.workspace.default_tax_rate = Decimal('15')
        self.workspace.sales_prices_include_tax = False
        self.workspace.save()
        record_registration(
            self.workspace, self.user, registered=True,
            effective_from=date(2026, 1, 1), gst_number='123456785',
            basis=GstRegistration.Basis.PAYMENTS,
            filing_frequency=GstRegistration.Frequency.MONTHLY,
            period_anchor_month=1,
        )
        self.order = self._paid_in_march_delivered_in_april()

    def _paid_in_march_delivered_in_april(self):
        """Build the one order both reports are asked about."""
        order = create_order(self.workspace, self.user, status=SalesOrder.Status.DRAFT)
        plant = make_specific_plant(workspace=self.workspace)
        line = SalesOrderLine.objects.create(
            order=order,
            line_type=SalesOrderLine.LineType.SEEDLING,
            variety=plant.batch.variety,
            description='Seedlings',
            quantity=1,
            unit_price=Decimal('100'),
            tax_rate=Decimal('15'),
            discount_type=SalesOrderLine.DiscountType.NONE,
        )
        SalesOrder.objects.filter(pk=order.pk).update(status=SalesOrder.Status.CONFIRMED)
        order.refresh_from_db()
        Payment.objects.create(
            workspace=self.workspace, order=order, paid_on=date(2026, 3, 10),
            amount=line.total_incl_tax, currency_code='NZD', method='cash',
            operation_key=uuid4(), request_fingerprint='separation-test',
        )
        allocation = SalesOrderAllocation.objects.create(line=line, plant=plant)
        fulfillment = Fulfillment.objects.create(
            workspace=self.workspace, order=order, fulfillment_number='FUL-000001',
            fulfilled_at=datetime(2026, 4, 20, 2, 0, tzinfo=utc_timezone.utc),
            operation_key=uuid4(), request_fingerprint='separation-test',
        )
        FulfillmentLine.objects.create(
            fulfillment=fulfillment, allocation=allocation, commercial_position=1,
            gross_ex_tax=line.gross_ex_tax, discount_ex_tax=line.discount_ex_tax,
            subtotal_ex_tax=line.subtotal_ex_tax, tax_total=line.tax_total,
            total_incl_tax=line.total_incl_tax, tax_treatment=line.tax_treatment,
            currency_code='NZD',
        )
        return order

    def _gst_output_tax(self, month):
        """Return the output tax the GST report puts in one calendar month."""
        report = gst_period_report(self.workspace, {
            'date_from': f'2026-{month:02d}-01', 'date_to': f'2026-{month:02d}-28',
        })
        return sum(Decimal(row['output_tax']) for row in report.rows)

    def _profit_net_sales(self, month):
        """Return the net sales the profitability report puts in one calendar month."""
        report = profitability_report(self.workspace, {
            'date_from': f'2026-{month:02d}-01', 'date_to': f'2026-{month:02d}-28',
        })
        return sum(
            (Decimal(summary['net_sales']) for summary in report.totals['currencies']),
            Decimal('0'),
        )

    def test_gst_recognises_the_payment_month(self):
        """On the payments basis, March is when the money arrived."""
        self.assertEqual(self._gst_output_tax(3), Decimal('15.0000'))
        self.assertEqual(self._gst_output_tax(4), Decimal('0.0000'))

    def test_profitability_recognises_the_fulfillment_month(self):
        """Revenue follows the delivery, a whole period later. Both are right."""
        self.assertEqual(self._profit_net_sales(3), Decimal('0'))
        self.assertEqual(self._profit_net_sales(4), Decimal('100.0000'))

    def test_the_two_reports_disagree_on_purpose(self):
        """Asserted together so neither can be quietly changed to match the other."""
        self.assertGreater(self._gst_output_tax(3), Decimal('0'))
        self.assertEqual(self._profit_net_sales(3), Decimal('0'))
        self.assertEqual(self._gst_output_tax(4), Decimal('0'))
        self.assertGreater(self._profit_net_sales(4), Decimal('0'))

    def test_profitability_reports_ex_tax_amounts_and_carries_no_gst_column(self):
        """Keeping GST out of the margin is what makes the separation legible."""
        report = profitability_report(self.workspace, {
            'date_from': '2026-04-01', 'date_to': '2026-04-30',
        })
        self.assertNotIn('output_tax', report.columns)
        self.assertNotIn('tax', report.columns)
        self.assertEqual(self._profit_net_sales(4), Decimal('100.0000'))

    def test_the_gst_report_says_the_two_differ(self):
        """A reader comparing them against a filed return has to be told why."""
        report = gst_period_report(self.workspace, {
            'date_from': '2026-01-01', 'date_to': '2026-12-31',
        })
        note = report.reconciliation['recognition_note']
        self.assertIn('basis in force', note)
        self.assertIn('fulfillment dates', note)
