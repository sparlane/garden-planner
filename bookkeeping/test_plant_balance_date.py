"""An individual plant valued as it stood on the balance date (task 162).

The plant half of the capture, on the same fixture shape task 148 gave the
cohort half: `costing.test_services`'s one seedling worth 1.0800 — four seed
clusters at 0.25 and 40 ml of two-dollar media — moved back to January 2026
against a year ending 31 March 2026. Whatever a test then does happens after
the balance date, the way an April media application happens after a 31 March
year end and before the capture that values it.

The plant's own membership needed nothing: `germinated` and every lifecycle
event are dated by the operator, so a plant that came up in March and was
typed in September was already captured and one that came up in September was
already not. Two tests pin that, because it is the decision the task asked for
rather than a change it made. What moved is the value.
"""

from decimal import Decimal
from uuid import uuid4

from applications.services import reverse_application
from costing.models import CostAllocation
from costing.test_services import CohortStockTestCase, CostingServiceTestCase
from plantings.cohorts import observe_cohort
from plantings.lifecycle import EventType, OutcomeRequest, record_lifecycle_event
from plantings.models import PlantLifecycleEvent, SpecificPlant, SpecificPlantLocation

from .models import StockValuationLine
from .services import build_report, capture_inventory
from .test_cohort_balance_date import JANUARY, MARCH, SEPTEMBER, backdate_to_january, open_year


def capture_plant_lines(case):
    """Capture `case`'s income year and return its plant lines by plant.

    A plain function for the same reason `capture_cohort_lines` is one: a
    shared `TestCase` between these classes and the costing fixture would put
    the leaves one generation deeper than pylint allows.
    """
    capture_inventory(case.income_year, case.user)
    lines = StockValuationLine.objects.filter(
        income_year=case.income_year, source_type='specific_plant',
    )
    return {int(line.source_id): line for line in lines}


def backdate_plants(case):
    """Move everything `case` has recorded so far back to January.

    `backdate_to_january` does the batch, the movements, the applications and
    the cost layers; the plant's own dated facts are here. None of them can be
    set to the past on the way in either — `germinated` defaults to now and
    both a germination event and the cell the seedling stands in are dated by
    it — so a fact that has to predate the balance date is recorded and then
    moved. The location goes with them because no outcome may predate the
    place the plant is standing in.
    """
    backdate_to_january(case)
    SpecificPlant.objects.filter(workspace=case.workspace).update(germinated=JANUARY)
    PlantLifecycleEvent.objects.filter(workspace=case.workspace).update(
        occurred_at=JANUARY, created=JANUARY,
    )
    SpecificPlantLocation.objects.filter(
        specific_plant__workspace=case.workspace,
    ).update(started=JANUARY)


class PlantStockTestCase(CostingServiceTestCase):
    """One seedling worth 1.0800, standing in its cell since January."""

    capture = capture_plant_lines
    backdate = backdate_plants

    def setUp(self):
        super().setUp()
        self.sowing = self.sow([(self.cells[0], 4)])
        self.apply_media([self.cells[0]], '0.04')
        self.plant = self.germinate(self.sowing, self.cells[0])[0]
        self.reallocate()
        self.income_year = open_year(self)
        self.backdate()

    def assert_held_at_year_end(self, line):
        """Assert one line is the seedling worth 1.0800 held on 31 March."""
        self.assertEqual(
            (f'{line.quantity:.9f}', f'{line.value:.4f}', f'{line.original_cost:.4f}', line.provisional),
            ('1.000000000', '1.0800', '1.0800', False),
        )


