"""Verification 2: the registration threshold across the rolling boundary.

The whole point of a rolling twelve-month test is that it un-fires. A sale
that pushes turnover over NZ$60,000 warns today and stops warning once it
falls out of the window, and a test that only ever checks the firing half
would pass against a system that never stopped warning at all.
"""

# pylint: disable=duplicate-code

from datetime import date
from decimal import Decimal

from inventory.models import InventoryItem
from inventory.units import UnitCode
from sales.models import SalesOrder, SalesOrderLine
from sales.services import create_order
from tests.api import RESTContractTestCase
from tests.factories import make_inventory_item
from workspaces.models import Workspace, get_current_workspace

from .models import GstRegistration
from .services import record_registration
from .turnover import (
    MONTHLY_COMPULSORY_FLOOR,
    PAYMENTS_BASIS_CEILING,
    SIX_MONTHLY_CEILING,
    months_before,
    registration_warnings,
    rolling_turnover,
    turnover_projection,
)


def codes(warnings):
    """Return just the finding codes, which is what the assertions are about."""
    return {warning['code'] for warning in warnings}


class MonthArithmeticTests(RESTContractTestCase):
    """A day-count subtraction gets the boundary wrong exactly where it matters."""

    def test_twelve_months_back_lands_on_the_same_day(self):
        """The window has to be a calendar year, not 365 days."""
        self.assertEqual(months_before(date(2027, 3, 15), 12), date(2026, 3, 15))

    def test_a_leap_day_clamps_to_the_end_of_february(self):
        """29 February has no counterpart in a common year."""
        self.assertEqual(months_before(date(2028, 2, 29), 12), date(2027, 2, 28))

    def test_three_months_back_crosses_a_year_end(self):
        """The projection window must not wrap into the wrong year."""
        self.assertEqual(months_before(date(2027, 2, 10), 3), date(2026, 11, 10))


class TurnoverTestCase(RESTContractTestCase):
    """A Nursery that can record sales at chosen dates and rates."""

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

    def sell(self, paid_on, ex_tax, tax_rate='15', treatment=None):
        """Record a supply of a given ex-GST value, paid on a date.

        A payment is used rather than a fulfillment because it needs no plants,
        no allocations and no stock, and the threshold is measured on supplies
        made — which the invoice basis recognises at the earlier of the two.
        """
        from sales.models import Payment  # pylint: disable=import-outside-toplevel
        from uuid import uuid4  # pylint: disable=import-outside-toplevel

        order = create_order(self.workspace, self.user, status=SalesOrder.Status.DRAFT)
        values = {
            'order': order,
            'line_type': SalesOrderLine.LineType.UNIT,
            'item': self.item,
            'description': 'Trays',
            'quantity': 1,
            'unit_price': Decimal(ex_tax),
            'tax_rate': Decimal(tax_rate),
            'discount_type': SalesOrderLine.DiscountType.NONE,
        }
        if treatment is not None:
            values['tax_treatment'] = treatment
        line = SalesOrderLine.objects.create(**values)
        SalesOrder.objects.filter(pk=order.pk).update(status=SalesOrder.Status.CONFIRMED)
        order.refresh_from_db()
        Payment.objects.create(
            workspace=self.workspace, order=order, paid_on=paid_on,
            amount=line.total_incl_tax, currency_code='NZD', method='cash',
            operation_key=uuid4(), request_fingerprint='threshold-test',
        )
        return order


