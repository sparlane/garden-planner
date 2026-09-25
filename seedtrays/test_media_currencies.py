"""Media cost covers one currency or says it cannot be stated (task 158).

Both media reports added the lots a fill drew on together whatever they were
bought in and labelled the sum with the workspace's currency, which is what
task 142 stopped `costing.services._totals` doing one layer above. A fill
topped up from a lot bought abroad therefore reported a wrong applied, held,
departed, loss and recovered cost, and disagreed with the costing subledger
beside it, which had already stopped stating such a figure.

The mixtures here are the measured ones. A three-pot fill holding 50 litres of
two-dollar media and 10 litres of two-euro media held `100.0000 NZD` and
`20.0000 EUR`, reported as `120.0000` labelled NZD; a two-cell tray fed
0.08 litres of two-dollar media and 0.08 of a one-euro lot held
`0.160000000000 USD` and `0.080000000000 EUR`, reported as `0.240000000000`.
The two halves keep the home currency of the fixture each is built on — the
pot fill's workspace files in NZD and the tray's in USD — which is worth
saying only because the point of the exercise is that a lot's own currency is
not the workspace's. No exchange rate exists
anywhere in this application, so the figures are two totals, never one.
"""

# pylint: disable=duplicate-code

from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APIClient

from applications.services import (
    ApplicationRequest,
    LineRequest,
    TargetRequest,
    create_application_draft,
    post_application,
    reverse_application,
)
from inventory.units import UnitCode
from plantings.counted_fills import plant_counted_fill
from sales.test_counted_lines import CountedStockTestCase
from tests.factories import make_specific_plant, make_specific_plant_location, make_stock_lot

from .container_fills import clean_pot_fill, open_counted_fill, open_numbered_fill
from .generation_costs import generation_cost_breakdown
from .generations import CloseRequest, Disposition, MediaDisposition
from .pot_media import pot_fill_cost_breakdown, pot_fill_remaining_media
from .test_generations import GenerationContentsTestCase
from .test_pot_media import PotMediaMixin


#: What each report would have published had it added the two currencies up.
#: Named so a test can assert the figure appears nowhere rather than only that
#: the right ones do: a wrong total that is merely relabelled is the failure.
COMBINED_FILL = ('120.0000', '40.0001', '79.9999')
COMBINED_TRAY = '0.240000000000'


def _figures(breakdown):
    """Every money figure a report published, as text, for a search over it."""
    return [str(value) for value in breakdown.values() if isinstance(value, Decimal)]


class MixedCurrencyFillTestCase(PotMediaMixin, CountedStockTestCase):
    """Three pots holding 50 litres bought in dollars and 10 bought in euros."""

    def setUp(self):
        super().setUp()
        self.setup_media()
        self.fill = open_counted_fill(self.workspace, self.user, self.pots, self.store, 3)
        post_application(self.draft(), self.user)
        self.foreign = make_stock_lot(
            item=self.media_item, location=self.store, quantity='100',
            base_unit_cost=Decimal('2'), currency_code='EUR',
        )

    def put_media(self, fill, lot, quantity):
        """Post one media application of `quantity` from `lot` into `fill`."""
        application = create_application_draft(self.workspace, self.user, ApplicationRequest(
            applied_at=timezone.now(),
            source_location=self.store,
            lines=(LineRequest(
                item=self.media_item, lot=lot, applied_quantity=quantity,
                unit_code=UnitCode.LITRE, usage_basis='manual',
                targets=(TargetRequest('container_fill', fill),),
            ),),
        ))
        return post_application(application, self.user)

    def top_up(self, quantity='10'):
        """Post a second media application, drawn from the lot bought abroad."""
        return self.put_media(self.fill, self.foreign, quantity)

    def unprice(self, lot):
        """Forget what a lot cost, the way a legacy receipt with no price does."""
        type(lot).objects.filter(pk=lot.pk).update(base_unit_cost=None)

    def depart(self):
        """Put one plant in a pot and take it away again, posting its share."""
        plant = make_specific_plant()
        row, = plant_counted_fill(self.workspace, self.user, self.fill, [plant.pk])
        with self.captureOnCommitCallbacks(execute=True):
            row.ended = timezone.now()
            row.save(update_fields=['ended'])
        return row

    def clean_remaining(self, disposition=Disposition.WASTE):
        """Dispose of exactly what the clean report says is left, lot by lot."""
        media = tuple(MediaDisposition(
            row['lot'].pk, row['base_quantity'], disposition, 'Tipped out.',
            self.store if disposition == Disposition.RECLAIMED else None,
        ) for row in pot_fill_remaining_media(self.fill))
        return clean_pot_fill(self.workspace, self.user, self.fill, CloseRequest(
            reason='Wash the pots.', media=media,
        ))


