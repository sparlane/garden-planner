"""Opening a fill of a tray, cleaning it, and correcting a mistaken clean."""
# pylint: disable=duplicate-code

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from applications.models import InputApplication
from applications.services import (
    ApplicationRequest,
    LineRequest,
    TargetRequest,
    create_application_draft,
    post_application,
)
from inventory.ledger import physical_balance, reverse_movement
from inventory.models import InventoryItem, StockLot, StockMovement
from inventory.units import UnitCode
from plantings.lifecycle import (
    LifecycleState,
    plant_lifecycle_summary,
    record_germination_event,
)
from plantings.models import SeedTrayPlanting, SpecificPlant
from seeds.services import ensure_packet_inventory_identity
from tests.factories import (
    make_batch_for_packet,
    make_inventory_item,
    make_location,
    make_seed_packet,
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_cell_planting,
    make_seed_tray_generation,
    make_seed_tray_model,
    make_seed_tray_planting,
    make_specific_plant,
    make_specific_plant_location,
    make_stock_lot,
)
from workspaces.models import Workspace

from .generation_costs import generation_cost_breakdown
from .generations import (
    CloseRequest,
    Disposition,
    Kind,
    MediaDisposition,
    PlantDisposition,
    SeedDisposition,
    close_generation,
    contents_digest,
    generation_contents,
    open_generation,
    open_generation_for,
    reopen_generation,
    require_open_generation,
    review_generation,
)
from .models import SeedTrayGeneration, SeedTrayGenerationEvent


class OpenGenerationTests(TestCase):
    """Filling a tray is an explicit act with a record behind it."""

    def setUp(self):
        self.tray = make_seed_tray()
        self.user = get_user_model().objects.create_user('filler', password='x')

    def test_the_first_fill_is_numbered_from_one(self):
        """A tray's fills are numbered so its history reads in order."""
        generation = open_generation(self.tray, self.user)

        self.assertEqual(generation.sequence, 1)
        self.assertEqual(generation.code, f'TRAY-{self.tray.pk}-1')
        self.assertEqual(generation.status, SeedTrayGeneration.Status.OPEN)
        self.assertEqual(generation.origin, SeedTrayGeneration.Origin.OPERATOR)
        self.assertEqual(generation.review_state, SeedTrayGeneration.ReviewState.NONE)
        self.assertEqual(generation.created_by, self.user)

    def test_opening_records_why_the_fill_exists(self):
        """The history starts at the moment the tray was filled."""
        generation = open_generation(self.tray, self.user)

        event = generation.events.get()
        self.assertEqual(event.event_type, SeedTrayGenerationEvent.EventType.OPENED)
        self.assertEqual(event.occurred_at, generation.opened_at)
        self.assertEqual(event.created_by, self.user)

    def test_a_tray_cannot_be_filled_twice_over(self):
        """The second fill would inherit the first one's seedlings and media."""
        existing = open_generation(self.tray, self.user)

        with self.assertRaises(ValidationError) as caught:
            open_generation(self.tray, self.user)

        self.assertIn(existing.code, ' '.join(caught.exception.messages))
        self.assertEqual(SeedTrayGeneration.objects.filter(tray=self.tray).count(), 1)

    def test_refilling_a_cleaned_tray_takes_the_next_number(self):
        """Reuse is the point; the numbering keeps the cycles apart."""
        first = open_generation(self.tray, self.user)
        SeedTrayGeneration.objects.filter(pk=first.pk).update(
            status=SeedTrayGeneration.Status.CLOSED,
            closed_at=timezone.now(),
        )

        second = open_generation(self.tray, self.user)

        self.assertEqual(second.sequence, 2)
        self.assertEqual(second.code, f'TRAY-{self.tray.pk}-2')

    def test_an_empty_tray_reports_no_open_fill(self):
        """Nothing pretends a tray is filled when it is not."""
        self.assertIsNone(open_generation_for(self.tray))

        with self.assertRaises(ValidationError) as caught:
            require_open_generation(self.tray)

        self.assertIn('no open generation', ' '.join(caught.exception.messages))

    def test_the_error_field_follows_the_caller(self):
        """An application reports this under its own targets field."""
        with self.assertRaises(ValidationError) as caught:
            require_open_generation(self.tray, field='targets')

        self.assertIn('targets', caught.exception.message_dict)


