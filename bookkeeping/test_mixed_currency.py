"""Closing stock states no cost for stock raised in two currencies (task 142).

`_capture_plants` and `_capture_cohorts` added a plant's layers up regardless of
the currency each was recorded in and filed the sum under the workspace's — a
wrong number in a figure that goes on a return. The line is captured either
way, because the stock was there on the balance date; what it no longer carries
is a cost nobody could reproduce.
"""

# pylint: disable=duplicate-code

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from costing.test_currencies import MixedCurrencyTestCase
from tests.factories import apply_costed_input, make_specific_plant
from workspaces.conversion import QuoteDirection
from workspaces.models import get_current_workspace

from .conversion import record_conversion
from .models import IncomeTaxYear
from .services import build_report, capture_inventory, finalize_income_year


def next_income_year(workspace):
    """An income year whose balance date is still ahead of everything posted."""
    return IncomeTaxYear.objects.create(
        workspace=workspace, basis=IncomeTaxYear.Basis.ACCRUAL,
        year_end=date(timezone.localdate().year + 1, 3, 31),
    )


class MixedCurrencyPlantStockTests(APITestCase):
    """One plant carrying a 1.08 home-currency input and a 0.08 euro one."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.user = get_user_model().objects.create_user(username='stock-currencies')
        self.plant = make_specific_plant(workspace=self.workspace)
        apply_costed_input(self.workspace, self.user, self.plant, '1.0800')
        apply_costed_input(self.workspace, self.user, self.plant, '0.0800', currency_code='EUR')
        self.income_year = next_income_year(self.workspace)

    def capture(self):
        """Capture the year's stock and return the line for this plant."""
        capture_inventory(self.income_year, self.user)
        return self.income_year.stock_lines.get(
            source_type='specific_plant', source_id=str(self.plant.pk),
        )

    def test_the_line_is_provisional_and_states_the_reason(self):
        """Verification 4: no cost, marked provisional, and told why."""
        line = self.capture()
        self.assertIsNone(line.original_cost)
        self.assertEqual(f'{line.value:.4f}', '0.0000')
        self.assertTrue(line.provisional)
        self.assertIn('EUR, USD', line.assumptions)
        self.assertIn('no exchange rate exists', line.assumptions)
        self.assertIn('Lifecycle replay through year end', line.assumptions)

    def test_the_line_does_not_file_the_two_amounts_added_together(self):
        """1.1600 is what the capture used to write under one currency code."""
        self.assertNotEqual(f'{self.capture().value:.4f}', '1.1600')


