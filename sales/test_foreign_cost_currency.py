"""A cost of sale keeps the currency the stock was raised in (task 157).

`FulfillmentLine` stored one currency code for both halves of the row. The
revenue really is the order's currency; the cost is whatever the inputs behind
the stock were bought in, so a batch raised wholly on a euro lot had its cost
of sale relabelled as dollars and the profitability report then subtracted it
from dollar revenue, wrong by the exchange rate, with the report's own
`mixed_currency` finding silent because from where it stood there was only one
currency on the row.

The line now carries both codes. Nothing is converted here — task 121 owns
rates, and each of these figures would be converted at the rate recorded
against the transaction behind it — and nothing is refused: a nursery raising
stock abroad and selling at home keeps dispatching it.
"""

# pylint: disable=duplicate-code

from decimal import Decimal
from uuid import uuid4

from django.utils import timezone

from applications.services import ApplicationRequest, LineRequest, TargetRequest, create_application_draft, post_application
from plantings.counted_fills import plant_counted_fill
from plantings.models import ProductionBatch
from reporting.commerce import profitability_report
from seedtrays.container_fills import open_counted_fill
from tests.factories import apply_costed_input, make_inventory_item, make_stock_lot

from .commerce import post_fulfillment
from .models import FulfillmentContainer, FulfillmentLine, SalesOrder
from .services import order_margin
from .test_commerce import CommerceFixtureTestCase


def freeze(batch):
    """Stop this batch's cost being provisional, so the report totals it.

    The audited transition wants a closed sowing activity these fixtures have
    no reason to build; what the report is being asked about here is a final
    cost, and `costing.services.is_frozen` reads exactly this column.
    """
    ProductionBatch.objects.filter(pk=batch.pk).update(
        status=ProductionBatch.Status.OUTPUT_FINALIZED,
        output_finalized_at=timezone.now(),
    )


class ForeignCostOfSaleTests(CommerceFixtureTestCase):
    """One plant raised on a 1.08 euro input, sold on a home-currency order."""

    def setUp(self):
        super().setUp()
        self.plant = self.available_plant()
        apply_costed_input(self.workspace, self.user, self.plant, '1.0800', currency_code='EUR')
        self.order, self.allocations = self.confirmed_order([self.plant])

    def dispatch(self):
        """Sell the plant and return the fulfillment line it recorded."""
        self.fulfill(self.order, [row['pk'] for row in self.allocations])
        return FulfillmentLine.objects.get()

    def test_the_line_records_the_cost_in_euros_beside_revenue_in_dollars(self):
        """Verification 1: 1.0800 EUR, not 1.0800 of the order's currency."""
        line = self.dispatch()

        self.assertEqual(line.cogs_amount, Decimal('1.0800'))
        self.assertEqual(line.cogs_currency_code, 'EUR')
        self.assertEqual(line.currency_code, 'NZD')

    def test_the_report_totals_the_cost_apart_from_the_revenue(self):
        """Verification 2: 9.0000 NZD earned and 1.0800 EUR spent, not 7.9200."""
        freeze(self.plant.batch)
        self.dispatch()

        report = profitability_report(self.workspace, {})
        totals = {row['currency_code']: row for row in report.totals['currencies']}
        self.assertEqual(totals['NZD']['net_sales'], '9.0000')
        self.assertEqual(totals['NZD']['plant_cogs'], '0.0000')
        self.assertEqual(totals['EUR']['plant_cogs'], '1.0800')
        self.assertEqual(totals['EUR']['net_sales'], '0.0000')

    def test_the_report_states_no_margin_and_says_two_currencies(self):
        """A margin over two currencies is the finding, not a number."""
        freeze(self.plant.batch)
        self.dispatch()

        report = profitability_report(self.workspace, {})
        self.assertFalse(report.totals['finalized_margin_available'])
        self.assertIn('mixed_currency', [row['code'] for row in report.data_quality])
        self.assertTrue(all(
            row['gross_profit'] is None for row in report.totals['currencies']
        ))

    def test_the_order_states_the_cost_in_euros_and_no_margin(self):
        """Verification 3: a stateable foreign cost is published; a margin is not."""
        margin = order_margin(SalesOrder.objects.get(pk=self.order['pk']))

        self.assertEqual(margin['cost_total'], '1.0800')
        self.assertEqual(margin['cost_currency_code'], 'EUR')
        self.assertEqual(margin['currency_code'], 'NZD')
        self.assertEqual(margin['cost_blocked'], 'foreign_currency')
        self.assertTrue(margin['cost_complete'])
        self.assertIsNone(margin['estimated_margin'])