class ReviewGenerationTests(TestCase):
    """A migrated fill stays flagged until somebody confirms it."""

    def setUp(self):
        self.user = get_user_model().objects.create_user('reviewer', password='x')
        self.generation = make_seed_tray_generation(
            origin=SeedTrayGeneration.Origin.LEGACY,
            review_state=SeedTrayGeneration.ReviewState.NEEDS_REVIEW,
        )

    def test_reviewing_clears_the_flag_and_records_who_said_so(self):
        """The confirmation is an operator's statement, so it is kept."""
        generation = review_generation(
            self.generation,
            self.user,
            'Checked against the sowing notebook; one fill.',
        )

        self.assertEqual(generation.review_state, SeedTrayGeneration.ReviewState.NONE)
        event = generation.events.get(
            event_type=SeedTrayGenerationEvent.EventType.REVIEWED,
        )
        self.assertEqual(event.created_by, self.user)
        self.assertIn('sowing notebook', event.reason)

    def test_a_review_needs_a_reason(self):
        """An unexplained confirmation is indistinguishable from a guess."""
        with self.assertRaises(ValidationError) as caught:
            review_generation(self.generation, self.user, '   ')

        self.assertIn('reason', caught.exception.message_dict)
        self.generation.refresh_from_db()
        self.assertEqual(
            self.generation.review_state,
            SeedTrayGeneration.ReviewState.NEEDS_REVIEW,
        )

    def test_a_reviewed_generation_cannot_be_reviewed_again(self):
        """Repeating it would append a second confirmation of nothing."""
        review_generation(self.generation, self.user, 'Confirmed.')

        with self.assertRaises(ValidationError) as caught:
            review_generation(self.generation, self.user, 'Confirmed again.')

        self.assertIn('review_state', caught.exception.message_dict)


class GenerationContentsTestCase(TestCase):  # pylint: disable=too-many-instance-attributes
    """One filled tray with media, seedlings, and leftover seed in it."""

    def setUp(self):
        """Fill one two-cell tray and stock the media it draws on."""
        super().setUp()
        self.workspace = Workspace.objects.get(pk=1)
        self.user = get_user_model().objects.create_user('cleaner', password='x')
        self.location = make_location()
        self.media_item = make_inventory_item(
            base_unit=UnitCode.LITRE,
            default_usage_basis=InventoryItem.UsageBasis.CELL_VOLUME,
        )
        self.media_lot = make_stock_lot(
            item=self.media_item,
            location=self.location,
            quantity='50',
            base_unit_cost=Decimal('2'),
        )
        tray_model = make_seed_tray_model(cell_size_ml=40, x_cells=2, y_cells=1)
        self.tray = make_seed_tray(model=tray_model)
        self.generation = open_generation(self.tray, self.user)
        self.cells = [
            make_seed_tray_cell(tray=self.tray, x_position=index)
            for index in range(2)
        ]

    def apply_media(self, quantity='0.08'):
        """Post one media application filling both cells of the tray."""
        application = create_application_draft(
            self.workspace,
            self.user,
            ApplicationRequest(
                applied_at=timezone.now(),
                source_location=self.location,
                lines=(LineRequest(
                    item=self.media_item,
                    lot=self.media_lot,
                    applied_quantity=Decimal(quantity),
                    unit_code=UnitCode.LITRE,
                    targets=tuple(
                        TargetRequest('seed_tray_cell', cell)
                        for cell in self.cells
                    ),
                ),),
            ),
        )
        return post_application(application, self.user)[0]

    def sow(self, quantity=4, allocations=((0, 2),)):
        """Sow into this fill, allocating the given (cell index, count) pairs."""
        packet = ensure_packet_inventory_identity(make_seed_packet())
        sowing = make_seed_tray_planting(
            seeds_used=packet,
            batch=make_batch_for_packet(packet),
            quantity=quantity,
            seed_tray=self.tray,
            generation=self.generation,
        )
        for index, count in allocations:
            make_seed_tray_cell_planting(
                seed_tray_planting=sowing,
                cell=self.cells[index],
                quantity=count,
            )
        return sowing

    def germinate(self, sowing, cell_index=0):
        """Observe one plant in a cell and put it there."""
        cell_planting = sowing.cell_plantings.get(cell=self.cells[cell_index])
        plant = make_specific_plant(cell_planting=cell_planting)
        record_germination_event(plant, self.user)
        make_specific_plant_location(specific_plant=plant)
        return plant

    def close(self, **overrides):
        """Clean the tray with a fully specified set of dispositions."""
        contents = generation_contents(self.generation)
        values = {
            'reason': 'End of the propagation run.',
            'plants': tuple(
                PlantDisposition(plant.pk, 'failed', 'Did not size up.')
                for plant in contents['plants']
            ),
            'seeds': tuple(
                SeedDisposition(
                    row['sowing'].pk,
                    row['quantity'],
                    Disposition.REMOVED,
                    'Swept up.',
                )
                for row in contents['seeds']
            ),
            'media': tuple(
                MediaDisposition(
                    row['lot'].pk,
                    row['base_quantity'],
                    Disposition.WASTE,
                    'Tipped out.',
                )
                for row in contents['media']
            ),
        }
        values.update(overrides)
        return close_generation(self.generation, self.user, CloseRequest(**values))