class SingleCurrencyFillIsUnchangedTests(MixedCurrencyFillTestCase):
    """Verification 1: one currency reads exactly as it did before task 158.

    Every workspace that has never bought media abroad is in this state, so
    the grouping has to be invisible to it — the same figures under the same
    keys, with the split showing only as the flag saying there is nothing to
    split.
    """

    def test_the_fill_states_its_one_total_as_it_always_has(self):
        """Fifty litres of two-dollar media is 100.0000 NZD, held in the fill."""
        breakdown = pot_fill_cost_breakdown(self.fill)

        self.assertFalse(breakdown['mixed_currency'])
        self.assertEqual(breakdown['currency_code'], 'NZD')
        self.assertEqual(breakdown['applied_cost'], Decimal('100'))
        self.assertEqual(breakdown['held_cost'], Decimal('100'))
        self.assertEqual(breakdown['departed_cost'], Decimal('0'))
        self.assertEqual(breakdown['production_loss'], Decimal('0'))
        self.assertEqual(breakdown['recovered_cost'], Decimal('0'))
        self.assertEqual(breakdown['rounding_difference'], Decimal('0'))

    def test_the_only_new_reading_is_the_one_currency_listed_on_its_own(self):
        """The grouped figures are the ungrouped ones when there is one group."""
        breakdown = pot_fill_cost_breakdown(self.fill)

        self.assertEqual(breakdown['currencies'], [{
            'currency_code': 'NZD',
            'amount': '100.000000000000',
            'totals': {
                'departed_cost': '0.000000000000',
                'held_cost': '100.000000000000',
                'production_loss': '0.000000000000',
                'recovered_cost': '0.000000000000',
                'rounding_difference': '0.000000000000',
            },
        }])

    def test_a_fill_that_has_drawn_on_nothing_still_names_a_currency(self):
        """With no amount in it to contradict, the workspace's currency stands."""
        empty = open_counted_fill(self.workspace, self.user, self.pots, self.store, 2)

        breakdown = pot_fill_cost_breakdown(empty)

        self.assertEqual(breakdown['currency_code'], 'NZD')
        self.assertFalse(breakdown['mixed_currency'])
        self.assertEqual(breakdown['applied_cost'], Decimal('0'))
        self.assertEqual(breakdown['currencies'], [])