class RollingThresholdTests(TurnoverTestCase):
    """The window is what decides, and it moves."""

    def test_turnover_is_measured_excluding_gst(self):
        """The threshold is a value of supplies, not of money received."""
        self.sell(date(2026, 6, 1), '1000')
        rolling = rolling_turnover(self.workspace, date(2026, 7, 1))
        self.assertEqual(rolling['taxable']['NZD'], Decimal('1000.0000'))

    def test_turnover_just_over_the_threshold_warns(self):
        """Registration becomes compulsory at NZ$60,000, not above it."""
        self.sell(date(2026, 6, 1), '60000')
        warnings = registration_warnings(self.workspace, date(2026, 7, 1))
        self.assertIn('threshold_exceeded', codes(warnings))

    def test_turnover_just_under_the_threshold_does_not_warn(self):
        """A warning that fires early is one an operator learns to ignore."""
        self.sell(date(2026, 6, 1), '59999')
        warnings = registration_warnings(self.workspace, date(2026, 7, 1))
        self.assertNotIn('threshold_exceeded', codes(warnings))

    def test_the_warning_clears_once_the_sale_falls_out_of_the_window(self):
        """This is the half a firing-only test would never catch."""
        self.sell(date(2026, 6, 1), '60000')
        inside = registration_warnings(self.workspace, date(2027, 6, 1))
        outside = registration_warnings(self.workspace, date(2027, 6, 2))
        self.assertIn('threshold_exceeded', codes(inside))
        self.assertNotIn('threshold_exceeded', codes(outside))

    def test_supplies_accumulate_across_the_window(self):
        """The test is on the period's total, not on any single sale."""
        self.sell(date(2026, 3, 1), '30000')
        self.sell(date(2026, 9, 1), '30000')
        self.assertIn(
            'threshold_exceeded',
            codes(registration_warnings(self.workspace, date(2026, 10, 1))),
        )

    def test_a_registered_workspace_is_never_told_to_register(self):
        """It already did; repeating it would bury the findings that matter."""
        record_registration(
            self.workspace, self.user, registered=True,
            effective_from=date(2026, 1, 1), gst_number='123456785',
            basis=GstRegistration.Basis.INVOICE,
            filing_frequency=GstRegistration.Frequency.TWO_MONTHLY,
            period_anchor_month=3,
        )
        self.sell(date(2026, 6, 1), '60000')
        warnings = registration_warnings(self.workspace, date(2026, 7, 1))
        self.assertNotIn('threshold_exceeded', codes(warnings))


class TaxCodeTests(TurnoverTestCase):
    """What counts as taxable turnover is the classification, not the rate."""

    def test_zero_rated_supplies_count(self):
        """A zero-rated export is taxable at zero, so it is still turnover."""
        self.sell(date(2026, 6, 1), '60000', tax_rate='0', treatment='zero_rated')
        self.assertIn(
            'threshold_exceeded',
            codes(registration_warnings(self.workspace, date(2026, 7, 1))),
        )

    def test_exempt_supplies_do_not_count(self):
        """Counting them would force registration on an entity that needs none."""
        self.sell(date(2026, 6, 1), '60000', tax_rate='0', treatment='exempt')
        warnings = registration_warnings(self.workspace, date(2026, 7, 1))
        self.assertNotIn('threshold_exceeded', codes(warnings))

    def test_unclassified_supplies_are_reported_rather_than_assumed(self):
        """Assuming either way would move the answer across a line nobody chose."""
        self.sell(date(2026, 6, 1), '60000', tax_rate='0')
        warnings = registration_warnings(self.workspace, date(2026, 7, 1))
        self.assertIn('unclassified_turnover', codes(warnings))
        self.assertNotIn('threshold_exceeded', codes(warnings))
        rolling = rolling_turnover(self.workspace, date(2026, 7, 1))
        self.assertEqual(rolling['unclassified']['NZD'], Decimal('60000.0000'))
        self.assertEqual(rolling['taxable'].get('NZD', Decimal('0')), Decimal('0.0000'))


class ProjectionTests(TurnoverTestCase):
    """A projection is a prompt to think, and says so in its own payload."""

    def test_recent_trading_is_annualised(self):
        """20,000 in a quarter annualises to 80,000, which is over the line."""
        self.sell(date(2026, 6, 1), '20000')
        projection = turnover_projection(self.workspace, date(2026, 7, 1))
        self.assertEqual(projection['taxable']['NZD'], Decimal('80000.0000'))
        self.assertEqual(projection['method'], 'last_3_months_annualised')

    def test_a_projection_over_the_threshold_warns(self):
        """The legal test is expectation, so a trend is worth surfacing."""
        self.sell(date(2026, 6, 1), '20000')
        warnings = registration_warnings(self.workspace, date(2026, 7, 1))
        self.assertIn('threshold_projected', codes(warnings))

    def test_the_measured_warning_replaces_the_projected_one(self):
        """Two warnings about the same threshold is noise, not emphasis."""
        self.sell(date(2026, 6, 1), '60000')
        warnings = codes(registration_warnings(self.workspace, date(2026, 7, 1)))
        self.assertIn('threshold_exceeded', warnings)
        self.assertNotIn('threshold_projected', warnings)


