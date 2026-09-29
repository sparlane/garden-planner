"""A cost layer dated by the fact behind it, not by the run (task 163).

Every layer carries two dates: `created`, the stamp of the run that wrote it,
and `effective_at`, the day the fact behind it happened. The second is the one
a year-end reader may ask, and these are the rules it follows — where it comes
from on a first posting, what happens to it when a batch's outputs re-divide,
and what a reposting that changes nothing does to it.

Driven through the real posting paths, as `costing.test_services` is: a media
application dated on the day it went on, a loss and a promotion each dated by
their own operation, and a dispatch dated by its fulfillment.
"""

# pylint: disable=duplicate-code

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from uuid import uuid4

from django.utils import timezone

from django.db.models import Q

from applications.models import InputApplication
from inventory.models import StockMovement
from plantings.cohorts import observe_cohort
from plantings.germination import close_germination
from plantings.models import CohortOperation, PlantCohort, ProductionBatch
from plantings.sowing import current_sowing_consumption

from .models import CostAllocation, CostAllocationRun
from .test_services import CohortStockTestCase, CostingServiceTestCase


#: The fixture's own history, once it has been moved out of the way.
SETTLED = datetime(2026, 1, 5, tzinfo=dt_timezone.utc)

#: A fact that happened after the fixture settled and before anything else.
EARLY = datetime(2026, 3, 20, tzinfo=dt_timezone.utc)

#: A fact that happened after `EARLY`, used to record two out of order.
LATE = datetime(2026, 5, 1, tzinfo=dt_timezone.utc)


def settle(case):
    """Move the fixture's whole history back to `SETTLED`.

    The inputs move with the layers, because a layer first posted from a
    source is dated by that source and not by the run that read it, so a block
    observed after this point would otherwise draw a January share on a
    September sowing. The batch's start moves too: an application cannot
    predate it.
    """
    ProductionBatch.objects.filter(pk=case.batch.pk).update(actual_start=SETTLED)
    case.batch.refresh_from_db()
    StockMovement.objects.filter(workspace=case.workspace).update(occurred_at=SETTLED)
    InputApplication.objects.filter(workspace=case.workspace).update(applied_at=SETTLED)
    CostAllocation.objects.filter(batch=case.batch).update(created=SETTLED, effective_at=SETTLED)
    PlantCohort.objects.filter(batch=case.batch).update(observed_at=SETTLED)


def live_at(case, when):
    """Return the batch's layers standing at one instant, whatever they target.

    The two clauses `bookkeeping.services._layers_at` reads a block or a plant
    with, widened to the whole batch: effective before `when`, and either
    never reversed or reversed only at or after it. A batch's live layers have
    to add back up to what it cost at every instant, which is what catches two
    versions of one key overlapping.
    """
    return CostAllocation.objects.filter(
        batch=case.batch, reversal_of=None, effective_at__lt=when,
    ).filter(Q(reversal__isnull=True) | Q(reversal__effective_at__gte=when))


def cohort_layer(case):
    """Return the block's standing seed layer, whichever version of it that is.

    The seed one, because the block carries a media layer beside it and the
    two are re-divided together; one of them is enough to read the dates off.
    """
    return CostAllocation.objects.get(
        batch=case.batch, target_type=CostAllocation.TargetType.PLANT_COHORT,
        source_type=CostAllocation.SourceType.SOWING_POSTING,
        reversal_of=None, reversal__isnull=True,
    )


