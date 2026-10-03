"""A seedling arriving after a batch declared its output final.

Recorded, its shares of the same sowing and the same media were posted beside
the frozen plant's rather than instead of them, so the seed carried 1.50 against
a 1.00 movement, the media 0.12 against 0.08, and a $1.08 batch reported 1.62 of
cost against inputs that never had it. Task 147's answer is to refuse the
germination while the batch is frozen and to name `reopen_batch`, which is the
audited way to redo a frozen allocation; these drive that through the two paths
an operator has, as `costing.test_services` does.

`plantings.test_germination.FinalizedBatchGerminationTests` holds the paths over
a fixture with no money in it. These hold the money.
"""

# pylint: disable=duplicate-code

from decimal import Decimal

from plantings.batches import reopen_batch
from plantings.models import SpecificPlant

from .models import CostAllocation
from .services import batch_cost_breakdown, plant_cost_breakdown
from .test_services import CostingServiceTestCase, Trigger


class LateGerminationRefusalTests(CostingServiceTestCase):
    """A frozen batch refuses a seedling until it is reopened.

    The same $1.08 cell as `FrozenBatchTests` — four seed clusters at 0.25 and
    40 ml of two-dollar media, one seedling, output finalized — with a second
    seedling arriving afterwards. Driven through the paths an operator has: the
    plant endpoint the tray grid posts a single germination to, and the bulk
    operation the grid posts a selection of cells to.
    """

    def setUp(self):
        super().setUp()
        self.sowing = self.sow([(self.cells[0], 4)])
        self.apply_media([self.cells[0]], '0.04')
        self.plant = self.germinate(self.sowing, self.cells[0])[0]
        self.reallocate()
        self.finalize()
        self.allocation_pk = self.allocation(self.sowing, self.cells[0]).pk

    def totals_by_source(self):
        """Return the effective amount drawn from each kind of source.

        The companion to `totals_by_target`, for a figure that has to be read
        against what an input actually cost rather than against where it went:
        a source charged one and a half times shows here as more than it was.
        """
        totals = {}
        for row in self.effective():
            totals[row.source_type] = totals.get(row.source_type, Decimal('0')) + (row.amount or 0)
        return totals

    def record_germination(self, quantity=1, key='11111111-1111-1111-1111-111111111111'):
        """Post one bulk germination the way the tray grid's selection does."""
        return self.client.post(
            '/plantings/bulk-operations/',
            {
                'action': 'germinate',
                'atomicity': 'all_or_nothing',
                'idempotency_key': key,
                'reason': 'A straggler came up.',
                'selection_source': {
                    'mode': 'cell_plantings',
                    'cell_plantings': [self.allocation_pk],
                },
                'action_payload': {
                    'germinations': [
                        {'cell_planting': self.allocation_pk, 'quantity': quantity},
                    ],
                },
            },
            format='json',
        )

    def assert_sources_carry_what_they_cost(self):
        """Assert the seed is still 1.00 and the media still 0.08."""
        sources = self.totals_by_source()
        self.assertEqual(sources[CostAllocation.SourceType.SOWING_POSTING], Decimal('1.0000'))
        self.assertEqual(sources[CostAllocation.SourceType.APPLICATION_LINE], Decimal('0.0800'))
        self.assert_sources_reconcile()

    def assert_still_one_seedling_at_cost(self):
        """Assert nothing was recorded and nothing was charged twice."""
        self.assertEqual(SpecificPlant.objects.filter(batch=self.batch).count(), 1)
        self.assertIsNone(self.reallocate(Trigger.GERMINATION))
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '1.0800')
        self.assertEqual(plant_cost_breakdown(self.plant)['final_value'], '1.0800')
        self.assert_sources_carry_what_they_cost()

    def test_the_plant_endpoint_names_the_batch_and_says_to_reopen_it(self):
        """A refusal nobody can act on is barely better than a wrong total."""
        response = self.client.post(
            '/plantings/specificplants/',
            {'cell_planting': self.allocation_pk, 'reason': 'A straggler came up.'},
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        message = response.data['batch'][0]
        self.assertIn(self.batch.code, message)
        self.assertIn('Reopen the batch', message)
        self.assert_still_one_seedling_at_cost()

    def test_a_bulk_germination_is_refused_whole(self):
        """Forty seedlings entered at once follow the same rule as one."""
        response = self.record_germination(quantity=3)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(self.batch.code, str(response.data))
        self.assertIn('Reopen the batch', str(response.data))
        self.assert_still_one_seedling_at_cost()

    def test_a_reopened_batch_divides_the_cell_over_both_seedlings(self):
        """Reopening is the way through, and it re-divides rather than adds.

        The seed is still 1.00 and the media still 0.08; what changes is that
        two seedlings share them, 0.54 each, instead of 1.50 and 0.12 being
        charged against inputs that cost 1.00 and 0.08.
        """
        reopen_batch(self.batch, self.user, 'A straggler came up after the close.')
        self.batch.refresh_from_db()
        response = self.record_germination()
        self.assertEqual(response.status_code, 201, response.data)
        plants = list(SpecificPlant.objects.filter(batch=self.batch).order_by('pk'))
        self.assertEqual(len(plants), 2)
        self.assertEqual(batch_cost_breakdown(self.batch)['provisional_total'], '1.0800')
        for plant in plants:
            self.assertEqual(
                plant_cost_breakdown(plant)['provisional_value'], '0.5400',
            )
        self.assert_sources_carry_what_they_cost()

    def test_a_reopened_and_refinalized_batch_reports_the_divided_total(self):
        """Repairing a batch is a reopen, a germination and a fresh freeze."""
        reopen_batch(self.batch, self.user, 'A straggler came up after the close.')
        self.batch.refresh_from_db()
        self.assertEqual(self.record_germination().status_code, 201)
        self.finalize('Done sowing, for real this time.')
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '1.0800')
        self.assert_sources_carry_what_they_cost()
