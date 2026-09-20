"""Neither register publishes a cost across two currencies (task 142).

The plant register summed a plant's layers in SQL and labelled the result with
the workspace's currency, and the cohort register did the same for the units a
block still holds — so both published 1.1600 USD for stock the costing
breakdowns had already refused to price. These pin that they now refuse in the
same words, and that stock bought in one currency still reads exactly as it
did.
"""

# pylint: disable=duplicate-code

from costing.test_currencies import MixedCurrencyTestCase
from tests.factories import apply_costed_input
from workspaces.models import Workspace

from .test_register import RegisterTestCase


class PlantRegisterCurrencyTests(RegisterTestCase):
    """One plant, costed through the real application posting path."""

    def row(self, plant):
        """Return this plant's register row."""
        return next(
            row for row in self.page()['results'] if row['pk'] == plant.pk
        )

    def test_a_plant_bought_in_two_currencies_is_not_priced(self):
        """1.0800 and 0.0800 from another supplier are not 1.1600."""
        plant = self.make_plant()
        apply_costed_input(self.workspace, self.operator, plant, '1.0800')
        apply_costed_input(self.workspace, self.operator, plant, '0.0800', currency_code='EUR')

        row = self.row(plant)
        self.assertTrue(row['mixed_currency'])
        self.assertIsNone(row['cost'])
        self.assertIsNone(row['currency_code'])

    def test_one_currency_is_priced_and_labelled_as_before(self):
        """Refusing a mixture must not stop the register pricing ordinary stock."""
        plant = self.make_plant()
        apply_costed_input(self.workspace, self.operator, plant, '1.0800')
        apply_costed_input(self.workspace, self.operator, plant, '0.0800')

        row = self.row(plant)
        self.assertFalse(row['mixed_currency'])
        self.assertEqual(row['cost'], '1.1600')
        self.assertEqual(row['currency_code'], self.workspace.currency_code)

    def test_a_wholly_foreign_cost_names_the_currency_it_is_money_in(self):
        """A stateable cost, stated — under the code it was recorded in."""
        plant = self.make_plant()
        apply_costed_input(self.workspace, self.operator, plant, '1.0800', currency_code='EUR')

        row = self.row(plant)
        self.assertFalse(row['mixed_currency'])
        self.assertEqual(row['cost'], '1.0800')
        self.assertEqual(row['currency_code'], 'EUR')

    def test_a_plant_that_has_drawn_on_nothing_still_names_a_currency(self):
        """No cost to contradict it, so the workspace's own currency stands."""
        row = self.row(self.make_plant())
        self.assertFalse(row['mixed_currency'])
        self.assertIsNone(row['cost'])
        self.assertEqual(row['currency_code'], self.workspace.currency_code)


class CohortRegisterCurrencyTests(MixedCurrencyTestCase):
    """The block of four raised on media bought in two currencies."""

    def setUp(self):
        super().setUp()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        self.cohort = self.observed_cohort()

    def detail(self):
        """Return the cohort register's detail payload for the block."""
        response = self.client.get(f'/plantings/cohorts/{self.cohort.pk}/')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_the_production_cost_card_states_no_figure(self):
        """The card read 1.1600 USD while a draw on the block cost unknown."""
        data = self.detail()
        self.assertTrue(data['mixed_currency'])
        self.assertIsNone(data['cost'])
        self.assertIsNone(data['currency_code'])

    def test_the_block_is_priced_again_once_one_currency_is_left(self):
        """Reversing the euro application leaves a stateable 1.0800 USD."""
        self.reverse_foreign()

        data = self.detail()
        self.assertFalse(data['mixed_currency'])
        self.assertEqual(data['cost'], '1.0800')
        self.assertEqual(data['currency_code'], 'USD')