class EligibilityTests(TurnoverTestCase):
    """A basis or frequency turnover has outgrown is a warning, never a refusal."""

    def register(self, **overrides):
        """Record an arrangement to test the eligibility of."""
        values = {
            'registered': True,
            'effective_from': date(2026, 1, 1),
            'gst_number': '123456785',
            'basis': GstRegistration.Basis.PAYMENTS,
            'filing_frequency': GstRegistration.Frequency.TWO_MONTHLY,
            'period_anchor_month': 3,
        }
        values.update(overrides)
        return record_registration(self.workspace, self.user, **values)

    def test_the_payments_basis_ceiling_is_reported(self):
        """Above NZ$2,000,000 the payments basis is no longer available."""
        self.register()
        self.sell(date(2026, 6, 1), str(PAYMENTS_BASIS_CEILING + 1))
        warnings = registration_warnings(self.workspace, date(2026, 7, 1))
        self.assertIn('payments_basis_ineligible', codes(warnings))

    def test_the_payments_basis_at_the_ceiling_is_not_reported(self):
        """The ceiling is a limit, not a threshold; equal to it is still inside."""
        self.register()
        self.sell(date(2026, 6, 1), str(PAYMENTS_BASIS_CEILING))
        warnings = registration_warnings(self.workspace, date(2026, 7, 1))
        self.assertNotIn('payments_basis_ineligible', codes(warnings))

    def test_the_six_monthly_ceiling_is_reported(self):
        """Above NZ$500,000 six-monthly filing is no longer available."""
        self.register(filing_frequency=GstRegistration.Frequency.SIX_MONTHLY)
        self.sell(date(2026, 6, 1), str(SIX_MONTHLY_CEILING + 1))
        self.assertIn(
            'six_monthly_ineligible',
            codes(registration_warnings(self.workspace, date(2026, 7, 1))),
        )

    def test_compulsory_monthly_filing_is_reported(self):
        """Above NZ$24,000,000 nothing but monthly filing is permitted."""
        self.register(basis=GstRegistration.Basis.INVOICE)
        self.sell(date(2026, 6, 1), str(MONTHLY_COMPULSORY_FLOOR + 1))
        self.assertIn(
            'monthly_filing_required',
            codes(registration_warnings(self.workspace, date(2026, 7, 1))),
        )

    def test_an_unusual_six_monthly_cycle_is_reported(self):
        """Inland Revenue offers three cycles; anything else is worth querying."""
        self.register(
            filing_frequency=GstRegistration.Frequency.SIX_MONTHLY,
            period_anchor_month=1,
        )
        self.assertIn(
            'six_monthly_cycle_unusual',
            codes(registration_warnings(self.workspace, date(2026, 7, 1))),
        )

    def test_an_ineligible_arrangement_is_still_recorded(self):
        """Refusing it would leave a workspace unable to produce its own returns."""
        self.sell(date(2026, 6, 1), str(PAYMENTS_BASIS_CEILING + 1))
        registration = self.register()
        self.assertEqual(registration.basis, GstRegistration.Basis.PAYMENTS)


class ConfigurationWarningTests(TurnoverTestCase):
    """A workspace whose settings do not describe a New Zealand entity."""

    def test_a_non_nzd_workspace_is_reported(self):
        """A GST return is filed in New Zealand dollars, whatever is configured."""
        self.workspace.currency_code = 'AUD'
        self.workspace.save()
        self.assertIn(
            'workspace_currency_not_nzd',
            codes(registration_warnings(self.workspace, date(2026, 7, 1))),
        )


class GstStatusWarningTests(TurnoverTestCase):
    """The settings screen reads its banner from this payload."""

    def test_the_status_route_reports_turnover_and_warnings(self):
        """The operator has to see the threshold before it is a problem."""
        self.sell(date(2026, 6, 1), '60000')
        response = self.client.get('/tax/gst/status/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['registration_threshold'], '60000.0000')
        self.assertIn('rolling_turnover', response.data)
        self.assertIn('warnings', response.data)

    def test_recording_an_arrangement_answers_with_its_consequences(self):
        """This is the moment the choice was made, so this is where to say so."""
        self.sell(date(2026, 6, 1), str(PAYMENTS_BASIS_CEILING + 1))
        response = self.client.post('/tax/gst/registrations/', {
            'registered': True,
            'effective_from': '2026-01-01',
            'gst_number': '123456785',
            'basis': 'payments',
            'filing_frequency': 'two_monthly',
            'period_anchor_month': 3,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn('payments_basis_ineligible', codes(response.data['warnings']))
