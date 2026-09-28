"""Cohort stock valued as it stood on the balance date (task 148).

`costing.test_services`'s block of four units worth 1.0800 is moved back to
January 2026 — the count, the cohort operations and the cost layers together —
and the income year ends on 31 March 2026. Whatever a test then does happens
after the balance date, the way spring sales happen after a 31 March year end
and before the capture that values it.

The fixture is two module-level functions rather than a shared `TestCase`,
because one more generation between these classes and `CohortStockTestCase`
is one more than pylint allows.
"""

from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from uuid import uuid4

from applications.models import InputApplication
from applications.services import reverse_application
from costing.models import CostAllocation
from costing.test_services import CohortStockTestCase
from inventory.models import StockMovement
from plantings.cohorts import change_cohort, merge_cohorts, observe_cohort, split_cohort
from plantings.models import CohortEvent, CohortOperation, PlantCohort, ProductionBatch

from .models import IncomeTaxYear, StockValuationLine
from .services import build_report
from .test_cohort_stock import capture_cohort_lines


#: Where the fixture's history is moved to: before the balance date, and far
#: enough back that nothing rounds into it.
JANUARY = datetime(2026, 1, 5, tzinfo=dt_timezone.utc)

#: Late in the income year, for a fact that happened before the balance date
#: and was typed after it.
MARCH = datetime(2026, 3, 20, tzinfo=dt_timezone.utc)

#: The other side of the balance date, for a run posted after year end.
SEPTEMBER = datetime(2026, 9, 1, tzinfo=dt_timezone.utc)


def open_year(case):
    """Open a draft income year ending 31 March 2026 for `case`."""
    return IncomeTaxYear.objects.create(
        workspace=case.workspace, basis=IncomeTaxYear.Basis.ACCRUAL,
        year_end=date(2026, 3, 31),
    )


def backdate_to_january(case):
    """Move everything `case` has recorded so far back to January.

    The capture reads two dates and neither can be set to the past on the way
    in: an operation's `occurred_at` defaults to now, and a cost layer takes
    the date of whatever prompted the run that wrote it, which here is also
    now. A fact that has to predate the balance date is therefore recorded and
    then moved, which leaves the history January would have left behind had it
    been recorded in January — the layer's `created` and the event's included,
    because a fact typed on the day it happened is what makes these blocks
    ordinary rather than recorded late.

    The batch and the two inputs behind it move as well. A layer first posted
    from a source is dated by that source's own record, so a block observed
    later takes a January date only if the sowing it draws on really is in
    January; and an application cannot predate the start of its batch, so a
    test dating one in March needs a batch that started before March.
    """
    ProductionBatch.objects.filter(pk=case.batch.pk).update(actual_start=JANUARY)
    case.batch.refresh_from_db()
    StockMovement.objects.filter(workspace=case.workspace).update(occurred_at=JANUARY)
    InputApplication.objects.filter(workspace=case.workspace).update(applied_at=JANUARY)
    PlantCohort.objects.filter(workspace=case.workspace).update(
        created=JANUARY, observed_at=JANUARY,
    )
    CohortOperation.objects.filter(workspace=case.workspace).update(occurred_at=JANUARY)
    CohortEvent.objects.filter(workspace=case.workspace).update(created=JANUARY)
    CostAllocation.objects.filter(batch=case.batch).update(
        created=JANUARY, effective_at=JANUARY,
    )


