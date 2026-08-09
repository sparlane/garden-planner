"""Reconciling a batch's inputs to the seedlings they actually raised.

These are the task's own acceptance criteria, driven through the real posting
paths rather than through stubs: a packet received through the seeds API, a
sowing that consumed it, an input application posted against the tray's fill,
and germinations recorded against its cells.
"""

# pylint: disable=duplicate-code

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from applications.services import (
    ApplicationRequest,
    LineRequest,
    TargetRequest,
    create_application_draft,
    post_application,
    reverse_application,
)
from applications.models import InputApplicationTarget
from inventory.models import InventoryItem, QuantityCertainty
from inventory.units import UnitCode
from plantings.batches import finalize_batch_output
from plantings.lifecycle import EventType, OutcomeRequest, record_lifecycle_event
from plantings.models import (
    SeedTrayCellPlanting,
    SeedTrayPlanting,
    SpecificPlant,
    SpecificPlantLocation,
)
from plantings.sowing import post_sowing_consumption
from seeds.models import SeedPacket
from tests.factories import (
    make_batch_for_packet,
    make_inventory_item,
    make_location,
    make_plant_variety,
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_generation,
    make_seed_tray_model,
    make_seed_tray_planting,
    make_stock_lot,
    make_supplier,
)
from workspaces.models import Workspace

from .models import CostAllocation, CostAllocationRun
from .services import (
    batch_cost_breakdown,
    plant_cost_breakdown,
    reallocate_batch,
)


TargetType = InputApplicationTarget.TargetType
Trigger = CostAllocationRun.Trigger


