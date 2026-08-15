"""Financial report contracts from recognized commerce source rows."""

# Test method names carry the contract.
# pylint: disable=missing-function-docstring

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from sales.models import (
    Customer,
    Fulfillment,
    FulfillmentLine,
    Payment,
    Refund,
    RefundLine,
    SalesOrder,
    SalesOrderAllocation,
    SalesOrderLine,
    SalesReturn,
    SalesReturnLine,
)
from tests.factories import make_specific_plant
from workspaces.models import get_current_workspace


FINGERPRINT = '0' * 64


class CommerceReportTests(APITestCase):  # pylint: disable=too-many-instance-attributes
    """P&L separates tax, cash, refunds, restored COGS, and incomplete cost."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = self.workspace.Mode.NURSERY
        self.workspace.timezone = 'UTC'
        self.workspace.save(update_fields=['mode', 'timezone'])
        self.user = get_user_model().objects.create_user(username='finance-reporter')
        self.client.force_authenticate(self.user)
        self.customer = Customer.objects.create(
            workspace=self.workspace, name='Nursery customer',
        )
        self.plant = make_specific_plant(workspace=self.workspace)
        self.order = SalesOrder.objects.create(
            workspace=self.workspace,
            order_number='SO-PNL',
            status=SalesOrder.Status.DRAFT,
            order_date=date(2026, 8, 1),
            requested_date=date(2026, 8, 10),
            customer=self.customer,
            currency_code='USD',
        )
        self.order_line = SalesOrderLine.objects.create(
            order=self.order,
            line_type=SalesOrderLine.LineType.SEEDLING,
            variety=self.plant.batch.variety,
            description='Seedling',
            quantity=1,
            unit_price=Decimal('10'),
            tax_rate=Decimal('10'),
            discount_type=SalesOrderLine.DiscountType.FIXED,
            discount_value=Decimal('1'),
        )
        SalesOrder.objects.filter(pk=self.order.pk).update(
            status=SalesOrder.Status.FULFILLED,
        )
        self.order.refresh_from_db()
        self.allocation = SalesOrderAllocation.objects.create(
            line=self.order_line,
            plant=self.plant,
            status=SalesOrderAllocation.Status.FULFILLED,
            created_by=self.user,
        )
        self.fulfillment = Fulfillment.objects.create(
            workspace=self.workspace,
            order=self.order,
            fulfillment_number='FUL-PNL',
            fulfilled_at=timezone.datetime(2026, 8, 5, 12, tzinfo=timezone.UTC),
            operation_key=uuid4(),
            request_fingerprint=FINGERPRINT,
            created_by=self.user,
        )
        self.fulfillment_line = FulfillmentLine.objects.create(
            fulfillment=self.fulfillment,
            allocation=self.allocation,
            commercial_position=1,
            gross_ex_tax=Decimal('10'),
            discount_ex_tax=Decimal('1'),
            subtotal_ex_tax=Decimal('9'),
            tax_total=Decimal('1'),
            total_incl_tax=Decimal('10'),
            cogs_amount=Decimal('2'),
            cogs_provisional=False,
            currency_code='USD',
        )

    def _payment(self):
        return Payment.objects.create(
            workspace=self.workspace,
            order=self.order,
            paid_on=date(2026, 8, 6),
            amount=Decimal('10'),
            currency_code='USD',
            method=Payment.Method.CARD,
            operation_key=uuid4(),
            request_fingerprint=FINGERPRINT,
            created_by=self.user,
        )

    def test_profitability_excludes_tax_and_payments_from_revenue(self):
        self._payment()
        response = self.client.get('/reports/profitability/', {
            'date_from': '2026-08-01', 'date_to': '2026-08-31',
        })
        self.assertEqual(response.status_code, 200, response.data)
        totals = response.data['totals']['currencies'][0]
        self.assertEqual(totals['gross_sales'], '10.0000')
        self.assertEqual(totals['discounts'], '1.0000')
        self.assertEqual(totals['net_sales'], '9.0000')
        self.assertEqual(totals['plant_cogs'], '2.0000')
        self.assertEqual(totals['gross_profit'], '7.0000')
        self.assertNotIn('tax_total', totals)

    def test_refund_and_available_return_use_their_own_dates(self):
        payment = self._payment()
        sales_return = SalesReturn.objects.create(
            workspace=self.workspace,
            order=self.order,
            returned_at=timezone.datetime(2026, 8, 20, 12, tzinfo=timezone.UTC),
            reason='Customer changed plans',
            operation_key=uuid4(),
            request_fingerprint=FINGERPRINT,
            created_by=self.user,
        )
        SalesReturnLine.objects.create(
            sales_return=sales_return,
            fulfillment_line=self.fulfillment_line,
            outcome=SalesReturnLine.Outcome.AVAILABLE,
        )
        refund = Refund.objects.create(
            workspace=self.workspace,
            order=self.order,
            payment=payment,
            sales_return=sales_return,
            refunded_at=timezone.datetime(2026, 8, 21, 12, tzinfo=timezone.UTC),
            amount=Decimal('1.10'),
            currency_code='USD',
            reason='Partial refund',
            operation_key=uuid4(),
            request_fingerprint=FINGERPRINT,
            created_by=self.user,
        )
        RefundLine.objects.create(
            refund=refund,
            fulfillment_line=self.fulfillment_line,
            gross_ex_tax=Decimal('1.10'),
            discount_ex_tax=Decimal('0.10'),
            subtotal_ex_tax=Decimal('1'),
            tax_total=Decimal('0.10'),
            total_incl_tax=Decimal('1.10'),
        )
        response = self.client.get('/reports/profitability/', {
            'date_from': '2026-08-01', 'date_to': '2026-08-31',
        })
        totals = response.data['totals']['currencies'][0]
        self.assertEqual(totals['refunds'], '1.0000')
        self.assertEqual(totals['net_sales'], '8.0000')
        self.assertEqual(totals['plant_cogs'], '0.0000')
        kinds = {row['kind'] for row in response.data['results']}
        self.assertEqual(kinds, {'fulfillment', 'refund', 'cogs_restoration'})

    def test_unknown_cogs_prevents_finalized_margin(self):
        self.fulfillment_line.cogs_amount = None
        self.fulfillment_line.save(update_fields=['cogs_amount'])
        response = self.client.get('/reports/profitability/', {
            'date_from': '2026-08-01', 'date_to': '2026-08-31',
        })
        self.assertFalse(response.data['totals']['finalized_margin_available'])
        self.assertIsNone(response.data['totals']['currencies'][0]['gross_profit'])
        self.assertEqual(response.data['data_quality'][0]['code'], 'unvalued_cost')

    def test_order_report_and_dashboard_keep_cash_separate(self):
        self._payment()
        orders = self.client.get('/reports/orders/', {'order': 'SO-PNL'})
        self.assertEqual(orders.status_code, 200, orders.data)
        self.assertEqual(orders.data['results'][0]['paid_total'], '10.0000')
        self.assertEqual(orders.data['results'][0]['outstanding_total'], '0.0000')
        dashboard = self.client.get('/reports/dashboard/', {
            'date_from': '2026-08-01', 'date_to': '2026-08-31',
        })
        self.assertEqual(dashboard.status_code, 200, dashboard.data)
        self.assertEqual(
            dashboard.data['results'][0]['recent_fulfillments'][0]['fulfillment_number'],
            'FUL-PNL',
        )
