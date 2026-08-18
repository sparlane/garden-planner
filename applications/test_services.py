"""Drafting, posting, and reversing input applications."""

# pylint: disable=duplicate-code

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from garden.models import GardenGeometryConfirmation
from inventory.ledger import physical_balance
from inventory.models import InventoryItem, StockMovement
from inventory.units import UnitCode
from plantings.lifecycle import EventType, OutcomeRequest, record_bulk_outcome
from plantings.models import ProductionBatch
from seedtrays.generations import open_generation_for
from seedtrays.models import SeedTrayGeneration
from tests.factories import (
    make_garden_area,
    make_garden_bed,
    make_garden_geometry_confirmation,
    make_garden_square,
    make_inventory_item,
    make_location,
    make_production_batch,
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_cell_planting,
    make_seed_tray_generation,
    make_seed_tray_model,
    make_specific_plant,
    make_stock_lot,
)
from workspaces.models import Workspace

from .models import InputApplication, InputApplicationTarget
from .services import (
    ApplicationRequest,
    LineRequest,
    TargetRequest,
    application_state,
    cells_for_tray,
    create_application_draft,
    post_application,
    reverse_application,
)

TargetType = InputApplicationTarget.TargetType


class ApplicationServiceTestCase(TestCase):
    """Stock, a batch, and helpers for building one document."""

    def setUp(self):
        """Stock one media lot and open a batch every case draws on."""
        super().setUp()
        self.workspace = Workspace.objects.get(pk=1)
        self.location = make_location()
        self.media = make_inventory_item(
            base_unit=UnitCode.LITRE,
            default_usage_basis=InventoryItem.UsageBasis.CELL_VOLUME,
        )
        self.lot = make_stock_lot(item=self.media, location=self.location, quantity='50')
        self.batch = make_production_batch()

    def draft(self, lines, **overrides):
        """Create a draft from the given line requests."""
        values = {
            'applied_at': timezone.now(),
            'source_location': self.location,
            'batch': self.batch,
            'lines': tuple(lines),
        }
        values.update(overrides)
        return create_application_draft(self.workspace, None, ApplicationRequest(**values))

    def media_line(self, targets, **overrides):
        """Build a cell-volume media line over the given targets."""
        values = {
            'item': self.media,
            'lot': self.lot,
            'applied_quantity': Decimal('0.96'),
            'unit_code': UnitCode.LITRE,
            'targets': tuple(targets),
        }
        values.update(overrides)
        return LineRequest(**values)

    def tray_cells(self, count=24, cell_size_ml=40):
        """Create one filled tray and `count` cells of a known volume.

        The tray is filled because media goes into a fill, not into bare cells;
        an unfilled tray is refused, which `GenerationTargetTests` covers.
        """
        model = make_seed_tray_model(cell_size_ml=cell_size_ml, x_cells=count, y_cells=1)
        tray = make_seed_tray(model=model)
        make_seed_tray_generation(tray=tray)
        return [make_seed_tray_cell(tray=tray, x_position=index) for index in range(count)]

    def cell_targets(self, cells):
        """Turn cells into target requests."""
        return [TargetRequest(TargetType.SEED_TRAY_CELL, cell) for cell in cells]