class MixedCurrencyFillTests(MixedCurrencyFillTestCase):
    """Verification 2 and 3: both figures on a fill topped up from abroad."""

    def test_the_lots_carry_the_currency_each_was_bought_in(self):
        """The mixture is reproduced through the real posting path, not stubbed."""
        self.top_up()

        codes = sorted(row['lot'].currency_code for row in pot_fill_remaining_media(self.fill))

        self.assertEqual(codes, ['EUR', 'NZD'])

    def test_the_fill_reports_both_totals_and_no_combined_figure(self):
        """100.0000 NZD and 20.0000 EUR, never the 120.0000 it used to state."""
        self.top_up()

        breakdown = pot_fill_cost_breakdown(self.fill)

        self.assertTrue(breakdown['mixed_currency'])
        self.assertIsNone(breakdown['currency_code'])
        self.assertIsNone(breakdown['applied_cost'])
        self.assertIsNone(breakdown['held_cost'])
        self.assertEqual(
            [(row['currency_code'], row['amount']) for row in breakdown['currencies']],
            [('EUR', '20.000000000000'), ('NZD', '100.000000000000')],
        )
        self.assertEqual(
            [row['totals']['held_cost'] for row in breakdown['currencies']],
            ['20.000000000000', '100.000000000000'],
        )
        for figure in _figures(breakdown):
            self.assertNotIn(figure.rstrip('0').rstrip('.'), COMBINED_FILL)

    def test_no_figure_states_an_amount_it_would_have_to_add_up(self):
        """Every derived figure goes null together, not only the headline one."""
        self.top_up()

        breakdown = pot_fill_cost_breakdown(self.fill)

        for key in ('applied_cost', 'departed_cost', 'held_cost',
                    'production_loss', 'recovered_cost', 'rounding_difference'):
            self.assertIsNone(breakdown[key], key)

    def test_a_departure_takes_its_share_of_each_currency_separately(self):
        """One pot of three is 33.3334 NZD and 6.6667 EUR, never 40.0001 of one."""
        self.top_up()
        self.depart()

        breakdown = pot_fill_cost_breakdown(self.fill)

        self.assertIsNone(breakdown['departed_cost'])
        self.assertEqual(
            [row['totals']['departed_cost'] for row in breakdown['currencies']],
            ['6.666700000000', '33.333400000000'],
        )
        self.assertEqual(
            [row['totals']['held_cost'] for row in breakdown['currencies']],
            ['13.333300000000', '66.666600000000'],
        )

    def test_cleaning_the_fill_states_no_combined_loss(self):
        """Tipping both lots out is a dollar loss and a euro loss, not one sum."""
        self.top_up()
        self.depart()
        self.clean_remaining()

        breakdown = pot_fill_cost_breakdown(self.fill)

        self.assertTrue(breakdown['mixed_currency'])
        self.assertIsNone(breakdown['production_loss'])
        self.assertIsNone(breakdown['rounding_difference'])
        self.assertEqual(
            [row['totals']['production_loss'] for row in breakdown['currencies']],
            ['13.333333332000', '66.666666666000'],
        )
        # The counted clean's per-pot rounding is reserved inside each currency
        # too: a dollar difference of a few hundred-millionths is not a euro
        # one, and neither is imaginary held media.
        self.assertEqual(
            [row['totals']['rounding_difference'] for row in breakdown['currencies']],
            ['-0.000033332000', '-0.000066666000'],
        )
        self.assertEqual(
            [row['totals']['held_cost'] for row in breakdown['currencies']],
            ['0.000000000000', '0.000000000000'],
        )

    def test_reclaiming_the_foreign_mix_recovers_it_in_its_own_currency(self):
        """Media put back on the shelf comes back at the rate it was bought at."""
        self.top_up()
        self.clean_remaining(Disposition.RECLAIMED)

        breakdown = pot_fill_cost_breakdown(self.fill)

        self.assertEqual(
            [row['totals']['recovered_cost'] for row in breakdown['currencies']],
            ['20.000000000000', '100.000000000000'],
        )
        self.assertEqual(
            [row['totals']['production_loss'] for row in breakdown['currencies']],
            ['0.000000000000', '0.000000000000'],
        )

    def test_each_currency_accounts_for_every_cent_it_put_in(self):
        """The sides add back up within a currency, which is why they can stand."""
        self.top_up()
        self.depart()
        self.clean_remaining()

        for row in pot_fill_cost_breakdown(self.fill)['currencies']:
            self.assertEqual(
                sum(Decimal(value) for value in row['totals'].values()),
                Decimal(row['amount']),
                row['currency_code'],
            )

    def test_reversing_the_foreign_application_leaves_one_currency_again(self):
        """Nothing bought abroad is left in the fill, so a figure can be stated."""
        application = self.top_up()[0]
        breakdown = pot_fill_cost_breakdown(self.fill)
        self.assertTrue(breakdown['mixed_currency'])

        reverse_application(application, self.user, 'Wrong lot.')

        breakdown = pot_fill_cost_breakdown(self.fill)
        self.assertFalse(breakdown['mixed_currency'])
        self.assertEqual(breakdown['currency_code'], 'NZD')
        self.assertEqual(breakdown['applied_cost'], Decimal('100'))


