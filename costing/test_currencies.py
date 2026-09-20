"""A cost total covers one currency or says it cannot be stated (task 142).

Driven through the real posting paths on `costing.test_services`'s batch: four
seed clusters at 0.25 and 40 ml of two-dollar media, with a second media
application of the same 40 ml drawn from a lot bought in euros. That is the
mixture the task measured — layer currencies `[('USD', '1.0000'), ('USD',
'0.0800'), ('EUR', '0.0800')]` — and `_totals` added them into `1.1600`
labelled USD, which then reached the recorded cost of sale and the closing
stock value. No exchange rate exists anywhere in this application, so the
figures here are two totals, never one.
"""

# pylint: disable=duplicate-code

from decimal import Decimal
from uuid import uuid4

from applications.services import reverse_application
from plantings.cohorts import observe_cohort
from sales.services import cohort_draw_cost
from tests.factories import (
    apply_costed_input,
    make_production_batch,
    make_specific_plant,
    make_stock_lot,
)

from .services import (
    VALUE_BUCKETS,
    batch_cost_breakdown,
    cohort_cost_breakdown,
    plant_cost_breakdown,
)
from .test_services import CostingServiceTestCase


class SingleCurrencyIsUnchangedTests(CostingServiceTestCase):
    """Verification 1: one currency reads exactly as it did before task 142.

    Every workspace that has never bought abroad is in this state, so the whole
    change has to be invisible to it: the same figures under the same keys, with
    the grouping showing only as the flag that says there is nothing to group.
    """

    def setUp(self):
        super().setUp()
        self.sowing = self.sow([(self.cells[0], 4)])
        self.apply_media([self.cells[0]], '0.04')
        self.plant = self.germinate(self.sowing, self.cells[0])[0]
        self.reallocate()

    def test_the_batch_states_its_one_total_as_it_always_has(self):
        """Four clusters at 0.25 and 40 ml of two-dollar media is 1.08 USD."""
        breakdown = batch_cost_breakdown(self.batch)
        self.assertFalse(breakdown['mixed_currency'])
        self.assertEqual(breakdown['currency_code'], 'USD')
        self.assertEqual(breakdown['provisional_total'], '1.0800')
        self.assertIsNone(breakdown['final_total'])
        self.assertEqual(breakdown['totals'], {
            'plant_inventory': '1.0800', 'cogs': '0.0000',
            'harvested_output': '0.0000', 'production_loss': '0.0000',
            'unresolved': '0.0000', 'unattributed': '0.0000',
        })
        self.assertEqual(breakdown['plants'], [{
            'plant': self.plant.pk, 'cost': '1.0800', 'currency_code': 'USD',
            'state': 'growing', 'disposition': 'plant_inventory',
        }])

    def test_the_only_new_reading_is_the_one_currency_listed_on_its_own(self):
        """The grouped figures are the ungrouped ones when there is one group."""
        breakdown = batch_cost_breakdown(self.batch)
        self.assertEqual(breakdown['currencies'], [{
            'currency_code': 'USD', 'amount': '1.0800',
            'totals': breakdown['totals'],
        }])

    def test_the_plant_states_its_value_and_what_selling_it_would_cost(self):
        """A committed value in the workspace's own currency still projects."""
        breakdown = plant_cost_breakdown(self.plant)
        self.assertFalse(breakdown['mixed_currency'])
        self.assertEqual(breakdown['currency_code'], 'USD')
        self.assertEqual(breakdown['provisional_value'], '1.0800')
        self.assertEqual(breakdown['currencies'], [
            {'currency_code': 'USD', 'amount': '1.0800'},
        ])
        self.assertEqual(breakdown['sale_without_pot'], '1.0800')

    def test_a_batch_that_has_drawn_on_nothing_still_names_a_currency(self):
        """With no amount to contradict it, the workspace's currency stands."""
        breakdown = batch_cost_breakdown(make_production_batch(variety=self.batch.variety))
        self.assertFalse(breakdown['mixed_currency'])
        self.assertEqual(breakdown['currency_code'], 'USD')
        self.assertEqual(breakdown['currencies'], [])
        self.assertEqual(breakdown['provisional_total'], '0.0000')
        self.assertEqual(set(breakdown['totals'].values()), {'0.0000'})


