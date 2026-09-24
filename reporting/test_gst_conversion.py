"""One GST return, consolidated into the workspace's currency (task 121).

The euro order `bookkeeping.test_supply_conversion` builds, read as a GST
return. The per-currency rows are unchanged -- they are what happened, in the
unit it happened in -- and a consolidated row follows them once every entry
behind the period carries a rate. Where one does not, the period states no
consolidated figure and the report says which currency is waiting.
"""

# Test names state their behavior and are clearer than repeated method docstrings.
# pylint: disable=missing-function-docstring,duplicate-code

import csv

from rest_framework.test import APIClient

from bookkeeping.conversion import backfill_identity_conversions, live_conversion
from bookkeeping.test_supply_conversion import SupplyConversionTestCase

from .gst import gst_entry_report, gst_period_report


#: The window the euro supply falls in. Which two-monthly period that is has a
#: label the registration decides, so the tests find the period that traded
#: rather than naming one.
WINDOW = {'date_from': '2026-05-01', 'date_to': '2026-06-30'}


class GstConversionTestCase(SupplyConversionTestCase):
    """The euro supply, read through the return it belongs in."""

    def periods(self):
        """Return the period rows for the window the euro supply falls in."""
        return gst_period_report(self.workspace, dict(WINDOW))

    def rows_for(self, report):
        """Return the rows that carry entries, keyed by currency and kind."""
        return {
            (row['currency_code'], row['consolidated']): row
            for row in report.rows if row['entry_count']
        }

    def codes(self, report):
        """Return the data-quality codes a report published."""
        return {row['code'] for row in report.data_quality}


class UnconvertedPeriodTests(GstConversionTestCase):
    """Verification 2: nothing is consolidated until a rate says how."""

    def test_the_period_states_its_euros_and_no_consolidated_figure(self):
        report = self.periods()

        rows = self.rows_for(report)
        self.assertEqual(rows[('EUR', False)]['taxable_supplies_incl_tax'], '23.0000')
        self.assertEqual(rows[('EUR', False)]['output_tax'], '3.0000')
        self.assertNotIn(('NZD', True), rows)
        self.assertIsNone(report.totals['converted'])

    def test_the_report_says_which_currency_is_waiting_for_a_rate(self):
        report = self.periods()

        self.assertIn('unconverted_source', self.codes(report))
        finding = next(
            row for row in report.data_quality
            if row['code'] == 'unconverted_source'
        )
        self.assertEqual(finding['currencies'], ['EUR'])
        self.assertFalse(finding['blocking'])

    def test_the_drill_down_states_no_converted_amount_either(self):
        report = gst_entry_report(self.workspace, dict(WINDOW))

        row = next(row for row in report.rows if row['kind'] == 'supply')
        self.assertEqual(row['taxable'], '20.0000')
        self.assertIsNone(row['converted_taxable'])
        self.assertEqual(row['converted_currency_code'], 'NZD')


class ConvertedPeriodTests(GstConversionTestCase):
    """Verification 2: the return, once the rate behind it has been typed."""

    def setUp(self):
        super().setUp()
        self.convert('supply_document', self.document.pk)

    def test_the_period_gains_one_consolidated_row_in_dollars(self):
        report = self.periods()

        rows = self.rows_for(report)
        consolidated = rows[('NZD', True)]
        self.assertEqual(consolidated['taxable_supplies_incl_tax'], '38.4124')
        self.assertEqual(consolidated['output_tax'], '5.0103')
        self.assertEqual(consolidated['net_gst'], '5.0103')
        self.assertEqual(consolidated['net_gst_direction'], 'payable')

    def test_the_euro_row_still_says_what_was_actually_charged(self):
        report = self.periods()

        rows = self.rows_for(report)
        self.assertEqual(rows[('EUR', False)]['taxable_supplies_incl_tax'], '23.0000')
        self.assertEqual(rows[('EUR', False)]['output_tax'], '3.0000')
        self.assertFalse(rows[('EUR', False)]['consolidated'])

    def test_the_consolidated_row_is_not_counted_as_a_second_currency(self):
        """A restatement is not trading, in the totals or in the findings."""
        report = self.periods()

        self.assertEqual(report.totals['currencies'], ['EUR'])
        self.assertNotIn('mixed_currency', self.codes(report))
        self.assertNotIn('unconverted_source', self.codes(report))

    def test_the_range_total_states_the_same_figure_once(self):
        report = self.periods()

        self.assertEqual(report.totals['converted_currency_code'], 'NZD')
        self.assertEqual(report.totals['converted']['output_tax'], '5.0103')
        self.assertEqual(report.totals['by_currency']['EUR']['output_tax'], '3.0000')

    def test_the_drill_down_converts_each_amount_on_its_own(self):
        report = gst_entry_report(self.workspace, dict(WINDOW))

        row = next(row for row in report.rows if row['kind'] == 'supply')
        self.assertEqual(row['converted_taxable'], '33.4021')
        self.assertEqual(row['converted_tax'], '5.0103')
        self.assertEqual(row['converted_gross'], '38.4124')
        self.assertEqual(row['currency_code'], 'EUR')

    def test_converting_the_payment_leaves_an_invoice_basis_return_alone(self):
        """Each rate converts its own transaction and nothing else's."""
        self.convert('payment', self.payment.pk)

        rows = self.rows_for(self.periods())

        self.assertEqual(rows[('NZD', True)]['taxable_supplies_incl_tax'], '38.4124')
        self.assertEqual(rows[('EUR', False)]['taxable_supplies_incl_tax'], '23.0000')