class CohortStockAtTheBalanceDateTests(CohortStockTestCase):
    """What the block held on 31 March, whatever the spring then did to it.

    The capture read each block as it stood when it ran, so a unit sold in
    September took itself and its cost out of the year that ended in March.
    Task 135 could only flag that; these are the figures it flagged.
    """

    capture = capture_cohort_lines
    backdate = backdate_to_january

    def setUp(self):
        super().setUp()
        self.income_year = open_year(self)
        self.backdate()

    def split_off(self, quantity=2):
        """Split `quantity` units into a block of their own, and return it."""
        self.cohort.refresh_from_db()
        child, _operation = split_cohort(
            self.workspace, self.user,
            cohort_id=self.cohort.pk, expected_revision=self.cohort.revision,
            quantity=quantity, idempotency_key=uuid4(), reason='Move part of the block.',
        )
        return child

    def assert_held_at_year_end(self, line):
        """Assert one line is the four units worth 1.0800 held on 31 March."""
        self.assertEqual(
            (f'{line.quantity:.9f}', f'{line.value:.4f}', f'{line.original_cost:.4f}', line.provisional),
            ('4.000000000', '1.0800', '1.0800', False),
        )

    def assert_three_left(self, line):
        """Assert one line is the three units worth 0.8100 a sale left behind."""
        self.assertEqual(
            (f'{line.quantity:.9f}', f'{line.value:.4f}', line.provisional),
            ('3.000000000', '0.8100', False),
        )

    def test_a_block_untouched_since_the_balance_date_is_unchanged(self):
        """The control: four units held then and now, at 1.0800."""
        self.assert_held_at_year_end(self.capture()[self.cohort.pk])

    def test_a_sale_after_the_balance_date_is_valued_as_it_stood(self):
        """Criterion 1: four at 1.0800, where the capture read three at 0.8100."""
        self.sell()

        self.assert_held_at_year_end(self.capture()[self.cohort.pk])
        # Nothing about the block is unsettled any more, so the year is free to
        # be finalized while the spring's selling carries on.
        codes = [row['code'] for row in build_report(self.income_year)['data_quality']]
        self.assertNotIn('provisional_stock', codes)

    def test_a_block_sold_out_after_the_balance_date_is_still_valued(self):
        """Criterion 2: emptied in the spring, it still held four in March."""
        self.sell(quantity=4)

        self.assert_held_at_year_end(self.capture()[self.cohort.pk])

    def test_a_block_sold_out_before_the_balance_date_captures_no_line(self):
        """Criterion 2, the other half: nothing stood there on 31 March."""
        self.sell(quantity=4)
        self.backdate()

        self.assertEqual(self.capture(), {})

    def test_a_block_partly_sold_before_the_balance_date_is_valued_on_what_is_left(self):
        """Task 135's figures, reached by reconstruction rather than by reading."""
        self.sell()
        self.backdate()

        self.assert_three_left(self.capture()[self.cohort.pk])

    def test_a_split_after_the_balance_date_leaves_the_units_where_they_stood(self):
        """Criterion 3: the block that held them in March is the one that carries them."""
        child = self.split_off()

        lines = self.capture()
        self.assert_held_at_year_end(lines[self.cohort.pk])
        self.assertNotIn(child.pk, lines)

    def test_a_loss_after_the_balance_date_belongs_to_the_next_year(self):
        """Criterion 3: a unit that died in the spring was stock in March."""
        self.lose()

        self.assert_held_at_year_end(self.capture()[self.cohort.pk])

    def test_a_loss_before_the_balance_date_corrected_after_it_stays_lost(self):
        """The units came back in the spring; on 31 March they were gone."""
        loss = self.lose()
        self.backdate()
        self.correct(loss)

        self.assert_three_left(self.capture()[self.cohort.pk])

    def test_a_promotion_after_the_balance_date_leaves_the_unit_in_the_block(self):
        """Criterion 3, and the plant it minted is not stock a second time."""
        plant = self.promote_one()

        self.assert_held_at_year_end(self.capture()[self.cohort.pk])
        # A promoted plant is germinated on the block's own observation date,
        # so without this it reads as a seedling that was standing in March.
        plants = StockValuationLine.objects.filter(
            income_year=self.income_year, source_type='specific_plant',
        )
        self.assertNotIn(str(plant.pk), [line.source_id for line in plants])

    def test_a_customer_return_after_the_balance_date_opens_no_line(self):
        """Criteria 3 and 4: the returned unit was the customer's in March."""
        fulfillment = self.sell()
        self.backdate()
        self.return_sale(fulfillment)

        lines = self.capture()
        self.assert_three_left(lines[self.cohort.pk])
        opened = PlantCohort.objects.exclude(pk=self.cohort.pk).get(batch=self.batch)
        self.assertNotIn(opened.pk, lines)

    def test_a_block_first_observed_after_the_balance_date_is_not_captured(self):
        """Criterion 4: stock that was not there cannot be closing stock."""
        later, _operation = observe_cohort(
            self.workspace, self.user, batch=self.batch, quantity=3,
            idempotency_key=uuid4(),
        )

        self.assertNotIn(later.pk, self.capture())

    def test_a_recount_after_the_balance_date_keeps_the_count_it_had(self):
        """Criterion 3: two units found in the spring were not there in March."""
        self.cohort.refresh_from_db()
        change_cohort(
            self.workspace, self.user,
            cohort_id=self.cohort.pk, expected_revision=self.cohort.revision,
            action=CohortOperation.Action.ADJUST, quantity=6,
            idempotency_key=uuid4(), reason='Counted at stocktake.',
        )

        self.assert_held_at_year_end(self.capture()[self.cohort.pk])


