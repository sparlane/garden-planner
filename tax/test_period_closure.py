"""A filed period keeps the figures it was filed on, and says when they drift.

Every GST figure in this application is derived, which is what makes a change
of basis harmless — but it also means re-reading a period after a late
correction quietly restates it, and a period already reported to Inland Revenue
is not something to restate quietly. Closing a period snapshots what was filed
so the difference becomes visible.
"""

# pylint: disable=duplicate-code

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError

from inventory.models import InventoryItem
from inventory.units import UnitCode
from sales.models import Payment, SalesOrder, SalesOrderLine
from sales.services import create_order
from tests.api import RESTContractTestCase
from tests.factories import make_inventory_item
from workspaces.models import Workspace, get_current_workspace

from .models import GstPeriodClosure, GstRegistration
from .periods import taxable_period_for
from .services import close_period, record_registration


URL = '/tax/gst/period-closures/'
PERIODS_URL = '/reports/gst-periods/'


class PeriodClosureTestCase(RESTContractTestCase):
    """A registered Nursery with one filed period to reason about."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.currency_code = 'NZD'
        self.workspace.timezone = 'Pacific/Auckland'
        self.workspace.default_tax_rate = Decimal('15')
        self.workspace.sales_prices_include_tax = False
        self.workspace.save()
        self.item = make_inventory_item(
            workspace=self.workspace,
            category=InventoryItem.Category.TRAY,
            tracking_mode=InventoryItem.TrackingMode.SERIALIZED,
            base_unit=UnitCode.EACH,
        )
        self.registration = record_registration(
            self.workspace, self.user, registered=True,
            effective_from=date(2026, 1, 1), gst_number='123456785',
            basis=GstRegistration.Basis.PAYMENTS,
            filing_frequency=GstRegistration.Frequency.TWO_MONTHLY,
            period_anchor_month=4,
        )

    def sell(self, paid_on, ex_tax='100'):
        """Record a paid supply."""
        order = create_order(self.workspace, self.user, status=SalesOrder.Status.DRAFT)
        line = SalesOrderLine.objects.create(
            order=order,
            line_type=SalesOrderLine.LineType.TRAY,
            tray_item=self.item,
            description='Trays',
            quantity=1,
            unit_price=Decimal(ex_tax),
            tax_rate=Decimal('15'),
            discount_type=SalesOrderLine.DiscountType.NONE,
        )
        SalesOrder.objects.filter(pk=order.pk).update(status=SalesOrder.Status.CONFIRMED)
        Payment.objects.create(
            workspace=self.workspace, order=order, paid_on=paid_on,
            amount=line.total_incl_tax, currency_code='NZD', method='cash',
            operation_key=uuid4(), request_fingerprint='closure-test',
        )
        return order

    def march_period(self):
        """Return the taxable period a March supply falls in."""
        return taxable_period_for(self.workspace, date(2026, 3, 10))

    def file_march(self, net_gst='15.0000'):
        """Record the March period as filed on a given net figure."""
        return close_period(
            self.workspace, self.user, self.march_period(),
            {'NZD': {'net_gst': net_gst}}, notes='Filed on time',
        )

    def periods(self):
        """Fetch the period report over the registered year."""
        response = self.client.get(
            PERIODS_URL, {'date_from': '2026-01-01', 'date_to': '2026-12-31'},
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def march_row(self):
        """Return the reported row for the March period."""
        label = self.march_period().label
        rows = [row for row in self.periods()['results'] if row['period_label'] == label]
        self.assertEqual(len(rows), 1, rows)
        return rows[0]


class ClosePeriodTests(PeriodClosureTestCase):
    """Closing records what was filed, and cannot be edited afterwards."""

    def test_a_closure_records_the_arrangement_it_was_filed_under(self):
        """A filed figure that cannot say which basis produced it cannot be checked."""
        self.sell(date(2026, 3, 10))
        closure = self.file_march()
        self.assertEqual(closure.basis, GstRegistration.Basis.PAYMENTS)
        self.assertEqual(closure.filing_frequency, GstRegistration.Frequency.TWO_MONTHLY)
        self.assertEqual(closure.registration, self.registration)
        self.assertEqual(closure.closed_by, self.user)

    def test_a_closure_is_immutable(self):
        """Editable filed figures would be no reconciliation anchor at all."""
        closure = self.file_march()
        closure.filed_totals = {'NZD': {'net_gst': '0.0000'}}
        with self.assertRaises(ValidationError):
            closure.save()
        with self.assertRaises(ValidationError):
            closure.delete()

    def test_one_period_cannot_be_filed_twice(self):
        """Two filed figures for one period leave no single answer."""
        self.file_march()
        with self.assertRaises(ValidationError):
            self.file_march()
        self.assertEqual(GstPeriodClosure.objects.count(), 1)

    def test_the_label_matches_the_report(self):
        """The label is how a filed period is matched to a reported one."""
        closure = self.file_march()
        self.assertEqual(closure.label, self.march_period().label)


class DriftTests(PeriodClosureTestCase):
    """A late correction to a filed period has to be visible, not silent."""

    def test_an_unfiled_period_reports_no_drift(self):
        """Drift only means something against a figure somebody filed."""
        self.sell(date(2026, 3, 10))
        row = self.march_row()
        self.assertFalse(row['filed'])
        self.assertIsNone(row['filed_total_drift'])

    def test_a_filed_period_that_still_agrees_reports_no_drift(self):
        """Zero has to be reachable, or a non-zero would mean nothing."""
        self.sell(date(2026, 3, 10))
        self.file_march('15.0000')
        row = self.march_row()
        self.assertTrue(row['filed'])
        self.assertEqual(row['filed_total_drift'], '0.0000')

    def test_a_later_supply_in_a_filed_period_shows_as_drift(self):
        """This is the whole reason the filed figures are stored at all."""
        self.sell(date(2026, 3, 10))
        self.file_march('15.0000')
        self.sell(date(2026, 3, 20))
        row = self.march_row()
        self.assertEqual(row['filed_total_drift'], '15.0000')

    def test_drift_is_reported_as_a_finding(self):
        """A number in a column nobody reads is not a warning."""
        self.sell(date(2026, 3, 10))
        self.file_march('15.0000')
        self.sell(date(2026, 3, 20))
        codes = {finding['code'] for finding in self.periods()['data_quality']}
        self.assertIn('filed_total_drift', codes)

    def test_an_agreeing_period_raises_no_finding(self):
        """Otherwise every filed period would carry a permanent warning."""
        self.sell(date(2026, 3, 10))
        self.file_march('15.0000')
        codes = {finding['code'] for finding in self.periods()['data_quality']}
        self.assertNotIn('filed_total_drift', codes)


class PeriodClosureRestTests(PeriodClosureTestCase):
    """The screen that files a period reads and writes through this route."""

    def test_authentication_is_required(self):
        """A filed GST figure is not public information."""
        self.assert_authentication_required([URL])

    def test_the_list_is_unpaginated(self):
        """This project serves bare lists; the frontend relies on it."""
        self.file_march()
        self.assert_list_contract([URL])

    def test_a_period_is_filed_and_read_back(self):
        """The create path is the only way a period becomes filed."""
        period = self.march_period()
        self.assert_create_retrieve(URL, {
            'period_start': period.start.isoformat(),
            'period_end': period.end.isoformat(),
            'registration': self.registration.pk,
            'basis': 'payments',
            'filing_frequency': 'two_monthly',
            'filed_totals': {'NZD': {'net_gst': '15.0000'}},
        }, expected_fields={
            'period_start': period.start.isoformat(),
            'period_end': period.end.isoformat(),
            'basis': 'payments',
            'filed_totals': {'NZD': {'net_gst': '15.0000'}},
        })

    def test_a_filed_period_cannot_be_edited_over_http(self):
        """An editable filed figure is not a record of what was filed."""
        closure = self.file_march()
        detail = f'{URL}{closure.pk}/'
        for method in (self.client.patch, self.client.put, self.client.delete):
            with self.subTest(method=method.__name__):
                response = method(detail, {}, format='json')
                self.assertEqual(response.status_code, 405)

    def test_garden_mode_is_refused(self):
        """A bookmarked URL must be refused by the server, not only the menu."""
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.save()
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_a_duplicate_period_is_reported_rather_than_crashing(self):
        """A double-submitted form has to produce an error, not a 500."""
        self.file_march()
        period = self.march_period()
        response = self.client.post(URL, {
            'period_start': period.start.isoformat(),
            'period_end': period.end.isoformat(),
            'registration': self.registration.pk,
            'basis': 'payments',
            'filing_frequency': 'two_monthly',
            'filed_totals': {'NZD': {'net_gst': '15.0000'}},
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)
