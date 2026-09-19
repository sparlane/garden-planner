"""A recount books what it did not find as loss, and what it found at no cost.

Driven through the real posting paths out of the same block of four units
worth 1.08 that `costing.test_services` sells from. Before task 149 a count
adjustment set the block to the counted quantity and recorded nothing else, so
a unit counted away stopped being an output and its cost re-divided over what
was left. With one unit sold at 0.2700 and one promoted, recounting the
remaining two to one read `cohort_sale` 0.3600 on an open batch and 0.4050 on
a finalized one, with production loss 0.0000. A count that came up higher
diluted everything already dispatched the same way.
"""

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from uuid import uuid4

from plantings.cohorts import change_cohort, split_cohort
from plantings.loss import batch_loss_by_cause
from plantings.models import CohortOperation
from reporting.commerce import profitability_report
from workspaces.models import Workspace

from .models import CostAllocation
from .services import (
    batch_cost_breakdown,
    cohort_cost_breakdown,
    plant_cost_breakdown,
    recalculate_batch_costs,
)
from .test_services import CohortStockTestCase


Action = CohortOperation.Action
Target = CostAllocation.TargetType

AUGUST = datetime(2026, 8, 15, 12, tzinfo=dt_timezone.utc)


def recount(case, quantity, occurred_at=None, **extra):
    """Reconcile the case's block to a physical count, and return the operation."""
    case.cohort.refresh_from_db()
    case.cohort, operation = change_cohort(
        case.workspace, case.user,
        cohort_id=case.cohort.pk,
        expected_revision=case.cohort.revision,
        action=Action.ADJUST,
        quantity=quantity,
        occurred_at=occurred_at,
        reason='Counted the bench.',
        idempotency_key=uuid4(),
        **extra,
    )
    return operation


def sell_promote_and_recount_short(case):
    """Reproduce the task's figures: sold one, promoted one, counted one of two."""
    fulfillment = case.sell()
    plant = case.promote_one()
    recount(case, 1)
    return fulfillment, plant


def assert_dispatch_kept_its_cost(case, fulfillment):
    """The ledger's cost of sale is still what the fulfillment line recorded."""
    case.assertEqual(fulfillment.lines.get().cogs_amount, Decimal('0.2700'))
    case.assertEqual(case.totals_by_target()[Target.COHORT_SALE], Decimal('0.2700'))


