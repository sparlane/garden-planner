"""Production loss reported in the period it happened in (task 143).

`_loss_rows` used to date a pool loss by `layer.created`, the stamp of the run
that wrote the row. Allocations are immutable, so a correction is a reversal
plus a replacement with a fresh stamp, and the report reads only the surviving
row: a batch recalculated in September took its March loss out of March and
published it in September instead. Task 163 put `effective_at` on the layer —
the day the fact behind it happened — and these say the report reads that,
except where the only thing that declared the cost lost was the freeze.

Driven through the real posting paths, as `costing.test_services` is: a
germination closed on a stated day, a tray cleaned on one, a batch finalized,
an input applied on one and posted on another, and a block's loss recorded with
its own date. The fixture's own history is settled back to January first, so
every fact below is months away from the run that writes its layer.

The posting clock is held in September, independently of the real run date.
The reporting period is derived from that controlled clock, and each test also
states the losses the batch reports over every period at once, so a figure
cannot hide in a month nobody named.
"""

# `setUp` is camel case because unittest calls it that, and pylint only
# recognises the name inside a TestCase — which `LossPeriodMixin` is
# deliberately not. Combining it with a costing fixture is also one ancestor
# more than pylint allows by default, and the fixture is what makes these tests
# run through the real posting paths.
# pylint: disable=invalid-name,too-many-ancestors

from calendar import monthrange
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from itertools import count
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

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


#: The month `EARLY` falls in: the period every back-dated loss below belongs
#: to. Written down because it is a property of `EARLY`, not of the clock.
MARCH = ('2026-03-01', '2026-03-31')


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


class LossPeriodMixin:
    """Read the profitability report's loss rows over a stated period."""

    def setUp(self):
        """Control the posting clock and put every fixture's periods in UTC."""
        start_reporting_clock(self)
        super().setUp()
        # Every period boundary below is then a UTC one, as it is in
        # `reporting.test_commerce`, and the clock the run month is derived
        # from is read in the same zone.
        self.workspace.timezone = 'UTC'
        self.workspace.save(update_fields=['timezone'])

    def local_today(self):
        """Return today in the workspace's zone, which is where periods live."""
        return timezone.now().astimezone(ZoneInfo(self.workspace.timezone)).date()

    def run_month(self):
        """Return the month this suite is running in, derived from the clock.

        Every pool loss below was reported in this period while `created` was
        the date, and it is also the month a freeze stamped now belongs to.
        """
        today = self.local_today()
        last = monthrange(today.year, today.month)[1]
        return (today.replace(day=1).isoformat(), today.replace(day=last).isoformat())

    def everything(self):
        """Return a range covering every day up to today.

        What a loss cannot hide from. A test that names one month can tell a
        month with nothing in it from a month that lost nothing only by reading
        the whole of time beside it.
        """
        return ('2000-01-01', self.local_today().isoformat())

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

    def currency(self, period):
        """Return the period's single currency summary, or None for an empty one."""
        currencies = self.report(period).totals['currencies']
        return currencies[0] if currencies else None

    def close_sowing(self, sowing, closed_at):
        """Declare one sowing finished germinating on a stated day."""
        return close_germination(
            sowing, self.user, closed_at=closed_at,
            loss_cause=CohortOperation.LossCause.FAILED,
            reason='The window has passed.',
        )

    def assert_only_in(self, period, expected):
        """Assert this period reports these losses and no other period reports any."""
        self.assertEqual(self.losses(period), expected)
        self.assertEqual(self.losses(self.everything()), expected)