class SourceDateTests(CostingServiceTestCase):
    """A layer first posted from a source is dated by that source's record."""

    def test_a_seed_layer_is_dated_by_the_movement_that_drew_the_packet(self):
        """The sowing's consumption movement, not the run that read it."""
        sowing = self.sow([(self.cells[0], 4)])

        posting = current_sowing_consumption(sowing)
        layer = CostAllocation.objects.filter(batch=self.batch, sowing_posting=posting).first()
        self.assertEqual(layer.effective_at, posting.movement.occurred_at)

    def test_a_media_layer_is_dated_by_the_day_the_media_went_on(self):
        """Criterion 3: applied on 20 March, posted today, and March's cost.

        The document is created and posted now, so `created` is today. Nothing
        but `applied_at` puts the layer in the year the media was used in. The
        batch is started back at `SETTLED` first, because an application
        cannot predate the start of the batch it is applied to.
        """
        self.sow([(self.cells[0], 4)])
        ProductionBatch.objects.filter(pk=self.batch.pk).update(actual_start=SETTLED)
        self.batch.refresh_from_db()
        application = self.apply_media([self.cells[0]], '0.04', applied_at=EARLY)

        layer = CostAllocation.objects.filter(
            batch=self.batch, application_line=application.lines.get(),
        ).first()
        self.assertEqual(layer.effective_at, EARLY)
        self.assertGreater(layer.created, layer.effective_at)

    def test_a_pool_loss_is_dated_by_the_closure_that_retired_it(self):
        """Not by the sowing the cost came out of (task 143 reads this).

        Retiring an ungerminated remainder re-targets the sowing's own cost
        from the cells to production loss, and the sowing is already on file,
        so the loss starts on the day somebody said the sowing was finished.
        Dated by the source it would have carried the sowing movement's stamp
        and landed in whatever period the seed went in. The sowing is settled
        back to January first, so the closure is the later of the two facts
        and the clamp has nothing to say about it.
        """
        sowing = self.sow([(self.cells[0], 4)])
        settle(self)

        close_germination(
            sowing, self.user, closed_at=EARLY,
            loss_cause=CohortOperation.LossCause.FAILED,
            reason='The window has passed.',
        )

        loss = CostAllocation.objects.get(
            batch=self.batch, target_type=CostAllocation.TargetType.PRODUCTION_LOSS,
            reversal_of=None, reversal__isnull=True,
        )
        self.assertEqual(loss.effective_at, EARLY)

    def test_every_layer_the_fixture_wrote_carries_a_date(self):
        """Criterion 1: no posting path leaves the column to be guessed at."""
        self.sow([(self.cells[0], 4)])
        self.apply_media([self.cells[0]], '0.04')

        layers = list(CostAllocation.objects.filter(batch=self.batch))
        self.assertTrue(layers)
        self.assertEqual([row for row in layers if row.effective_at is None], [])


class RedivisionDateTests(CohortStockTestCase):
    """What a re-division does to the two versions of one layer.

    A block's cost is re-divided whenever its outputs change, and the change is
    a reversal plus a replacement. The superseded layer stays effective right
    up to the day the thing that superseded it happened, and the replacement
    starts there — so the pair bounds an interval with no gap and no overlap,
    and no cost moves between years.
    """

    def setUp(self):
        super().setUp()
        settle(self)

    def test_a_loss_dates_both_the_withdrawal_and_the_replacement(self):
        """Dated 20 March and typed today: 20 March on both sides."""
        superseded = cohort_layer(self)
        self.lose(occurred_at=EARLY)

        superseded.refresh_from_db()
        self.assertEqual(superseded.effective_at, SETTLED)
        self.assertEqual(superseded.reversal.effective_at, EARLY)
        self.assertEqual(cohort_layer(self).effective_at, EARLY)

    def test_a_dispatch_dates_the_re_division_by_its_fulfillment(self):
        """A sale names its own `fulfilled_at`, and the layers take it."""
        superseded = cohort_layer(self)
        self.sell(fulfilled_at=EARLY)

        superseded.refresh_from_db()
        self.assertEqual(superseded.reversal.effective_at, EARLY)
        self.assertEqual(cohort_layer(self).effective_at, EARLY)

    def test_a_sibling_observed_late_withdraws_the_first_block_on_its_own_day(self):
        """Observing block B on 20 March is what takes A's whole-batch layer off."""
        superseded = cohort_layer(self)
        observe_cohort(
            self.workspace, self.user, batch=self.batch, quantity=3,
            idempotency_key=uuid4(), occurred_at=EARLY,
        )

        superseded.refresh_from_db()
        self.assertEqual(superseded.reversal.effective_at, EARLY)

    def test_reposting_a_batch_with_no_new_facts_moves_no_date(self):
        """Criterion 2: a recalculation that changes nothing writes nothing."""
        before = {row.pk: row.effective_at for row in self.effective()}

        self.assertIsNone(self.reallocate())

        self.assertEqual({row.pk: row.effective_at for row in self.effective()}, before)

    def test_a_recalculation_never_dates_a_layer_before_the_one_it_withdraws(self):
        """Two facts typed out of order across a year end read as the later one.

        A promotion dated 20 March recorded after a loss dated 1 May cannot
        start on 20 March: the loss's layer is effective from 1 May, and a
        replacement starting before its predecessor ended would leave both live
        at once and a year-end reader would count the block twice.
        """
        self.lose(occurred_at=LATE)
        superseded = cohort_layer(self)

        self.promote_one(occurred_at=EARLY)

        superseded.refresh_from_db()
        self.assertEqual(superseded.reversal.effective_at, LATE)
        self.assertEqual(cohort_layer(self).effective_at, LATE)

    def test_the_clamp_leaves_the_fact_its_own_date_on_the_run(self):
        """What the clamp costs is recorded, not silently absorbed.

        The layers say 1 May because they cannot say otherwise, but the run
        still says 20 March, which is how `bookkeeping.services` knows a year
        end between the two has lost a fact it should have counted.
        """
        self.lose(occurred_at=LATE)

        self.promote_one(occurred_at=EARLY)

        run = CostAllocationRun.objects.filter(batch=self.batch).order_by('pk').last()
        self.assertEqual(run.occurred_at, EARLY)
        self.assertEqual(cohort_layer(self).effective_at, LATE)

    def test_a_batch_reconciles_at_every_instant_a_sale_divides_it(self):
        """Criterion 2's real invariant: no instant sees two versions of a key.

        A dispatch gives an already-posted source a new key — the block's
        `cohort_sale` half — and dating that by the source rather than by the
        sale would start it in January while the layer it supersedes runs to
        May. Read as at 20 March the batch then held 1.3500 of a cost of
        1.0800, the same unit counted twice.
        """
        self.sell(fulfilled_at=LATE)

        for when in (EARLY, timezone.now()):
            with self.subTest(when=when):
                self.assertEqual(
                    sum(row.amount for row in live_at(self, when)),
                    Decimal('1.0800'),
                )
        sold = CostAllocation.objects.get(
            batch=self.batch, target_type=CostAllocation.TargetType.COHORT_SALE,
            source_type=CostAllocation.SourceType.SOWING_POSTING,
            reversal_of=None, reversal__isnull=True,
        )
        self.assertEqual(sold.effective_at, LATE)

    def test_a_loss_gives_an_already_posted_source_no_earlier_date(self):
        """The same shape one target over: `cohort_loss` starts at the loss."""
        self.lose(occurred_at=LATE)

        self.assertEqual(
            sum(row.amount for row in live_at(self, EARLY)), Decimal('1.0800'),
        )
        lost = CostAllocation.objects.get(
            batch=self.batch, target_type=CostAllocation.TargetType.COHORT_LOSS,
            source_type=CostAllocation.SourceType.SOWING_POSTING,
            reversal_of=None, reversal__isnull=True,
        )
        self.assertEqual(lost.effective_at, LATE)

    def test_a_recalculation_nothing_prompted_is_dated_now(self):
        """No fact to date it by, so the run's own moment stands in."""
        started = timezone.now()
        self.lose()

        self.assertGreaterEqual(cohort_layer(self).effective_at, started)


