"""A new individual output arriving after a batch declared its output final.

Recorded, its share of an input is posted *beside* the frozen plant's rather
than instead of it, so the same input is charged one and a half times. Two
shapes, both reproduced below as the figures they produced:

- a tray seedling in a cell whose seed and media already sit wholly on a frozen
  plant — seed 1.50 against a 1.00 movement, media 0.12 against 0.08, a $1.08
  batch reporting 1.62;
- a plant individualized out of a direct-sown crop standing on ground a frozen
  plant already holds whole — a 0.80 surface-area treatment reporting 1.20.

Task 147's answer is to refuse the new output while the batch is frozen and to
name `reopen_batch`, the audited way to redo a frozen allocation. These drive
that through the paths an operator has, as `costing.test_services` does, and
each suite keeps one test that reaches past the refusal to pin what it prevents.

`plantings.test_germination.FinalizedBatchGerminationTests` holds the tray paths
over a fixture with no money in it. These hold the money.
"""

# pylint: disable=duplicate-code

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError

from applications.models import InputApplicationTarget
from applications.services import (
    ApplicationRequest,
    LineRequest,
    TargetRequest,
    create_application_draft,
    post_application,
)
from garden.models import GardenGeometryConfirmation
from inventory.models import InventoryItem
from inventory.units import UnitCode
from plantings.batches import finalize_batch_output, reopen_batch
from plantings.direct_sown import (
    individualize_direct_sown_crop,
    record_direct_sown_event,
)
from plantings.garden_status import correct_garden_status, finish_garden_planting
from plantings.lifecycle import record_germination_event
from plantings.models import (
    GardenPlanting,
    GardenPlantingStatusEvent,
    ProductionBatch,
    SpecificPlant,
    SpecificPlantLocation,
)
from tests.factories import (
    make_garden_area,
    make_garden_bed,
    make_garden_geometry_confirmation,
    make_garden_planting,
    make_garden_square,
    make_inventory_item,
    make_stock_lot,
)

from .models import CostAllocation
from .services import batch_cost_breakdown, plant_cost_breakdown
from .test_services import CostingServiceTestCase, Trigger


def totals_by_source(rows):
    """Return the effective amount drawn from each kind of source.

    The companion to `CostingServiceTestCase.totals_by_target`, for a figure
    that has to be read against what an input actually cost rather than against
    where it went: a source charged one and a half times shows here as more than
    it was. A function rather than a method because both suites below measure
    the same thing about different kinds of source.
    """
    totals = {}
    for row in rows:
        totals[row.source_type] = totals.get(row.source_type, Decimal('0')) + (row.amount or 0)
    return totals


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
        sources = totals_by_source(self.effective())
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

    def test_a_seedling_that_got_past_the_refusal_still_raises_the_total(self):
        """What the refusal is worth, in the figures it prevents.

        Every other test here asserts that nothing moved, which is also what a
        batch that simply never gained a seedling looks like. This one writes
        the plant straight through the ORM — the one way past
        `validate_batch_for_new_output`, and what the deployed rows task 134's
        audit lists were written by — and measures the surplus: the seed charged
        1.5000 against a 1.0000 movement, the media 0.1200 against 0.0800, and
        the batch reporting 1.6200 of cost against inputs that had 1.0800. The
        frozen plant keeps its whole 1.0800 and the straggler is handed 0.5400
        nothing paid for.
        """
        self.germinate(self.sowing, self.cells[0])
        self.reallocate(Trigger.GERMINATION)
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '1.6200')
        sources = totals_by_source(self.effective())
        self.assertEqual(sources[CostAllocation.SourceType.SOWING_POSTING], Decimal('1.5000'))
        self.assertEqual(sources[CostAllocation.SourceType.APPLICATION_LINE], Decimal('0.1200'))
        plants = list(SpecificPlant.objects.filter(batch=self.batch).order_by('pk'))
        self.assertEqual(plant_cost_breakdown(plants[0])['final_value'], '1.0800')
        self.assertEqual(plant_cost_breakdown(plants[1])['final_value'], '0.5400')
        with self.assertRaises(AssertionError):
            self.assert_sources_reconcile()


