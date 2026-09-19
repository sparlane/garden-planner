"""A lost anonymous unit books its cost as production loss.

Driven through the real posting paths out of the same block of four units
worth 1.08 that `costing.test_services` sells from. Before task 136 a loss
simply stopped listing the units, so their cost re-divided over the survivors:
production loss stayed 0.0000, a promoted sibling rose from 0.2700 to 0.3600,
and a unit already dispatched rose from 0.2700 to 0.3600 in the ledger while
its fulfillment line still said 0.2700.
"""

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from unittest import mock
from uuid import uuid4

from django.core.exceptions import ValidationError

from plantings.cohorts import change_cohort, correct_cohort_loss, observe_cohort
from plantings.loss import batch_loss_by_cause
from plantings.models import CohortOperation, PlantCohort
from reporting.commerce import profitability_report
from sales.commerce import post_return
from sales.models import SalesReturnLine
from tests.factories import make_plant, make_plant_variety, make_production_batch
from workspaces.models import Workspace

from . import services, sources
from .models import CostAllocation
from .services import (
    batch_cost_breakdown,
    cohort_cost_breakdown,
    plant_cost_breakdown,
    recalculate_batch_costs,
)
from .test_services import CohortStockTestCase, CostingServiceTestCase


LossCause = CohortOperation.LossCause
Target = CostAllocation.TargetType

AUGUST = datetime(2026, 8, 15, 12, tzinfo=dt_timezone.utc)


