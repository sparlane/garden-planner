"""Verification 3: every GST period total reconciles to its own source rows.

The claim this feature makes is that a period figure is the sum of immutable
commerce records and nothing else. That is only worth making if it is checked,
so every box in a period row is asserted against a direct sum over the
drill-down rows the report itself publishes.

The separation tests are task 117 change 6: GST recognition and profitability
recognition use different dates for the same order, on purpose, and the pair of
assertions in one test is the contract rather than a coincidence.
"""

# pylint: disable=duplicate-code

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from billing.documents import issue_supply_document
from billing.test_fixtures import DocumentScenarioMixin
from inventory.ledger import post_receipt
from inventory.models import InventoryItem, StockReceipt, StockReceiptLine
from inventory.units import UnitCode
from locations.models import Location
from sales.models import Payment, SalesOrder, SalesOrderLine
from sales.services import create_order
from tax.models import GstRegistration
from tax.services import record_registration
from tests.api import RESTContractTestCase
from tests.factories import make_inventory_item, make_supplier
from workspaces.models import Workspace, get_current_workspace


PERIODS_URL = '/reports/gst-periods/'
ENTRIES_URL = '/reports/gst-entries/'

MONEY_COLUMNS = (
    'taxable_supplies_incl_tax', 'zero_rated_supplies', 'exempt_supplies',
    'unclassified_supplies', 'supply_credits_incl_tax', 'output_tax',
    'purchases_incl_tax', 'input_tax', 'non_recoverable_tax', 'net_gst',
)


class GstReportTestCase(RESTContractTestCase):
    """A registered Nursery with sales that can be dated precisely."""

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

    def register(self, basis=GstRegistration.Basis.PAYMENTS, **overrides):
        """Record an arrangement to report under."""
        values = {
            'registered': True,
            'effective_from': date(2026, 1, 1),
            'gst_number': '123456785',
            'basis': basis,
            'filing_frequency': GstRegistration.Frequency.TWO_MONTHLY,
            # Periods ending in the even months, so a registration on 1 January
            # starts a whole period and the year is exactly six of them.
            'period_anchor_month': 4,
        }
        values.update(overrides)
        return record_registration(self.workspace, self.user, **values)

    def sell(self, paid_on, ex_tax='100', *, tax_rate='15', treatment=None, currency='NZD'):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Record a paid supply of a given ex-GST value."""
        order = create_order(self.workspace, self.user, status=SalesOrder.Status.DRAFT)
        if currency != order.currency_code:
            SalesOrder.objects.filter(pk=order.pk).update(currency_code=currency)
            order.refresh_from_db()
        values = {
            'order': order,
            'line_type': SalesOrderLine.LineType.TRAY,
            'tray_item': self.item,
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
        Payment.objects.create(
            workspace=self.workspace, order=order, paid_on=paid_on,
            amount=line.total_incl_tax, currency_code=currency, method='cash',
            operation_key=uuid4(), request_fingerprint='gst-report-test',
        )
        return order

    def buy(self, received_date, ex_tax='200', tax_rate='15', recoverable=True):
        """Post a supplier receipt, which is where input tax comes from."""
        store = Location.objects.create(
            workspace=self.workspace, name=f'Store {received_date}',
            code=f'STORE-{received_date.isoformat()}',
            location_type=Location.LocationType.STORAGE,
        )
        media = make_inventory_item(workspace=self.workspace)
        receipt = StockReceipt.objects.create(
            workspace=self.workspace, supplier=make_supplier(workspace=self.workspace),
            received_date=received_date, currency_code='NZD',
            tax_rate=Decimal(tax_rate), tax_recoverable=recoverable,
            created_by=self.user,
        )
        StockReceiptLine.objects.create(
            receipt=receipt, item=media, quantity=Decimal('2'),
            unit_code=UnitCode.LITRE, base_quantity=Decimal('2'),
            line_cost_ex_tax=Decimal(ex_tax), destination=store,
        )
        return post_receipt(receipt, self.user)[0]

    def periods(self, **params):
        """Fetch the period report over an explicit range."""
        query = {'date_from': '2026-01-01', 'date_to': '2026-12-31'}
        query.update(params)
        response = self.client.get(PERIODS_URL, query)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def entries(self, **params):
        """Fetch the drill-down over the same range, unpaginated."""
        query = {'date_from': '2026-01-01', 'date_to': '2026-12-31', 'page_size': '200'}
        query.update(params)
        response = self.client.get(ENTRIES_URL, query)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data


class GstPeriodContractTests(GstReportTestCase):
    """The report's shape, and who is allowed to ask for it."""

    def test_authentication_is_required(self):
        """A GST return is not public information."""
        self.assert_authentication_required([PERIODS_URL, ENTRIES_URL])

    def test_garden_mode_is_refused(self):
        """A bookmarked URL must be refused by the server, not only the menu."""
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.save()
        for url in (PERIODS_URL, ENTRIES_URL):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_an_unknown_filter_is_rejected(self):
        """A misspelled filter that was ignored would silently change scope."""
        response = self.client.get(PERIODS_URL, {'date_form': '2026-01-01'})
        self.assertEqual(response.status_code, 400)

    def test_a_reversed_range_is_rejected(self):
        """An empty report is not the right answer to an impossible range."""
        response = self.client.get(
            PERIODS_URL, {'date_from': '2026-06-01', 'date_to': '2026-01-01'},
        )
        self.assertEqual(response.status_code, 400)

    def test_the_periods_of_a_registered_year_are_all_reported(self):
        """A period with no trading still had a return due, so it must appear."""
        self.register()
        data = self.periods()
        self.assertEqual(len(data['results']), 6)
        self.assertEqual(data['results'][0]['period_start'], '2026-01-01')
        self.assertEqual(data['results'][0]['period_end'], '2026-02-28')

    def test_an_unregistered_workspace_reports_no_periods(self):
        """Inventing periods would be the first step to filing a wrong return."""
        data = self.periods()
        self.assertEqual(data['results'], [])

    def test_the_recognition_difference_is_published(self):
        """Somebody comparing this against profitability must be told why they differ."""
        self.register()
        data = self.periods()
        self.assertIn('recognition_note', data['reconciliation'])
        self.assertIn('profitability', data['reconciliation']['recognition_note'])