class MixedCurrencyTestCase(CostingServiceTestCase):
    """One cell fed 1.00 of USD seed, 0.08 of USD media and 0.08 of EUR media.

    Fixture only, with no tests of its own: the suites below and the
    bookkeeping and reporting ones all read this same batch, so the mixture
    the task measured is built in exactly one place.
    """

    def setUp(self):
        super().setUp()
        # The same fifty litres at two a litre, invoiced by a supplier abroad.
        self.euro_media = make_stock_lot(
            item=self.media,
            location=self.location,
            quantity='50',
            acquisition_total=Decimal('100'),
            base_unit_cost=Decimal('2'),
            currency_code='EUR',
        )
        self.sowing = self.sow([(self.cells[0], 4)])
        self.applied = self.apply_media([self.cells[0]], '0.04')
        self.foreign = self.apply_media([self.cells[0]], '0.04', lot=self.euro_media)

    def germinated_plant(self):
        """Bring one seedling up out of the mixed cell and cost the batch."""
        plant = self.germinate(self.sowing, self.cells[0])[0]
        self.reallocate()
        return plant

    def reverse_foreign(self, reason='Bought in the wrong currency.'):
        """Take the euro application back off the batch and recost it."""
        reverse_application(self.foreign, self.user, reason)
        self.reallocate()

    def observed_cohort(self, quantity=4):
        """Count anonymous units out of the mixed cell instead of naming one."""
        cohort, _observed = observe_cohort(
            self.workspace, self.user, batch=self.batch,
            source_sowing=self.sowing, quantity=quantity, idempotency_key=uuid4(),
        )
        return cohort


