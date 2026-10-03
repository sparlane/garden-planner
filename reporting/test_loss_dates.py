"""Production loss reported in the period it happened in (task 143).

`_loss_rows` used to date a pool loss by `layer.created`, the stamp of the run
that wrote the row. Allocations are immutable, so a correction is a reversal
plus a replacement with a fresh stamp, and the report reads only the surviving
row: a batch recalculated in September took its March loss out of March and
published it in September instead. Task 163 put `effective_at` on the layer —
the day the fact behind it happened — and these say the report reads that.

Driven through the real posting paths, as `costing.test_services` is: a
germination closed on a stated day, a tray cleaned on one, a batch finalized,
and a block's loss recorded with its own date. The fixture's own history is
settled back to January first, so every fact below is months away from the run
that writes its layer and a period dated by the run is a different month from
one dated by the fact.
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from itertools import count
from unittest.mock import patch
from uuid import uuid4

from django.utils import timezone

from costing.models import CostAllocation
from costing.services import recalculate_batch_costs
from costing.test_effective_dates import EARLY, SETTLED, settle
from costing.test_services import CohortStockTestCase, CostingServiceTestCase
from plantings.cohorts import correct_cohort_loss
from plantings.germination import close_germination
from plantings.lifecycle import EventType, OutcomeRequest, record_lifecycle_event
from plantings.models import CohortOperation, SpecificPlant, SpecificPlantLocation
from seedtrays.generations import (
    CloseRequest,
    Disposition,
    MediaDisposition,
    SeedDisposition,
    close_generation,
    generation_contents,
)

from .commerce import profitability_report


#: The month `EARLY` falls in: the period every loss below belongs to.
MARCH = ('2026-03-01', '2026-03-31')

#: The controlled clock's month, which is the period every loss below was
#: reported in while `created` was the date.
RUN_MONTH = ('2026-09-01', '2026-09-30')


def start_reporting_clock(case):
    """Keep postings in September regardless of when the suite is run."""
    instant = datetime(2026, 9, 15, tzinfo=dt_timezone.utc)
    ticks = count()
    # Advance on each call so finalization and layer creation remain distinct
    # instants, as required by the finalization-date regression below.
    clock = patch(
        'django.utils.timezone.now',
        side_effect=lambda: instant + timedelta(microseconds=next(ticks)),
    )
    clock.start()
    case.addCleanup(clock.stop)


class LossPeriodTestCase(CostingServiceTestCase):
    """Read the profitability report's loss rows over a stated period."""

    def setUp(self):
        start_reporting_clock(self)
        super().setUp()
        # Fixed so a month is a month: every boundary below is a UTC one.
        self.workspace.timezone = 'UTC'
        self.workspace.save(update_fields=['timezone'])

    def report(self, period):
        """Return the whole report for one closed range of local days."""
        return profitability_report(self.workspace, {
            'date_from': period[0], 'date_to': period[1],
        })

    def losses(self, period):
        """Return (instant, amount, cause) for the period's loss rows."""
        return [
            (row['occurred_at'], row['production_loss'], row['loss_cause'])
            for row in self.report(period).rows
            if row['kind'] == 'production_loss'
        ]

    def loss_total(self, period):
        """Return the money one period reports as production loss."""
        return sum(
            (Decimal(amount) for _when, amount, _cause in self.losses(period)),
            Decimal('0'),
        )

    def pool_loss(self):
        """Return the batch's standing pool production-loss layer."""
        return CostAllocation.objects.get(
            batch=self.batch,
            target_type=CostAllocation.TargetType.PRODUCTION_LOSS,
            reversal_of=None, reversal__isnull=True,
        )

    def close_sowing(self, sowing, closed_at):
        """Declare one sowing finished germinating on a stated day."""
        return close_germination(
            sowing, self.user, closed_at=closed_at,
            loss_cause=CohortOperation.LossCause.FAILED,
            reason='The window has passed.',
        )

    def clean_tray(self, occurred_at):
        """Empty the tray on a stated day, tipping out what is left in it."""
        contents = generation_contents(self.generation)
        return close_generation(self.generation, self.user, CloseRequest(
            reason='End of the propagation run.',
            occurred_at=occurred_at,
            seeds=tuple(
                SeedDisposition(
                    row['sowing'].pk, row['quantity'], Disposition.REMOVED,
                    'Swept up.',
                )
                for row in contents['seeds']
            ),
            media=tuple(
                MediaDisposition(
                    row['lot'].pk, row['base_quantity'], Disposition.WASTE,
                    'Tipped out.',
                )
                for row in contents['media']
            ),
        ))