class ShortRecountTests(CohortStockTestCase):
    """On an open batch, a unit counted away is a loss and nothing else moves."""

    def test_the_dispatched_unit_and_the_promoted_plant_keep_their_cost(self):
        """Criterion 1: 0.2700 each, where the ledger read 0.3600."""
        fulfillment, plant = sell_promote_and_recount_short(self)

        assert_dispatch_kept_its_cost(self, fulfillment)
        self.assertEqual(plant_cost_breakdown(plant)['provisional_value'], '0.2700')
        self.assert_sources_reconcile()

    def test_the_missing_unit_is_production_loss(self):
        """Criterion 2: 0.2700 moves to loss, and the block keeps one unit's worth."""
        sell_promote_and_recount_short(self)

        totals = self.totals_by_target()
        self.assertEqual(totals[Target.COHORT_LOSS], Decimal('0.2700'))
        self.assertEqual(totals[Target.PLANT_COHORT], Decimal('0.2700'))
        breakdown = batch_cost_breakdown(self.batch)
        self.assertEqual(breakdown['totals']['production_loss'], '0.2700')
        self.assertEqual(breakdown['provisional_total'], '1.0800')

    def test_the_shortfall_is_recorded_as_lost_at_stocktake(self):
        """The recount is recorded as the loss it found, under `lost`."""
        operation = recount(self, 3)

        self.assertEqual(operation.action, Action.LOSS)
        self.assertEqual(operation.loss_cause, CohortOperation.LossCause.LOST)
        self.assertEqual(operation.payload['quantity'], 3)
        self.assertEqual(operation.payload['shortfall'], 1)
        self.assertEqual(operation.events.get().quantity_delta, -1)
        self.assertEqual(batch_loss_by_cause(self.batch)['lost'], 1)

    def test_a_replayed_recount_is_the_same_loss(self):
        """Idempotency still recognises the request, though it recorded a loss."""
        request = {
            'cohort_id': self.cohort.pk,
            'expected_revision': self.cohort.revision,
            'action': Action.ADJUST,
            'quantity': 3,
            'reason': 'Counted the bench.',
            'idempotency_key': uuid4(),
        }
        _cohort, first = change_cohort(self.workspace, self.user, **request)

        _cohort, replayed = change_cohort(self.workspace, self.user, **request)

        self.assertEqual(replayed.pk, first.pk)
        self.cohort.refresh_from_db()
        self.assertEqual(self.cohort.quantity, 3)

    def test_a_count_that_matches_records_no_loss(self):
        """Only a shortfall is a loss; an unchanged count is still an adjustment."""
        operation = recount(self, 4)

        self.assertEqual(operation.action, Action.ADJUST)
        self.assertEqual(operation.loss_cause, '')
        self.assertNotIn(Target.COHORT_LOSS, self.totals_by_target())

    def test_the_shortfall_can_be_corrected(self):
        """A miscount is withdrawn like any loss, and its cost comes back."""
        fulfillment, _plant = sell_promote_and_recount_short(self)
        shortfall = CohortOperation.objects.get(action=Action.LOSS)

        self.correct(shortfall)

        totals = self.totals_by_target()
        self.assertNotIn(Target.COHORT_LOSS, totals)
        self.assertEqual(totals[Target.PLANT_COHORT], Decimal('0.5400'))
        assert_dispatch_kept_its_cost(self, fulfillment)
        self.assert_sources_reconcile()

    def test_a_second_recalculation_changes_nothing(self):
        """Criterion 4: the shortfall is derived, so it cannot accumulate."""
        sell_promote_and_recount_short(self)

        self.assertIsNone(self.reallocate())
        self.assertIsNone(self.reallocate())
        self.assertEqual(self.totals_by_target()[Target.COHORT_LOSS], Decimal('0.2700'))

    def test_a_short_count_over_rest_is_recorded_as_a_loss(self):
        """The cohort screen's reconcile-count endpoint reaches the same rule."""
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        self.cohort.refresh_from_db()

        response = self.client.post(f'/plantings/cohorts/{self.cohort.pk}/adjust/', {
            'expected_revision': self.cohort.revision,
            'quantity': 3,
            'idempotency_key': str(uuid4()),
            'reason': 'Counted the bench.',
        }, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['quantity'], 3)
        recorded = [(event['action'], event['loss_cause']) for event in response.data['events']]
        self.assertIn((Action.LOSS, CohortOperation.LossCause.LOST), recorded)
        self.assertEqual(self.totals_by_target()[Target.COHORT_LOSS], Decimal('0.2700'))


class FoundRecountTests(CohortStockTestCase):
    """A unit counted into being carries no cost of its own.

    Only the units standing in the block share their value with it; what was
    already dispatched, lost or promoted keeps the cost it left with.
    """

    def test_a_dispatched_unit_keeps_its_cost(self):
        """Criterion 3: two found units dilute the three still standing, not the sale."""
        fulfillment = self.sell()

        recount(self, 5)

        assert_dispatch_kept_its_cost(self, fulfillment)
        totals = self.totals_by_target()
        self.assertEqual(totals[Target.PLANT_COHORT], Decimal('0.8100'))
        self.assertNotIn(Target.COHORT_LOSS, totals)
        self.cohort.refresh_from_db()
        self.assertEqual(Decimal(cohort_cost_breakdown(self.cohort)['unit_value']), Decimal('0.162'))
        self.assert_sources_reconcile()

    def test_a_unit_sold_after_the_count_leaves_at_the_diluted_value(self):
        """The next dispatch is charged what a standing unit is now worth, and keeps it."""
        first = self.sell()
        recount(self, 5)

        second = self.sell()

        self.assertEqual(first.lines.get().cogs_amount, Decimal('0.2700'))
        self.assertEqual(second.lines.get().cogs_amount, Decimal('0.1620'))
        totals = self.totals_by_target()
        self.assertEqual(totals[Target.COHORT_SALE], Decimal('0.4320'))
        self.assertEqual(totals[Target.PLANT_COHORT], Decimal('0.6480'))
        self.assert_sources_reconcile()

    def test_a_promoted_sibling_keeps_its_value(self):
        """A plant promoted before the count is not diluted by it."""
        plant = self.promote_one()

        recount(self, 5)

        self.assertEqual(plant_cost_breakdown(plant)['provisional_value'], '0.2700')
        self.assertEqual(self.totals_by_target()[Target.PLANT_COHORT], Decimal('0.8100'))

    def test_a_loss_after_the_count_takes_the_diluted_value(self):
        """A unit lost from the diluted block takes only what a standing unit held."""
        recount(self, 6)

        self.lose(quantity=2)

        totals = self.totals_by_target()
        self.assertEqual(totals[Target.COHORT_LOSS], Decimal('0.3600'))
        self.assertEqual(totals[Target.PLANT_COHORT], Decimal('0.7200'))
        self.assert_sources_reconcile()

    def test_a_split_carries_the_diluted_value(self):
        """Units moved into a new block keep what they were worth in the old one."""
        self.sell()
        recount(self, 5)
        self.cohort.refresh_from_db()

        child, _split = split_cohort(
            self.workspace, self.user,
            cohort_id=self.cohort.pk,
            expected_revision=self.cohort.revision,
            quantity=2,
            idempotency_key=uuid4(),
            reason='Spaced them out.',
        )

        self.assertEqual(cohort_cost_breakdown(child)['provisional_value'], '0.3240')
        self.assertEqual(self.totals_by_target()[Target.COHORT_SALE], Decimal('0.2700'))
        self.assert_sources_reconcile()

    def test_a_second_recalculation_changes_nothing(self):
        """Criterion 4: the weights are replayed from history, not accumulated."""
        self.sell()
        recount(self, 5)
        self.sell()

        self.assertIsNone(self.reallocate())
        self.assertIsNone(self.reallocate())