class LossPeriodTestCase(LossPeriodMixin, CostingServiceTestCase):
    """One filled tray to lose cost out of, and the facts that retire it."""

    def pool_loss(self):
        """Return the batch's standing pool production-loss layer."""
        return CostAllocation.objects.get(
            batch=self.batch,
            target_type=CostAllocation.TargetType.PRODUCTION_LOSS,
            reversal_of=None, reversal__isnull=True,
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


class CohortLossPeriodTestCase(LossPeriodMixin, CohortStockTestCase):
    """Four anonymous units worth 1.0800, with periods to read them over."""


class PoolLossDateTests(LossPeriodTestCase):
    """A loss with no plant to date it by is dated by the fact that retired it.

    The three the task names: ungerminated seed retired at a germination
    closure, a media remainder discarded by a clean, and whatever finalization
    never placed. Each has a real date on a real record, and none of them is
    the day somebody's recalculation ran. The fourth case is the one no fact
    but the freeze declares, and it is below with them.
    """

    def test_ungerminated_seed_is_reported_in_the_month_the_closure_retired_it(self):
        """Four clusters at 0.25 each: 1.0000 of loss, in March and nowhere else."""
        sowing = self.sow([(self.cells[0], 4)])
        settle(self)

        self.close_sowing(sowing, EARLY)

        layer = self.pool_loss()
        self.assertEqual(layer.effective_at, EARLY)
        self.assertGreater(layer.created, layer.effective_at)
        self.assert_only_in(MARCH, [(EARLY, '1.0000', None)])

    def test_a_discarded_media_remainder_is_reported_when_the_clean_tipped_it_out(self):
        """0.08 litres at 2.00 a litre: 0.1600 of media, dated by the clean.

        A clean is a declaration that the remainder was tipped out, so it dates
        its own loss however late it is typed — here after the batch's output
        was finalized, which is the ordinary order and the one that makes the
        remainder loss outright rather than cost still waiting to be resolved.

        Cleaned in the other order the figure is the freeze's, and the layer's
        date with it: the remainder is unresolved cost until the freeze declares
        it, and the freeze reposts it with its own date. So the clean's date
        reaches the report in this order only, and the task's note says so.
        """
        self.sow([(self.cells[0], 4)])
        self.apply_media(self.cells, '0.08')
        settle(self)
        self.finalize()
        frozen_at = self.batch.output_finalized_at

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
            self.losses(self.run_month()),
            [(frozen_at, '1.0000', None), (frozen_at, '0.0800', None)],
        )
        self.assertEqual(self.loss_total(self.everything()), Decimal('1.2400'))

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
        self.assert_only_in(
            self.run_month(), [(self.batch.output_finalized_at, '1.0000', None)],
        )

    def test_an_input_applied_before_the_freeze_but_posted_after_it_is_the_freezes_loss(self):
        """Nothing was lost in March: the media was sitting on a cell.

        The layer is dated 20 March, and rightly — that is the day the media
        went on, and task 163's column is about the fact behind the layer. But
        the fact behind this layer is an application, not a loss, and on 31
        March that 0.0800 was unresolved cost counted in closing stock. The
        freeze is the only thing that ever said it was lost, so the freeze is
        when it was lost. Dated by the layer alone, a published March would
        gain 0.0800 of loss it never had, which is this task's own defect in
        the other direction.
        """
        self.sow([(self.cells[0], 4)])
        settle(self)
        self.finalize()
        frozen_at = self.batch.output_finalized_at

        self.apply_media([self.cells[0]], '0.04', applied_at=EARLY)

        layer = CostAllocation.objects.get(
            batch=self.batch,
            source_type=CostAllocation.SourceType.APPLICATION_LINE,
            target_type=CostAllocation.TargetType.PRODUCTION_LOSS,
            reversal_of=None, reversal__isnull=True,
        )
        self.assertEqual(layer.effective_at, EARLY)
        self.assertEqual(self.losses(MARCH), [])
        self.assert_only_in(self.run_month(), [
            (frozen_at, '1.0000', None), (frozen_at, '0.0800', None),
        ])

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

        self.assert_only_in(MARCH, [(EARLY, '1.0000', 'culled')])


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
        self.assert_only_in(MARCH, [(EARLY, '1.0000', None)])

    def test_a_later_posting_leaves_the_closed_period_where_it_was(self):
        """A run that really writes layers, months after the loss happened.

        Media going onto a cell that raised nothing is loss of its own, and the
        freeze is what declares it, so it is this month's: 0.0800 here against
        March's 1.0000. Dated by the run, both would have been reported
        together in whatever month the posting happened in; the closure's seed
        stays exactly where the closure put it.
        """
        self.apply_media([self.cells[0]], '0.04')
        self.finalize()
        frozen_at = self.batch.output_finalized_at

        self.assertEqual(self.losses(MARCH), [(EARLY, '1.0000', None)])
        self.assertEqual(
            self.losses(self.run_month()), [(frozen_at, '0.0800', None)],
        )
        self.assertEqual(self.loss_total(self.everything()), Decimal('1.0800'))