class PoolLossDateTests(LossPeriodTestCase):
    """A loss with no plant to date it by is dated by the fact that retired it.

    The three are the ones task 143 names: ungerminated seed retired at a
    germination closure, a media remainder discarded by a clean, and whatever
    finalization never placed. Each has a real date on a real record, and none
    of them is the day somebody's recalculation ran.
    """

    def test_ungerminated_seed_is_reported_in_the_month_the_closure_retired_it(self):
        """Four clusters at 0.25 each: 1.0000 of loss, in March and not now."""
        sowing = self.sow([(self.cells[0], 4)])
        settle(self)

        self.close_sowing(sowing, EARLY)

        layer = self.pool_loss()
        self.assertEqual(layer.effective_at, EARLY)
        self.assertGreater(layer.created, layer.effective_at)
        self.assertEqual(self.losses(MARCH), [(EARLY, '1.0000', None)])
        self.assertEqual(self.losses(RUN_MONTH), [])

    def test_a_discarded_media_remainder_is_reported_when_the_clean_tipped_it_out(self):
        """0.08 litres at 2.00 a litre: 0.1600 of media, dated by the clean.

        The tray is cleaned after the batch says no more seedlings are coming,
        which is the ordinary order and the one that makes the remainder loss
        outright. Cleaned first, the remainder is unresolved cost rather than
        loss until the freeze declares it, and it is then the freeze's date
        that places it; the layer still carries the clean's, and task 143's
        note records the difference.
        """
        self.sow([(self.cells[0], 4)])
        self.apply_media(self.cells, '0.08')
        settle(self)
        self.finalize()

        self.clean_tray(EARLY)

        residual = CostAllocation.objects.get(
            batch=self.batch,
            source_type=CostAllocation.SourceType.GENERATION_RESIDUAL,
            target_type=CostAllocation.TargetType.PRODUCTION_LOSS,
            reversal_of=None, reversal__isnull=True,
        )
        self.assertEqual(residual.effective_at, EARLY)
        self.assertGreater(residual.created, residual.effective_at)
        self.assertEqual(residual.amount, Decimal('0.1600'))
        self.assertEqual(self.losses(MARCH), [(EARLY, '0.1600', None)])
        self.assertEqual(
            [when for when, _amount, _cause in self.losses(RUN_MONTH)],
            [self.batch.output_finalized_at, self.batch.output_finalized_at],
        )

    def test_cost_retired_at_finalization_is_dated_by_the_finalization(self):
        """To the instant: `output_finalized_at`, not the run's own stamp.

        The freeze runs inside the same transaction that stamps the batch, so
        the two land in one month either way; the instant is what tells them
        apart, and `created` is never the batch's.
        """
        self.sow([(self.cells[0], 4)])
        settle(self)

        self.finalize()

        layer = self.pool_loss()
        self.assertEqual(layer.effective_at, self.batch.output_finalized_at)
        self.assertNotEqual(layer.created, self.batch.output_finalized_at)
        self.assertEqual(
            self.losses(RUN_MONTH),
            [(self.batch.output_finalized_at, '1.0000', None)],
        )

    def test_a_culled_plant_is_still_dated_by_the_cull(self):
        """The half the report already had right, held where it was.

        The one seedling that came up carries the whole cell: nothing has
        said the other three clusters are finished, so their cost is still
        riding on the plant that did come up, and all 1.0000 of it is loss on
        the day of the cull.
        """
        sowing = self.sow([(self.cells[0], 4)])
        plant = self.germinate(sowing, self.cells[0])[0]
        self.reallocate()
        # The seedling has to have been standing in its cell before it can be
        # culled out of it, so it comes up with the rest of the fixture.
        SpecificPlant.objects.filter(pk=plant.pk).update(germinated=SETTLED)
        SpecificPlantLocation.objects.filter(specific_plant=plant).update(started=SETTLED)
        settle(self)

        record_lifecycle_event(plant, self.user, OutcomeRequest(
            EventType.CULLED, occurred_at=EARLY, reason='Too weak to sell.',
        ))

        self.assertEqual(self.losses(MARCH), [(EARLY, '1.0000', 'culled')])
        self.assertEqual(self.losses(RUN_MONTH), [])


class LossRecalculationTests(LossPeriodTestCase):
    """A closed period's reported loss survives a later recalculation.

    The invariant task 143 asks for, and the promise `costing.models` opens
    with: what was reported last month stays readable next to its correction.
    """

    def setUp(self):
        super().setUp()
        self.sowing = self.sow([(self.cells[0], 4)])
        settle(self)
        self.close_sowing(self.sowing, EARLY)

    def test_a_recalculation_with_no_new_facts_changes_no_period(self):
        """Nothing to repost, so nothing to re-date: March keeps its 1.0000."""
        self.assertEqual(self.loss_total(MARCH), Decimal('1.0000'))

        run = recalculate_batch_costs(self.batch, self.user, 'Nothing has changed.')

        self.assertIsNone(run)
        self.assertEqual(self.losses(MARCH), [(EARLY, '1.0000', None)])
        self.assertEqual(self.losses(RUN_MONTH), [])

    def test_a_later_posting_leaves_the_closed_period_where_it_was(self):
        """A run that really writes layers, months after the loss happened.

        Media going on a cell that raised nothing is loss of its own, and it
        is this month's: 0.0800 here, against March's 1.0000. Dated by the
        run, both would have been reported together in whatever month the
        posting happened in. The freeze is what declares the media loss, and
        it leaves the closure's seed exactly where the closure put it.
        """
        self.apply_media([self.cells[0]], '0.04')
        self.finalize()

        self.assertEqual(self.losses(MARCH), [(EARLY, '1.0000', None)])
        self.assertEqual(self.loss_total(RUN_MONTH), Decimal('0.0800'))
        self.assertEqual(
            self.loss_total(MARCH) + self.loss_total(RUN_MONTH), Decimal('1.0800'),
        )