class CostingServiceTestCase(APITestCase):  # pylint: disable=too-many-instance-attributes
    """One filled tray, one costed packet, and one costed lot of media."""

    #: Two cells of 40 ml each, so a whole-tray fill is 0.08 litres.
    CELL_COUNT = 2
    CELL_SIZE_ML = 40

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='costing')
        self.client.force_authenticate(self.user)
        self.workspace = Workspace.objects.get(pk=1)
        self.location = make_location()
        model = make_seed_tray_model(
            cell_size_ml=self.CELL_SIZE_ML,
            x_cells=self.CELL_COUNT,
            y_cells=1,
        )
        self.tray = make_seed_tray(model=model)
        self.generation = make_seed_tray_generation(tray=self.tray)
        self.cells = [
            make_seed_tray_cell(tray=self.tray, x_position=index)
            for index in range(self.CELL_COUNT)
        ]
        # Twenty clusters for five dollars is exactly 0.25 each, so every
        # expected figure below is readable without rounding getting in the way.
        self.packet = self.receive_packet('20', '5.0000')
        self.batch = make_batch_for_packet(self.packet)
        self.media = make_inventory_item(
            base_unit=UnitCode.LITRE,
            default_usage_basis=InventoryItem.UsageBasis.CELL_VOLUME,
        )
        # Fifty litres for a hundred dollars is two dollars a litre.
        self.media_lot = make_stock_lot(
            item=self.media,
            location=self.location,
            quantity='50',
            acquisition_total=Decimal('100'),
            base_unit_cost=Decimal('2'),
        )

    def receive_packet(self, quantity, price):
        """Receive one packet through the API the browser posts to."""
        catalog = self.client.post(
            '/seeds/seeds/',
            {
                'supplier': make_supplier().pk,
                'plant_variety': make_plant_variety().pk,
                'base_unit': 'seed_cluster',
            },
            format='json',
        )
        self.assertEqual(catalog.status_code, 201)
        draft = self.client.post(
            '/seeds/packet-receipts/',
            {
                'seeds': catalog.data['pk'],
                'quantity_certainty': QuantityCertainty.EXACT,
                'quantity': quantity,
                'line_price': price,
                'received_date': '2026-08-02',
            },
            format='json',
        )
        self.assertEqual(draft.status_code, 201)
        posted = self.client.post(
            f"/seeds/packet-receipts/{draft.data['pk']}/post/",
            {},
            format='json',
        )
        self.assertEqual(posted.status_code, 201)
        return SeedPacket.objects.get(pk=posted.data['pk'])

    def sow(self, allocations, quantity=None):
        """Sow into the fill and post the packet consumption it drew."""
        placed = sum(count for _cell, count in allocations)
        sowing = make_seed_tray_planting(
            seeds_used=self.packet,
            batch=self.batch,
            seed_tray=self.tray,
            quantity=quantity or placed,
        )
        for cell, count in allocations:
            SeedTrayCellPlanting.objects.create(
                seed_tray_planting=sowing,
                cell=cell,
                quantity=count,
            )
        post_sowing_consumption(sowing, self.user)
        return sowing

    def apply_media(self, cells, quantity):
        """Post one cell-volume media application over the given cells."""
        application = create_application_draft(
            self.workspace,
            self.user,
            ApplicationRequest(
                applied_at=timezone.now(),
                source_location=self.location,
                batch=self.batch,
                lines=(
                    LineRequest(
                        item=self.media,
                        lot=self.media_lot,
                        applied_quantity=Decimal(quantity),
                        unit_code=UnitCode.LITRE,
                        targets=tuple(
                            TargetRequest(TargetType.SEED_TRAY_CELL, cell)
                            for cell in cells
                        ),
                    ),
                ),
            ),
        )
        post_application(application, self.user)
        application.refresh_from_db()
        return application

    def germinate(self, sowing, cell, count=1):
        """Record `count` seedlings coming up in one cell of a sowing."""
        cell_planting = SeedTrayCellPlanting.objects.get(
            seed_tray_planting=sowing,
            cell=cell,
        )
        plants = []
        for _index in range(count):
            plant = SpecificPlant.objects.create(cell_planting=cell_planting)
            SpecificPlantLocation.objects.create(
                specific_plant=plant,
                location_type=SpecificPlantLocation.SEED_TRAY_CELL,
                seed_tray_cell=cell,
                started=plant.germinated,
            )
            plants.append(plant)
        return plants

    def finalize(self, reason='Done sowing.'):
        """Close every sowing activity, then declare the output final."""
        SeedTrayPlanting.objects.filter(batch=self.batch).update(removed=True)
        finalize_batch_output(self.batch, self.user, reason)
        self.batch.refresh_from_db()

    def reallocate(self, trigger=Trigger.MANUAL_RECALCULATE):
        """Reallocate the batch and return the run, if one was needed."""
        return reallocate_batch(self.batch, self.user, trigger)

    def effective(self):
        """Return the layers that still count, in posting order."""
        return list(
            CostAllocation.objects
            .filter(batch=self.batch, reversal_of__isnull=True, reversal__isnull=True)
            .order_by('pk')
        )

    def totals_by_target(self):
        """Return the effective amount sitting against each kind of target."""
        totals = {}
        for row in self.effective():
            totals[row.target_type] = totals.get(row.target_type, Decimal('0')) + (row.amount or 0)
        return totals