class TwoForeignCurrenciesOnOneOrderTests(CommerceFixtureTestCase):
    """Two plants on one order, each raised wholly in a different currency.

    Neither is a mixture on its own, so both have a stateable cost and task 142
    leaves both alone. Added together they are a sum across currencies, which
    is the case this order has to decline.
    """

    def setUp(self):
        super().setUp()
        self.first = self.available_plant()
        self.second = self.available_plant(
            batch=self.first.batch, cell_planting=self.first.cell_planting,
        )
        apply_costed_input(self.workspace, self.user, self.first, '1.0800', currency_code='EUR')
        apply_costed_input(self.workspace, self.user, self.second, '2.0000', currency_code='USD')
        self.order, self.allocations = self.confirmed_order([self.first, self.second])

    def test_the_order_states_no_cost_total_and_lists_both_sides(self):
        """Verification 4: 3.0800 is not money in any currency."""
        margin = order_margin(SalesOrder.objects.get(pk=self.order['pk']))

        self.assertIsNone(margin['cost_total'])
        self.assertIsNone(margin['cost_currency_code'])
        self.assertEqual(margin['cost_blocked'], 'mixed_currency')
        self.assertEqual(
            [(row['currency_code'], row['amount']) for row in margin['currencies']],
            [('EUR', '1.0800'), ('USD', '2.0000')],
        )
        self.assertIsNone(margin['estimated_margin'])

    def test_each_dispatched_line_still_keeps_its_own_figure(self):
        """Declining the sum must not decline either of the two costs in it."""
        self.fulfill(self.order, [row['pk'] for row in self.allocations])

        self.assertEqual(
            {line.cogs_currency_code: line.cogs_amount for line in FulfillmentLine.objects.all()},
            {'EUR': Decimal('1.0800'), 'USD': Decimal('2.0000')},
        )


class ForeignPotDispatchTests(CommerceFixtureTestCase):
    """A pot bought abroad, dispatched with the plant standing in it.

    The dispatch used to be refused outright, because the pot's cost had to be
    stored under the order's currency and a foreign pot had nowhere to say
    otherwise. It goes out now, with the pot's own row saying what the pot cost
    and in what.
    """

    pots = None
    fill = None
    media = None
    plant = None
    order = None
    allocation_ids = ()

    def stand_a_potted_plant(self, *, media_currency=None):
        """Pot one plant in a 3.00 euro pot and two litres of 2.00 media."""
        self.pots = make_stock_lot(
            item=make_inventory_item(category='pot_container', base_unit='each', tracking_mode='mixed'),
            location=self.store, quantity='50', base_unit_cost=Decimal('3'), currency_code='EUR',
        )
        self.fill = open_counted_fill(self.workspace, self.user, self.pots, self.store, 50)
        values = {} if media_currency is None else {'currency_code': media_currency}
        self.media = make_stock_lot(
            location=self.store, quantity='100', base_unit_cost=Decimal('2'), **values,
        )
        post_application(create_application_draft(self.workspace, self.user, ApplicationRequest(
            timezone.now(), self.store, lines=(LineRequest(
                self.media.item, self.media, '100', 'l', usage_basis='manual',
                targets=(TargetRequest('container_fill', self.fill),),
            ),),
        )), self.user)
        self.plant = self.available_plant()
        plant_counted_fill(self.workspace, self.user, self.fill, [self.plant.pk])
        order, allocations = self.confirmed_order([self.plant])
        self.order = SalesOrder.objects.get(pk=order['pk'])
        self.allocation_ids = [row['pk'] for row in allocations]

    def dispatch(self):
        """Send the plant with the exact pot it is standing in."""
        return post_fulfillment(
            self.order, self.user, operation_key=uuid4(),
            allocation_ids=self.allocation_ids,
            container_allocations=self.allocation_ids,
        )

    def test_the_imported_pot_leaves_with_its_own_currency_on_its_own_row(self):
        """Verification 5: the dispatch happens, and the pot's row says EUR."""
        self.stand_a_potted_plant()

        self.dispatch()

        container = FulfillmentContainer.objects.get()
        self.assertEqual(container.cogs_amount, Decimal('3.0000'))
        self.assertEqual(container.currency_code, 'EUR')

    def test_the_line_states_no_cost_where_the_pot_and_the_plant_disagree(self):
        """Four dollars of media and three euros of pot are not seven of either."""
        self.stand_a_potted_plant()

        line = self.dispatch().lines.get()

        self.assertIsNone(line.cogs_amount)
        self.assertEqual(line.cogs_currency_code, '')

    def test_a_pot_and_a_plant_in_one_currency_still_add_to_one_figure(self):
        """Verification 6: both halves euro, so the line states 7.0000 EUR."""
        self.stand_a_potted_plant(media_currency='EUR')

        line = self.dispatch().lines.get()

        self.assertEqual(line.cogs_amount, Decimal('7.0000'))
        self.assertEqual(line.cogs_currency_code, 'EUR')


class HomeCurrencyDispatchTests(CommerceFixtureTestCase):
    """The ordinary sale names the workspace's own currency and is unchanged."""

    def test_a_home_raised_plant_labels_its_cost_with_the_home_currency(self):
        """Verification 7: carrying a second code changes nothing at home."""
        plant = self.available_plant()
        apply_costed_input(self.workspace, self.user, plant, '1.0800')
        order, allocations = self.confirmed_order([plant])
        self.fulfill(order, [row['pk'] for row in allocations])

        line = FulfillmentLine.objects.get()
        self.assertEqual(line.cogs_amount, Decimal('1.0800'))
        self.assertEqual(line.cogs_currency_code, 'NZD')
        self.assertEqual(line.currency_code, 'NZD')
        margin = order_margin(SalesOrder.objects.get(pk=order['pk']))
        self.assertIsNone(margin['cost_blocked'])
        self.assertEqual(margin['cost_currency_code'], 'NZD')