class DraftApplicationTests(ApplicationServiceTestCase):
    """Assembling a draft freezes every measurement it calculates from."""

    def test_a_draft_records_the_calculation(self):
        """Twenty-four 40 ml cells suggest 0.96 litres of media."""
        application = self.draft([self.media_line(self.cell_targets(self.tray_cells()))])
        line = application.lines.get()

        self.assertEqual(application.status, InputApplication.Status.DRAFT)
        self.assertEqual(line.calculated_base_quantity, Decimal('0.960000000'))
        self.assertEqual(line.formula_basis_quantity, Decimal('960.000000000'))
        self.assertEqual(line.formula_basis_unit, UnitCode.MILLILITRE)
        self.assertEqual(line.base_unit, UnitCode.LITRE)

    def test_a_draft_freezes_each_cell_volume(self):
        """Every target carries the volume it measured, not a live lookup."""
        cells = self.tray_cells(count=2)
        application = self.draft([self.media_line(self.cell_targets(cells))])

        volumes = list(
            InputApplicationTarget.objects
            .filter(line__application=application)
            .values_list('cell_volume_ml', flat=True)
        )
        self.assertEqual(volumes, [40, 40])

    def test_a_draft_makes_no_stock_movement(self):
        """Nothing leaves the shelf until the document is posted."""
        targets = self.cell_targets(self.tray_cells())
        before = StockMovement.objects.count()
        self.draft([self.media_line(targets)])
        self.assertEqual(StockMovement.objects.count(), before)

    def test_a_whole_tray_still_names_its_cells(self):
        """The shortcut records which cells were filled, not just the tray."""
        cells = self.tray_cells(count=6)
        application = self.draft([self.media_line(cells_for_tray(cells[0].tray))])

        self.assertEqual(application.lines.get().targets.count(), 6)

    def test_a_draft_summarizes_its_targets(self):
        """The header says what the document went on without opening it."""
        application = self.draft([self.media_line(self.cell_targets(self.tray_cells(count=3)))])
        self.assertEqual(application.target_summary, '3 tray cells')

    def test_a_document_needs_a_line(self):
        """An application that consumes nothing is not an application."""
        with self.assertRaises(ValidationError) as caught:
            self.draft([])
        self.assertIn('lines', caught.exception.message_dict)

    def test_a_tray_model_without_a_cell_volume_is_refused(self):
        """A missing volume cannot silently become zero litres of media."""
        cells = self.tray_cells(count=2, cell_size_ml=0)
        with self.assertRaises(ValidationError) as caught:
            self.draft([self.media_line(self.cell_targets(cells))])
        self.assertIn('targets', caught.exception.message_dict)


class PostApplicationTests(ApplicationServiceTestCase):
    """Posting turns a confirmed draft into ledger movements."""

    def test_posting_consumes_the_confirmed_quantity(self):
        """The operator's number is the inventory fact, not the suggestion."""
        application = self.draft([self.media_line(self.cell_targets(self.tray_cells()))])
        application, movements = post_application(application, None)

        self.assertEqual(application.status, InputApplication.Status.POSTED)
        self.assertIsNotNone(application.posted_at)
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0].movement_type, StockMovement.MovementType.CONSUMPTION)
        self.assertEqual(movements[0].quantity, Decimal('0.960000000'))
        self.assertEqual(physical_balance(self.lot, self.location), Decimal('49.04'))

    def test_waste_posts_a_second_linked_movement(self):
        """Spillage is recorded as its own fact against the same lot."""
        application = self.draft([self.media_line(
            self.cell_targets(self.tray_cells()),
            waste_quantity=Decimal('0.04'),
            waste_reason='Spilled while filling',
        )])
        application, movements = post_application(application, None)

        self.assertEqual(len(movements), 2)
        self.assertEqual(movements[1].movement_type, StockMovement.MovementType.WASTE)
        self.assertEqual(movements[1].quantity, Decimal('0.040000000'))
        self.assertEqual(physical_balance(self.lot, self.location), Decimal('49'))

        line = application.lines.get()
        self.assertEqual(line.consumption_movement, movements[0])
        self.assertEqual(line.waste_movement, movements[1])

    def test_waste_without_a_reason_is_refused(self):
        """Discarded stock has to say why it was discarded."""
        application = self.draft([self.media_line(
            self.cell_targets(self.tray_cells()),
            waste_quantity=Decimal('0.04'),
        )])
        with self.assertRaises(ValidationError) as caught:
            post_application(application, None)
        self.assertIn('lines', caught.exception.message_dict)

    def test_a_material_override_needs_a_reason(self):
        """Using notably more than suggested is what the audit wants explained."""
        application = self.draft([self.media_line(
            self.cell_targets(self.tray_cells()),
            applied_quantity=Decimal('1.5'),
        )])
        with self.assertRaises(ValidationError) as caught:
            post_application(application, None)
        self.assertIn('lines', caught.exception.message_dict)

    def test_an_explained_override_keeps_both_numbers(self):
        """The suggestion and the fact stay visible side by side."""
        application = self.draft([self.media_line(
            self.cell_targets(self.tray_cells()),
            applied_quantity=Decimal('1.5'),
            override_reason='Cells were overfilled to settle the mix',
        )])
        application, _ = post_application(application, None)

        line = application.lines.get()
        self.assertEqual(line.calculated_base_quantity, Decimal('0.960000000'))
        self.assertEqual(line.applied_base_quantity, Decimal('1.500000000'))
        self.assertEqual(line.override_reason, 'Cells were overfilled to settle the mix')

    def test_a_small_difference_needs_no_reason(self):
        """Ordinary imprecision does not interrupt the operator."""
        application = self.draft([self.media_line(
            self.cell_targets(self.tray_cells()),
            applied_quantity=Decimal('0.98'),
        )])
        application, _ = post_application(application, None)
        self.assertEqual(application.status, InputApplication.Status.POSTED)

    def test_posting_more_than_the_lot_holds_is_refused(self):
        """A lot cannot issue stock it does not have."""
        application = self.draft([self.media_line(
            self.cell_targets(self.tray_cells()),
            applied_quantity=Decimal('80'),
            override_reason='Testing the balance guard',
        )])
        with self.assertRaises(ValidationError):
            post_application(application, None)
        self.assertEqual(physical_balance(self.lot, self.location), Decimal('50'))

    def test_a_document_cannot_be_posted_twice(self):
        """Double posting would consume the stock a second time."""
        application = self.draft([self.media_line(self.cell_targets(self.tray_cells()))])
        application, _ = post_application(application, None)
        with self.assertRaises(ValidationError) as caught:
            post_application(application, None)
        self.assertIn('status', caught.exception.message_dict)

    def test_a_cancelled_batch_cannot_receive_an_input(self):
        """A batch that declared it produced nothing cannot consume stock."""
        application = self.draft([self.media_line(self.cell_targets(self.tray_cells()))])
        ProductionBatch.objects.filter(pk=application.batch_id).update(
            status=ProductionBatch.Status.CANCELLED,
        )
        with self.assertRaises(ValidationError) as caught:
            post_application(application, None)
        self.assertIn('batch', caught.exception.message_dict)