class CohortLossCostTests(CohortStockTestCase):
    """On an open batch, a loss moves cost to loss and nothing else moves."""

    def test_a_lost_unit_takes_its_share_to_production_loss(self):
        """Verification 1: 0.8100 stays with the block, 0.2700 is lost."""
        self.lose()

        totals = self.totals_by_target()
        self.assertEqual(totals[Target.PLANT_COHORT], Decimal('0.8100'))
        self.assertEqual(totals[Target.COHORT_LOSS], Decimal('0.2700'))
        breakdown = batch_cost_breakdown(self.batch)
        self.assertEqual(breakdown['totals']['production_loss'], '0.2700')
        self.assertEqual(breakdown['provisional_total'], '1.0800')
        self.assert_sources_reconcile()

    def test_the_survivors_are_worth_what_they_were(self):
        """The block's unit value does not rise because some of it died."""
        self.lose()

        self.cohort.refresh_from_db()
        self.assertEqual(Decimal(cohort_cost_breakdown(self.cohort)['unit_value']), Decimal('0.27'))

    def test_a_promoted_sibling_keeps_its_value(self):
        """Verification 2: a different plant dying does not raise this one."""
        plant = self.promote_one()

        self.lose()

        self.assertEqual(plant_cost_breakdown(plant)['provisional_value'], '0.2700')
        self.assertEqual(self.totals_by_target()[Target.COHORT_LOSS], Decimal('0.2700'))
        self.assert_sources_reconcile()

    def test_a_dispatched_unit_keeps_the_cost_it_left_at(self):
        """Verification 3: the ledger and the fulfillment line both stay 0.2700."""
        fulfillment = self.sell()

        self.lose()

        self.assertEqual(fulfillment.lines.get().cogs_amount, Decimal('0.2700'))
        totals = self.totals_by_target()
        self.assertEqual(totals[Target.COHORT_SALE], Decimal('0.2700'))
        self.assertEqual(totals[Target.PLANT_COHORT], Decimal('0.5400'))
        self.assertEqual(batch_cost_breakdown(self.batch)['totals']['cogs'], '0.2700')
        self.assert_sources_reconcile()

    def test_a_second_recalculation_changes_nothing(self):
        """Verification 5: the loss is derived, so it cannot accumulate."""
        self.lose()

        self.assertIsNone(self.reallocate())
        self.assertIsNone(self.reallocate())
        self.assertEqual(self.totals_by_target()[Target.COHORT_LOSS], Decimal('0.2700'))

    def test_two_losses_from_one_block_share_one_layer_per_source(self):
        """A later loss adds its units to the block's loss, not a second copy."""
        self.lose()
        self.lose(cause=LossCause.CULLED)

        rows = [row for row in self.effective() if row.target_type == Target.COHORT_LOSS]
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row.amount for row in rows), Decimal('0.5400'))
        self.assert_sources_reconcile()

    def test_losing_the_whole_block_books_it_all_now(self):
        """Verification 6: the whole 1.0800 is loss before any finalization."""
        self.lose(quantity=4)

        self.batch.refresh_from_db()
        self.assertIsNone(self.batch.output_finalized_at)
        totals = self.totals_by_target()
        self.assertEqual(totals[Target.COHORT_LOSS], Decimal('1.0800'))
        self.assertNotIn(Target.PLANT_COHORT, totals)
        self.assertEqual(batch_cost_breakdown(self.batch)['totals']['production_loss'], '1.0800')
        self.assert_sources_reconcile()

    def test_a_corrected_loss_gives_the_cost_back(self):
        """The loss never happened, so neither did its production loss."""
        loss = self.lose()

        correction = self.correct(loss)

        self.assertEqual(correction.reversal_of, loss)
        self.assertEqual(self.cohort.quantity, 4)
        totals = self.totals_by_target()
        self.assertEqual(totals[Target.PLANT_COHORT], Decimal('1.0800'))
        self.assertNotIn(Target.COHORT_LOSS, totals)
        self.assertEqual(batch_loss_by_cause(self.batch)[LossCause.FAILED], 0)
        self.assert_sources_reconcile()

    def test_correcting_a_whole_block_loss_puts_the_block_back_on_sale(self):
        """An emptied block comes back in the state the loss found it in."""
        loss = self.lose(quantity=4)
        self.assertEqual(self.cohort.lifecycle_state, PlantCohort.LifecycleState.DEPLETED)

        self.correct(loss)

        self.assertEqual(self.cohort.quantity, 4)
        self.assertEqual(self.cohort.lifecycle_state, PlantCohort.LifecycleState.AVAILABLE)
        self.assertEqual(self.totals_by_target()[Target.PLANT_COHORT], Decimal('1.0800'))

    def test_one_correction_leaves_the_other_loss_standing(self):
        """Only the withdrawn loss gives its cost back."""
        first = self.lose()
        self.lose(cause=LossCause.CULLED)

        self.correct(first)

        totals = self.totals_by_target()
        self.assertEqual(totals[Target.PLANT_COHORT], Decimal('0.8100'))
        self.assertEqual(totals[Target.COHORT_LOSS], Decimal('0.2700'))
        causes = batch_loss_by_cause(self.batch)
        self.assertEqual((causes[LossCause.FAILED], causes[LossCause.CULLED]), (0, 1))

    def test_a_loss_is_corrected_once_and_only_a_loss_is_corrected(self):
        """A second correction, or one naming anything but a loss, is refused."""
        loss = self.lose()
        self.correct(loss)

        with self.assertRaises(ValidationError):
            self.correct(loss)
        made_ready = CohortOperation.objects.get(
            workspace=self.workspace, action=CohortOperation.Action.READY,
        )
        with self.assertRaises(ValidationError):
            self.correct(made_ready)

    def test_a_correction_cannot_predate_its_loss(self):
        """The history stays in the order things happened."""
        loss = self.lose(occurred_at=AUGUST)

        with self.assertRaises(ValidationError):
            correct_cohort_loss(
                self.workspace, self.user, operation_id=loss.pk, idempotency_key=uuid4(),
                occurred_at=datetime(2020, 1, 1, tzinfo=dt_timezone.utc), reason='Backdated.',
            )
        self.assertEqual(self.totals_by_target()[Target.COHORT_LOSS], Decimal('0.2700'))

    def test_a_discarded_return_is_not_corrected_here(self):
        """The write-off belongs to the customer return that recorded it."""
        fulfillment = self.sell()
        post_return(
            fulfillment.order, self.user, operation_key=uuid4(),
            items=[{
                'fulfillment_line': fulfillment.lines.get(),
                'outcome': SalesReturnLine.Outcome.DISCARDED,
                'destination': self.location,
            }],
            reason='Arrived back crushed.',
        )
        write_off = CohortOperation.objects.get(
            workspace=self.workspace, action=CohortOperation.Action.LOSS,
        )

        with self.assertRaises(ValidationError):
            self.correct(write_off)

    def test_the_correction_can_be_requested_over_rest(self):
        """The cohort endpoint names the loss by the operation in its history."""
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        loss = self.lose()
        history = self.client.get(f'/plantings/cohorts/{self.cohort.pk}/')
        self.assertIn(loss.pk, [event['operation'] for event in history.data['events']])

        response = self.client.post(f'/plantings/cohorts/{self.cohort.pk}/correct-loss/', {
            'operation': loss.pk,
            'idempotency_key': str(uuid4()),
            'reason': 'Found them behind the bench.',
        }, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['quantity'], 4)
        self.assertNotIn(Target.COHORT_LOSS, self.totals_by_target())

    def test_rest_refuses_a_loss_recorded_against_another_cohort(self):
        """The endpoint only withdraws losses from the cohort it names."""
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        loss = self.lose()
        other, _observed = observe_cohort(
            self.workspace, self.user, batch=self.batch, quantity=2, idempotency_key=uuid4(),
        )

        response = self.client.post(f'/plantings/cohorts/{other.pk}/correct-loss/', {
            'operation': loss.pk,
            'idempotency_key': str(uuid4()),
            'reason': 'Wrong block.',
        }, format='json')

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('operation', response.data)
        self.assertFalse(CohortOperation.objects.filter(reversal_of=loss).exists())
        other.refresh_from_db()
        self.assertEqual(other.quantity, 2)

    def test_rest_cannot_reach_another_workspaces_cohort(self):
        """A cohort in another workspace is not there to correct."""
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        other_workspace = Workspace.objects.create(name='Other nursery', currency_code='NZD')
        variety = make_plant_variety(workspace=other_workspace, plant=make_plant(workspace=other_workspace))
        batch = make_production_batch(workspace=other_workspace, variety=variety)
        cohort, _observed = observe_cohort(
            other_workspace, self.user, batch=batch, quantity=3, idempotency_key=uuid4(),
        )
        cohort, loss = change_cohort(
            other_workspace, self.user, cohort_id=cohort.pk, expected_revision=cohort.revision,
            action=CohortOperation.Action.LOSS, quantity=1, loss_cause=LossCause.FAILED,
            reason='Damped off.', idempotency_key=uuid4(),
        )

        response = self.client.post(f'/plantings/cohorts/{cohort.pk}/correct-loss/', {
            'operation': loss.pk,
            'idempotency_key': str(uuid4()),
            'reason': 'Not ours.',
        }, format='json')

        self.assertEqual(response.status_code, 404)
        self.assertFalse(CohortOperation.objects.filter(reversal_of=loss).exists())