class FrozenRedivisionDateTests(CohortStockTestCase):
    """Criterion 1: a finalized batch re-divides its blocks, and dates them too.

    A frozen batch never touches a plant's share again, but its cohort layers
    still move when units are sold, lost or promoted. Those postings go through
    `_frozen_plan` rather than the ordinary one, which is the second path the
    column has to be set on.
    """

    def setUp(self):
        super().setUp()
        self.finalize()
        settle(self)

    def test_a_loss_on_a_finalized_batch_dates_its_re_division(self):
        """The frozen total is unmoved, and the layers say when it moved within."""
        superseded = cohort_layer(self)
        self.lose(occurred_at=EARLY)

        superseded.refresh_from_db()
        self.assertEqual(superseded.reversal.effective_at, EARLY)
        replacement = cohort_layer(self)
        self.assertEqual(replacement.effective_at, EARLY)
        self.assertEqual(
            sum((row.amount or Decimal('0') for row in self.effective()), Decimal('0')),
            Decimal('1.0800'),
        )


class UndatedRunTests(CostingServiceTestCase):
    """What a run with nothing to date it by falls back to.

    `reallocate_batch` left without an `occurred_at` stamps the moment it ran,
    which is the status quo the whole subledger used before the column existed.
    It is kept for the caller reacting to no particular fact — an operator
    pressing recalculate — and for the backfilled rows, which have only that.
    """

    def test_a_recalculation_no_fact_prompted_dates_its_layers_by_the_run(self):
        """A seedling recorded without a reallocation, then swept up by one.

        The withdrawal is the half that has no other date to take: the cell's
        layer is being cancelled by no recorded fact at all, only by somebody
        asking for a recalculation, so the run's own moment is what it gets.
        The plant layer posted beside it is a first posting of its key and so
        is dated by the sowing, which is earlier.
        """
        sowing = self.sow([(self.cells[0], 4)])
        self.germinate(sowing, self.cells[0])
        started = timezone.now()

        self.assertIsNotNone(self.reallocate())

        withdrawn = CostAllocation.objects.filter(
            batch=self.batch, reversal_of__isnull=False,
        ).order_by('pk').last()
        self.assertGreaterEqual(withdrawn.effective_at, started)
        self.assertEqual(
            withdrawn.reversal_of.target_type, CostAllocation.TargetType.SEED_TRAY_CELL,
        )