class PlantTargetTests(ApplicationServiceTestCase):
    """Applying an input to individual plants."""

    def setUp(self):
        super().setUp()
        self.cell_planting = make_seed_tray_cell_planting()
        self.batch = self.cell_planting.seed_tray_planting.batch
        self.labels = make_inventory_item(
            base_unit=UnitCode.EACH,
            category=InventoryItem.Category.LABEL,
            default_usage_basis=InventoryItem.UsageBasis.PER_UNIT,
            default_usage_rate=Decimal('1'),
            usage_rate_unit=UnitCode.EACH,
        )
        self.label_lot = make_stock_lot(
            item=self.labels,
            location=self.location,
            quantity='500',
        )

    def label_line(self, plants, **overrides):
        """Build a per-unit label line over the given plants."""
        values = {
            'item': self.labels,
            'lot': self.label_lot,
            'applied_quantity': Decimal(len(plants)),
            'unit_code': UnitCode.EACH,
            'targets': tuple(
                TargetRequest(TargetType.SPECIFIC_PLANT, plant) for plant in plants
            ),
        }
        values.update(overrides)
        return LineRequest(**values)

    def batch_plants(self, count):
        """Create plants that came from this document's batch."""
        return [
            make_specific_plant(cell_planting=self.cell_planting)
            for _ in range(count)
        ]

    def test_one_label_per_plant(self):
        """Twelve plants consume twelve labels."""
        application = self.draft([self.label_line(self.batch_plants(12))])
        application, movements = post_application(application, None)

        self.assertEqual(movements[0].quantity, Decimal('12.000000000'))
        self.assertEqual(application.lines.get().calculated_base_quantity, Decimal('12.000000000'))

    def test_a_plant_outside_the_batch_is_refused(self):
        """A document cannot attribute an input to somebody else's crop."""
        stranger = make_specific_plant()
        application = self.draft([self.label_line(self.batch_plants(2) + [stranger])])
        with self.assertRaises(ValidationError) as caught:
            post_application(application, None)
        self.assertIn('targets', caught.exception.message_dict)

    def test_a_finished_plant_cannot_receive_an_input(self):
        """A culled plant is not there to be labelled."""
        plants = self.batch_plants(2)
        application = self.draft([self.label_line(plants)])
        record_bulk_outcome(
            [plants[0].pk],
            None,
            OutcomeRequest(
                EventType.CULLED,
                occurred_at=timezone.now(),
                reason='Damaged',
            ),
        )
        with self.assertRaises(ValidationError) as caught:
            post_application(application, None)
        self.assertIn('targets', caught.exception.message_dict)