class CohortCostAtTheBalanceDateTests(CohortStockTestCase):
    """What the block cost on 31 March, whatever the spring then charged it.

    A cost-only change leaves no cohort event behind, so task 135's flag could
    not see one at all. The layers are read as at the balance date instead,
    which covers a later input, a reversed one, and a sibling block's cost
    moving in — none of which needs recognising individually.
    """

    capture = capture_cohort_lines
    backdate = backdate_to_january

    def setUp(self):
        super().setUp()
        self.income_year = open_year(self)
        self.backdate()

    def test_an_input_posted_after_the_balance_date_does_not_change_the_value(self):
        """Criterion 5: next year's media is next year's cost."""
        self.apply_media([self.cells[0]], '0.04')
        self.reallocate()

        line = self.capture()[self.cohort.pk]
        self.assertEqual(f'{line.value:.4f}', '1.0800')
        self.assertFalse(line.provisional)

    def test_an_input_reversed_after_the_balance_date_does_not_change_the_value(self):
        """The other direction of criterion 5: the media was on the block in March."""
        application = self.apply_media([self.cells[0]], '0.04')
        self.backdate()
        reverse_application(application, self.user, 'Applied to the wrong batch.')

        line = self.capture()[self.cohort.pk]
        self.assertEqual(f'{line.value:.4f}', '1.1600')
        self.assertFalse(line.provisional)

    def test_a_sibling_merged_in_after_the_balance_date_keeps_its_own_cost(self):
        """Two blocks stood there in March, so two blocks are captured."""
        self.cohort.refresh_from_db()
        child, _split = split_cohort(
            self.workspace, self.user,
            cohort_id=self.cohort.pk, expected_revision=self.cohort.revision,
            quantity=2, idempotency_key=uuid4(), reason='Two benches.',
        )
        self.backdate()
        self.cohort.refresh_from_db()
        child.refresh_from_db()
        merge_cohorts(
            self.workspace, self.user, target_id=self.cohort.pk, source_ids=[child.pk],
            revisions={self.cohort.pk: self.cohort.revision, child.pk: child.revision},
            idempotency_key=uuid4(), reason='One bench again.',
        )

        lines = self.capture()
        for line in (lines[self.cohort.pk], lines[child.pk]):
            self.assertEqual(
                (f'{line.quantity:.9f}', f'{line.value:.4f}', line.provisional),
                ('2.000000000', '0.5400', False),
            )

    def test_a_block_the_ledger_had_not_costed_is_provisional(self):
        """What is left provisional: stock standing on 31 March with no cost yet.

        The block's own history says it was there, and was typed while it was,
        but no layer had been posted against it by the balance date. Filing a
        zero would value year-end stock out of a cost that had not been divided
        yet, so the count stands and the value is withheld.

        Observing a block reallocates its batch in the same transaction, so the
        gap is narrow: it is legacy stock, or a block whose only layer was
        reversed before year end. It is opened here by moving the block's
        layers, and only its layers, to the other side of the balance date.
        """
        later, _operation = observe_cohort(
            self.workspace, self.user, batch=self.batch, quantity=3,
            idempotency_key=uuid4(),
        )
        self.backdate()
        CostAllocation.objects.filter(plant_cohort=later).update(
            created=SEPTEMBER, effective_at=SEPTEMBER,
        )

        line = self.capture()[later.pk]
        self.assertEqual(f'{line.quantity:.9f}', '3.000000000')
        self.assertIsNone(line.original_cost)
        self.assertEqual(f'{line.value:.4f}', '0.0000')
        self.assertTrue(line.provisional)
        self.assertIn('No cost layer stood against this block', line.assumptions)
        # And a provisional line is still what holds the year open.
        codes = [row['code'] for row in build_report(self.income_year)['data_quality']]
        self.assertIn('provisional_stock', codes)