class SingleCurrencyPlantStockTests(APITestCase):
    """Stock bought at home keeps the cost and the label it always had."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.user = get_user_model().objects.create_user(username='stock-one-currency')

    def test_one_currency_is_valued_and_labelled_as_before(self):
        """Refusing a mixture must leave the ordinary closing stock alone."""
        plant = make_specific_plant(workspace=self.workspace)
        apply_costed_input(self.workspace, self.user, plant, '1.0800')
        apply_costed_input(self.workspace, self.user, plant, '0.0800')
        income_year = next_income_year(self.workspace)

        capture_inventory(income_year, self.user)
        line = income_year.stock_lines.get(
            source_type='specific_plant', source_id=str(plant.pk),
        )
        self.assertEqual(f'{line.value:.4f}', '1.1600')
        self.assertEqual(line.currency_code, self.workspace.currency_code)
        self.assertFalse(line.provisional)


class ForeignCurrencyStockTests(APITestCase):
    """Stock costed wholly abroad is labelled with the currency it cost.

    Filing a euro figure under the workspace's code was the relabelling this
    task exists to stop. Labelling it honestly is what makes a conversion
    finding fire — and task 121 has since supplied the conversion the finding
    was waiting for, so what this now pins is the pair of answers it settled:
    an unconverted line withholds the figures it belongs to without blocking
    the year, and a rate typed against it states them.
    """

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.user = get_user_model().objects.create_user(username='stock-foreign')
        self.plant = make_specific_plant(workspace=self.workspace)
        apply_costed_input(self.workspace, self.user, self.plant, '1.0800', currency_code='EUR')
        self.income_year = next_income_year(self.workspace)

    def test_the_line_states_its_value_under_its_own_currency(self):
        """A stateable cost, stated, rather than refused or relabelled."""
        capture_inventory(self.income_year, self.user)

        line = self.income_year.stock_lines.get(
            source_type='specific_plant', source_id=str(self.plant.pk),
        )
        self.assertEqual(f'{line.value:.4f}', '1.0800')
        self.assertEqual(line.currency_code, 'EUR')
        self.assertFalse(line.provisional)

    def test_an_unconverted_line_states_no_closing_stock_and_says_why(self):
        """The honest label is what makes the conversion finding fire."""
        capture_inventory(self.income_year, self.user)

        report = build_report(self.income_year)

        codes = {row['code'] for row in report['data_quality']}
        self.assertIn('unconverted_source', codes)
        self.assertIsNone(report['totals']['closing_stock'])
        self.assertIsNone(report['totals']['cost_of_sales'])
        self.assertIsNone(report['totals']['working_result'])
        self.assertFalse(report['conversion']['complete'])

    def test_an_unconverted_line_does_not_stop_the_year_being_filed(self):
        """Task 121's decision: an untyped rate flags, it does not block."""
        capture_inventory(self.income_year, self.user)

        finalized = finalize_income_year(
            self.income_year, self.user, confirm_zero_opening=True,
        )

        self.assertEqual(finalized.status, IncomeTaxYear.Status.FINALIZED)
        self.assertIsNone(finalized.frozen_report['totals']['closing_stock'])
        self.assertIn(
            'unconverted_source',
            {row['code'] for row in finalized.frozen_report['data_quality']},
        )

    def test_a_rate_typed_against_the_line_states_the_closing_stock(self):
        """Both euro lines converted: 178.5677 and 1.8037, so 180.3714 stands.

        The capture puts the plant's own 1.0800 beside the 106.9200 still
        standing in the euro media lot it drew from, and a year states its
        closing stock once every line in it has a rate -- not before, which is
        the test above.
        """
        capture_inventory(self.income_year, self.user)
        plant_line = self.income_year.stock_lines.get(
            source_type='specific_plant', source_id=str(self.plant.pk),
        )
        for line in self.income_year.stock_lines.filter(currency_code='EUR'):
            record_conversion(
                self.workspace, 'stock_valuation_line', line.pk,
                {
                    'rate': Decimal('1.6701057193'),
                    'quote_direction': QuoteDirection.TARGET_PER_SOURCE,
                    'rate_source': 'Reserve Bank of New Zealand',
                },
                self.user,
            )

        report = build_report(self.income_year)

        self.assertEqual(report['totals']['closing_stock'], '180.3714')
        self.assertTrue(report['conversion']['complete'])
        stated = next(
            row for row in report['stock_lines'] if row['id'] == plant_line.pk
        )
        self.assertEqual(stated['converted_value'], '1.8037')
        self.assertEqual(stated['currency_code'], 'EUR')
        self.assertEqual(stated['converted_currency_code'], 'USD')


class MixedCurrencyCohortStockTests(MixedCurrencyTestCase):
    """A block of four raised on media bought in two currencies."""

    def setUp(self):
        super().setUp()
        self.cohort = self.observed_cohort()
        self.income_year = next_income_year(self.workspace)

    def test_the_block_is_counted_and_left_unvalued(self):
        """The four units were there; what they cost cannot be stated."""
        capture_inventory(self.income_year, self.user)

        line = self.income_year.stock_lines.get(source_type='plant_cohort')
        self.assertEqual(f'{line.quantity:.9f}', '4.000000000')
        self.assertIsNone(line.original_cost)
        self.assertEqual(f'{line.value:.4f}', '0.0000')
        self.assertTrue(line.provisional)
        self.assertIn('EUR, USD', line.assumptions)
        self.assertIn('Cohort events replayed through year end', line.assumptions)