class LossUnitReconciliationTests(CohortStockTestCase):
    """The money and the unit counts of one period describe one population.

    `_lost_units` has always dated its counts from the events behind them, so
    a pool loss dated by the run was money in a period whose units were
    counted in another. The caused money is what reconciles against the
    counts; a pool loss carries no cause because ungerminated seed and a
    tipped-out remainder were never units anybody could count.
    """

    def setUp(self):
        start_reporting_clock(self)
        super().setUp()
        self.workspace.timezone = 'UTC'
        self.workspace.save(update_fields=['timezone'])
        settle(self)

    def totals(self, period):
        """Return the period's totals and its single currency's summary."""
        report = profitability_report(self.workspace, {
            'date_from': period[0], 'date_to': period[1],
        })
        currencies = report.totals['currencies']
        return report.totals, currencies[0] if currencies else None

    def test_the_caused_money_and_the_unit_counts_cover_the_same_losses(self):
        """One of four units at 1.0800: 0.2700 of failure, and one unit."""
        self.lose(occurred_at=EARLY)
        self.finalize()

        totals, summary = self.totals(MARCH)
        self.assertEqual(totals['lost_units_by_cause']['failed'], 1)
        self.assertEqual(totals['lost_units'], 1)
        self.assertEqual(summary['production_loss'], '0.2700')
        self.assertEqual(summary['loss_by_cause']['failed'], '0.2700')
        self.assertEqual(
            sum(Decimal(value) for value in summary['loss_by_cause'].values()),
            Decimal(summary['production_loss']),
        )

    def test_the_period_the_loss_was_recorded_in_counts_neither(self):
        """Both halves stay in March when the batch is recalculated here."""
        self.lose(occurred_at=EARLY)
        self.finalize()
        recalculate_batch_costs(self.batch, self.user, 'Check the figures again.')

        totals, summary = self.totals(RUN_MONTH)
        self.assertEqual(totals['lost_units'], 0)
        self.assertIsNone(summary)
        self.assertEqual(self.totals(MARCH)[1]['production_loss'], '0.2700')


class UndatedLossTests(CohortStockTestCase):
    """Loss no recorded fact dates is published, not dropped.

    After task 163 a pool loss always carries a date, so the one loss left
    that nothing dates is a block's: its recorded losses have all been
    corrected while its loss layer still stands, which is a batch whose cost
    has not been recalculated against its own corrected facts. The report used
    to drop that money out of every period without saying so.
    """

    def setUp(self):
        start_reporting_clock(self)
        super().setUp()
        self.workspace.timezone = 'UTC'
        self.workspace.save(update_fields=['timezone'])
        settle(self)
        self.operation = self.lose(occurred_at=EARLY)
        self.finalize()

    def correct_without_recosting(self):
        """Withdraw the loss but leave the batch's layers where they are."""
        with patch('plantings.cohorts.reallocate_batch', return_value=None):
            correct_cohort_loss(
                self.workspace, self.user,
                operation_id=self.operation.pk,
                idempotency_key=uuid4(),
                occurred_at=timezone.now(),
                reason='The count was wrong.',
            )

    def report(self, period):
        """Return the whole report over one closed range of local days."""
        return profitability_report(self.workspace, {
            'date_from': period[0], 'date_to': period[1],
        })

    def test_a_loss_layer_with_no_recorded_loss_left_is_named_not_dropped(self):
        """0.2700 that was March's is in no period, and the report says so.

        Two layers, because a block's loss carries a share of each source the
        batch drew on: 0.2500 of seed and 0.0200 of media.
        """
        self.assertEqual(self.report(MARCH).totals['currencies'][0]['production_loss'], '0.2700')

        self.correct_without_recosting()

        report = self.report(MARCH)
        self.assertEqual(
            [row['kind'] for row in report.rows if row['kind'] == 'production_loss'], [],
        )
        self.assertEqual(report.totals['undated_loss_layers'], 2)
        finding = {row['code']: row for row in report.data_quality}['undated_loss']
        self.assertEqual(finding['count'], 2)
        self.assertFalse(report.totals['finalized_margin_available'])