class CohortRecordedLateTests(CohortStockTestCase):
    """A fact dated before the balance date and typed after it (task 163).

    Both halves of the capture read the day a thing happened: the count reads
    `CohortOperation.occurred_at` and the value reads
    `CostAllocation.effective_at`. Task 148 had only the run's stamp for the
    cost, so a block would have been counted after the fact and valued before
    it — four units' cost carried by three — and it fell back to reading such a
    block as it stands. These are the figures that fallback stood in for,
    reached now by reading the dates instead.
    """

    capture = capture_cohort_lines
    backdate = backdate_to_january

    def setUp(self):
        super().setUp()
        self.income_year = open_year(self)
        self.backdate()

    def assert_three_left(self, line, provisional=False):
        """Assert one line is the three units worth 0.8100 the fact left behind."""
        self.assertEqual(
            (f'{line.quantity:.9f}', f'{line.value:.4f}', line.provisional),
            ('3.000000000', '0.8100', provisional),
        )

    def assert_the_batch_reconciles(self, lines):
        """Assert the batch's captured blocks sum to the cohort cost it carries.

        A layer's amount is a share of a whole batch, so the batch is the level
        a reading has to be consistent at: read one block on one day and its
        sibling on another and the same cost lands in both lines, with nothing
        else in the capture noticing.
        """
        blocks = set(PlantCohort.objects.filter(batch=self.batch).values_list('pk', flat=True))
        captured = sum((line.value for pk, line in lines.items() if pk in blocks), Decimal('0'))
        carried = sum((row.amount for row in CostAllocation.objects.filter(
            batch=self.batch, target_type=CostAllocation.TargetType.PLANT_COHORT,
            reversal_of=None, reversal__isnull=True,
        )), Decimal('0'))
        self.assertEqual(f'{captured:.4f}', f'{carried:.4f}')

    def test_a_dispatch_dated_before_the_balance_date_but_recorded_after_it(self):
        """The customer had the unit on 31 March, so three units carry 0.8100."""
        self.sell(fulfilled_at=MARCH)

        lines = self.capture()
        self.assert_three_left(lines[self.cohort.pk])
        self.assert_the_batch_reconciles(lines)

    def test_a_block_observed_late_does_not_leave_its_siblings_holding_the_batch(self):
        """A block typed in September takes a share of what its sibling held whole.

        `observe_cohort` writes an event on the new block alone and then
        reallocates the batch, so the sibling's count never moves. What keeps
        the two lines adding up to the batch is that the sibling's old layer is
        withdrawn on the day the new block appeared, not on the day it was
        typed: read as at 31 March, the sibling carries its reduced share and
        the new block carries the rest.
        """
        second, _operation = observe_cohort(
            self.workspace, self.user, batch=self.batch, quantity=3,
            idempotency_key=uuid4(), occurred_at=MARCH,
        )

        lines = self.capture()
        self.assertEqual(
            (f'{lines[self.cohort.pk].quantity:.9f}', f'{lines[self.cohort.pk].value:.4f}'),
            ('4.000000000', '0.6171'),
        )
        self.assertEqual(
            (f'{lines[second.pk].quantity:.9f}', f'{lines[second.pk].value:.4f}'),
            ('3.000000000', '0.4629'),
        )
        self.assert_the_batch_reconciles(lines)

    def test_a_loss_dated_before_the_balance_date_but_recorded_after_it(self):
        """The unit was already dead on 31 March, and its cost went with it."""
        self.lose(occurred_at=MARCH)

        lines = self.capture()
        self.assert_three_left(lines[self.cohort.pk])
        self.assert_the_batch_reconciles(lines)

    def test_a_promotion_dated_before_the_balance_date_but_recorded_after_it(self):
        """The unit was a named plant on 31 March: 0.8100 plus 0.2700, not 1.3500."""
        plant = self.promote_one(occurred_at=MARCH)

        lines = self.capture()
        self.assert_three_left(lines[self.cohort.pk])
        self.assert_the_batch_reconciles(lines)
        named = StockValuationLine.objects.get(
            income_year=self.income_year, source_type='specific_plant', source_id=str(plant.pk),
        )
        self.assertEqual(f'{named.value:.4f}', '0.2700')

    def test_a_late_record_and_spring_activity_are_each_dated_where_they_belong(self):
        """The March loss counts, the September sale does not, and nothing is flagged.

        This is the case task 148 could only flag. Reading the late-recorded
        block as it stood gave two units at 0.5400 — the spring's sale taken
        off a March figure — and marked the line provisional to say so. Each
        run is now dated by its own fact, so the loss leaves on 20 March and
        the sale leaves in September, and the line is firm.
        """
        self.lose(occurred_at=MARCH)
        self.sell()

        line = self.capture()[self.cohort.pk]
        self.assert_three_left(line)
        self.assertIn('valued on the layers standing then', line.assumptions)

    def test_the_same_two_facts_typed_the_other_way_round_are_flagged(self):
        """Recorded out of order, the cost cannot follow the count, and it says so.

        The spring sale is typed first and the March loss after it, so the
        loss's run has to be clamped to the sale's date: dating it 20 March
        would restart the block's layer before the one it supersedes ended and
        leave both live on the balance date. The count still reads 20 March,
        so the block is counted after the loss and valued before it — the
        mismatch task 148's `_recorded_late` existed to stop, arriving by the
        other route, and left unflagged it overstates closing stock.

        There is no right figure to publish: the version that reflects the
        loss and not the sale was never written. So the as-at reading stands
        and the line is provisional, which holds the year open.
        """
        self.sell()
        self.lose(occurred_at=MARCH)

        line = self.capture()[self.cohort.pk]
        self.assertEqual(
            (f'{line.quantity:.9f}', f'{line.value:.4f}', line.provisional),
            ('3.000000000', '1.0800', True),
        )
        self.assertIn('recorded after a later one', line.assumptions)
        codes = [row['code'] for row in build_report(self.income_year)['data_quality']]
        self.assertIn('provisional_stock', codes)

    def test_two_facts_typed_in_order_before_the_balance_date_are_not_flagged(self):
        """The control: the clamp only matters where it crosses the year end."""
        self.lose(occurred_at=MARCH)
        self.backdate()

        line = self.capture()[self.cohort.pk]
        self.assert_three_left(line)
        self.assertNotIn('recorded after a later one', line.assumptions)