class FrozenCohortLossTests(CohortStockTestCase):
    """On a finalized batch, a loss moves cost to loss and the total holds."""

    def setUp(self):
        super().setUp()
        self.finalize()

    def test_a_corrected_loss_restores_the_frozen_block(self):
        """The loss layer is reversed and its cost returns to the block."""
        loss = self.lose()

        self.correct(loss)

        totals = self.totals_by_target()
        self.assertEqual(totals[Target.PLANT_COHORT], Decimal('1.0800'))
        self.assertNotIn(Target.COHORT_LOSS, totals)
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '1.0800')
        self.assert_sources_reconcile()

    def test_a_recalculation_books_a_loss_recorded_before_the_fix(self):
        """Change 6: an audited recalculation repairs a deployed batch in place.

        The loss is posted as it was before task 136 — left out of the outputs,
        with the cohort side posted from the whole split — which reproduces task
        134's shortfall beside a frozen plant. The ordinary correction run then
        books it, without reopening.
        """
        plant = self.promote_one()
        with mock.patch.object(sources, 'lost_cohort_quantities', return_value={}), \
                mock.patch.object(services, '_redivide_around_frozen', lambda intended, *_rest: intended):
            self.lose()
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '0.9900')

        recalculate_batch_costs(self.batch, self.user, 'Book cohort losses as production loss (task 136).')

        totals = self.totals_by_target()
        self.assertEqual(totals[Target.PLANT_COHORT], Decimal('0.5400'))
        self.assertEqual(totals[Target.COHORT_LOSS], Decimal('0.2700'))
        self.assertEqual(plant_cost_breakdown(plant)['final_value'], '0.2700')
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '1.0800')
        self.assert_sources_reconcile()

    def test_the_report_values_the_loss_under_its_recorded_cause(self):
        """Verification 4: the P&L's loss by cause includes the anonymous half."""
        self.lose(cause=LossCause.CULLED, occurred_at=AUGUST)

        report = profitability_report(self.workspace, {'date_from': '2026-08-01', 'date_to': '2026-08-31'})

        summary = report.totals['currencies'][0]
        self.assertEqual(summary['production_loss'], '0.2700')
        self.assertEqual(summary['loss_by_cause']['culled'], '0.2700')
        self.assertEqual(report.totals['lost_units_by_cause']['culled'], 1)
        loss_rows = [row for row in report.rows if row['kind'] == 'production_loss']
        self.assertEqual([row['cohort_id'] for row in loss_rows], [self.cohort.pk, self.cohort.pk])
        self.assertEqual({row['occurred_at'] for row in loss_rows}, {AUGUST})

    def test_a_corrected_loss_leaves_the_report(self):
        """A withdrawn loss has neither units nor money in its period."""
        self.correct(self.lose(cause=LossCause.CULLED, occurred_at=AUGUST))

        report = profitability_report(self.workspace, {'date_from': '2026-08-01', 'date_to': '2026-08-31'})

        self.assertEqual(report.totals['lost_units'], 0)
        self.assertFalse([row for row in report.rows if row['kind'] == 'production_loss'])