class UnknownCostAndCurrencyTests(MixedCurrencyFillTestCase):
    """An unknown price and a missing rate are different absences.

    One is a lot nobody wrote a price for and the other is two currencies with
    no rate between them, and this report has always answered the first by
    stating nothing rather than a figure that treats the unpriced lot as free.
    Task 158 holds the per-currency rows to that same rule, which is where it
    departs from `costing.services.batch_cost_breakdown`: that one publishes the
    understated figure with the flag beside it, and a report doing both would
    contradict itself between its top level and its list.
    """

    def test_an_unpriced_foreign_lot_still_counts_as_a_second_currency(self):
        """It was bought in one whether or not anybody wrote down what it cost."""
        self.top_up()
        self.unprice(self.foreign)

        breakdown = pot_fill_cost_breakdown(self.fill)

        self.assertTrue(breakdown['unknown_cost'])
        self.assertTrue(breakdown['mixed_currency'])
        self.assertIsNone(breakdown['currency_code'])
        self.assertEqual(
            [row['currency_code'] for row in breakdown['currencies']],
            ['EUR', 'NZD'],
        )

    def test_an_unpriced_lot_leaves_no_figure_anywhere_to_understate(self):
        """Not even the currency that is fully priced, which is the module's rule."""
        self.top_up()
        self.unprice(self.foreign)

        breakdown = pot_fill_cost_breakdown(self.fill)

        for key in ('applied_cost', 'departed_cost', 'held_cost',
                    'production_loss', 'recovered_cost', 'rounding_difference'):
            self.assertIsNone(breakdown[key], key)
        for row in breakdown['currencies']:
            self.assertIsNone(row['amount'], row['currency_code'])
            self.assertEqual(set(row['totals'].values()), {None}, row['currency_code'])

    def test_an_unpriced_lot_in_one_currency_reads_as_it_always_has(self):
        """The rule is the module's own, not something two currencies brought."""
        self.unprice(self.media)

        breakdown = pot_fill_cost_breakdown(self.fill)

        self.assertTrue(breakdown['unknown_cost'])
        self.assertFalse(breakdown['mixed_currency'])
        self.assertEqual(breakdown['currency_code'], 'NZD')
        self.assertIsNone(breakdown['applied_cost'])
        self.assertEqual(breakdown['currencies'], [{
            'currency_code': 'NZD', 'amount': None,
            'totals': {'departed_cost': None, 'held_cost': None,
                       'production_loss': None, 'recovered_cost': None,
                       'rounding_difference': None},
        }])


class UnknownAllocationAndCurrencyTests(MixedCurrencyFillTestCase):
    """A departure whose share nobody recorded hides two figures, per currency.

    `unknown_allocation` is the legacy case: a plant left a shared pot before
    the denominator was frozen, so nothing says what it took. That has always
    left `departed_cost` and `held_cost` unstateable while `applied_cost`
    stands, because what went into the fill is known whoever carried it off —
    and the per-currency rows answer the same way, bucket by bucket.
    """

    def setUp(self):
        super().setUp()
        self.unit = self.number(self.pots, 1)[0]
        self.shared = open_numbered_fill(self.workspace, self.user, self.unit)
        self.put_media(self.shared, self.media, '50')
        self.put_media(self.shared, self.foreign, '10')

    def test_a_legacy_departure_hides_two_figures_in_every_currency(self):
        """Applied still stands in both; what left and what stayed do not."""
        placement = make_specific_plant_location(
            location_type='container_unit', container_unit=self.unit, seed_tray_cell=None,
        )
        type(placement).objects.filter(pk=placement.pk).update(ended=timezone.now())

        breakdown = pot_fill_cost_breakdown(self.shared)

        self.assertTrue(breakdown['unknown_allocation'])
        self.assertFalse(breakdown['unknown_cost'])
        self.assertTrue(breakdown['mixed_currency'])
        self.assertIsNone(breakdown['departed_cost'])
        self.assertIsNone(breakdown['held_cost'])
        self.assertEqual(
            [(row['currency_code'], row['amount']) for row in breakdown['currencies']],
            [('EUR', '20.000000000000'), ('NZD', '100.000000000000')],
        )
        for row in breakdown['currencies']:
            self.assertIsNone(row['totals']['departed_cost'], row['currency_code'])
            self.assertIsNone(row['totals']['held_cost'], row['currency_code'])
            self.assertEqual(row['totals']['production_loss'], '0.000000000000')