class ReconciliationTests(GstReportTestCase):
    """Every period figure is the sum of the rows the report itself publishes."""

    def test_output_tax_is_the_sum_of_its_entries(self):
        """115.00 paid at 15% carries 15.00 of GST, in the period it was paid."""
        self.register()
        self.sell(date(2026, 3, 10), '100')
        row = self._period('2026-03-01..2026-04-30')
        self.assertEqual(row['taxable_supplies_incl_tax'], '115.0000')
        self.assertEqual(row['output_tax'], '15.0000')

    def test_every_money_column_equals_a_direct_sum_over_the_drill_down(self):
        """This is the claim the whole feature rests on, checked column by column."""
        self.register()
        self.sell(date(2026, 3, 10), '100')
        self.sell(date(2026, 4, 2), '200', tax_rate='0', treatment='zero_rated')
        row = self._period('2026-03-01..2026-04-30')
        rows = self.entries(period='2026-03-01..2026-04-30')['results']
        supplies = [entry for entry in rows if entry['kind'] == 'supply']
        self.assertEqual(
            row['taxable_supplies_incl_tax'],
            self._total(entry['gross'] for entry in supplies if entry['tax_code'] == 'standard'),
        )
        self.assertEqual(
            row['zero_rated_supplies'],
            self._total(entry['gross'] for entry in supplies if entry['tax_code'] == 'zero_rated'),
        )
        self.assertEqual(row['output_tax'], self._total(entry['tax'] for entry in supplies))
        self.assertEqual(row['entry_count'], len(rows))

    def test_net_gst_equals_output_less_input(self):
        """The return's bottom line has to follow from the boxes above it."""
        self.register()
        self.sell(date(2026, 3, 10), '100')
        row = self._period('2026-03-01..2026-04-30')
        expected = Decimal(row['total_output_tax']) - Decimal(row['total_input_tax'])
        self.assertEqual(Decimal(row['net_gst']), expected)
        self.assertEqual(row['net_gst_direction'], 'payable')

    def test_a_period_with_no_trading_is_reported_as_nil(self):
        """Nil and forgotten must not look the same in a list of returns."""
        self.register()
        self.sell(date(2026, 3, 10), '100')
        row = self._period('2026-01-01..2026-02-28')
        for column in MONEY_COLUMNS:
            with self.subTest(column=column):
                self.assertEqual(row[column], '0.0000')
        self.assertEqual(row['net_gst_direction'], 'nil')

    def test_every_entry_balances(self):
        """gross = taxable + claimable tax + non-claimable tax, on every row."""
        self.register()
        self.sell(date(2026, 3, 10), '100')
        for entry in self.entries()['results']:
            with self.subTest(entry=entry['source_id']):
                parts = (
                    Decimal(entry['taxable']),
                    Decimal(entry['tax']),
                    Decimal(entry['non_recoverable_tax']),
                )
                self.assertEqual(Decimal(entry['gross']), sum(parts, Decimal('0')))

    def _period(self, label):
        """Return the one period row carrying a label."""
        rows = [row for row in self.periods()['results'] if row['period_label'] == label]
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def _total(self, values):
        """Sum drill-down strings the same way the report renders its own."""
        return f'{sum((Decimal(value) for value in values), Decimal("0")):.4f}'