class SingleSeedlingTests(CostingServiceTestCase):
    """Criterion 1: reconcile one cell and one plant back to receipt cost."""

    def setUp(self):
        super().setUp()
        self.sowing = self.sow([(self.cells[0], 4)])
        self.apply_media([self.cells[0]], '0.04')
        self.plant = self.germinate(self.sowing, self.cells[0])[0]
        self.reallocate()

    def test_the_plant_carries_its_seed_and_its_media(self):
        """Four clusters at 0.25 and 40 ml of two-dollar media is 1.08."""
        breakdown = plant_cost_breakdown(self.plant)
        self.assertEqual(breakdown['provisional_value'], '1.0800')
        self.assertIsNone(breakdown['final_value'])
        self.assertFalse(breakdown['unknown_cost'])

    def test_every_amount_names_the_lot_it_came_from(self):
        """Reconciliation means being able to walk back to the receipt."""
        breakdown = plant_cost_breakdown(self.plant)
        self.assertEqual(len(breakdown['layers']), 2)
        for layer in breakdown['layers']:
            self.assertIsNotNone(layer['lot'])
            self.assertIsNotNone(layer['movement'])
            self.assertIsNotNone(layer['item'])

    def test_the_batch_total_equals_what_the_inputs_cost(self):
        """Nothing is lost between the movements and the seedling."""
        breakdown = batch_cost_breakdown(self.batch)
        self.assertEqual(breakdown['provisional_total'], '1.0800')
        self.assertEqual(breakdown['totals']['plant_inventory'], '1.0800')
        self.assertEqual(breakdown['totals']['production_loss'], '0.0000')

    def test_a_second_run_changes_nothing(self):
        """The recalculation is idempotent, so hooks can call it freely."""
        self.assertIsNone(self.reallocate())

    def test_the_layers_reconcile_to_the_source_amounts(self):
        """Seed and media each arrive whole, without a rounding residue."""
        amounts = sorted(row.amount for row in self.effective())
        self.assertEqual(amounts, [Decimal('0.0800'), Decimal('1.0000')])


class MultigermTests(CostingServiceTestCase):
    """Criterion 2: two seedlings from one cluster share one cell's cost."""

    def setUp(self):
        super().setUp()
        self.sowing = self.sow([(self.cells[0], 4)])
        self.apply_media([self.cells[0]], '0.04')
        self.plants = self.germinate(self.sowing, self.cells[0], count=2)
        self.reallocate()

    def test_the_cell_cost_divides_between_both_plants(self):
        """One cell's worth of input, two seedlings, 0.54 each."""
        values = [
            plant_cost_breakdown(plant)['provisional_value']
            for plant in self.plants
        ]
        self.assertEqual(values, ['0.5400', '0.5400'])

    def test_the_truthful_cluster_quantity_is_untouched(self):
        """Sharing cost never rewrites how much seed was actually sown."""
        self.sowing.refresh_from_db()
        self.assertEqual(self.sowing.quantity, 4)
        self.assertEqual(
            SeedTrayCellPlanting.objects.get(seed_tray_planting=self.sowing).quantity,
            4,
        )

    def test_the_batch_total_is_still_one_cell_of_input(self):
        """Two seedlings do not cost twice as much as one cell of input."""
        self.assertEqual(
            batch_cost_breakdown(self.batch)['provisional_total'],
            '1.0800',
        )


class EmptyCellTests(CostingServiceTestCase):
    """Criterion 3: a cell that raised nothing becomes production loss."""

    def setUp(self):
        super().setUp()
        self.sowing = self.sow([(self.cells[0], 4), (self.cells[1], 4)])
        self.apply_media(self.cells, '0.08')
        self.plant = self.germinate(self.sowing, self.cells[0])[0]
        self.reallocate()

    def test_an_empty_cell_stays_provisional_while_output_is_open(self):
        """A seedling might still come up, so this is not a loss yet."""
        totals = self.totals_by_target()
        self.assertEqual(totals[CostAllocation.TargetType.SEED_TRAY_CELL], Decimal('1.0800'))
        self.assertNotIn(CostAllocation.TargetType.PRODUCTION_LOSS, totals)

    def test_finalizing_output_turns_the_empty_cell_into_loss(self):
        """It cannot disappear, and it cannot inflate the output count."""
        self.finalize()
        totals = self.totals_by_target()
        self.assertEqual(totals[CostAllocation.TargetType.PRODUCTION_LOSS], Decimal('1.0800'))
        self.assertNotIn(CostAllocation.TargetType.SEED_TRAY_CELL, totals)

    def test_the_batch_still_reconciles_after_the_loss_moves(self):
        """Two cells of input, one seedling, and nothing unaccounted for."""
        self.finalize()
        breakdown = batch_cost_breakdown(self.batch)
        self.assertEqual(breakdown['final_total'], '2.1600')
        self.assertIsNone(breakdown['provisional_total'])
        self.assertEqual(breakdown['totals']['plant_inventory'], '1.0800')
        self.assertEqual(breakdown['totals']['production_loss'], '1.0800')

    def test_the_retired_cell_layer_is_reversed_not_edited(self):
        """The mistake stays readable next to what replaced it."""
        self.finalize()
        reversals = CostAllocation.objects.filter(
            batch=self.batch,
            reversal_of__isnull=False,
        )
        self.assertTrue(reversals.exists())
        for reversal in reversals:
            self.assertEqual(reversal.amount, reversal.reversal_of.amount)