class FrozenRecountTests(CohortStockTestCase):
    """On a finalized batch, the frozen plant and the dispatched unit both hold."""

    def setUp(self):
        super().setUp()
        self.finalize()

    def test_a_short_recount_keeps_the_sale_and_the_plant(self):
        """Criterion 1: 0.2700 each, where the ledger read 0.4050 for the sale."""
        fulfillment, plant = sell_promote_and_recount_short(self)

        assert_dispatch_kept_its_cost(self, fulfillment)
        self.assertEqual(plant_cost_breakdown(plant)['final_value'], '0.2700')
        totals = self.totals_by_target()
        self.assertEqual(totals[Target.COHORT_LOSS], Decimal('0.2700'))
        self.assertEqual(totals[Target.PLANT_COHORT], Decimal('0.2700'))
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '1.0800')
        self.assert_sources_reconcile()
        self.assertIsNone(self.reallocate())

    def test_the_report_counts_the_shortfall_as_lost(self):
        """Criterion 2: the P&L values the missing unit under `lost`."""
        self.sell()
        self.promote_one()
        recount(self, 1, occurred_at=AUGUST)

        report = profitability_report(self.workspace, {'date_from': '2026-08-01', 'date_to': '2026-08-31'})

        summary = report.totals['currencies'][0]
        self.assertEqual(summary['production_loss'], '0.2700')
        self.assertEqual(summary['loss_by_cause']['lost'], '0.2700')
        self.assertEqual(report.totals['lost_units_by_cause']['lost'], 1)

    def test_found_units_keep_the_sale_and_the_total(self):
        """Criterion 3 on a frozen batch: the sale holds and the total does not move."""
        fulfillment = self.sell()
        plant = self.promote_one()

        recount(self, 4)

        assert_dispatch_kept_its_cost(self, fulfillment)
        self.assertEqual(plant_cost_breakdown(plant)['final_value'], '0.2700')
        self.assertEqual(self.totals_by_target()[Target.PLANT_COHORT], Decimal('0.5400'))
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '1.0800')
        self.assert_sources_reconcile()


class LegacyShortRecountTests(CohortStockTestCase):
    """A downward count recorded as a bare adjustment, as it was before task 149.

    Such a count is not reinterpreted as a loss: nobody recorded one, and no
    cause was given. The recalculation stops it moving what already left,
    though — the units simply withdraw from the block and their cost stays on
    the units still standing there.
    """

    def setUp(self):
        super().setUp()
        self.fulfillment = self.sell()
        self.plant = self.promote_one()
        recount(self, 1, shortfall_is_loss=False)

    def test_the_bare_adjustment_is_recorded_as_one(self):
        """The old shape: an adjustment, no cause, no loss layer."""
        self.assertFalse(CohortOperation.objects.filter(action=Action.LOSS).exists())
        self.assertNotIn(Target.COHORT_LOSS, self.totals_by_target())

    def test_a_recalculation_leaves_what_left_at_its_cost(self):
        """Deployed data: the sale and the plant hold, the survivor carries the rest."""
        recalculate_batch_costs(self.batch, self.user, 'Stop recounts moving what left (task 149).')

        assert_dispatch_kept_its_cost(self, self.fulfillment)
        self.assertEqual(plant_cost_breakdown(self.plant)['provisional_value'], '0.2700')
        self.assertEqual(self.totals_by_target()[Target.PLANT_COHORT], Decimal('0.5400'))
        self.assertEqual(batch_cost_breakdown(self.batch)['totals']['production_loss'], '0.0000')
        self.assert_sources_reconcile()