class BasisTests(GstReportTestCase):
    """A change of basis moves which period an order lands in, never its value."""

    def test_the_payments_basis_reports_in_the_payment_period(self):
        """Money received in March belongs to March's return."""
        self.register(basis=GstRegistration.Basis.PAYMENTS)
        self.sell(date(2026, 3, 10), '100')
        labels = {
            row['period_label'] for row in self.periods()['results']
            if row['output_tax'] != '0.0000'
        }
        self.assertEqual(labels, {'2026-03-01..2026-04-30'})

    def test_each_period_reports_the_basis_it_was_filed_under(self):
        """A return that cannot say which basis produced it cannot be checked."""
        self.register(basis=GstRegistration.Basis.PAYMENTS)
        record_registration(
            self.workspace, self.user, registered=True,
            effective_from=date(2026, 7, 1), gst_number='123456785',
            basis=GstRegistration.Basis.INVOICE,
            filing_frequency=GstRegistration.Frequency.TWO_MONTHLY,
            period_anchor_month=4,
        )
        bases = {row['period_start']: row['basis'] for row in self.periods()['results']}
        self.assertEqual(bases['2026-01-01'], 'payments')
        self.assertEqual(bases['2026-07-01'], 'invoice')

    def test_a_basis_change_closes_the_period_it_lands_in(self):
        """No single return may be filed on two different bases."""
        self.register(basis=GstRegistration.Basis.PAYMENTS)
        record_registration(
            self.workspace, self.user, registered=True,
            effective_from=date(2026, 6, 10), gst_number='123456785',
            basis=GstRegistration.Basis.INVOICE,
            filing_frequency=GstRegistration.Frequency.TWO_MONTHLY,
            period_anchor_month=4,
        )
        rows = {row['period_start']: row for row in self.periods()['results']}
        self.assertEqual(rows['2026-05-01']['period_end'], '2026-06-09')
        self.assertTrue(rows['2026-05-01']['clipped'])
        self.assertEqual(rows['2026-06-10']['period_end'], '2026-06-30')


class ExclusionTests(GstReportTestCase):
    """Nothing is silently dropped, and no gap is reported as a zero."""

    def test_supplies_before_registration_are_reported_as_excluded(self):
        """A report that skipped a year of trading would look complete."""
        self.register(effective_from=date(2026, 7, 1))
        self.sell(date(2026, 3, 10), '100')
        codes = {finding['code'] for finding in self.periods()['data_quality']}
        self.assertIn('no_registration', codes)
        excluded = self.entries(exclusion='no_registration')['results']
        self.assertTrue(excluded)
        self.assertTrue(all(entry['period_label'] is None for entry in excluded))

    def test_supplies_in_a_deregistered_gap_are_told_apart_from_never_registered(self):
        """They are different situations and a report must not conflate them."""
        self.register()
        record_registration(
            self.workspace, self.user, registered=False, effective_from=date(2026, 7, 1),
        )
        self.sell(date(2026, 9, 10), '100')
        codes = {finding['code'] for finding in self.periods()['data_quality']}
        self.assertIn('deregistered_gap', codes)
        self.assertNotIn('no_registration', codes)

    def test_unclassified_supplies_are_reported_in_their_own_column(self):
        """Counting them as zero-rated would put them in a box nobody chose."""
        self.register()
        self.sell(date(2026, 3, 10), '100', tax_rate='0')
        row = [
            row for row in self.periods()['results']
            if row['period_label'] == '2026-03-01..2026-04-30'
        ][0]
        self.assertEqual(row['unclassified_supplies'], '100.0000')
        self.assertEqual(row['zero_rated_supplies'], '0.0000')
        codes = {finding['code'] for finding in self.periods()['data_quality']}
        self.assertIn('unclassified_tax_code', codes)

    def test_a_data_quality_finding_links_to_the_rows_behind_it(self):
        """A count nobody can drill into is an assertion, not evidence."""
        self.register(effective_from=date(2026, 7, 1))
        self.sell(date(2026, 3, 10), '100')
        finding = [
            item for item in self.periods()['data_quality']
            if item['code'] == 'no_registration'
        ][0]
        self.assertIn('/reports/gst-entries/', finding['drill_down'])