class SurfaceAreaTargetTests(ApplicationServiceTestCase):
    """Applying a treatment over measured ground."""

    def setUp(self):
        super().setUp()
        self.treatment = make_inventory_item(
            base_unit=UnitCode.GRAM,
            category=InventoryItem.Category.FERTILIZER_TREATMENT,
            default_usage_basis=InventoryItem.UsageBasis.SURFACE_AREA,
            default_usage_rate=Decimal('2'),
            usage_rate_unit=UnitCode.SQUARE_METRE,
        )
        self.treatment_lot = make_stock_lot(
            item=self.treatment,
            location=self.location,
            quantity='5000',
        )
        # Wide enough for the 300 x 300 mm square these tests measure.
        self.bed = make_garden_bed(
            area=make_garden_area(size_x=300, size_y=300),
            size_x=300,
            size_y=300,
        )

    def treatment_line(self, square, quantity, **overrides):
        """Build a surface-area treatment line over one square."""
        values = {
            'item': self.treatment,
            'lot': self.treatment_lot,
            'applied_quantity': Decimal(quantity),
            'unit_code': UnitCode.GRAM,
            'targets': (TargetRequest(TargetType.GARDEN_SQUARE, square),),
        }
        values.update(overrides)
        return LineRequest(**values)

    def test_unconfirmed_geometry_is_refused(self):
        """An integer of unknown unit never becomes an area silently."""
        square = make_garden_square(bed=self.bed, size_x=300, size_y=300)
        with self.assertRaises(ValidationError) as caught:
            self.draft([self.treatment_line(square, '18')], batch=None)
        self.assertIn('targets', caught.exception.message_dict)

    def test_confirmed_geometry_measures_the_dose(self):
        """A 300 x 300 mm square is 0.09 m2, so 2 g per m2 is 0.18 g."""
        make_garden_geometry_confirmation(
            area=self.bed.area,
            length_unit=GardenGeometryConfirmation.LengthUnit.MILLIMETRE,
            cell_length=Decimal('1'),
        )
        square = make_garden_square(bed=self.bed, size_x=300, size_y=300)
        application = self.draft([self.treatment_line(square, '0.18')], batch=None)

        line = application.lines.get()
        self.assertEqual(line.calculated_base_quantity, Decimal('0.180000000'))
        self.assertEqual(line.targets.get().area_m2, Decimal('0.090000'))

    def test_a_document_needs_no_batch(self):
        """A garden with no production batch can still record an input."""
        make_garden_geometry_confirmation(
            area=self.bed.area,
            length_unit=GardenGeometryConfirmation.LengthUnit.METRE,
            cell_length=Decimal('1'),
        )
        square = make_garden_square(bed=self.bed, size_x=2, size_y=2)
        application = self.draft([self.treatment_line(square, '8')], batch=None)
        application, movements = post_application(application, None)

        self.assertIsNone(application.batch)
        self.assertEqual(movements[0].quantity, Decimal('8.000000000'))


class SnapshotDurabilityTests(ApplicationServiceTestCase):
    """A posted document cannot be rewritten by a later configuration edit."""

    def test_editing_the_tray_model_leaves_the_document_alone(self):
        """The frozen cell volumes are what the calculation replays from."""
        cells = self.tray_cells()
        application = self.draft([self.media_line(self.cell_targets(cells))])
        application, movements = post_application(application, None)

        model = cells[0].tray.model
        model.cell_size_ml = 999
        model.save()

        line = application.lines.get()
        line.refresh_from_db()
        movements[0].refresh_from_db()
        self.assertEqual(line.calculated_base_quantity, Decimal('0.960000000'))
        self.assertEqual(line.formula_basis_quantity, Decimal('960.000000000'))
        self.assertEqual(movements[0].quantity, Decimal('0.960000000'))
        self.assertEqual(application_state(application)['lines'][0]['calculated_base_quantity'], Decimal('0.960000000'))

    def test_editing_the_item_rate_leaves_the_document_alone(self):
        """A recalculation reads the line's own columns, never the catalog."""
        application = self.draft([self.media_line(self.cell_targets(self.tray_cells()))])
        application, _ = post_application(application, None)

        self.media.refresh_from_db()
        self.media.default_usage_basis = InventoryItem.UsageBasis.MANUAL
        self.media.save()

        state = application_state(application)
        self.assertEqual(state['lines'][0]['calculated_base_quantity'], Decimal('0.960000000'))