class IndividualizedPlantRefusalTests(CostingServiceTestCase):
    """A frozen batch refuses a plant individualized out of a direct-sown crop.

    The Garden shape of the same defect, and the one the first pass of task 147
    wrongly recorded as needing nothing. A surface-area treatment divides over
    the plants standing on the ground it covers
    (`costing.sources._area_reach` through `_plants_standing_in`), and
    `plantings.direct_sown.individualize_direct_sown_crop` stands every plant it
    creates in the crop's own garden square — so a plant individualized after
    the freeze takes a share of ground the plant already there holds whole.

    One 4 m2 square, 8 g of treatment at 0.10 a gram, one plant individualized
    before the freeze: 0.8000, all of it on that plant.
    """

    #: The spring this crop was grown in. Every date here is a fact about that
    #: season rather than a stand-in for today, and nothing below compares one
    #: with the clock: the freeze happens whenever the suite runs.
    SOWN_ON = date(2026, 3, 1)

    def setUp(self):
        super().setUp()
        # The fixture's batch started today, and an application cannot predate
        # the start of its batch, so the batch is backdated to its own season.
        ProductionBatch.objects.filter(pk=self.batch.pk).update(
            actual_start=self.day(self.SOWN_ON),
        )
        self.batch.refresh_from_db()
        area = make_garden_area(size_x=10, size_y=10)
        make_garden_geometry_confirmation(
            area=area,
            length_unit=GardenGeometryConfirmation.LengthUnit.METRE,
            cell_length=Decimal('1'),
        )
        # Two grid steps each way, one metre a step, so the square is 4 m2 and
        # a two-gram-per-metre dose is eight grams.
        self.square = make_garden_square(
            bed=make_garden_bed(area=area, size_x=10, size_y=10),
            size_x=2,
            size_y=2,
        )
        self.treatment = make_inventory_item(
            base_unit=UnitCode.GRAM,
            category=InventoryItem.Category.FERTILIZER_TREATMENT,
            default_usage_basis=InventoryItem.UsageBasis.SURFACE_AREA,
            default_usage_rate=Decimal('2'),
            usage_rate_unit=UnitCode.SQUARE_METRE,
        )
        # A thousand grams for a hundred dollars is ten cents a gram.
        self.treatment_lot = make_stock_lot(
            item=self.treatment,
            location=self.location,
            quantity='1000',
            acquisition_total=Decimal('100'),
            base_unit_cost=Decimal('0.10'),
        )
        self.crop = make_garden_planting(
            workspace=self.workspace,
            batch=self.batch,
            source=GardenPlanting.Source.DIRECT_SEED,
            tracking=GardenPlanting.Tracking.AGGREGATE,
            quantity=20,
            recorded_on=self.SOWN_ON,
            garden_square=self.square,
        )
        record_direct_sown_event(
            self.crop, self.user, 'emerged', date(2026, 3, 10), 6,
            count_quality='exact',
        )
        self.first = individualize_direct_sown_crop(
            self.crop, self.user, 1, date(2026, 3, 20), ['Keeper one'],
            notes='Keeping the strongest seedling.',
        )[1][0]
        self.treat()
        self.reallocate()
        self.finish = finish_garden_planting(
            self.crop, self.user, GardenPlantingStatusEvent.EventType.FINISHED,
            date(2026, 4, 1), 'Row cleared.',
        )
        finalize_batch_output(self.batch, self.user, 'Done with this row.')
        self.batch.refresh_from_db()

    def day(self, when):
        """Return the start of one calendar day where the workspace keeps time."""
        return datetime.combine(when, time.min, ZoneInfo(self.workspace.timezone))

    def treat(self):
        """Post the surface-area treatment over the crop's whole square."""
        application = create_application_draft(
            self.workspace,
            self.user,
            ApplicationRequest(
                applied_at=self.day(date(2026, 3, 25)),
                source_location=self.location,
                batch=self.batch,
                lines=(
                    LineRequest(
                        item=self.treatment,
                        lot=self.treatment_lot,
                        applied_quantity=Decimal('8'),
                        unit_code=UnitCode.GRAM,
                        targets=(
                            TargetRequest(
                                InputApplicationTarget.TargetType.GARDEN_SQUARE,
                                self.square,
                            ),
                        ),
                    ),
                ),
            ),
        )
        post_application(application, self.user)
        return application

    def unfinish_the_crop(self):
        """Clear `finished_on` the way the hole used to, bypassing the guard.

        `correct_garden_status` is the only service that clears it, and it now
        refuses on a finalized batch, so a test of what stands behind that
        refusal has to write the column directly.
        """
        GardenPlanting.objects.filter(pk=self.crop.pk).update(finished_on=None)
        self.crop.refresh_from_db()

    def stand_a_plant_in_the_square(self):
        """Create a second individualized plant past both refusals."""
        plant = SpecificPlant.objects.create(
            workspace=self.workspace,
            garden_planting=self.crop,
            name='Keeper two',
        )
        SpecificPlantLocation.objects.create(
            specific_plant=plant,
            location_type=SpecificPlantLocation.GARDEN_SQUARE,
            garden_square=self.square,
            started=plant.germinated,
        )
        record_germination_event(plant, self.user)
        return plant

    def test_the_frozen_crop_carries_its_whole_treatment(self):
        """The baseline the surplus is measured against."""
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '0.8000')
        self.assertEqual(plant_cost_breakdown(self.first)['final_value'], '0.8000')
        self.assert_sources_reconcile()

    def test_correcting_the_finish_is_refused_while_the_batch_is_frozen(self):
        """Finalizing required the crop to be finished, so it stays finished."""
        with self.assertRaises(ValidationError) as caught:
            correct_garden_status(
                self.finish, self.user, 'Row was not cleared after all.',
                date(2026, 4, 2),
            )
        message = str(caught.exception)
        self.assertIn(self.batch.code, message)
        self.assertIn('Reopen the batch', message)
        self.crop.refresh_from_db()
        self.assertIsNotNone(self.crop.finished_on)
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '0.8000')

    def test_individualizing_is_refused_even_with_the_crop_made_current(self):
        """The second guard, behind the one that keeps the crop finished."""
        self.unfinish_the_crop()
        with self.assertRaises(ValidationError) as caught:
            individualize_direct_sown_crop(
                self.crop, self.user, 1, date(2026, 4, 2), ['Keeper two'],
                notes='A second keeper.',
            )
        message = str(caught.exception)
        self.assertIn(self.batch.code, message)
        self.assertIn('Reopen the batch', message)
        self.assertIn('individualization', message)
        self.assertEqual(SpecificPlant.objects.filter(batch=self.batch).count(), 1)
        self.assertIsNone(self.reallocate(Trigger.GERMINATION))
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '0.8000')
        self.assert_sources_reconcile()

    def test_a_plant_that_got_past_both_refusals_still_raises_the_total(self):
        """What the two refusals are worth, in the figures they prevent.

        This is the state the reviewer reproduced through `correct-status` and
        `individualize`: the treatment divides over two plants instead of one,
        the frozen plant keeps its whole 0.8000, the new one is handed 0.4000
        nothing paid for, and the batch reports 1.2000 against an 8 g treatment
        that cost 0.8000.
        """
        self.unfinish_the_crop()
        second = self.stand_a_plant_in_the_square()
        self.reallocate(Trigger.GERMINATION)
        self.assertEqual(batch_cost_breakdown(self.batch)['final_total'], '1.2000')
        self.assertEqual(plant_cost_breakdown(self.first)['final_value'], '0.8000')
        self.assertEqual(plant_cost_breakdown(second)['final_value'], '0.4000')
        self.assertEqual(
            totals_by_source(self.effective())[CostAllocation.SourceType.APPLICATION_LINE],
            Decimal('1.2000'),
        )
        with self.assertRaises(AssertionError):
            self.assert_sources_reconcile()

    def test_a_reopened_batch_divides_the_ground_over_both_plants(self):
        """Reopening is the way through here too, and it re-divides."""
        reopen_batch(self.batch, self.user, 'Row was not cleared after all.')
        self.batch.refresh_from_db()
        correct_garden_status(
            self.finish, self.user, 'Row was not cleared after all.',
            date(2026, 4, 2),
        )
        _event, plants = individualize_direct_sown_crop(
            self.crop, self.user, 1, date(2026, 4, 2), ['Keeper two'],
            notes='A second keeper.',
        )
        self.reallocate(Trigger.GERMINATION)
        self.assertEqual(batch_cost_breakdown(self.batch)['provisional_total'], '0.8000')
        self.assertEqual(plant_cost_breakdown(self.first)['provisional_value'], '0.4000')
        self.assertEqual(plant_cost_breakdown(plants[0])['provisional_value'], '0.4000')
        self.assert_sources_reconcile()
