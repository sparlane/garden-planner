"""Anonymous stock on a bench is reported as plant inventory.

Driven through the real posting paths out of the same block of four units
worth 1.08 that `costing.test_services` sells from. Before task 137 a
`PLANT_COHORT` layer fell through `_bucket_of` into `unresolved`, so a finalized
batch with one unit promoted reported `final_total 1.0800` as
`{plant_inventory 0.2700, unresolved 0.8100}` — unresolved cost on a batch
whose finalization exists to guarantee there is none.
"""

from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from tests.factories import quarantine_stock

from .allocation import Share
from .models import CostAllocation
from .services import (
    _bucket_of,
    _resolve_for_freeze,
    batch_cost_breakdown,
    cohort_cost_breakdown,
)
from .test_services import CohortStockTestCase


Target = CostAllocation.TargetType


class FrozenBucketInvariantTests(SimpleTestCase):
    """Once output is final, no target type is left reporting unresolved cost.

    Stated over every target type rather than the ones in use today, so a new
    target that falls through `_bucket_of`, or one `_resolve_for_freeze` stops
    retiring, fails here rather than surfacing as unresolved cost on a
    finalized batch.
    """

    def test_no_target_type_is_unresolved_after_the_freeze(self):
        """Every share a frozen batch keeps lands in a resolved bucket."""
        shares = [
            Share(key=target, target_type=target, weight=Decimal('1'), basis=CostAllocation.Basis.DIRECT)
            for target in Target.values
        ]
        for share in _resolve_for_freeze(shares, frozen=True):
            row = SimpleNamespace(target_type=share.target_type, specific_plant_id=None, plant_cohort_id=None)
            with self.subTest(target=share.target_type):
                self.assertNotEqual(_bucket_of(row, {}, {}), 'unresolved')


class CohortBucketTests(CohortStockTestCase):
    """The units a block still holds are stock on hand, like a promoted plant."""

    def assert_held_as_inventory(self, total_field='provisional_total'):
        """Assert the whole 1.08 is plant inventory and none of it unresolved."""
        breakdown = batch_cost_breakdown(self.batch)
        self.assertEqual(breakdown[total_field], '1.0800')
        self.assertEqual(breakdown['totals']['plant_inventory'], '1.0800')
        self.assertEqual(breakdown['totals']['unresolved'], '0.0000')

    def test_an_anonymous_block_is_plant_inventory(self):
        """Verification 1: four units worth 1.0800 report 1.0800 as inventory."""
        self.assertEqual(self.totals_by_target()[Target.PLANT_COHORT], Decimal('1.0800'))
        self.assert_held_as_inventory()

    def test_a_half_promoted_block_reports_both_halves_as_inventory(self):
        """Verification 2: the same plants under two bookkeepings, one bucket."""
        self.promote_one()
        self.promote_one()

        totals = self.totals_by_target()
        self.assertEqual(totals[Target.PLANT_COHORT], Decimal('0.5400'))
        self.assertEqual(totals[Target.SPECIFIC_PLANT], Decimal('0.5400'))
        self.assert_held_as_inventory()

    def test_a_quarantined_block_is_still_inventory(self):
        """Verification 3: quarantine is an overlay, as it is for a plant."""
        quarantine_stock(self.workspace, self.user, [{'type': 'cohort', 'id': self.cohort.pk}])

        self.assert_held_as_inventory()

    def test_a_finalized_block_reports_nothing_unresolved(self):
        """Verification 4: the reported 0.2700 / 0.8100 split is all inventory."""
        self.finalize()
        self.promote_one()

        self.assert_held_as_inventory(total_field='final_total')

    def test_sold_and_lost_units_keep_their_own_buckets(self):
        """Only the units still standing move; cogs and loss are unchanged."""
        self.sell()
        self.lose()

        totals = batch_cost_breakdown(self.batch)['totals']
        self.assertEqual(totals['plant_inventory'], '0.5400')
        self.assertEqual(totals['cogs'], '0.2700')
        self.assertEqual(totals['production_loss'], '0.2700')
        self.assertEqual(totals['unresolved'], '0.0000')

    def test_the_block_breakdown_is_unaffected(self):
        """Verification 6: one block's own figures never read the buckets."""
        self.cohort.refresh_from_db()
        breakdown = cohort_cost_breakdown(self.cohort)
        self.assertEqual(Decimal(breakdown['unit_value']), Decimal('0.27'))

    def test_the_production_report_publishes_the_block_as_inventory(self):
        """The per-batch report reads the same buckets, so it moves with them."""
        self.finalize()
        self.workspace.mode = self.workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])

        response = self.client.get('/reports/production-batches/', {'batch': self.batch.pk})

        self.assertEqual(response.status_code, 200)
        row = response.data['results'][0]
        self.assertEqual(row['final_total'], '1.0800')
        self.assertEqual(row['plant_inventory_value'], '1.0800')
        self.assertEqual(row['unresolved_value'], '0.0000')