class ReverseApplicationTests(ApplicationServiceTestCase):
    """Reversal restores stock and keeps the document readable."""

    def posted(self, **overrides):
        """Post one media application with optional waste."""
        application = self.draft([self.media_line(
            self.cell_targets(self.tray_cells()),
            **overrides,
        )])
        return post_application(application, None)[0]

    def test_reversal_restores_the_balance(self):
        """Everything the document took goes back."""
        application = self.posted()
        self.assertEqual(physical_balance(self.lot, self.location), Decimal('49.04'))

        application = reverse_application(application, None, 'Applied to the wrong tray')

        self.assertEqual(application.status, InputApplication.Status.REVERSED)
        self.assertEqual(application.reverse_reason, 'Applied to the wrong tray')
        self.assertEqual(physical_balance(self.lot, self.location), Decimal('50'))

    def test_reversal_restores_waste_too(self):
        """Both movements a line posted are put back together."""
        application = self.posted(
            waste_quantity=Decimal('0.04'),
            waste_reason='Spilled while filling',
        )
        self.assertEqual(physical_balance(self.lot, self.location), Decimal('49'))

        reverse_application(application, None, 'Wrong lot')
        self.assertEqual(physical_balance(self.lot, self.location), Decimal('50'))

    def test_reversal_keeps_the_calculation_and_targets(self):
        """The record of what was intended survives the correction."""
        application = self.posted()
        application = reverse_application(application, None, 'Wrong tray')

        line = application.lines.get()
        self.assertEqual(line.calculated_base_quantity, Decimal('0.960000000'))
        self.assertEqual(line.targets.count(), 24)

    def test_a_reason_is_required(self):
        """A correction that explains nothing is not an audit trail."""
        application = self.posted()
        with self.assertRaises(ValidationError) as caught:
            reverse_application(application, None, '   ')
        self.assertIn('reason', caught.exception.message_dict)

    def test_a_document_cannot_be_reversed_twice(self):
        """The stock would otherwise come back a second time."""
        application = self.posted()
        application = reverse_application(application, None, 'Wrong tray')
        with self.assertRaises(ValidationError) as caught:
            reverse_application(application, None, 'Again')
        self.assertIn('status', caught.exception.message_dict)

    def test_a_draft_cannot_be_reversed(self):
        """There is nothing to put back until it posted."""
        application = self.draft([self.media_line(self.cell_targets(self.tray_cells()))])
        with self.assertRaises(ValidationError) as caught:
            reverse_application(application, None, 'Never mind')
        self.assertIn('status', caught.exception.message_dict)


class ApplicationStateTests(ApplicationServiceTestCase):
    """What a preview reports and what posting revalidates against."""

    def test_state_reports_availability_before_and_after(self):
        """An operator sees what the lot holds and what it will hold."""
        application = self.draft([self.media_line(self.cell_targets(self.tray_cells()))])
        state = application_state(application)

        line = state['lines'][0]
        self.assertEqual(line['available_base_quantity'], Decimal('50'))
        self.assertEqual(line['available_after_base_quantity'], Decimal('49.04'))
        self.assertFalse(line['short'])
        self.assertFalse(line['override_required'])

    def test_state_flags_a_line_the_lot_cannot_cover(self):
        """The shortfall is visible before anything is attempted."""
        application = self.draft([self.media_line(
            self.cell_targets(self.tray_cells()),
            applied_quantity=Decimal('80'),
            override_reason='Deliberately too much',
        )])
        self.assertTrue(application_state(application)['lines'][0]['short'])

    def test_a_stale_revision_is_refused(self):
        """A draft edited in another tab invalidates what the client saw."""
        application = self.draft([self.media_line(self.cell_targets(self.tray_cells()))])
        state = application_state(application)
        InputApplication.objects.filter(pk=application.pk).update(revision=state['revision'] + 1)

        with self.assertRaises(ValidationError) as caught:
            post_application(application, None, revision=state['revision'])
        self.assertIn('revision', caught.exception.message_dict)

    def test_a_stale_availability_digest_is_refused(self):
        """Stock spent elsewhere since the preview invalidates it."""
        application = self.draft([self.media_line(self.cell_targets(self.tray_cells()))])
        state = application_state(application)

        other = self.draft([self.media_line(
            self.cell_targets(self.tray_cells(count=2)),
            applied_quantity=Decimal('0.08'),
        )])
        post_application(other, None)

        with self.assertRaises(ValidationError) as caught:
            post_application(
                application,
                None,
                revision=state['revision'],
                digest=state['availability_digest'],
            )
        self.assertIn('availability_digest', caught.exception.message_dict)

    def test_a_current_digest_posts(self):
        """Nothing moved, so what the operator confirmed still holds."""
        application = self.draft([self.media_line(self.cell_targets(self.tray_cells()))])
        state = application_state(application)

        application, _ = post_application(
            application,
            None,
            revision=state['revision'],
            digest=state['availability_digest'],
        )
        self.assertEqual(application.status, InputApplication.Status.POSTED)