class CleanGenerationTests(GenerationContentsTestCase):  # pylint: disable=too-many-public-methods
    """Emptying a tray resolves everything in it, and deletes nothing."""

    def test_contents_report_what_is_still_in_the_tray(self):
        """The confirmation screen is built from this, so it has to be complete."""
        self.apply_media()
        sowing = self.sow(quantity=4, allocations=((0, 2),))
        plant = self.germinate(sowing)

        contents = generation_contents(self.generation)

        self.assertEqual([row.pk for row in contents['plants']], [plant.pk])
        self.assertEqual(
            [(row['sowing'].pk, row['quantity']) for row in contents['seeds']],
            [(sowing.pk, 2)],
        )
        self.assertEqual(
            [(row['lot'].pk, row['base_quantity']) for row in contents['media']],
            [(self.media_lot.pk, Decimal('0.080000000'))],
        )

    def test_a_plant_already_planted_out_does_not_hold_up_the_clean(self):
        """It is not in the tray, so asking to dispose of it makes no sense."""
        sowing = self.sow()
        plant = self.germinate(sowing)
        plant.locations.update(ended=timezone.now())

        contents = generation_contents(self.generation)

        self.assertEqual(contents['plants'], [])

    def test_cleaning_closes_the_fill_and_records_why(self):
        """The close is a fact with a stated reason, kept for good."""
        generation, following = self.close()

        self.assertEqual(generation.status, SeedTrayGeneration.Status.CLOSED)
        self.assertIsNotNone(generation.closed_at)
        self.assertEqual(generation.close_reason, 'End of the propagation run.')
        self.assertEqual(generation.closed_by, self.user)
        self.assertIsNone(following)
        event = generation.events.get(
            event_type=SeedTrayGenerationEvent.EventType.CLOSED,
        )
        self.assertEqual(event.reason, 'End of the propagation run.')

    def test_every_remaining_plant_needs_an_explicit_outcome(self):
        """Nothing is quietly assumed to have failed."""
        sowing = self.sow()
        plant = self.germinate(sowing)

        with self.assertRaises(ValidationError) as caught:
            self.close(plants=())

        self.assertIn(str(plant.pk), ' '.join(caught.exception.messages))
        self.generation.refresh_from_db()
        self.assertEqual(self.generation.status, SeedTrayGeneration.Status.OPEN)

    def test_a_resolved_plant_leaves_the_cell_it_was_sitting_in(self):
        """The tray is being emptied, so nothing can still be in a cell."""
        sowing = self.sow()
        plant = self.germinate(sowing)

        self.close()

        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.FAILED)
        self.assertFalse(plant.locations.filter(ended__isnull=True).exists())

    def test_a_retained_plant_keeps_its_state_but_leaves_the_tray(self):
        """Retention does not close a location, but an emptied tray does."""
        sowing = self.sow()
        plant = self.germinate(sowing)

        self.close(plants=(PlantDisposition(plant.pk, 'retained', 'Keeping it.'),))

        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.RETAINED)
        self.assertFalse(plant.locations.filter(ended__isnull=True).exists())

    def test_every_leftover_seed_needs_an_explicit_disposition(self):
        """Seed drawn from the packet went somewhere; the record says where."""
        sowing = self.sow(quantity=4, allocations=((0, 2),))

        with self.assertRaises(ValidationError) as caught:
            self.close(seeds=())

        self.assertIn(str(sowing.pk), ' '.join(caught.exception.messages))

    def test_leftover_seed_thrown_away_moves_no_stock(self):
        """Sowing already consumed it; a second row would double count."""
        self.sow(quantity=4, allocations=((0, 2),))
        packet_lot = self.generation.sowings.get().seeds_used.stock_lot
        before = StockMovement.objects.filter(lot=packet_lot).count()

        self.close()

        residual = self.generation.residuals.get(kind=Kind.SEED)
        self.assertEqual(residual.disposition, Disposition.REMOVED)
        self.assertEqual(residual.base_quantity, Decimal('2.000000000'))
        self.assertIsNone(residual.movement)
        self.assertEqual(
            StockMovement.objects.filter(lot=packet_lot).count(),
            before,
        )

    def test_leftover_seed_put_back_returns_to_its_packet(self):
        """It physically came back, so the ledger has to say so."""
        sowing = self.sow(quantity=4, allocations=((0, 2),))
        packet = sowing.seeds_used

        self.close(seeds=(SeedDisposition(
            sowing.pk,
            2,
            Disposition.RETURNED,
            'Back in the packet.',
        ),))

        residual = self.generation.residuals.get(kind=Kind.SEED)
        self.assertIsNotNone(residual.movement)
        self.assertEqual(
            residual.movement.movement_type,
            StockMovement.MovementType.ADJUSTMENT_GAIN,
        )
        self.assertEqual(residual.movement.destination, packet.storage_location)
        self.assertEqual(residual.movement.quantity, Decimal('2.000000000'))

    def test_more_seed_than_was_left_over_is_refused(self):
        """Putting back stock that never existed would invent inventory."""
        sowing = self.sow(quantity=4, allocations=((0, 2),))

        with self.assertRaises(ValidationError) as caught:
            self.close(seeds=(SeedDisposition(
                sowing.pk,
                5,
                Disposition.RETURNED,
                'Too many.',
            ),))

        self.assertIn('more than the', ' '.join(caught.exception.messages))

    def test_every_applied_lot_of_media_needs_a_disposition(self):
        """Media does not vanish because the tray was tipped out."""
        self.apply_media()

        with self.assertRaises(ValidationError) as caught:
            self.close(media=())

        self.assertIn('account for all', ' '.join(caught.exception.messages))

    def test_discarded_media_records_no_movement(self):
        """The application consumed it already."""
        self.apply_media()
        before = physical_balance(self.media_lot, self.location)

        self.close()

        residual = self.generation.residuals.get(kind=Kind.MEDIA)
        self.assertEqual(residual.disposition, Disposition.WASTE)
        self.assertEqual(residual.base_quantity, Decimal('0.080000000'))
        self.assertEqual(residual.unit_cost, Decimal('2'))
        self.assertIsNone(residual.movement)
        self.assertEqual(physical_balance(self.media_lot, self.location), before)

    def test_reclaimed_media_goes_back_on_the_shelf(self):
        """Both the quantity and its cost return to the lot it came from."""
        self.apply_media()
        before = physical_balance(self.media_lot, self.location)

        self.close(media=(MediaDisposition(
            self.media_lot.pk,
            Decimal('0.08'),
            Disposition.RECLAIMED,
            'Scooped back into the bag.',
            destination=self.location,
        ),))

        residual = self.generation.residuals.get(kind=Kind.MEDIA)
        self.assertEqual(
            residual.movement.movement_type,
            StockMovement.MovementType.ADJUSTMENT_GAIN,
        )
        self.assertEqual(
            physical_balance(self.media_lot, self.location),
            before + Decimal('0.08'),
        )

    def test_recovering_media_needs_somewhere_to_put_it(self):
        """Stock has to come back to a place, not to nowhere."""
        self.apply_media()

        with self.assertRaises(ValidationError) as caught:
            self.close(media=(MediaDisposition(
                self.media_lot.pk,
                Decimal('0.08'),
                Disposition.RECLAIMED,
                'Kept it.',
            ),))

        self.assertIn('destination', caught.exception.message_dict)

    def test_cleaning_deletes_nothing(self):
        """The archive is a filter on status, not a loss."""
        self.apply_media()
        sowing = self.sow()
        plant = self.germinate(sowing)
        applications = list(InputApplication.objects.values_list('pk', flat=True))
        movements = StockMovement.objects.count()

        self.close()

        self.assertTrue(SeedTrayPlanting.objects.filter(pk=sowing.pk).exists())
        self.assertTrue(SpecificPlant.objects.filter(pk=plant.pk).exists())
        self.assertEqual(
            list(InputApplication.objects.values_list('pk', flat=True)),
            applications,
        )
        self.assertGreaterEqual(StockMovement.objects.count(), movements)
        self.assertEqual(self.generation.sowings.count(), 1)

    def test_cleaning_twice_is_refused_without_half_applying(self):
        """A resubmitted confirmation must not resolve anything a second time."""
        self.apply_media()
        self.close()
        residuals = self.generation.residuals.count()

        with self.assertRaises(ValidationError) as caught:
            close_generation(
                self.generation,
                self.user,
                CloseRequest(reason='Again.'),
            )

        self.assertIn('already closed', ' '.join(caught.exception.messages))
        self.assertEqual(self.generation.residuals.count(), residuals)

    def test_a_stale_confirmation_is_refused(self):
        """The operator has to see what they are deciding about."""
        digest = contents_digest(generation_contents(self.generation))
        self.sow()

        with self.assertRaises(ValidationError) as caught:
            self.close(digest=digest)

        self.assertIn('changed after', ' '.join(caught.exception.messages))

    def test_a_current_confirmation_is_accepted(self):
        """Nothing moved, so the operator saw what they decided about."""
        self.sow()
        digest = contents_digest(generation_contents(self.generation))

        generation, _ = self.close(digest=digest)

        self.assertEqual(generation.status, SeedTrayGeneration.Status.CLOSED)

    def test_a_migrated_fill_cannot_be_cleaned_before_review(self):
        """Its contents may belong to two cycles nobody has separated yet."""
        SeedTrayGeneration.objects.filter(pk=self.generation.pk).update(
            origin=SeedTrayGeneration.Origin.LEGACY,
            review_state=SeedTrayGeneration.ReviewState.NEEDS_REVIEW,
        )
        self.generation.refresh_from_db()

        with self.assertRaises(ValidationError) as caught:
            self.close()

        self.assertIn('review_state', caught.exception.message_dict)

    def test_a_clean_needs_a_reason(self):
        """The closing reason is the one thing the history cannot derive."""
        with self.assertRaises(ValidationError) as caught:
            self.close(reason='  ')

        self.assertIn('reason', caught.exception.message_dict)

    def test_the_tray_can_be_refilled_in_the_same_step(self):
        """Filling again is the usual next act, and it is still its own fill."""
        generation, following = self.close(open_next=True)

        self.assertEqual(generation.status, SeedTrayGeneration.Status.CLOSED)
        self.assertEqual(following.sequence, 2)
        self.assertEqual(following.status, SeedTrayGeneration.Status.OPEN)
        self.assertEqual(following.sowings.count(), 0)