class MixedCurrencyNumberedFillTests(MixedCurrencyFillTestCase):
    """The other fill kind: one numbered pot shared by three plants.

    This is the branch whose arithmetic actually changed. A counted fill's
    departures were already whole money per line, because `counted_fill_balance`
    splits each line's cost across the pots and a line draws on exactly one lot.
    A numbered fill instead takes the departed fraction of what was applied, and
    now takes it inside each currency: a third of 120 is money in neither.
    """

    def setUp(self):
        super().setUp()
        self.unit = self.number(self.pots, 1)[0]
        self.shared = open_numbered_fill(self.workspace, self.user, self.unit)
        self.put_media(self.shared, self.media, '50')

    def occupy(self, count=3):
        """Put `count` plants in the one pot, sharing its media between them."""
        return [make_specific_plant_location(
            location_type='container_unit', container_unit=self.unit, seed_tray_cell=None,
        ) for _ in range(count)]

    def depart_one_of_three(self):
        """Let the first of three occupants go, freezing the third it took."""
        placements = self.occupy()
        placements[0].ended = timezone.now()
        placements[0].save()
        return placements

    def test_a_third_of_the_pot_is_a_third_of_each_currency(self):
        """33.333333333333 NZD and 6.666666666667 EUR, not a third of 120."""
        self.put_media(self.shared, self.foreign, '10')
        self.depart_one_of_three()

        breakdown = pot_fill_cost_breakdown(self.shared)

        self.assertTrue(breakdown['mixed_currency'])
        self.assertFalse(breakdown['unknown_allocation'])
        self.assertIsNone(breakdown['departed_cost'])
        self.assertEqual(
            [(row['currency_code'], row['totals']['departed_cost'])
             for row in breakdown['currencies']],
            [('EUR', '6.666666666667'), ('NZD', '33.333333333333')],
        )
        self.assertEqual(
            [row['totals']['held_cost'] for row in breakdown['currencies']],
            ['13.333333333333', '66.666666666667'],
        )
        listed = [row['amount'] for row in breakdown['currencies']]
        listed += [value for row in breakdown['currencies'] for value in row['totals'].values()]
        self.assertNotIn('40.000000000000', listed)

    def test_each_currency_still_accounts_for_every_cent_it_put_in(self):
        """The partition holds on this branch too, where the rounding differs."""
        self.put_media(self.shared, self.foreign, '10')
        self.depart_one_of_three()

        for row in pot_fill_cost_breakdown(self.shared)['currencies']:
            self.assertEqual(
                sum(Decimal(value) for value in row['totals'].values()),
                Decimal(row['amount']),
                row['currency_code'],
            )

    def test_a_numbered_fill_is_unchanged_while_it_draws_on_one_currency(self):
        """Nothing about the fraction moved for a pot bought all in one place."""
        self.depart_one_of_three()

        breakdown = pot_fill_cost_breakdown(self.shared)

        self.assertFalse(breakdown['mixed_currency'])
        self.assertEqual(breakdown['currency_code'], 'NZD')
        self.assertEqual(breakdown['applied_cost'], Decimal('100'))
        self.assertEqual(breakdown['departed_cost'], Decimal('33.333333333333'))
        self.assertEqual(breakdown['held_cost'], Decimal('66.666666666667'))

    def test_cleaning_an_unshared_numbered_pot_states_no_combined_loss(self):
        """Tipping both lots out of one pot is a dollar loss and a euro loss."""
        self.put_media(self.shared, self.foreign, '10')
        clean_pot_fill(self.workspace, self.user, self.shared, CloseRequest(
            reason='Wash the pot.',
            media=tuple(MediaDisposition(row['lot'].pk, row['base_quantity'],
                                         Disposition.WASTE, 'Tipped out.')
                        for row in pot_fill_remaining_media(self.shared)),
        ))

        breakdown = pot_fill_cost_breakdown(self.shared)

        self.assertIsNone(breakdown['production_loss'])
        self.assertEqual(
            [(row['currency_code'], row['totals']['production_loss'])
             for row in breakdown['currencies']],
            [('EUR', '20.000000000000'), ('NZD', '100.000000000000')],
        )
        self.assertEqual(
            [row['totals']['held_cost'] for row in breakdown['currencies']],
            ['0.000000000000', '0.000000000000'],
        )