class LossUnitReconciliationTests(CohortLossPeriodTestCase):
    """The money and the unit counts of one period describe one population.

    `_lost_units` has always dated its counts from the events behind them, so a
    pool loss dated by the run was money in a period whose units were counted
    in another. The caused money is what reconciles against the counts; a pool
    loss carries no cause because ungerminated seed was never a unit anybody
    could count, so it is the gap between the two. The fixture holds one of
    each in one period, which is the only way either figure can be checked
    against the other.
    """

    def setUp(self):
        super().setUp()
        settle(self)
        # Closing the germination retires the whole 1.0000 of seed as pool
        # loss: the four units are a block and `observed_plants` counts
        # seedlings, so the sowing reads as having produced none. That leaves
        # the block its 0.0800 of media, and one unit of four lost out of it is
        # 0.0200 of caused money beside 1.0000 that never was a unit.
        self.close_sowing(self.cohort.source_sowing, EARLY)
        self.operation = self.lose(occurred_at=EARLY)
        self.finalize()

    def test_the_caused_money_and_the_unit_counts_cover_the_same_losses(self):
        """One unit lost is one unit counted and 0.0200 of caused money."""
        totals = self.report(MARCH).totals
        summary = self.currency(MARCH)

        self.assertEqual(totals['lost_units_by_cause']['failed'], 1)
        self.assertEqual(totals['lost_units'], 1)
        self.assertEqual(summary['loss_by_cause']['failed'], '0.0200')
        self.assertEqual(
            sum(Decimal(value) for value in summary['loss_by_cause'].values()),
            Decimal('0.0200'),
        )

    def test_a_pool_loss_is_the_money_in_the_period_with_no_unit_to_count(self):
        """1.0200 of loss over one counted unit: 1.0000 of it never was one."""
        summary = self.currency(MARCH)
        caused = sum(Decimal(value) for value in summary['loss_by_cause'].values())

        self.assertEqual(summary['production_loss'], '1.0200')
        self.assertEqual(Decimal(summary['production_loss']) - caused, Decimal('1.0000'))
        self.assertEqual(self.loss_total(self.everything()), Decimal('1.0200'))

    def test_the_period_the_losses_were_recorded_in_counts_neither(self):
        """Both halves stay in March when the batch is recalculated here."""
        recalculate_batch_costs(self.batch, self.user, 'Check the figures again.')

        self.assertEqual(self.report(self.run_month()).totals['lost_units'], 0)
        self.assertIsNone(self.currency(self.run_month()))
        self.assertEqual(self.currency(MARCH)['production_loss'], '1.0200')


class UndatedLossTests(CohortLossPeriodTestCase):
    """Loss no recorded fact dates is published, not dropped.

    After task 163 a pool loss always carries a date, so the one loss left that
    nothing dates is a block's: its recorded losses have all been corrected
    while its loss layer still stands. No write path leaves a batch like that —
    correcting a loss reallocates in the same transaction, and `COHORT_LOSS` is
    re-divided even on a finalized batch — so the state takes a stale row, and
    the correction here is recorded with the reallocation stubbed out to reach
    it. Task 136's audit query is how such a row is found in earnest. The
    report used to drop the money out of every period without a word.
    """

    def setUp(self):
        super().setUp()
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

    def test_a_loss_layer_with_no_recorded_loss_left_is_named_not_dropped(self):
        """0.2700 that was March's is in no period, and the report says so.

        Two layers, because a block's loss carries a share of each source the
        batch drew on: 0.2500 of seed and 0.0200 of media.
        """
        self.assertEqual(self.currency(MARCH)['production_loss'], '0.2700')

        self.correct_without_recosting()

        report = self.report(MARCH)
        self.assertEqual(self.losses(self.everything()), [])
        self.assertEqual(report.totals['undated_loss_layers'], 2)
        finding = {row['code']: row for row in report.data_quality}['undated_loss']
        self.assertEqual(finding['count'], 2)
        self.assertFalse(report.totals['finalized_margin_available'])

    def test_a_garden_square_filter_reaches_no_block_and_so_no_finding(self):
        """A square selects no anonymous stock, so it cannot select its loss.

        `_lost_units` empties the cohort events for a square filter, and the
        money has to agree: a square that holds no block of this batch would
        otherwise publish a finding, and withhold every margin on the report,
        over loss it would never show a row for.
        """
        self.correct_without_recosting()

        report = profitability_report(self.workspace, {
            'date_from': MARCH[0], 'date_to': MARCH[1], 'garden_square': 999999,
        })

        self.assertEqual(report.totals['undated_loss_layers'], 0)
        self.assertEqual(
            [row['code'] for row in report.data_quality if row['code'] == 'undated_loss'],
            [],
        )
        self.assertEqual(report.totals['lost_units'], 0)