class ReopenGenerationTests(GenerationContentsTestCase):
    """A mistaken clean is corrected by appending, never by erasing."""

    def test_reopening_leaves_the_close_on_file(self):
        """The mistake and its correction are both visible afterwards."""
        generation, _ = self.close()
        closed_at = generation.closed_at

        generation = reopen_generation(generation, self.user, 'Cleaned the wrong tray.')

        self.assertEqual(generation.status, SeedTrayGeneration.Status.OPEN)
        self.assertIsNone(generation.closed_at)
        self.assertEqual(generation.close_reason, '')
        closed = generation.events.get(
            event_type=SeedTrayGenerationEvent.EventType.CLOSED,
        )
        self.assertEqual(closed.occurred_at, closed_at)
        self.assertEqual(closed.reason, 'End of the propagation run.')
        reopened = generation.events.get(
            event_type=SeedTrayGenerationEvent.EventType.REOPENED,
        )
        self.assertEqual(reopened.reason, 'Cleaned the wrong tray.')

    def test_reopening_corrects_the_outcomes_the_clean_recorded(self):
        """The seedling is growing again, and the mistake stays readable."""
        sowing = self.sow()
        plant = self.germinate(sowing)
        generation, _ = self.close()

        reopen_generation(generation, self.user, 'Wrong tray.')

        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.GROWING)
        self.assertTrue(
            plant.lifecycle_events.filter(event_type='failed').exists(),
        )
        self.assertTrue(
            plant.lifecycle_events.filter(event_type='corrected').exists(),
        )

    def test_reopening_takes_back_the_stock_the_clean_recovered(self):
        """Media put back on a mistaken clean was never really recovered."""
        self.apply_media()
        before = physical_balance(self.media_lot, self.location)
        generation, _ = self.close(media=(MediaDisposition(
            self.media_lot.pk,
            Decimal('0.08'),
            Disposition.RECLAIMED,
            'Scooped back.',
            destination=self.location,
        ),))

        reopen_generation(generation, self.user, 'Wrong tray.')

        self.assertEqual(physical_balance(self.media_lot, self.location), before)
        residual = generation.residuals.get(kind=Kind.MEDIA)
        self.assertIsNotNone(residual.reversed_movement)

    def test_the_residual_record_survives_the_correction(self):
        """What the operator said happened stays on file either way."""
        self.apply_media()
        generation, _ = self.close()

        reopen_generation(generation, self.user, 'Wrong tray.')

        residual = generation.residuals.get(kind=Kind.MEDIA)
        self.assertEqual(residual.base_quantity, Decimal('0.080000000'))
        self.assertEqual(residual.disposition, Disposition.WASTE)

    def test_returned_seed_is_taken_back_out_of_the_packet(self):
        """An unopened packet's container is negative by design, not by error.

        Sowing takes seed out of a container whose contents were never counted,
        so the balance goes below zero. Correcting a clean that put seed back
        has to be able to take it out again anyway.
        """
        sowing = self.sow(quantity=4, allocations=((0, 2),))
        packet = sowing.seeds_used
        generation, _ = self.close(seeds=(SeedDisposition(
            sowing.pk,
            2,
            Disposition.RETURNED,
            'Back in the packet.',
        ),))
        returned = physical_balance(packet.stock_lot, packet.storage_location)

        reopen_generation(generation, self.user, 'Wrong tray.')

        self.assertEqual(
            physical_balance(packet.stock_lot, packet.storage_location),
            returned - Decimal('2'),
        )

    def test_a_closed_location_is_not_reopened(self):
        """Where a plant has been remains true; a replacement is recorded."""
        sowing = self.sow()
        plant = self.germinate(sowing)
        generation, _ = self.close()

        reopen_generation(generation, self.user, 'Wrong tray.')

        self.assertFalse(plant.locations.filter(ended__isnull=True).exists())

    def test_an_open_generation_cannot_be_reopened(self):
        """There is nothing to correct."""
        with self.assertRaises(ValidationError) as caught:
            reopen_generation(self.generation, self.user, 'Why not.')

        self.assertIn('status', caught.exception.message_dict)

    def test_a_correction_needs_a_reason(self):
        """Undoing an audited action without saying why is not an audit trail."""
        generation, _ = self.close()

        with self.assertRaises(ValidationError) as caught:
            reopen_generation(generation, self.user, '')

        self.assertIn('reason', caught.exception.message_dict)

    def test_a_refilled_tray_blocks_the_correction(self):
        """Two open fills of one tray is the state this feature forbids."""
        generation, _ = self.close(open_next=True)

        with self.assertRaises(ValidationError) as caught:
            reopen_generation(generation, self.user, 'Wrong tray.')

        self.assertIn('filled again', ' '.join(caught.exception.messages))

    def test_the_recovered_movement_cannot_be_reversed_on_its_own(self):
        """It belongs to the clean, so it comes back through the correction."""
        self.apply_media()
        generation, _ = self.close(media=(MediaDisposition(
            self.media_lot.pk,
            Decimal('0.08'),
            Disposition.RECLAIMED,
            'Scooped back.',
            destination=self.location,
        ),))
        movement = generation.residuals.get(kind=Kind.MEDIA).movement

        with self.assertRaises(ValidationError) as caught:
            reverse_movement(movement, self.user, 'Standalone.')

        self.assertIn('through their generation', ' '.join(caught.exception.messages))