class PlantStockAtTheBalanceDateTests(PlantStockTestCase):
    """What the plant cost on 31 March, whatever the spring then charged it.

    The value was read as it stood when the capture ran, so an input posted in
    April, a reversal of one posted in March, and any re-division of the batch
    moved a figure that had already been filed — silently, and with nothing on
    the line to say so.
    """

    def test_a_plant_untouched_since_the_balance_date_is_unchanged(self):
        """The control: one seedling worth 1.0800 then and now."""
        self.assert_held_at_year_end(self.capture()[self.plant.pk])

    def test_an_input_posted_after_the_balance_date_does_not_change_the_value(self):
        """Criterion 1: next year's media is next year's cost.

        The plant carries 1.1600 today. On 31 March the media had not gone on
        it, and the capture read 1.1600 into the closed year.
        """
        self.apply_media([self.cells[0]], '0.04')
        self.reallocate()

        self.assert_held_at_year_end(self.capture()[self.plant.pk])

    def test_an_input_reversed_after_the_balance_date_still_counts(self):
        """Criterion 2: the media was on the plant in March.

        Taking it back off in September does not unwind March, so the closed
        year keeps 1.1600 although the plant carries 1.0800 now.
        """
        application = self.apply_media([self.cells[0]], '0.04')
        self.backdate()
        reverse_application(application, self.user, 'Applied to the wrong batch.')

        line = self.capture()[self.plant.pk]
        self.assertEqual(
            (f'{line.value:.4f}', line.provisional), ('1.1600', False),
        )

    def test_a_plant_the_ledger_had_not_costed_is_provisional(self):
        """Criterion 3: standing on 31 March with no cost divided onto it yet.

        The seedling's own record says it was there, and was typed while it
        was, but no layer had been posted against it by the balance date.
        Filing a zero would state a cost the ledger had not worked out; filing
        today's would value the closed year out of next year's run. So the
        plant is counted, the value is withheld, and the line says which.
        """
        CostAllocation.objects.filter(specific_plant=self.plant).update(
            created=SEPTEMBER, effective_at=SEPTEMBER,
        )

        line = self.capture()[self.plant.pk]
        self.assertEqual(f'{line.quantity:.9f}', '1.000000000')
        self.assertIsNone(line.original_cost)
        self.assertEqual(f'{line.value:.4f}', '0.0000')
        self.assertTrue(line.provisional)
        self.assertIn('No cost layer stood against this plant', line.assumptions)
        # And a provisional line is still what holds the year open.
        codes = [row['code'] for row in build_report(self.income_year)['data_quality']]
        self.assertIn('provisional_stock', codes)

    def test_an_unpriced_input_still_marks_the_plant_provisional(self):
        """What was provisional before stays provisional, for its own reason."""
        CostAllocation.objects.filter(specific_plant=self.plant).update(
            unit_cost=None, amount=None,
        )

        line = self.capture()[self.plant.pk]
        self.assertTrue(line.provisional)
        self.assertEqual(f'{line.value:.4f}', '0.0000')

    def test_a_plant_culled_after_the_balance_date_was_stock_in_march(self):
        """The membership replay already reads the fact's own date.

        Nothing here changed: `germinated` and every lifecycle event carry an
        operator's `occurred_at`, so a seedling culled in September was stock
        on 31 March, at what it was worth then.
        """
        record_lifecycle_event(
            self.plant, self.user,
            OutcomeRequest(EventType.CULLED, reason='Damped off in the spring.'),
        )

        self.assert_held_at_year_end(self.capture()[self.plant.pk])

    def test_a_plant_culled_before_the_balance_date_captures_no_line(self):
        """The other half of the same replay: it was not there on 31 March."""
        record_lifecycle_event(
            self.plant, self.user,
            OutcomeRequest(EventType.CULLED, occurred_at=MARCH, reason='Damped off.'),
        )

        self.assertEqual(self.capture(), {})

    def test_a_plant_that_came_up_after_the_balance_date_is_not_captured(self):
        """Stock that was not there cannot be closing stock."""
        later = self.germinate(self.sowing, self.cells[0])[0]
        self.reallocate()

        lines = self.capture()
        self.assertNotIn(later.pk, lines)
        # And the seedling that was there keeps what it carried while it was
        # the cell's only output, although the cell now divides over two.
        self.assert_held_at_year_end(lines[self.plant.pk])