class MixedCurrencyBatchTests(MixedCurrencyTestCase):
    """Verification 2: a USD seed lot and one media lot from each currency."""

    def setUp(self):
        super().setUp()
        self.plant = self.germinated_plant()

    def test_the_layers_carry_the_currency_each_lot_was_bought_in(self):
        """The mixture the task measured, reproduced through the real paths."""
        self.assertEqual(
            sorted((row.currency_code, f'{row.amount:.4f}') for row in self.effective()),
            [('EUR', '0.0800'), ('USD', '0.0800'), ('USD', '1.0000')],
        )

    def test_the_batch_reports_both_totals_and_no_combined_figure(self):
        """1.08 and 0.08 are two totals; 1.1600 is not one of them."""
        breakdown = batch_cost_breakdown(self.batch)
        self.assertTrue(breakdown['mixed_currency'])
        self.assertIsNone(breakdown['currency_code'])
        self.assertIsNone(breakdown['provisional_total'])
        self.assertIsNone(breakdown['final_total'])
        self.assertEqual(
            [(row['currency_code'], row['amount']) for row in breakdown['currencies']],
            [('EUR', '0.0800'), ('USD', '1.0800')],
        )
        self.assertNotIn('1.1600', repr(breakdown))

    def test_no_bucket_states_a_figure_it_would_have_to_add_up(self):
        """A bucket holding two currencies has no amount, and says so once."""
        breakdown = batch_cost_breakdown(self.batch)
        self.assertEqual(breakdown['totals'], {bucket: None for bucket in VALUE_BUCKETS})
        self.assertEqual(
            [row['totals']['plant_inventory'] for row in breakdown['currencies']],
            ['0.0800', '1.0800'],
        )

    def test_the_plant_row_names_no_cost_and_no_currency(self):
        """Per plant is the same refusal: the seedling holds two amounts."""
        row, = batch_cost_breakdown(self.batch)['plants']
        self.assertEqual(row['plant'], self.plant.pk)
        self.assertIsNone(row['cost'])
        self.assertIsNone(row['currency_code'])

    def test_the_plant_states_no_value_to_sell_against(self):
        """Committed and projected both go null; unknown cost stays separate."""
        breakdown = plant_cost_breakdown(self.plant)
        self.assertTrue(breakdown['mixed_currency'])
        self.assertFalse(breakdown['unknown_cost'])
        self.assertIsNone(breakdown['currency_code'])
        self.assertIsNone(breakdown['provisional_value'])
        self.assertIsNone(breakdown['final_value'])
        self.assertIsNone(breakdown['sale_without_pot'])
        self.assertIsNone(breakdown['sale_with_pot'])
        self.assertEqual(
            [(row['currency_code'], row['amount']) for row in breakdown['currencies']],
            [('EUR', '0.0800'), ('USD', '1.0800')],
        )

    def test_the_batch_endpoint_publishes_the_refusal(self):
        """The screen reads the same refusal the service made, not a total."""
        response = self.client.get(f'/costing/batches/{self.batch.pk}/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['mixed_currency'])
        self.assertIsNone(response.data['currency_code'])
        self.assertIsNone(response.data['provisional_total'])
        self.assertEqual(
            [row['currency_code'] for row in response.data['currencies']],
            ['EUR', 'USD'],
        )

    def test_reversing_the_foreign_application_restores_one_currency(self):
        """Verification 5: with the euro layer gone, the total is stateable."""
        self.reverse_foreign()

        breakdown = batch_cost_breakdown(self.batch)
        self.assertFalse(breakdown['mixed_currency'])
        self.assertEqual(breakdown['currency_code'], 'USD')
        self.assertEqual(breakdown['provisional_total'], '1.0800')
        self.assertEqual(breakdown['totals']['plant_inventory'], '1.0800')
        self.assertEqual(plant_cost_breakdown(self.plant)['provisional_value'], '1.0800')


class MixedCurrencyCohortTests(MixedCurrencyTestCase):
    """A block of four bought in two currencies has no unit value at all."""

    def setUp(self):
        super().setUp()
        self.cohort = self.observed_cohort()

    def test_the_block_states_no_value_and_no_unit_value(self):
        """Dividing 1.16 by four would price a unit in neither currency."""
        breakdown = cohort_cost_breakdown(self.cohort)
        self.assertTrue(breakdown['mixed_currency'])
        self.assertIsNone(breakdown['currency_code'])
        self.assertIsNone(breakdown['provisional_value'])
        self.assertIsNone(breakdown['unit_value'])
        self.assertEqual(breakdown['units'], 4)
        self.assertEqual(
            [(row['currency_code'], row['amount']) for row in breakdown['currencies']],
            [('EUR', '0.0800'), ('USD', '1.0800')],
        )

    def test_a_draw_on_the_block_costs_an_unknown_amount(self):
        """What a dispatch out of it is charged: unknown, not a share of 1.16."""
        self.assertEqual(cohort_draw_cost(self.cohort, 1), (None, True, True))


class ForeignCurrencyProjectionTests(CostingServiceTestCase):
    """A plant costed wholly abroad: a stateable value, an unstateable sale.

    Its committed cost is one currency and can be published. What cannot is
    the sale projection, because the pending media and pot shares beside it are
    projected in the workspace's own currency and nothing may add the two.
    `sale_blocked` is what tells that apart from an unpriced input, which a
    bare null could not.
    """

    def setUp(self):
        super().setUp()
        self.plant = make_specific_plant(workspace=self.workspace)
        apply_costed_input(self.workspace, self.user, self.plant, '1.0800', currency_code='EUR')

    def test_the_committed_value_is_stated_in_the_currency_it_was_bought_in(self):
        """One currency is one currency, whichever one it is."""
        breakdown = plant_cost_breakdown(self.plant)
        self.assertFalse(breakdown['mixed_currency'])
        self.assertEqual(breakdown['currency_code'], 'EUR')
        self.assertEqual(breakdown['provisional_value'], '1.0800')
        self.assertEqual(breakdown['currencies'], [
            {'currency_code': 'EUR', 'amount': '1.0800'},
        ])

    def test_the_sale_projection_says_which_absence_it_is(self):
        """Not a missing price: a committed cost the pending shares are not in."""
        breakdown = plant_cost_breakdown(self.plant)
        self.assertIsNone(breakdown['sale_without_pot'])
        self.assertIsNone(breakdown['sale_with_pot'])
        self.assertEqual(breakdown['sale_blocked'], 'foreign_currency')
        self.assertFalse(breakdown['unknown_cost'])

    def test_a_home_currency_plant_still_projects_a_sale(self):
        """The guard is the currency, not the presence of a committed cost."""
        plant = make_specific_plant(workspace=self.workspace)
        apply_costed_input(self.workspace, self.user, plant, '1.0800')

        breakdown = plant_cost_breakdown(plant)
        self.assertEqual(breakdown['sale_without_pot'], '1.0800')
        self.assertIsNone(breakdown['sale_blocked'])

    def test_a_mixed_plant_names_the_rate_as_what_is_missing(self):
        """The third reading of the same blank, told apart from the other two."""
        apply_costed_input(self.workspace, self.user, self.plant, '0.0800')

        self.assertEqual(plant_cost_breakdown(self.plant)['sale_blocked'], 'mixed_currency')