class GenerationCostTests(GenerationContentsTestCase):
    """Media cost reaches the seedlings of one fill, and no others."""

    def test_a_fill_carries_only_its_own_media_cost(self):
        """Two litres a litre over 0.08 litres is 0.16 across two cells."""
        self.apply_media()
        sowing = self.sow(quantity=4, allocations=((0, 2), (1, 2)))
        plant = self.germinate(sowing, cell_index=0)

        breakdown = generation_cost_breakdown(self.generation)

        self.assertEqual(breakdown['applied_cost'], Decimal('0.160000000000'))
        self.assertFalse(breakdown['unknown_cost'])
        self.assertEqual(
            breakdown['plants'],
            [{'plant': plant.pk, 'cost': Decimal('0.080000000000')}],
        )
        self.assertEqual(breakdown['allocated_cost'], Decimal('0.080000000000'))
        self.assertEqual(breakdown['unallocated_cost'], Decimal('0.080000000000'))

    def test_plants_sharing_a_cell_share_its_media(self):
        """A multigerm cluster splits one cell's cost without changing counts."""
        self.apply_media()
        sowing = self.sow(quantity=4, allocations=((0, 2),))
        first = self.germinate(sowing)
        second = self.germinate(sowing)

        breakdown = generation_cost_breakdown(self.generation)

        self.assertEqual(
            breakdown['plants'],
            [
                {'plant': first.pk, 'cost': Decimal('0.040000000000')},
                {'plant': second.pk, 'cost': Decimal('0.040000000000')},
            ],
        )
        self.assertEqual(sowing.quantity, 4)
        self.assertEqual(SpecificPlant.objects.count(), 2)

    def test_an_empty_cell_is_provisional_until_the_fill_is_closed(self):
        """A seedling may still come up in it."""
        self.apply_media()
        sowing = self.sow(quantity=4, allocations=((0, 2), (1, 2)))
        self.germinate(sowing, cell_index=0)

        breakdown = generation_cost_breakdown(self.generation)

        empty = [row for row in breakdown['cells'] if not row['plants']]
        self.assertTrue(all(row['provisional'] for row in empty))
        self.assertEqual(breakdown['production_loss'], Decimal('0'))

    def test_a_closed_fill_turns_its_empty_cells_into_production_loss(self):
        """Nothing more will come up, so the cost has to land somewhere."""
        self.apply_media()
        sowing = self.sow(quantity=4, allocations=((0, 2), (1, 2)))
        plant = self.germinate(sowing, cell_index=0)
        self.close(plants=(PlantDisposition(plant.pk, 'retained', 'Kept.'),))
        self.generation.refresh_from_db()

        breakdown = generation_cost_breakdown(self.generation)

        empty = [row for row in breakdown['cells'] if not row['plants']]
        self.assertFalse(any(row['provisional'] for row in empty))
        self.assertEqual(breakdown['wasted_cost'], Decimal('0.160000000000'))

    def test_the_next_fill_starts_with_no_cost_carried_over(self):
        """Reusing the tray must not hand the old crop's media to the new one."""
        self.apply_media()
        sowing = self.sow(quantity=4, allocations=((0, 2),))
        self.germinate(sowing)
        _, following = self.close(open_next=True)

        breakdown = generation_cost_breakdown(following)

        self.assertEqual(breakdown['applied_cost'], Decimal('0'))
        self.assertEqual(breakdown['media'], [])
        self.assertEqual(breakdown['plants'], [])

    def test_a_lot_with_no_recorded_cost_reports_unknown(self):
        """A zero would quietly understate every total built on it."""
        StockLot.objects.filter(pk=self.media_lot.pk).update(base_unit_cost=None)
        self.media_lot.refresh_from_db()
        self.apply_media()

        breakdown = generation_cost_breakdown(self.generation)

        self.assertTrue(breakdown['unknown_cost'])
        self.assertIsNone(breakdown['media'][0]['cost'])
        self.assertEqual(breakdown['applied_cost'], Decimal('0'))
