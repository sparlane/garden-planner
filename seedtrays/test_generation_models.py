"""Storage-level guarantees for tray generations and their records.

These tests exercise the database constraints directly rather than the service
layer, because the invariants they protect — one open generation per tray, an
append-only history, a residual that cannot be edited — have to survive a shell
session and a bad migration as well as a well-behaved API call.
"""
# pylint: disable=duplicate-code

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from applications.models import InputApplicationTarget
from inventory.models import StockMovement
from plantings.models import SeedTrayPlanting
from seeds.services import ensure_packet_inventory_identity
from tests.factories import (
    make_batch_for_packet,
    make_inventory_location,
    make_seed_packet,
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_generation,
    make_seed_tray_planting,
    make_stock_lot,
)

from .models import SeedTrayGeneration, SeedTrayGenerationResidual


class SeedTrayGenerationConstraintTests(TestCase):
    """One tray may reuse its cells only one cultivation cycle at a time.

    Every insert here goes through ``bulk_create``, which skips ``save()``. The
    point is to prove the database itself refuses these rows, so a shell session
    or a future service that forgets the check still cannot write them.
    """

    def setUp(self):
        self.tray = make_seed_tray()

    def _insert(self, **overrides):
        """Insert one generation straight into the table, bypassing save()."""
        values = {
            'workspace_id': self.tray.workspace_id,
            'tray': self.tray,
            'code': f'TRAY-{self.tray.pk}-x',
            'sequence': 9,
            'opened_at': timezone.now(),
        }
        values.update(overrides)
        SeedTrayGeneration.objects.bulk_create([SeedTrayGeneration(**values)])

    def test_a_tray_can_have_only_one_open_generation(self):
        """The database refuses a second open fill of the same tray."""
        make_seed_tray_generation(tray=self.tray)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._insert(sequence=2, code=f'TRAY-{self.tray.pk}-2')

    def test_a_tray_can_have_many_closed_generations(self):
        """Reuse over the tray's life is exactly what the model is for."""
        first = make_seed_tray_generation(tray=self.tray)
        SeedTrayGeneration.objects.filter(pk=first.pk).update(
            status=SeedTrayGeneration.Status.CLOSED,
            closed_at=timezone.now(),
        )
        second = make_seed_tray_generation(tray=self.tray, sequence=2)
        SeedTrayGeneration.objects.filter(pk=second.pk).update(
            status=SeedTrayGeneration.Status.CLOSED,
            closed_at=timezone.now(),
        )
        third = make_seed_tray_generation(tray=self.tray, sequence=3)

        self.assertEqual(self.tray.generations.count(), 3)
        self.assertEqual(third.sequence, 3)

    def test_a_sequence_cannot_repeat_within_one_tray(self):
        """Numbering a tray's fills twice would make history ambiguous."""
        generation = make_seed_tray_generation(tray=self.tray)
        SeedTrayGeneration.objects.filter(pk=generation.pk).update(
            status=SeedTrayGeneration.Status.CLOSED,
            closed_at=timezone.now(),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._insert(sequence=1, code=f'TRAY-{self.tray.pk}-repeat')

    def test_saving_reports_a_second_open_generation_as_a_field_error(self):
        """The service layer gets a readable error, not a database failure."""
        make_seed_tray_generation(tray=self.tray)
        with self.assertRaises(ValidationError):
            SeedTrayGeneration.objects.create(
                tray=self.tray,
                code=f'TRAY-{self.tray.pk}-2',
                sequence=2,
                opened_at=timezone.now(),
            )

    def test_a_closed_generation_must_carry_a_closing_stamp(self):
        """Status and the closing timestamp cannot disagree."""
        generation = make_seed_tray_generation(tray=self.tray)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SeedTrayGeneration.objects.filter(pk=generation.pk).update(
                    status=SeedTrayGeneration.Status.CLOSED,
                )

    def test_a_generation_stays_on_the_tray_it_was_opened_for(self):
        """Moving a fill to another tray would move its media with it."""
        generation = make_seed_tray_generation(tray=self.tray)
        generation.tray = make_seed_tray()
        with self.assertRaises(ValidationError) as caught:
            generation.save()
        self.assertIn('tray', caught.exception.message_dict)

    def test_a_generation_requires_a_tray_in_its_own_workspace(self):
        """Cross-workspace tray ownership is refused before it is stored."""
        generation = SeedTrayGeneration(
            tray=self.tray,
            code='TRAY-X-1',
            sequence=1,
            opened_at=timezone.now(),
        )
        generation.workspace_id = self.tray.workspace_id + 1
        with self.assertRaises(ValidationError):
            generation.save()


class SeedTrayGenerationEventTests(TestCase):
    """The generation's history is append-only."""

    def test_events_cannot_be_edited_or_deleted(self):
        """A close stays readable next to the correction that undid it."""
        generation = make_seed_tray_generation()
        event = generation.events.get()

        event.reason = 'Something else'
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()

        event.refresh_from_db()
        self.assertEqual(event.reason, 'Created for tests.')

    def test_a_generation_with_events_cannot_be_deleted(self):
        """History protects the row it describes."""
        generation = make_seed_tray_generation()
        with self.assertRaises(Exception):  # pylint: disable=broad-exception-caught
            with transaction.atomic():
                generation.delete()


class SeedTrayGenerationResidualTests(TestCase):
    """What was left over is recorded once and never rewritten."""

    def setUp(self):
        self.generation = make_seed_tray_generation()
        self.lot = make_stock_lot()

    def _residual(self, **overrides):
        values = {
            'generation': self.generation,
            'kind': SeedTrayGenerationResidual.Kind.MEDIA,
            'disposition': SeedTrayGenerationResidual.Disposition.WASTE,
            'lot': self.lot,
            'base_quantity': Decimal('2.5'),
            'base_unit': self.lot.item.base_unit,
            'unit_cost': self.lot.base_unit_cost,
            'reason': 'Contaminated.',
        }
        values.update(overrides)
        return SeedTrayGenerationResidual.objects.create(**values)

    def test_discarded_media_records_no_stock_movement(self):
        """The application already consumed it; a second row would double count."""
        before = StockMovement.objects.filter(lot=self.lot).count()

        residual = self._residual()

        self.assertIsNone(residual.movement)
        self.assertEqual(StockMovement.objects.filter(lot=self.lot).count(), before)

    def test_a_residual_cannot_be_edited_or_deleted(self):
        """A recorded disposition is a fact, corrected by a reversal instead."""
        residual = self._residual()

        residual.base_quantity = Decimal('99')
        with self.assertRaises(ValidationError):
            residual.save()
        with self.assertRaises(ValidationError):
            residual.delete()

        residual.refresh_from_db()
        self.assertEqual(residual.base_quantity, Decimal('2.500000000'))

    def test_media_cannot_take_a_seed_disposition(self):
        """Media is wasted or reclaimed; seed is removed or returned."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SeedTrayGenerationResidual.objects.bulk_create([
                    SeedTrayGenerationResidual(
                        generation=self.generation,
                        kind=SeedTrayGenerationResidual.Kind.MEDIA,
                        disposition=SeedTrayGenerationResidual.Disposition.RETURNED,
                        lot=self.lot,
                        base_quantity=Decimal('1'),
                        base_unit=self.lot.item.base_unit,
                    ),
                ])

    def test_a_seed_residual_names_the_sowing_it_was_drawn_for(self):
        """Loose seed belongs to the sowing that took it out of the packet."""
        packet = ensure_packet_inventory_identity(make_seed_packet())
        sowing = make_seed_tray_planting(
            seeds_used=packet,
            batch=make_batch_for_packet(packet),
            seed_tray=self.generation.tray,
            generation=self.generation,
        )
        residual = self._residual(
            kind=SeedTrayGenerationResidual.Kind.SEED,
            disposition=SeedTrayGenerationResidual.Disposition.REMOVED,
            lot=packet.stock_lot,
            base_unit=packet.stock_lot.item.base_unit,
            unit_cost=packet.stock_lot.base_unit_cost,
            sowing=sowing,
        )

        self.assertEqual(residual.sowing, sowing)

    def test_a_seed_residual_from_another_generation_is_refused(self):
        """Leftover seed belongs to the fill whose sowing drew it."""
        other = make_seed_tray_generation()
        sowing = make_seed_tray_planting(
            seed_tray=other.tray,
            generation=other,
        )
        with self.assertRaises(ValidationError) as caught:
            self._residual(
                kind=SeedTrayGenerationResidual.Kind.SEED,
                disposition=SeedTrayGenerationResidual.Disposition.REMOVED,
                sowing=sowing,
            )
        self.assertIn('sowing', caught.exception.message_dict)

    def test_a_recovering_disposition_requires_its_movement(self):
        """Stock that came physically back has to appear in the ledger."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SeedTrayGenerationResidual.objects.bulk_create([
                    SeedTrayGenerationResidual(
                        generation=self.generation,
                        kind=SeedTrayGenerationResidual.Kind.MEDIA,
                        disposition=SeedTrayGenerationResidual.Disposition.WASTE,
                        lot=self.lot,
                        base_quantity=Decimal('1'),
                        base_unit=self.lot.item.base_unit,
                        movement=StockMovement.objects.create(
                            workspace=self.generation.workspace,
                            lot=self.lot,
                            movement_type=StockMovement.MovementType.ADJUSTMENT_GAIN,
                            quantity=Decimal('1'),
                            destination=make_inventory_location(),
                            occurred_at=timezone.now(),
                        ),
                    ),
                ])