class UnevenCohortLossTests(CohortStockTestCase):
    """Two blocks whose cost does not divide into whole cents.

    Six units share 1.00 of seed and 0.08 of media, so every split leaves
    leftover cents for `distribute_exactly` to hand out. A frozen promoted
    plant keeps the cent it was given at promotion, and a later split that
    hands the cents out again among more outputs can give that cent to another
    one as well: with the cohort side posted from the whole split, a loss out
    of each block left the final total at 1.0801.
    """

    def setUp(self):
        super().setUp()
        self.second, _observed = observe_cohort(
            self.workspace, self.user,
            batch=self.batch,
            quantity=2,
            idempotency_key=uuid4(),
        )
        self.finalize()

    def test_a_promotion_and_a_loss_keep_every_source_whole(self):
        """Each source still adds back up to itself, to the cent."""
        plant = self.promote_one()
        frozen_value = plant_cost_breakdown(plant)['final_value']

        self.lose()

        self.assertEqual(plant_cost_breakdown(plant)['final_value'], frozen_value)
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '1.0800')
        self.assert_sources_reconcile()

    def test_a_loss_from_each_block_keeps_every_source_whole(self):
        """Losses out of both blocks beside a frozen plant still reconcile."""
        self.promote_one()
        self.lose()
        self.second.refresh_from_db()
        change_cohort(
            self.workspace, self.user,
            cohort_id=self.second.pk,
            expected_revision=self.second.revision,
            action=CohortOperation.Action.LOSS,
            quantity=1,
            loss_cause=LossCause.LOST,
            reason='Not found at stocktake.',
            idempotency_key=uuid4(),
        )

        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '1.0800')
        self.assert_sources_reconcile()


class LegacyWholeBlockLossTests(CohortStockTestCase):
    """A block lost whole before task 136, then finalized.

    With the loss not an output, finalization found nowhere to put the batch's
    cost and retired all 1.0800 into the pool `PRODUCTION_LOSS`, undated and
    uncaused. A recalculation after the fix kept that pool row as frozen and
    posted 0.0000 cohort-loss layers beside it, so a later correction restored
    four units worth nothing while 1.0800 stayed in loss.
    """

    def setUp(self):
        super().setUp()
        with mock.patch.object(sources, 'lost_cohort_quantities', return_value={}), \
                mock.patch.object(services, '_reclaimed_losses', lambda *_args: set()), \
                mock.patch.object(services, '_redivide_around_frozen', lambda intended, *_rest: intended):
            self.loss = self.lose(quantity=4)
            self.finalize()

    def test_the_legacy_state_is_all_pool_loss(self):
        """The reproduction: every cent of the batch sits in the pool."""
        totals = self.totals_by_target()
        self.assertEqual(totals, {Target.PRODUCTION_LOSS: Decimal('1.0800')})

    def test_a_recalculation_moves_the_pool_loss_onto_the_block(self):
        """The loss is dated and caused by the block it came out of."""
        recalculate_batch_costs(self.batch, self.user, 'Book cohort losses as production loss (task 136).')

        totals = self.totals_by_target()
        self.assertEqual(totals, {Target.COHORT_LOSS: Decimal('1.0800')})
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '1.0800')
        self.assert_sources_reconcile()
        self.assertIsNone(self.reallocate())

    def test_correcting_it_afterwards_restores_the_block_at_full_value(self):
        """The four units come back worth what they cost, not nothing."""
        recalculate_batch_costs(self.batch, self.user, 'Book cohort losses as production loss (task 136).')

        self.correct(self.loss)

        self.assertEqual(self.cohort.quantity, 4)
        self.assertEqual(self.totals_by_target(), {Target.PLANT_COHORT: Decimal('1.0800')})
        self.assertEqual(batch_cost_breakdown(self.batch)['totals']['production_loss'], '0.0000')
        self.assert_sources_reconcile()
        self.assertIsNone(self.reallocate())


class LateCohortLossTests(CostingServiceTestCase):
    """A batch finalized with nothing up keeps its pool loss.

    Everything became pool loss at finalization because nothing had been
    observed. A block first recorded afterwards and then lost claims none of
    it: whether a late arrival may take a frozen batch's cost is task 147's.
    """

    def test_a_block_observed_after_finalization_claims_no_pool_loss(self):
        """The pool loss stays exactly where finalization put it."""
        self.sow([(self.cells[0], 4)])
        self.apply_media([self.cells[0]], '0.04')
        self.finalize()
        self.assertEqual(self.totals_by_target(), {Target.PRODUCTION_LOSS: Decimal('1.0800')})
        cohort, _observed = observe_cohort(
            self.workspace, self.user, batch=self.batch, quantity=2, idempotency_key=uuid4(),
        )

        change_cohort(
            self.workspace, self.user, cohort_id=cohort.pk, expected_revision=cohort.revision,
            action=CohortOperation.Action.LOSS, quantity=2, loss_cause=LossCause.FAILED,
            reason='Damped off.', idempotency_key=uuid4(),
        )

        totals = self.totals_by_target()
        self.assertEqual(totals[Target.PRODUCTION_LOSS], Decimal('1.0800'))
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '1.0800')
        self.assert_sources_reconcile()