class PlantShareOfTheBatchTests(CohortStockTestCase):
    """A plant's share moves when anything re-divides the batch it came from.

    One unit of the block of four is promoted in January, so the batch's 1.0800
    divides over three anonymous units and one named plant at 0.2700 each.
    Everything the spring then does to the block reaches the plant through the
    same recalculation, which is why reading one of them as at the balance date
    and the other as it stands would not add up.
    """

    capture = capture_plant_lines
    backdate = backdate_plants

    def setUp(self):
        super().setUp()
        self.income_year = open_year(self)
        self.plant = self.promote_one()
        self.backdate()

    def observe_sibling(self, quantity=3, occurred_at=None):
        """Observe a second block on the same batch, and return it.

        This is what provably re-divides a plant's share. A recount does not:
        the split is over the outputs a batch has, and adjusting how many units
        one of them holds leaves the same outputs in place. Nor does a sale or
        a loss, which keep the unit as an output and only re-target its cost.
        A new block is a new output, so the cell's seed and media divide over
        more of them and every existing share shrinks.
        """
        block, _operation = observe_cohort(
            self.workspace, self.user, batch=self.batch, quantity=quantity,
            idempotency_key=uuid4(), occurred_at=occurred_at,
        )
        return block

    def assert_promoted_share(self, line, provisional=False):
        """Assert the line is the quarter of the batch the plant took in January."""
        self.assertEqual(
            (f'{line.value:.4f}', line.provisional), ('0.2700', provisional),
        )

    def test_a_plant_promoted_before_the_balance_date_is_captured(self):
        """The control: a quarter of 1.0800, with the block holding the rest."""
        line = self.capture()[self.plant.pk]
        self.assert_promoted_share(line)
        self.assertEqual(f'{line.original_cost:.4f}', '0.2700')

    def test_a_sibling_block_observed_after_the_balance_date_leaves_the_share_alone(self):
        """Stock that came up in the spring divides the spring's batch, not March's.

        The new block is a new output, so the plant's live share drops away
        from a quarter of 1.0800. On 31 March the batch had four outputs and
        the plant was one of them, worth 0.2700.
        """
        self.observe_sibling()

        self.assert_promoted_share(self.capture()[self.plant.pk])

    def test_a_sibling_the_clamp_held_back_flags_the_plant(self):
        """The one case the two dates disagree, and it says so instead of hiding it.

        The spring's sale is typed first and a block dated 20 March after it,
        so the block's run cannot be dated before the layers it withdraws and
        is clamped to the sale's day. The plant's share really moved on 20
        March and the ledger holds it in September, and there is no figure
        that reflects the new block and not the sale to publish, because that
        version was never written. So the line keeps the as-at reading and is
        flagged, which holds the year open until somebody re-costs the batch.
        """
        self.cohort.refresh_from_db()
        self.sell()
        self.observe_sibling(occurred_at=MARCH)

        line = self.capture()[self.plant.pk]
        self.assert_promoted_share(line, provisional=True)
        self.assertIn('recorded after a later one', line.assumptions)
        codes = [row['code'] for row in build_report(self.income_year)['data_quality']]
        self.assertIn('provisional_stock', codes)

    def test_the_batch_reconciles_across_both_halves_of_the_capture(self):
        """The invariant the shared reading exists for: one batch, one total.

        A layer's amount is a share of a whole batch, so the batch is the
        level the two halves have to agree at. Read the block as at the
        balance date and the plant as it stands and the new block's
        re-division lands in one line and not the other.
        """
        self.observe_sibling()

        capture_inventory(self.income_year, self.user)
        captured = sum(
            (line.value for line in StockValuationLine.objects.filter(
                income_year=self.income_year,
                source_type__in=('plant_cohort', 'specific_plant'),
            )),
            Decimal('0'),
        )
        self.assertEqual(f'{captured:.4f}', '1.0800')