class GenerationTargetTests(ApplicationServiceTestCase):
    """Media applied to a cell is attributed to the fill using that cell."""

    def test_a_cell_target_records_the_fill_it_went_into(self):
        """The cell says where; the generation says which crop was there."""
        cells = self.tray_cells()
        generation = open_generation_for(cells[0].tray)

        application = self.draft([self.media_line(self.cell_targets(cells))])

        targets = application.lines.get().targets.all()
        self.assertTrue(targets)
        for target in targets:
            with self.subTest(target=target.pk):
                self.assertEqual(target.seed_tray_generation_id, generation.pk)

    def test_an_unfilled_tray_cannot_receive_media(self):
        """Attributing media to a fill nobody recorded is the ambiguity itself."""
        model = make_seed_tray_model(cell_size_ml=40, x_cells=2, y_cells=1)
        tray = make_seed_tray(model=model)
        cells = [make_seed_tray_cell(tray=tray, x_position=index) for index in range(2)]

        with self.assertRaises(ValidationError) as caught:
            self.draft([self.media_line(self.cell_targets(cells))])

        self.assertIn('no open generation', ' '.join(caught.exception.messages))

    def test_a_whole_tray_shortcut_records_the_same_fill(self):
        """The expansion goes through the same measurement, so it must agree."""
        cells = self.tray_cells()
        generation = open_generation_for(cells[0].tray)

        application = self.draft([self.media_line(cells_for_tray(cells[0].tray))])

        recorded = set(
            application.lines.get().targets.values_list(
                'seed_tray_generation_id',
                flat=True,
            )
        )
        self.assertEqual(recorded, {generation.pk})

    def test_a_target_of_another_kind_records_no_fill(self):
        """A batch or a garden square has no tray fill to attribute."""
        application = self.draft([LineRequest(
            item=self.media,
            lot=self.lot,
            applied_quantity=Decimal('1'),
            unit_code=UnitCode.LITRE,
            usage_basis=InventoryItem.UsageBasis.MANUAL,
            targets=(TargetRequest(TargetType.BATCH, self.batch),),
        )])

        target = application.lines.get().targets.get()
        self.assertIsNone(target.seed_tray_generation_id)

    def test_a_draft_cannot_be_posted_after_its_tray_is_cleaned(self):
        """Posting would charge this media to a crop no longer in the tray."""
        cells = self.tray_cells()
        generation = open_generation_for(cells[0].tray)
        application = self.draft([self.media_line(self.cell_targets(cells))])
        SeedTrayGeneration.objects.filter(pk=generation.pk).update(
            status=SeedTrayGeneration.Status.CLOSED,
            closed_at=timezone.now(),
        )

        with self.assertRaises(ValidationError) as caught:
            post_application(application, None)

        self.assertIn('have been cleaned', ' '.join(caught.exception.messages))
        application.refresh_from_db()
        self.assertEqual(application.status, InputApplication.Status.DRAFT)

    def test_a_posted_document_keeps_the_fill_it_named(self):
        """Cleaning the tray afterwards must not repoint the history."""
        cells = self.tray_cells()
        generation = open_generation_for(cells[0].tray)
        application = self.draft([self.media_line(self.cell_targets(cells))])
        application, _ = post_application(application, None)
        SeedTrayGeneration.objects.filter(pk=generation.pk).update(
            status=SeedTrayGeneration.Status.CLOSED,
            closed_at=timezone.now(),
        )

        recorded = set(
            application.lines.get().targets.values_list(
                'seed_tray_generation_id',
                flat=True,
            )
        )
        self.assertEqual(recorded, {generation.pk})