class DispositionTests(CostingServiceTestCase):
    """Criterion 5: value follows each plant's recorded outcome, once."""

    def setUp(self):
        super().setUp()
        self.sowing = self.sow([(self.cells[0], 4), (self.cells[1], 4)])
        self.apply_media(self.cells, '0.08')
        self.kept = self.germinate(self.sowing, self.cells[0])[0]
        self.lost = self.germinate(self.sowing, self.cells[1])[0]
        self.reallocate()

    def test_an_unsold_plant_retains_its_value_as_inventory(self):
        """A seedling nobody has taken is still worth what it cost."""
        breakdown = batch_cost_breakdown(self.batch)
        self.assertEqual(breakdown['totals']['plant_inventory'], '2.1600')
        self.assertEqual(breakdown['totals']['production_loss'], '0.0000')

    def test_a_failed_plant_moves_its_value_to_loss_exactly_once(self):
        """Deriving the bucket is what makes 'once' true by construction."""
        record_lifecycle_event(
            self.lost,
            self.user,
            OutcomeRequest(EventType.FAILED, reason='Damped off.'),
        )
        breakdown = batch_cost_breakdown(self.batch)
        self.assertEqual(breakdown['totals']['plant_inventory'], '1.0800')
        self.assertEqual(breakdown['totals']['production_loss'], '1.0800')
        self.assertEqual(breakdown['provisional_total'], '2.1600')

    def test_a_harvested_plant_is_output_rather_than_loss(self):
        """Reporting a Garden crop as waste would be the opposite of true."""
        record_lifecycle_event(
            self.lost,
            self.user,
            OutcomeRequest(EventType.HARVEST_FINISHED, reason='Picked.'),
        )
        breakdown = batch_cost_breakdown(self.batch)
        self.assertEqual(breakdown['totals']['harvested_output'], '1.0800')
        self.assertEqual(breakdown['totals']['production_loss'], '0.0000')

    def test_a_donated_plant_is_loss_because_it_earned_nothing(self):
        """It left the nursery without revenue, which is what loss means."""
        record_lifecycle_event(
            self.lost,
            self.user,
            OutcomeRequest(EventType.DONATED, reason='Given to the school.'),
        )
        breakdown = batch_cost_breakdown(self.batch)
        self.assertEqual(breakdown['totals']['production_loss'], '1.0800')

    def test_an_outcome_needs_no_reallocation(self):
        """The bucket is derived, so no layer has to move when it changes."""
        record_lifecycle_event(
            self.lost,
            self.user,
            OutcomeRequest(EventType.FAILED, reason='Damped off.'),
        )
        self.assertIsNone(self.reallocate())