class MixedCurrencyTests(GstReportTestCase):
    """There is no exchange rate here, so nothing is consolidated across one."""

    def test_each_currency_is_reported_separately(self):
        """Adding NZD and AUD would invent a rate this application does not hold."""
        self.register()
        self.sell(date(2026, 3, 10), '100')
        self.sell(date(2026, 3, 11), '100', currency='AUD')
        rows = [
            row for row in self.periods()['results']
            if row['period_label'] == '2026-03-01..2026-04-30'
        ]
        self.assertEqual({row['currency_code'] for row in rows}, {'NZD', 'AUD'})

    def test_the_consolidated_net_is_withheld(self):
        """A withheld figure is honest; a wrong one gets filed."""
        self.register()
        self.sell(date(2026, 3, 10), '100')
        self.sell(date(2026, 3, 11), '100', currency='AUD')
        data = self.periods()
        self.assertIsNone(data['totals']['net_gst'])
        self.assertEqual(data['totals']['currencies'], ['AUD', 'NZD'])
        codes = {finding['code'] for finding in data['data_quality']}
        self.assertIn('mixed_currency', codes)

    def test_a_single_currency_still_reports_a_consolidated_net(self):
        """Withholding must mean something, so the ordinary case must not."""
        self.register()
        self.sell(date(2026, 3, 10), '100')
        self.assertEqual(self.periods()['totals']['net_gst'], '15.0000')

    def test_a_range_with_no_trading_is_nil_rather_than_withheld(self):
        """None has to mean "cannot be stated", not "nothing happened"."""
        self.register()
        self.assertEqual(self.periods()['totals']['net_gst'], '0.0000')