class StaleIdentityConversionTests(GstConversionTestCase):
    """A rate recorded for one pair is not a rate for another.

    The backfill records an identity conversion for every row already in the
    workspace's own currency, so a workspace that traded in euros and later
    files in dollars holds a table of EUR-to-EUR rates of one against rows
    that are now foreign. Reading one of those as the rate would restate
    23.00 EUR as 23.00 NZD, call the return complete, and understate the GST
    by 40%.
    """

    def setUp(self):
        super().setUp()
        self.record_in('EUR')
        backfill_identity_conversions(self.workspace)
        self.record_in('NZD')

    def test_the_identity_rate_was_recorded_for_the_pair_it_was_recorded_for(self):
        recorded = live_conversion(
            self.workspace, 'supply_document', self.document.pk,
        )
        self.assertEqual(recorded.source_currency_code, 'EUR')
        self.assertEqual(recorded.target_currency_code, 'EUR')
        self.assertEqual(f'{recorded.rate:f}', '1.0000000000')

    def test_the_period_states_no_consolidated_figure_off_a_stale_rate(self):
        report = self.periods()

        rows = self.rows_for(report)
        self.assertNotIn(('NZD', True), rows)
        self.assertEqual(rows[('EUR', False)]['taxable_supplies_incl_tax'], '23.0000')
        self.assertIsNone(report.totals['converted'])

    def test_the_row_is_reported_as_one_still_awaiting_a_rate(self):
        report = self.periods()

        finding = next(
            row for row in report.data_quality
            if row['code'] == 'unconverted_source'
        )
        self.assertEqual(finding['currencies'], ['EUR'])

    def test_a_rate_for_the_pair_in_hand_supersedes_it_and_states_the_figure(self):
        stale = live_conversion(self.workspace, 'supply_document', self.document.pk)

        self.convert('supply_document', self.document.pk, supersedes=stale.pk)

        rows = self.rows_for(self.periods())
        self.assertEqual(rows[('NZD', True)]['taxable_supplies_incl_tax'], '38.4124')


class ConvertedTotalsShapeTests(GstConversionTestCase):
    """What the range totals say, and what the drill-down file writes."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def entry_csv(self):
        """Return the entry export's header and rows, as a spreadsheet reads it."""
        response = self.client.get('/reports/gst-entries/export/', dict(WINDOW))
        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(response.content.decode('utf-8').splitlines()))
        header = next(row for row in rows if row and row[0] == 'period_label')
        return header, [row for row in rows[rows.index(header) + 1:] if row]

    def test_the_restatement_flag_is_not_added_up_as_money(self):
        self.convert('supply_document', self.document.pk)

        converted = self.periods().totals['converted']

        self.assertNotIn('consolidated', converted)
        self.assertEqual(converted['output_tax'], '5.0103')

    def test_a_missing_converted_total_says_which_absence_it_is(self):
        self.assertEqual(self.periods().totals['converted_state'], 'withheld')

        self.convert('supply_document', self.document.pk)

        self.assertEqual(self.periods().totals['converted_state'], 'stated')

    def test_a_range_with_nothing_to_restate_is_not_reported_as_withheld(self):
        """The ordinary case is not a problem, and must not read as one."""
        report = gst_period_report(
            self.workspace, {'date_from': '2027-01-01', 'date_to': '2027-02-28'},
        )

        self.assertEqual(report.totals['converted_state'], 'not_required')
        self.assertIsNone(report.totals['converted'])

    def test_a_withheld_converted_amount_is_words_in_the_file_not_a_blank(self):
        header, rows = self.entry_csv()

        supply = next(
            dict(zip(header, row)) for row in rows if row[header.index('kind')] == 'supply'
        )
        self.assertEqual(supply['converted_taxable'], 'not stated')
        self.assertEqual(supply['converted_tax'], 'not stated')
        self.assertEqual(supply['converted_gross'], 'not stated')

    def test_a_converted_amount_is_the_figure_in_the_file(self):
        self.convert('supply_document', self.document.pk)

        header, rows = self.entry_csv()

        supply = next(
            dict(zip(header, row)) for row in rows if row[header.index('kind')] == 'supply'
        )
        self.assertEqual(supply['converted_taxable'], '33.4021')
        self.assertEqual(supply['converted_gross'], '38.4124')
