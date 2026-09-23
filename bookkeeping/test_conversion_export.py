"""What a converted year publishes and exports (task 121).

The euro order read as an income-tax year: what the REST payload the screens
draw from says while a rate is missing, what it says once one is typed, and
what the export file carries so the figure can be reproduced years later
without the application.

These are also the contract tests for the screen changes, this repository
having no JavaScript test runner: every key the income-year panel reads is
asserted here.
"""

# Test names state their behavior and are clearer than repeated method docstrings.
# pylint: disable=missing-function-docstring,duplicate-code

import csv
from datetime import date

from rest_framework.test import APIClient

from .test_supply_conversion import SupplyConversionTestCase


class IncomeYearConversionTestCase(SupplyConversionTestCase):
    """The euro order, read through the year it falls in."""

    url = '/bookkeeping/income-years/'

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        created = self.client.post(
            self.url, {'year_end': '2027-03-31', 'basis': 'accrual'}, format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        self.year = created.data['id']

    def report(self):
        """Return the live income-tax report the screen draws."""
        response = self.client.get(f'{self.url}{self.year}/')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['live_report']

    def export_sections(self):
        """Return the export split into its sections, in the order it writes them."""
        response = self.client.get(f'{self.url}{self.year}/export/')
        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(response.content.decode('utf-8').splitlines()))
        sections = [[]]
        for row in rows:
            if row:
                sections[-1].append(row)
            else:
                sections.append([])
        return sections


class UnconvertedYearTests(IncomeYearConversionTestCase):
    """A year holding an untyped rate states less, and says so."""

    def test_the_year_states_no_sales_figure_and_names_the_reason(self):
        report = self.report()

        self.assertIsNone(report['totals']['sales_ex_tax'])
        self.assertFalse(report['conversion']['complete'])
        self.assertEqual(report['conversion']['policy'], 'spot')
        finding = next(
            row for row in report['data_quality']
            if row['code'] == 'unconverted_source'
        )
        self.assertFalse(finding['blocking'])

    def test_the_row_carries_no_converted_amount(self):
        report = self.report()

        row = next(row for row in report['rows'] if row['source_type'] == 'supply_document')
        self.assertEqual(row['amount'], '20.0000')
        self.assertEqual(row['currency_code'], 'EUR')
        self.assertIsNone(row['converted_amount'])
        self.assertEqual(row['converted_currency_code'], 'NZD')

    def test_the_export_says_not_stated_rather_than_leaving_a_blank(self):
        summary = self.export_sections()[1]

        stated = dict((row[0], row[1]) for row in summary[1:])
        self.assertEqual(stated['sales_ex_tax'], 'not stated')


class ConvertedYearTests(IncomeYearConversionTestCase):
    """A year whose rates have been typed states its figures and exports them."""

    def setUp(self):
        super().setUp()
        self.conversion = self.convert('supply_document', self.document.pk)

    def test_the_year_states_the_converted_sales_figure(self):
        report = self.report()

        self.assertEqual(report['totals']['sales_ex_tax'], '33.4021')
        self.assertTrue(report['conversion']['complete'])
        # Only the rates actually read: an amount already in the workspace's
        # currency is converted at one without a record being looked up, so
        # `base_currency` appears only where one was.
        self.assertEqual(report['conversion']['methods'], ['spot'])
        self.assertNotIn(
            'unconverted_source', {row['code'] for row in report['data_quality']},
        )

    def test_the_row_states_both_what_was_charged_and_what_it_comes_to(self):
        report = self.report()

        row = next(row for row in report['rows'] if row['source_type'] == 'supply_document')
        self.assertEqual(row['amount'], '20.0000')
        self.assertEqual(row['currency_code'], 'EUR')
        self.assertEqual(row['converted_amount'], '33.4021')

    def test_the_export_carries_the_rate_as_its_own_record(self):
        sections = self.export_sections()

        rates = sections[3]
        self.assertEqual(rates[0][0], 'source_type')
        recorded = dict(zip(rates[0], next(
            row for row in rates[1:] if row[0] == 'supply_document'
        )))
        self.assertEqual(recorded['source_id'], str(self.document.pk))
        self.assertEqual(recorded['source_currency_code'], 'EUR')
        self.assertEqual(recorded['target_currency_code'], 'NZD')
        self.assertEqual(recorded['rate'], '1.6701057193')
        self.assertEqual(recorded['quote_direction'], 'target_per_source')
        self.assertEqual(recorded['method'], 'spot')
        self.assertEqual(recorded['rate_source'], 'Reserve Bank of New Zealand')
        self.assertEqual(recorded['effective_date'], '2026-05-04')
        self.assertEqual(recorded['superseded'], 'no')

    def test_the_export_row_carries_what_the_rate_made_of_it(self):
        rows = self.export_sections()[2]

        columns = rows[0]
        line = dict(zip(columns, next(
            row for row in rows[1:] if row[1] == '2026-05-04'
        )))
        self.assertEqual(line['amount'], '20.0000')
        self.assertEqual(line['currency_code'], 'EUR')
        self.assertEqual(line['converted_amount'], '33.4021')
        self.assertEqual(line['converted_currency_code'], 'NZD')

    def test_a_superseded_rate_is_exported_beside_the_one_that_replaced_it(self):
        """What a corrected rate used to be is the question a reviewer asks."""
        self.convert(
            'supply_document', self.document.pk,
            rate='2.0000000000', supersedes=self.conversion.pk,
            effective_date=date(2026, 5, 4),
        )

        rates = self.export_sections()[3]

        recorded = [
            dict(zip(rates[0], row)) for row in rates[1:]
            if row[0] == 'supply_document'
        ]
        self.assertEqual(
            {(row['rate'], row['superseded']) for row in recorded},
            {('1.6701057193', 'yes'), ('2.0000000000', 'no')},
        )
