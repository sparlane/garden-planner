"""Closing stock states no cost for stock raised in two currencies (task 142).

`_capture_plants` and `_capture_cohorts` added a plant's layers up regardless of
the currency each was recorded in and filed the sum under the workspace's — a
wrong number in a figure that goes on a return. The line is captured either
way, because the stock was there on the balance date; what it no longer carries
is a cost nobody could reproduce.
"""

# pylint: disable=duplicate-code

from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APITestCase

from costing.test_currencies import MixedCurrencyTestCase
from tests.factories import apply_costed_input, make_specific_plant
from workspaces.models import get_current_workspace

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
    task exists to stop. Labelling it honestly is what
    `build_report`'s existing `unsupported_currency` finding is for, and that
    finding blocks finalization — so a year holding such stock now waits for
    task 121's conversion instead of filing a figure in the wrong unit.
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

    def test_the_year_cannot_be_finalized_until_a_rate_converts_it(self):
        """The honest label is what makes the existing finding fire."""
        capture_inventory(self.income_year, self.user)

        codes = {row['code'] for row in build_report(self.income_year)['data_quality']}
        self.assertIn('unsupported_currency', codes)
        with self.assertRaisesMessage(ValidationError, 'Task 121 must convert'):
            finalize_income_year(self.income_year, self.user, confirm_zero_opening=True)


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
