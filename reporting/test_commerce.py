"""Financial report contracts from recognized commerce source rows."""

# Test method names carry the contract.
# pylint: disable=missing-function-docstring

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db.models import Sum
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


class CommerceReportTestCase(APITestCase):  # pylint: disable=too-many-instance-attributes
    """One fulfilled seedling order, ready to be reported on."""

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

    def _return(self, returned_at, outcome=SalesReturnLine.Outcome.AVAILABLE):
        """Take the fulfilled seedling back on a given instant."""
        sales_return = SalesReturn.objects.create(
            workspace=self.workspace,
            order=self.order,
            returned_at=returned_at,
            reason='Customer changed plans',
            operation_key=uuid4(),
            request_fingerprint=FINGERPRINT,
            created_by=self.user,
        )
        SalesReturnLine.objects.create(
            sales_return=sales_return,
            fulfillment_line=self.fulfillment_line,
            outcome=outcome,
        )
        return sales_return

    def _refund(self, refunded_at, payment=None, sales_return=None, amount='1.10'):
        """Refund part of the paid value on a given instant."""
        refund = Refund.objects.create(
            workspace=self.workspace,
            order=self.order,
            payment=payment or self._payment(),
            sales_return=sales_return,
            refunded_at=refunded_at,
            amount=Decimal(amount),
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
        return refund

    def profitability(self, date_from, date_to):
        """Ask for the P&L over one closed range of workspace-local dates."""
        response = self.client.get('/reports/profitability/', {
            'date_from': date_from, 'date_to': date_to,
        })
        self.assertEqual(response.status_code, 200, response.data)
        return response.data


class CommerceReportTests(CommerceReportTestCase):
    """P&L separates tax, cash, refunds, restored COGS, and incomplete cost."""

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


class ReportingPeriodBoundaryTests(CommerceReportTestCase):
    """A period is a range of workspace-local days, not of UTC days.

    Every boundary here is one where reading the stored instant as UTC puts
    the fact in the wrong month. Auckland is far enough ahead of UTC that a
    September morning is still August in UTC and an August evening is already
    September, so both directions are checked.
    """

    AUGUST = ('2026-08-01', '2026-08-31')
    SEPTEMBER = ('2026-09-01', '2026-09-30')

    def setUp(self):
        super().setUp()
        self.workspace.timezone = 'Pacific/Auckland'
        self.workspace.save(update_fields=['timezone'])

    def move_fulfillment(self, moment):
        """Restate when the seedling was dispatched."""
        Fulfillment.objects.filter(pk=self.fulfillment.pk).update(fulfilled_at=moment)

    def gross_sales(self, period):
        """Return the gross sales the P&L reports for one period."""
        totals = self.profitability(*period)['totals']['currencies']
        return totals[0]['gross_sales'] if totals else None

    def test_a_local_evening_that_is_already_the_next_utc_day_stays_in_its_month(self):
        """23:30 on 31 August in Auckland is 11:30Z on 31 August."""
        self.move_fulfillment(timezone.datetime(2026, 8, 31, 11, 30, tzinfo=timezone.UTC))
        self.assertEqual(self.gross_sales(self.AUGUST), '10.0000')
        self.assertIsNone(self.gross_sales(self.SEPTEMBER))

    def test_a_local_morning_that_is_still_the_previous_utc_day_moves_month(self):
        """00:30 on 1 September in Auckland is 12:30Z on 31 August."""
        self.move_fulfillment(timezone.datetime(2026, 8, 31, 12, 30, tzinfo=timezone.UTC))
        self.assertIsNone(self.gross_sales(self.AUGUST))
        self.assertEqual(self.gross_sales(self.SEPTEMBER), '10.0000')

    def test_the_last_local_instant_of_a_period_is_inside_it(self):
        """A period ends at the close of its final local day, not at noon."""
        self.move_fulfillment(
            timezone.datetime(2026, 8, 31, 11, 59, 59, tzinfo=timezone.UTC),
        )
        self.assertEqual(self.gross_sales(self.AUGUST), '10.0000')
        self.move_fulfillment(timezone.datetime(2026, 8, 31, 12, tzinfo=timezone.UTC))
        self.assertIsNone(self.gross_sales(self.AUGUST))

    def test_a_refund_is_placed_by_its_own_local_day_too(self):
        """The same boundary applies to the money, not only to the goods."""
        self._refund(timezone.datetime(2026, 8, 31, 12, 30, tzinfo=timezone.UTC))
        self.assertEqual(
            self.profitability(*self.AUGUST)['totals']['currencies'][0]['refunds'],
            '0.0000',
        )
        self.assertEqual(
            self.profitability(*self.SEPTEMBER)['totals']['currencies'][0]['refunds'],
            '1.0000',
        )

    def test_a_return_restores_cost_in_its_own_local_day(self):
        """A return crossing the boundary restores COGS on the far side."""
        self._return(timezone.datetime(2026, 8, 31, 12, 30, tzinfo=timezone.UTC))
        august = self.profitability(*self.AUGUST)['totals']['currencies'][0]
        september = self.profitability(*self.SEPTEMBER)['totals']['currencies'][0]
        self.assertEqual(august['plant_cogs'], '2.0000')
        self.assertEqual(september['plant_cogs'], '-2.0000')


class CrossPeriodCommerceTests(CommerceReportTestCase):
    """A correction is reported where it happened, not where the sale was."""

    AUGUST = ('2026-08-01', '2026-08-31')
    SEPTEMBER = ('2026-09-01', '2026-09-30')

    def test_a_later_return_and_refund_leave_the_sale_period_untouched(self):
        """August still reports the sale it made, in full, at its own cost."""
        payment = self._payment()
        sales_return = self._return(
            timezone.datetime(2026, 9, 4, 12, tzinfo=timezone.UTC),
        )
        self._refund(
            timezone.datetime(2026, 9, 5, 12, tzinfo=timezone.UTC),
            payment=payment, sales_return=sales_return,
        )

        august = self.profitability(*self.AUGUST)
        totals = august['totals']['currencies'][0]
        self.assertEqual(totals['gross_sales'], '10.0000')
        self.assertEqual(totals['refunds'], '0.0000')
        self.assertEqual(totals['net_sales'], '9.0000')
        self.assertEqual(totals['plant_cogs'], '2.0000')
        self.assertEqual(totals['gross_profit'], '7.0000')
        self.assertEqual({row['kind'] for row in august['results']}, {'fulfillment'})

    def test_the_correction_period_carries_the_refund_and_the_restored_cost(self):
        """September reports a negative month, which is what happened in it."""
        payment = self._payment()
        sales_return = self._return(
            timezone.datetime(2026, 9, 4, 12, tzinfo=timezone.UTC),
        )
        self._refund(
            timezone.datetime(2026, 9, 5, 12, tzinfo=timezone.UTC),
            payment=payment, sales_return=sales_return,
        )

        september = self.profitability(*self.SEPTEMBER)
        totals = september['totals']['currencies'][0]
        self.assertEqual(totals['gross_sales'], '0.0000')
        self.assertEqual(totals['refunds'], '1.0000')
        self.assertEqual(totals['net_sales'], '-1.0000')
        self.assertEqual(totals['plant_cogs'], '-2.0000')
        self.assertEqual(totals['gross_profit'], '1.0000')
        self.assertEqual(
            {row['kind'] for row in september['results']},
            {'refund', 'cogs_restoration'},
        )

    def test_a_range_covering_both_periods_nets_them_against_each_other(self):
        """Widening the range must not double-count or drop either side."""
        payment = self._payment()
        sales_return = self._return(
            timezone.datetime(2026, 9, 4, 12, tzinfo=timezone.UTC),
        )
        self._refund(
            timezone.datetime(2026, 9, 5, 12, tzinfo=timezone.UTC),
            payment=payment, sales_return=sales_return,
        )

        whole = self.profitability('2026-08-01', '2026-09-30')
        totals = whole['totals']['currencies'][0]
        self.assertEqual(totals['gross_sales'], '10.0000')
        self.assertEqual(totals['refunds'], '1.0000')
        self.assertEqual(totals['net_sales'], '8.0000')
        self.assertEqual(totals['plant_cogs'], '0.0000')
        self.assertEqual(totals['gross_profit'], '8.0000')

    def test_a_discarded_return_restores_no_cost(self):
        """Stock thrown away on return was still sold and still cost money."""
        self._return(
            timezone.datetime(2026, 9, 4, 12, tzinfo=timezone.UTC),
            outcome=SalesReturnLine.Outcome.DISCARDED,
        )
        september = self.profitability(*self.SEPTEMBER)
        self.assertEqual(september['results'], [])


class ProfitabilityReconciliationTests(CommerceReportTestCase):
    """Every reported total is the sum of the rows and ledger behind it."""

    MONEY_FIELDS = (
        'gross_sales', 'discounts', 'refunds', 'net_sales', 'plant_cogs',
        'tray_cogs', 'packaging_cogs', 'other_cogs', 'production_loss',
    )
    RANGE = ('2026-08-01', '2026-09-30')

    def setUp(self):
        super().setUp()
        payment = self._payment()
        self.sales_return = self._return(
            timezone.datetime(2026, 9, 4, 12, tzinfo=timezone.UTC),
        )
        self.refund = self._refund(
            timezone.datetime(2026, 9, 5, 12, tzinfo=timezone.UTC),
            payment=payment, sales_return=self.sales_return,
        )
        self.report = self.profitability(*self.RANGE)
        self.totals = self.report['totals']['currencies'][0]

    def test_every_money_column_equals_a_direct_sum_over_the_drill_down(self):
        """No total is computed by a path the drill-down cannot show."""
        for field in self.MONEY_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(
                    Decimal(self.totals[field]),
                    sum(
                        (Decimal(row[field]) for row in self.report['results']
                         if row['currency_code'] == self.totals['currency_code']),
                        Decimal('0'),
                    ),
                )

    def test_the_published_equations_are_the_ones_the_totals_satisfy(self):
        """The report states three identities; all three have to hold."""
        totals = {field: Decimal(self.totals[field]) for field in self.MONEY_FIELDS}
        direct_cogs = Decimal(self.totals['direct_cogs'])
        self.assertEqual(
            totals['gross_sales'] - totals['discounts'] - totals['refunds'],
            totals['net_sales'],
        )
        self.assertEqual(
            direct_cogs,
            sum(
                (totals[field] for field in (
                    'plant_cogs', 'tray_cogs', 'packaging_cogs', 'other_cogs',
                )),
                Decimal('0'),
            ),
        )
        self.assertEqual(
            Decimal(self.totals['gross_profit']),
            totals['net_sales'] - direct_cogs - totals['production_loss'],
        )

    def test_sales_and_refunds_reconcile_against_their_source_rows(self):
        """The report adds up the ledger rather than restating it."""
        self.assertEqual(
            Decimal(self.totals['gross_sales']),
            FulfillmentLine.objects.filter(
                fulfillment__workspace=self.workspace,
            ).aggregate(total=Sum('gross_ex_tax'))['total'],
        )
        self.assertEqual(
            Decimal(self.totals['discounts']),
            FulfillmentLine.objects.filter(
                fulfillment__workspace=self.workspace,
            ).aggregate(total=Sum('discount_ex_tax'))['total'],
        )
        self.assertEqual(
            Decimal(self.totals['refunds']),
            RefundLine.objects.filter(
                refund__workspace=self.workspace,
            ).aggregate(total=Sum('subtotal_ex_tax'))['total'],
        )

    def test_a_reversed_fulfillment_leaves_the_report_and_takes_its_row_with_it(self):
        """A reversed document is not a fact, so no total may still hold it."""
        reversal = Fulfillment.objects.create(
            workspace=self.workspace,
            order=self.order,
            fulfillment_number='FUL-PNL-R',
            fulfilled_at=self.fulfillment.fulfilled_at,
            reversal_of=self.fulfillment,
            operation_key=uuid4(),
            request_fingerprint=FINGERPRINT,
            created_by=self.user,
        )
        self.assertIsNotNone(reversal.pk)
        report = self.profitability(*self.RANGE)
        self.assertEqual(report['results'], [])
        self.assertEqual(report['totals']['currencies'], [])

    def test_a_period_with_no_commerce_reports_no_currency_at_all(self):
        """A nil period says nothing rather than inventing a zero currency."""
        report = self.profitability('2026-10-01', '2026-10-31')
        self.assertEqual(report['results'], [])
        self.assertEqual(report['totals']['currencies'], [])