class MixedCurrencyFillPayloadTests(MixedCurrencyFillTestCase):
    """The contract test for the fill screen: no JavaScript runner (task 107)."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_the_contents_endpoint_publishes_the_refusal(self):
        """`container_fills.tsx` reads these keys to list the sides, not a sum."""
        self.top_up()

        response = self.client.get(f'/seedtrays/container-fills/{self.fill.pk}/contents/')

        self.assertEqual(response.status_code, 200, response.data)
        costs = response.data['costs']
        self.assertTrue(costs['mixed_currency'])
        self.assertIsNone(costs['currency_code'])
        self.assertIsNone(costs['applied_cost'])
        self.assertEqual(costs['currencies'], [
            {'currency_code': 'EUR', 'amount': '20.000000000000', 'totals': {
                'departed_cost': '0.000000000000', 'held_cost': '20.000000000000',
                'production_loss': '0.000000000000', 'recovered_cost': '0.000000000000',
                'rounding_difference': '0.000000000000'}},
            {'currency_code': 'NZD', 'amount': '100.000000000000', 'totals': {
                'departed_cost': '0.000000000000', 'held_cost': '100.000000000000',
                'production_loss': '0.000000000000', 'recovered_cost': '0.000000000000',
                'rounding_difference': '0.000000000000'}},
        ])


class MixedCurrencyGenerationTestCase(GenerationContentsTestCase):
    """One two-cell tray fed 0.08 litres of each of two lots, bought apart.

    The euro lot is priced at one a litre rather than two so the two sides of
    the mixture cannot be mistaken for each other in an assertion. Both lines
    apply the quantity the cell-volume basis calculates, because a media line
    that applies anything else has to say why (`applications.services`).
    """

    def setUp(self):
        super().setUp()
        self.foreign_lot = make_stock_lot(
            item=self.media_item, location=self.location, quantity='50',
            base_unit_cost=Decimal('1'), currency_code='EUR',
        )

    def top_up(self, quantity='0.08'):
        """Post a second media application across both cells, bought in euros."""
        application = create_application_draft(self.workspace, self.user, ApplicationRequest(
            applied_at=timezone.now(),
            source_location=self.location,
            lines=(LineRequest(
                item=self.media_item, lot=self.foreign_lot,
                applied_quantity=Decimal(quantity), unit_code=UnitCode.LITRE,
                targets=tuple(TargetRequest('seed_tray_cell', cell) for cell in self.cells),
            ),),
        ))
        return post_application(application, self.user)

    def seedling(self, cell_index=0):
        """Observe one plant in a cell so its media has somewhere to land."""
        sowing = self.sow(quantity=4, allocations=((cell_index, 2),))
        return self.germinate(sowing, cell_index=cell_index)


class SingleCurrencyGenerationIsUnchangedTests(MixedCurrencyGenerationTestCase):
    """Verification 1 for the tray: the figures a one-currency fill always had."""

    def test_the_tray_states_its_one_total_as_it_always_has(self):
        """Two litres a litre over 0.08 litres is 0.160000000000 USD."""
        self.apply_media()
        plant = self.seedling()

        breakdown = generation_cost_breakdown(self.generation)

        self.assertFalse(breakdown['mixed_currency'])
        self.assertEqual(breakdown['currency_code'], 'USD')
        self.assertEqual(breakdown['applied_cost'], Decimal('0.160000000000'))
        self.assertEqual(breakdown['allocated_cost'], Decimal('0.080000000000'))
        self.assertEqual(breakdown['unallocated_cost'], Decimal('0.080000000000'))
        self.assertEqual(breakdown['plants'], [{
            'plant': plant.pk, 'cost': Decimal('0.080000000000'),
            'currency_code': 'USD', 'mixed_currency': False,
            'currencies': [{'currency_code': 'USD', 'amount': '0.080000000000'}],
        }])

    def test_the_only_new_reading_is_the_one_currency_listed_on_its_own(self):
        """The grouped figures are the ungrouped ones when there is one group."""
        self.apply_media()
        self.seedling()

        breakdown = generation_cost_breakdown(self.generation)

        self.assertEqual(breakdown['currencies'], [{
            'currency_code': 'USD',
            'amount': '0.160000000000',
            'totals': {
                'recovered_cost': '0.000000000000',
                'wasted_cost': '0.000000000000',
                'allocated_cost': '0.080000000000',
                'unallocated_cost': '0.080000000000',
                'production_loss': '0.000000000000',
            },
        }])

    def test_a_tray_that_has_drawn_on_nothing_still_names_a_currency(self):
        """An unfed tray has no amount in it to contradict the workspace's own."""
        breakdown = generation_cost_breakdown(self.generation)

        self.assertEqual(breakdown['currency_code'], 'USD')
        self.assertFalse(breakdown['mixed_currency'])
        self.assertEqual(breakdown['applied_cost'], Decimal('0'))
        self.assertEqual(breakdown['currencies'], [])