class SowingGenerationLinkTests(TestCase):
    """A sowing names the fill of the tray it went into."""

    def test_a_generation_must_belong_to_the_sowing_tray(self):
        """Cells and media would otherwise cross between two trays."""
        planting = make_seed_tray_planting()
        planting.generation = make_seed_tray_generation()
        with self.assertRaises(ValidationError) as caught:
            planting.full_clean()
        self.assertIn('generation', caught.exception.message_dict)

    def test_a_generation_cannot_be_recorded_without_a_tray(self):
        """A fill of nothing in particular is not a fill."""
        generation = make_seed_tray_generation()
        packet = make_seed_packet()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SeedTrayPlanting.objects.bulk_create([
                    SeedTrayPlanting(
                        workspace=packet.workspace,
                        seeds_used=packet,
                        batch=make_batch_for_packet(packet),
                        quantity=1,
                        seed_tray=None,
                        generation=generation,
                    ),
                ])

    def test_a_generation_in_use_cannot_be_deleted(self):
        """Sowings protect the fill they were made into."""
        generation = make_seed_tray_generation()
        make_seed_tray_planting(
            seed_tray=generation.tray,
            generation=generation,
        )
        with self.assertRaises(Exception):  # pylint: disable=broad-exception-caught
            with transaction.atomic():
                generation.delete()


class ApplicationTargetGenerationTests(TestCase):
    """A cell target records which fill of its tray was using it."""

    def test_only_a_cell_target_carries_a_generation(self):
        """A batch or a garden square has no tray fill to attribute."""
        generation = make_seed_tray_generation()
        target = InputApplicationTarget(
            target_type=InputApplicationTarget.TargetType.GARDEN_SQUARE,
            seed_tray_generation=generation,
        )
        with self.assertRaises(ValidationError) as caught:
            target.clean()
        self.assertIn('seed_tray_generation', caught.exception.message_dict)

    def test_the_generation_must_be_a_fill_of_the_cell_tray(self):
        """Attributing one tray's media to another tray's crop is refused."""
        cell = make_seed_tray_cell()
        target = InputApplicationTarget(
            target_type=InputApplicationTarget.TargetType.SEED_TRAY_CELL,
            seed_tray_cell=cell,
            seed_tray_generation=make_seed_tray_generation(),
        )
        with self.assertRaises(ValidationError) as caught:
            target.clean()
        self.assertIn('seed_tray_generation', caught.exception.message_dict)