class CohortLayerEffectiveDateTests(CohortStockTestCase):
    """Cost incurred before the balance date and posted after it (task 163).

    The other direction of task 148's limitation, and the one its fallback
    could not reach at all: a layer posted in April for work done in March was
    next year's cost, because the only date it had was the run's.
    """

    capture = capture_cohort_lines
    backdate = backdate_to_january

    def setUp(self):
        super().setUp()
        self.income_year = open_year(self)
        self.backdate()

    def test_an_input_applied_before_the_balance_date_and_posted_after_it(self):
        """Media that went on the block on 20 March is in the year it went on.

        The document is posted now, months after year end, and its layer is
        dated by `applied_at`. Four units worth 1.0800 plus 0.08 of media is
        1.1600; with the run's stamp for a date the capture read 1.0800 and
        put the media in the following year.
        """
        self.apply_media([self.cells[0]], '0.04', applied_at=MARCH)

        line = self.capture()[self.cohort.pk]
        self.assertEqual(
            (f'{line.quantity:.9f}', f'{line.value:.4f}', line.provisional),
            ('4.000000000', '1.1600', False),
        )

    def test_an_input_applied_after_the_balance_date_is_still_next_years(self):
        """The control: media applied in the spring stays in the spring."""
        self.apply_media([self.cells[0]], '0.04')

        line = self.capture()[self.cohort.pk]
        self.assertEqual(f'{line.value:.4f}', '1.0800')
        self.assertFalse(line.provisional)