class ExportTests(GstReportTestCase):
    """Every report in this app has a CSV twin carrying the same figures."""

    def test_the_period_export_carries_the_version_and_the_same_totals(self):
        """An export that disagreed with the screen would be filed from anyway."""
        self.register()
        self.sell(date(2026, 3, 10), '100')
        response = self.client.get(
            f'{PERIODS_URL}export/', {'date_from': '2026-01-01', 'date_to': '2026-12-31'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('X-Report-Version', response.headers)
        body = response.content.decode()
        self.assertIn('gst-periods', body)
        self.assertIn('115.0000', body)

    def test_the_entry_export_is_available_too(self):
        """The drill-down is the evidence, so it has to leave the screen as well."""
        self.register()
        self.sell(date(2026, 3, 10), '100')
        response = self.client.get(
            f'{ENTRIES_URL}export/', {'date_from': '2026-01-01', 'date_to': '2026-12-31'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('gst-entries', response.content.decode())


class InputTaxTests(GstReportTestCase):
    """Where input tax lands depends on the basis, and sometimes on nothing."""

    def test_the_invoice_basis_claims_input_tax_on_the_receipt_date(self):
        """The receipt date stands in for the supplier invoice task 119 will add."""
        self.register(basis=GstRegistration.Basis.INVOICE)
        self.buy(date(2026, 3, 10), '200')
        row = self._period('2026-03-01..2026-04-30')
        self.assertEqual(row['purchases_incl_tax'], '230.0000')
        self.assertEqual(row['input_tax'], '30.0000')
        self.assertEqual(row['net_gst_direction'], 'refundable')

    def test_the_payments_basis_holds_input_tax_back(self):
        """It is claimed when the supplier is paid, and no payment date exists."""
        self.register(basis=GstRegistration.Basis.PAYMENTS)
        self.buy(date(2026, 3, 10), '200')
        row = self._period('2026-03-01..2026-04-30')
        self.assertEqual(row['input_tax'], '0.0000')
        self.assertEqual(row['input_tax_awaiting_payment'], '30.0000')

    def test_the_held_back_claim_is_reported_as_a_finding(self):
        """A return quietly claiming nothing would look like a return claiming nothing."""
        self.register(basis=GstRegistration.Basis.PAYMENTS)
        self.buy(date(2026, 3, 10), '200')
        codes = {finding['code'] for finding in self.periods()['data_quality']}
        self.assertIn('input_tax_awaiting_payment', codes)

    def test_the_hybrid_basis_holds_input_tax_back_too(self):
        """Hybrid is invoice for output tax and payments for input tax."""
        self.register(basis=GstRegistration.Basis.HYBRID)
        self.buy(date(2026, 3, 10), '200')
        row = self._period('2026-03-01..2026-04-30')
        self.assertEqual(row['input_tax'], '0.0000')
        self.assertEqual(row['input_tax_awaiting_payment'], '30.0000')

    def test_non_recoverable_tax_is_reported_as_a_memo_not_a_claim(self):
        """It is already inside the stock cost, so claiming it would double-count."""
        self.register(basis=GstRegistration.Basis.INVOICE)
        self.buy(date(2026, 3, 10), '200', recoverable=False)
        row = self._period('2026-03-01..2026-04-30')
        self.assertEqual(row['input_tax'], '0.0000')
        self.assertEqual(row['non_recoverable_tax'], '30.0000')
        self.assertEqual(row['purchases_incl_tax'], '230.0000')
        codes = {finding['code'] for finding in self.periods()['data_quality']}
        self.assertIn('non_recoverable_input_tax', codes)

    def test_net_gst_nets_output_against_input(self):
        """Both sides of the return have to meet in the one figure that is filed."""
        self.register(basis=GstRegistration.Basis.INVOICE)
        self.sell(date(2026, 3, 10), '400')
        self.buy(date(2026, 3, 11), '200')
        row = self._period('2026-03-01..2026-04-30')
        self.assertEqual(row['output_tax'], '60.0000')
        self.assertEqual(row['input_tax'], '30.0000')
        self.assertEqual(row['net_gst'], '30.0000')

    def test_the_receipt_level_limitation_is_reported(self):
        """Task 119 owns per-line treatment; until then the gap is stated."""
        self.register(basis=GstRegistration.Basis.INVOICE)
        self.buy(date(2026, 3, 10), '200')
        codes = {finding['code'] for finding in self.periods()['data_quality']}
        self.assertIn('receipt_level_tax_treatment', codes)

    def _period(self, label):
        """Return the one period row carrying a label."""
        rows = [row for row in self.periods()['results'] if row['period_label'] == label]
        self.assertEqual(len(rows), 1, rows)
        return rows[0]


class InvoiceDateReportingTests(DocumentScenarioMixin, RESTContractTestCase):
    """A period reports supplies on the date a document says, where one exists.

    Task 117 shipped with a fulfillment standing in for the invoice date, and
    every entry relying on it marked. These are the two halves of superseding
    it: the report says how much still rests on the stand-in, and stops saying
    so once a document has been issued.
    """

    dispatched_at = datetime(2026, 6, 10, 9, 0, tzinfo=timezone.utc)

    def setUp(self):
        """A registered nursery on the invoice basis, with one dispatched order."""
        super().setUp()
        self.register_for_gst(period_anchor_month=4)
        plants = self.ready_plants(2, ready_at=datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc))
        self.order, self.line, allocations = self.confirmed_order(
            plants, order_date=date(2026, 3, 15),
        )
        self.fulfill(self.order, allocations, fulfilled_at=self.dispatched_at)

    def periods(self):
        """Fetch the period report over the whole year."""
        response = self.client.get(
            PERIODS_URL, {'date_from': '2026-01-01', 'date_to': '2026-12-31'},
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def row(self, label):
        """Return the one period row carrying a label."""
        rows = [item for item in self.periods()['results'] if item['period_label'] == label]
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def test_an_uninvoiced_supply_is_reported_as_resting_on_its_dispatch_date(self):
        """A gap worth naming: the date a return needs is a date nobody chose."""
        codes = {finding['code'] for finding in self.periods()['data_quality']}
        self.assertIn('invoice_not_issued', codes)
        self.assertEqual(self.row('2026-05-01..2026-06-30')['output_tax'], '3.0000')

    def test_issuing_a_document_moves_the_supply_and_clears_the_finding(self):
        """May's return carries it once May's invoice exists to say so."""
        issue_supply_document(
            self.order, self.user,
            operation_key=uuid4(),
            lines=[{'order_line': self.line, 'positions': [1, 2]}],
            issued_on=date(2026, 4, 20),
        )
        codes = {finding['code'] for finding in self.periods()['data_quality']}

        self.assertNotIn('invoice_not_issued', codes)
        self.assertEqual(self.row('2026-03-01..2026-04-30')['output_tax'], '3.0000')
        self.assertEqual(self.row('2026-05-01..2026-06-30')['output_tax'], '0.0000')

    def test_the_reconciliation_note_describes_the_rule_now_in_force(self):
        """The payload explains itself, so nobody infers the rule from a flag."""
        note = self.periods()['reconciliation']['proxy_note']
        self.assertIn('Where a taxable supply document was issued', note)
