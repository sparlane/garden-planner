"""Anonymous stock leaving a batch after its output was finalized.

Driven through the real posting paths, as `costing.test_services` is: an order
dispatched and reversed, a recorded loss, a promotion and a customer return,
each out of a block of four units whose batch was finalized first.
"""

from decimal import Decimal
from unittest import mock
from uuid import uuid4

from sales.commerce import reverse_fulfillment

from .models import CostAllocation, CostAllocationRun
from . import services
from .services import batch_cost_breakdown, plant_cost_breakdown, recalculate_batch_costs
from .test_services import CohortStockTestCase


fixed_frozen_plan = services._frozen_plan  # pylint: disable=protected-access


def frozen_plan_before_the_fix(intended, stored):
    """Plan as the frozen branch did before task 134: repost only missing keys."""
    reverse, _post = fixed_frozen_plan(intended, stored)
    return reverse, [spec for key, spec in intended.items() if key not in stored]


class FrozenCohortCostTests(CohortStockTestCase):
    """Anonymous stock leaving a finalized batch never takes its cost with it.

    A frozen batch still re-divides its cohort layers, because a sale, loss,
    promotion or return changes how many units share them. Selling one unit of
    four once reversed the whole cohort layer and posted only the sold quarter
    back, dropping the final total from 1.0800 to 0.2700 and leaving the three
    units still on the bench worth nothing. Finalizing when the last tray is
    sown and selling the crop over the following weeks is the ordinary order.
    """

    def setUp(self):
        super().setUp()
        self.finalize()
        self.frozen_run = CostAllocationRun.objects.filter(batch=self.batch).latest('pk')

    def assert_total_held(self):
        """Assert the batch still holds exactly what its inputs cost.

        Every run after the freeze is checked on its own, so a total that
        dipped and recovered inside one operation's reallocations fails too.
        """
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '1.0800')
        self.assert_sources_reconcile()
        runs = CostAllocationRun.objects.filter(batch=self.batch, pk__gt=self.frozen_run.pk)
        for run in runs:
            rows = list(CostAllocation.objects.filter(run=run))
            reversed_amount = sum((row.amount for row in rows if row.reversal_of_id), Decimal('0'))
            posted_amount = sum((row.amount for row in rows if not row.reversal_of_id), Decimal('0'))
            self.assertEqual(posted_amount, reversed_amount, f'Run {run.pk} moved the total.')

    def test_the_finalized_block_holds_the_whole_cost(self):
        """Before anything leaves, all four units carry the 1.08."""
        totals = self.totals_by_target()
        self.assertEqual(totals[CostAllocation.TargetType.PLANT_COHORT], Decimal('1.0800'))
        self.assert_total_held()

    def test_a_sale_leaves_the_final_total_where_it_was(self):
        """A quarter goes to cost of sale; three quarters stay on the bench."""
        self.sell()

        breakdown = batch_cost_breakdown(self.batch)
        self.assertEqual(breakdown['totals']['cogs'], '0.2700')
        totals = self.totals_by_target()
        self.assertEqual(totals[CostAllocation.TargetType.PLANT_COHORT], Decimal('0.8100'))
        self.assertEqual(totals[CostAllocation.TargetType.COHORT_SALE], Decimal('0.2700'))
        self.assert_total_held()

    def test_a_reversed_dispatch_returns_the_cost_to_the_block(self):
        """The dispatch never happened, so neither did its cost of sale."""
        fulfillment = self.sell()

        reverse_fulfillment(
            fulfillment, self.user,
            operation_key=uuid4(), reason='Dispatched in error.',
        )

        self.assertEqual(batch_cost_breakdown(self.batch)['totals']['cogs'], '0.0000')
        totals = self.totals_by_target()
        self.assertEqual(totals[CostAllocation.TargetType.PLANT_COHORT], Decimal('1.0800'))
        self.assertNotIn(CostAllocation.TargetType.COHORT_SALE, totals)
        self.assert_total_held()

    def test_a_loss_keeps_the_final_total(self):
        """The lost quarter goes to production loss; the rest stays on the bench."""
        self.lose()

        self.assertEqual(batch_cost_breakdown(self.batch)['totals']['production_loss'], '0.2700')
        self.assertEqual(self.totals_by_target()[CostAllocation.TargetType.PLANT_COHORT], Decimal('0.8100'))
        self.assert_total_held()

    def test_a_loss_after_a_promotion_keeps_the_final_total(self):
        """A loss must not shrink the divisor a frozen plant share was cut from.

        The promoted plant's 0.27 is frozen. Before task 136 the loss
        re-divided the block's layer over three units instead of four, so the
        cohort dropped from 0.81 to 0.72 and the final total from 1.0800 to
        0.9900. The lost unit now keeps its place and takes its 0.27 to loss.
        """
        plant = self.promote_one()
        self.lose()

        totals = self.totals_by_target()
        self.assertEqual(totals[CostAllocation.TargetType.PLANT_COHORT], Decimal('0.5400'))
        self.assertEqual(totals[CostAllocation.TargetType.COHORT_LOSS], Decimal('0.2700'))
        self.assertEqual(plant_cost_breakdown(plant)['final_value'], '0.2700')
        self.assert_total_held()

    def test_a_promotion_transfers_rather_than_duplicates(self):
        """The named plant takes its quarter out of the block, not on top of it."""
        plant = self.promote_one()

        totals = self.totals_by_target()
        self.assertEqual(totals[CostAllocation.TargetType.PLANT_COHORT], Decimal('0.8100'))
        self.assertEqual(totals[CostAllocation.TargetType.SPECIFIC_PLANT], Decimal('0.2700'))
        self.assertEqual(plant_cost_breakdown(plant)['final_value'], '0.2700')
        self.assert_total_held()

    def test_a_customer_return_keeps_the_final_total(self):
        """A returned unit comes back into stock with its cost, not without it."""
        fulfillment = self.sell()

        self.return_sale(fulfillment)

        self.assertEqual(batch_cost_breakdown(self.batch)['totals']['cogs'], '0.0000')
        self.assert_total_held()

    def test_every_reversal_names_and_carries_the_layer_it_cancels(self):
        """Each correction is a balanced, valid pair, never a bare removal."""
        fulfillment = self.sell()
        reverse_fulfillment(
            fulfillment, self.user,
            operation_key=uuid4(), reason='Dispatched in error.',
        )

        reversals = CostAllocation.objects.filter(
            batch=self.batch, run__pk__gt=self.frozen_run.pk, reversal_of__isnull=False,
        )
        self.assertTrue(reversals.exists())
        for reversal in reversals:
            reversal.full_clean()
            self.assertEqual(reversal.amount, reversal.reversal_of.amount)

    def test_a_recalculation_repairs_a_layer_reversed_without_replacement(self):
        """The hole deployed batches carry is closed by an audited recalculation.

        The sale is posted through the plan as it stood before the fix, which
        reproduces the reported figures; the repair is then the ordinary
        append-only correction run, with no reopening of the batch.
        """
        with mock.patch.object(services, '_frozen_plan', frozen_plan_before_the_fix):
            self.sell()
        breakdown = batch_cost_breakdown(self.batch)
        self.assertEqual(breakdown['final_total'], '0.2700')
        self.assertEqual(breakdown['totals']['cogs'], '0.2700')

        run = recalculate_batch_costs(self.batch, self.user, 'Repost the cohort layer (task 134).')

        self.assertEqual(run.reversed_count, 0)
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '1.0800')
        self.assertEqual(self.totals_by_target()[CostAllocation.TargetType.PLANT_COHORT], Decimal('0.8100'))
        self.assert_sources_reconcile()
        self.batch.refresh_from_db()
        self.assertIsNotNone(self.batch.output_finalized_at)