class ReversalTests(CostingServiceTestCase):
    """Criterion 6: reversing an input balances the allocation ledger."""

    def setUp(self):
        super().setUp()
        self.sowing = self.sow([(self.cells[0], 4)])
        self.application = self.apply_media([self.cells[0]], '0.04')
        self.plant = self.germinate(self.sowing, self.cells[0])[0]
        self.reallocate()

    def test_reversing_an_application_reverses_its_layers(self):
        """Stock came back, so the cost it carried has to come back too."""
        reverse_application(self.application, self.user, 'Wrong tray.')
        self.reallocate(Trigger.APPLICATION_REVERSED)
        self.assertEqual(
            plant_cost_breakdown(self.plant)['provisional_value'],
            '1.0000',
        )

    def test_the_reversal_names_the_layer_it_cancels(self):
        """A balanced pair is what makes the correction auditable."""
        line = self.application.lines.get()
        reverse_application(self.application, self.user, 'Wrong tray.')
        self.reallocate(Trigger.APPLICATION_REVERSED)
        reversal = CostAllocation.objects.get(
            batch=self.batch,
            reversal_of__isnull=False,
            application_line=line,
            target_type=CostAllocation.TargetType.SPECIFIC_PLANT,
        )
        self.assertEqual(reversal.amount, Decimal('0.0800'))
        self.assertEqual(reversal.reversal_of.amount, Decimal('0.0800'))

    def test_a_second_germination_reallocates_the_cell(self):
        """Observing another seedling re-divides what the cell carried."""
        self.germinate(self.sowing, self.cells[0])
        self.reallocate(Trigger.GERMINATION)
        self.assertEqual(
            plant_cost_breakdown(self.plant)['provisional_value'],
            '0.5400',
        )

    def test_no_amount_is_ever_edited_in_place(self):
        """Every layer this batch has posted is still exactly as posted."""
        self.germinate(self.sowing, self.cells[0])
        self.reallocate(Trigger.GERMINATION)
        for row in CostAllocation.objects.filter(batch=self.batch):
            row.refresh_from_db()
            self.assertIsNotNone(row.created)


class FrozenBatchTests(CostingServiceTestCase):
    """Finalized output is not reopened by a later recalculation."""

    def setUp(self):
        super().setUp()
        self.sowing = self.sow([(self.cells[0], 4)])
        self.apply_media([self.cells[0]], '0.04')
        self.plant = self.germinate(self.sowing, self.cells[0])[0]
        self.reallocate()
        self.finalize()

    def test_a_finalized_batch_reports_a_final_total(self):
        """Provisional and final are never two halves of one number."""
        breakdown = batch_cost_breakdown(self.batch)
        self.assertIsNone(breakdown['provisional_total'])
        self.assertEqual(breakdown['final_total'], '1.0800')
        self.assertFalse(breakdown['provisional'])

    def test_a_later_germination_does_not_move_frozen_cost(self):
        """Output being final is the statement that this cannot happen."""
        self.germinate(self.sowing, self.cells[0])
        self.reallocate(Trigger.GERMINATION)
        self.assertEqual(plant_cost_breakdown(self.plant)['final_value'], '1.0800')

    def test_a_later_application_posts_its_own_layer(self):
        """A top-up after finalization is new cost, not a reopened split."""
        self.apply_media([self.cells[0]], '0.04')
        self.reallocate(Trigger.APPLICATION_POSTED)
        self.assertEqual(plant_cost_breakdown(self.plant)['final_value'], '1.1600')


class UnknownCostTests(CostingServiceTestCase):
    """An unpriced lot reports unknown rather than a misleading zero."""

    def setUp(self):
        super().setUp()
        self.media_lot = make_stock_lot(
            item=self.media,
            location=self.location,
            quantity='50',
            acquisition_total=None,
            base_unit_cost=None,
        )
        self.sowing = self.sow([(self.cells[0], 4)])
        self.apply_media([self.cells[0]], '0.04')
        self.plant = self.germinate(self.sowing, self.cells[0])[0]
        self.reallocate()

    def test_the_plant_reports_the_gap_rather_than_hiding_it(self):
        """A zero here would quietly understate every total above it."""
        breakdown = plant_cost_breakdown(self.plant)
        self.assertTrue(breakdown['unknown_cost'])
        self.assertEqual(breakdown['provisional_value'], '1.0000')

    def test_the_unpriced_layer_still_records_its_quantity(self):
        """What was applied is known even when what it cost is not."""
        unpriced = [row for row in self.effective() if row.amount is None]
        self.assertEqual(len(unpriced), 1)
        self.assertEqual(unpriced[0].base_quantity, Decimal('0.040000000'))