class MixedCurrencyGenerationTests(MixedCurrencyGenerationTestCase):
    """Verification 2 and 3 for the tray, down to the cell and the seedling."""

    def test_the_tray_reports_both_totals_and_no_combined_figure(self):
        """0.16 USD and 0.08 EUR, never the 0.24 the report used to state."""
        self.apply_media()
        self.top_up()

        breakdown = generation_cost_breakdown(self.generation)

        self.assertTrue(breakdown['mixed_currency'])
        self.assertIsNone(breakdown['currency_code'])
        self.assertIsNone(breakdown['applied_cost'])
        self.assertEqual(breakdown['currencies'], [
            {'currency_code': 'EUR', 'amount': '0.080000000000', 'totals': {
                'recovered_cost': '0.000000000000', 'wasted_cost': '0.000000000000',
                'allocated_cost': '0.000000000000', 'unallocated_cost': '0.080000000000',
                'production_loss': '0.000000000000'}},
            {'currency_code': 'USD', 'amount': '0.160000000000', 'totals': {
                'recovered_cost': '0.000000000000', 'wasted_cost': '0.000000000000',
                'allocated_cost': '0.000000000000', 'unallocated_cost': '0.160000000000',
                'production_loss': '0.000000000000'}},
        ])
        self.assertNotIn(COMBINED_TRAY, _figures(breakdown))

    def test_no_figure_states_an_amount_it_would_have_to_add_up(self):
        """Every derived figure goes null together, not only the headline one."""
        self.apply_media()
        self.top_up()

        breakdown = generation_cost_breakdown(self.generation)

        for key in ('applied_cost', 'recovered_cost', 'wasted_cost',
                    'allocated_cost', 'unallocated_cost', 'production_loss'):
            self.assertIsNone(breakdown[key], key)

    def test_each_media_line_names_the_currency_its_lot_was_bought_in(self):
        """The rows under the total say where the mixture came from."""
        self.apply_media()
        self.top_up()

        breakdown = generation_cost_breakdown(self.generation)

        self.assertEqual(
            [(row['currency_code'], row['cost']) for row in breakdown['media']],
            [('USD', Decimal('0.160000000000')), ('EUR', Decimal('0.080000000000'))],
        )

    def test_a_cell_fed_from_both_lots_states_no_cost_of_its_own(self):
        """0.08 USD and 0.04 EUR in one cell is not 0.12 of anything."""
        self.apply_media()
        self.top_up()

        cells = generation_cost_breakdown(self.generation)['cells']

        self.assertEqual([row['cost'] for row in cells], [None, None])
        self.assertEqual([row['currency_code'] for row in cells], [None, None])
        self.assertEqual([row['per_plant_cost'] for row in cells], [None, None])
        self.assertTrue(all(row['mixed_currency'] for row in cells))

    def test_the_seedling_names_no_cost_and_lists_what_it_drew_on(self):
        """Dividing a mixture would give a plant money in neither currency."""
        self.apply_media()
        self.top_up()
        plant = self.seedling()

        breakdown = generation_cost_breakdown(self.generation)

        self.assertEqual(breakdown['plants'], [{
            'plant': plant.pk, 'cost': None, 'currency_code': None,
            'mixed_currency': True,
            'currencies': [
                {'currency_code': 'EUR', 'amount': '0.040000000000'},
                {'currency_code': 'USD', 'amount': '0.080000000000'},
            ],
        }])

    def test_a_closed_tray_states_its_loss_in_each_currency(self):
        """An empty cell and a tipped-out litre are both loss, in their own money."""
        self.apply_media()
        self.top_up()
        generation, _ = self.close()

        breakdown = generation_cost_breakdown(generation)

        self.assertIsNone(breakdown['wasted_cost'])
        self.assertIsNone(breakdown['production_loss'])
        self.assertEqual(
            [(row['currency_code'], row['totals']['wasted_cost'])
             for row in breakdown['currencies']],
            [('EUR', '0.080000000000'), ('USD', '0.160000000000')],
        )
        # These two figures are wrong, and are pinned only to show the currency
        # split reaching them. A tray that applied 0.16 USD cannot have lost
        # 0.32: the media in an empty cell is counted once as `unallocated_cost`
        # and again as `wasted_cost` when the clean tips that same media out.
        # The doubling predates this task and is wrong in one currency too, and
        # `costing/sources.py` already answers it correctly for the cost layers
        # by netting a clean's removals off the cell allocation first. It is
        # filed as task 164, which owns the corrected figure — and until it
        # lands, this is the one place the tray's `amount == sum(totals)` does
        # not hold where the pot fill's does.
        self.assertEqual(
            [(row['currency_code'], row['totals']['production_loss'])
             for row in breakdown['currencies']],
            [('EUR', '0.160000000000'), ('USD', '0.320000000000')],
        )

    def test_an_unpriced_lot_still_names_the_currency_it_came_from(self):
        """The two reports agree on what mixed means, so one screen can ask.

        Unlike the pot fill, the tray keeps publishing the figures it does know
        with `unknown_cost` beside them — `batch_cost_breakdown`'s answer, and
        this report's own since it was written. What task 158 adds is that the
        unpriced lot's currency is still counted, so a tray holding euro media
        is not labelled with the workspace's code.
        """
        self.apply_media()
        self.top_up()
        type(self.foreign_lot).objects.filter(pk=self.foreign_lot.pk).update(base_unit_cost=None)

        breakdown = generation_cost_breakdown(self.generation)

        self.assertTrue(breakdown['unknown_cost'])
        self.assertTrue(breakdown['mixed_currency'])
        self.assertIsNone(breakdown['currency_code'])
        self.assertEqual(
            [(row['currency_code'], row['amount']) for row in breakdown['currencies']],
            [('EUR', '0.000000000000'), ('USD', '0.160000000000')],
        )

    def test_the_cost_breakdown_endpoint_publishes_the_refusal(self):
        """The contract `generation_clean.tsx` reads, with no JavaScript runner."""
        self.apply_media()
        self.top_up()
        client = APIClient()
        client.force_authenticate(self.user)

        response = client.get(
            f'/seedtrays/seedtraygenerations/{self.generation.pk}/cost-breakdown/'
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['mixed_currency'])
        self.assertIsNone(response.data['currency_code'])
        self.assertIsNone(response.data['applied_cost'])
        self.assertEqual(
            [row['currency_code'] for row in response.data['currencies']],
            ['EUR', 'USD'],
        )
