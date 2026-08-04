"""Tests for the harvest record and the services that post and reverse it."""
# pylint: disable=duplicate-code
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from inventory.units import UnitCode
from tests.factories import (
    make_garden_row,
    make_garden_square,
    make_harvest,
    make_harvest_plant,
    make_plant,
    make_plant_family,
    make_plant_variety,
    make_production_batch,
    make_seed_tray_cell_planting,
    make_specific_plant,
    make_specific_plant_location,
)
from workspaces.models import Workspace

from .batches import cancel_batch
from .harvests import (
    HarvestRequest,
    harvest_finished_plant_ids,
    record_harvest,
    reverse_harvest,
)
from .lifecycle import (
    EventType,
    LifecycleState,
    plant_lifecycle_summary,
    record_germination_event,
)
from .models import (
    Harvest,
    HarvestPlant,
    PlantLifecycleEvent,
    ProductionBatch,
    SpecificPlantLocation,
)


class HarvestModelTests(TestCase):
    """A harvest is posted when recorded and never edited afterwards."""

    def setUp(self):
        super().setUp()
        self.batch = make_production_batch()

    def test_a_new_harvest_is_posted_and_stamped(self):
        """Recording a harvest counts it immediately, with no draft step."""
        harvest = make_harvest(batch=self.batch)
        self.assertEqual(harvest.status, Harvest.Status.POSTED)
        self.assertIsNotNone(harvest.posted_at)
        self.assertIsNone(harvest.reversed_at)
        self.assertEqual(harvest.reverse_reason, '')

    def test_saving_an_existing_harvest_is_refused(self):
        """The record is immutable, so a correction must reverse it instead."""
        harvest = make_harvest(batch=self.batch)
        harvest.notes = 'Edited'
        with self.assertRaises(ValidationError) as caught:
            harvest.save()
        self.assertIn('immutable', str(caught.exception))

    def test_deleting_a_harvest_is_refused(self):
        """Yield history survives a mistake rather than disappearing."""
        harvest = make_harvest(batch=self.batch)
        with self.assertRaises(ValidationError):
            harvest.delete()
        self.assertTrue(Harvest.objects.filter(pk=harvest.pk).exists())

    def test_a_zero_quantity_is_rejected(self):
        """A harvest that measured nothing is not an observation."""
        with self.assertRaises(ValidationError) as caught:
            make_harvest(batch=self.batch, quantity=Decimal('0'))
        self.assertIn('quantity', caught.exception.message_dict)

    def test_a_negative_quantity_is_rejected(self):
        """Yield is never negative; a mistake is reversed, not subtracted."""
        with self.assertRaises(ValidationError) as caught:
            make_harvest(batch=self.batch, quantity=Decimal('-1'))
        self.assertIn('quantity', caught.exception.message_dict)

    def test_a_sub_quantum_quantity_is_rejected_as_a_field_error(self):
        """A value the column rounds to zero fails cleanly, not as a crash."""
        with self.assertRaises(ValidationError) as caught:
            make_harvest(batch=self.batch, quantity=Decimal('0.0000000004'))
        self.assertIn('quantity', caught.exception.message_dict)

    def test_input_and_area_units_are_rejected(self):
        """Seed and area units describe inputs and space, never a yield."""
        for unit in (UnitCode.SEED, UnitCode.SEED_CLUSTER, UnitCode.SQUARE_METRE):
            with self.subTest(unit=unit):
                with self.assertRaises(ValidationError) as caught:
                    make_harvest(batch=self.batch, unit_code=unit)
                self.assertIn('unit_code', caught.exception.message_dict)

    def test_every_yield_unit_is_accepted(self):
        """Count, mass, and volume all describe a real crop measurement."""
        for unit in (UnitCode.EACH, UnitCode.GRAM, UnitCode.KILOGRAM,
                     UnitCode.MILLILITRE, UnitCode.LITRE):
            with self.subTest(unit=unit):
                harvest = make_harvest(batch=self.batch, unit_code=unit)
                self.assertEqual(harvest.unit_code, unit)

    def test_a_square_and_a_row_cannot_both_be_recorded(self):
        """One harvest came from one place, so only one location may be named."""
        with self.assertRaises(ValidationError) as caught:
            make_harvest(
                batch=self.batch,
                garden_square=make_garden_square(),
                garden_row=make_garden_row(),
            )
        self.assertIn('garden_row', caught.exception.message_dict)

    def test_the_database_also_refuses_two_locations(self):
        """The single-location rule survives a write that skips validation."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Harvest.objects.bulk_create([Harvest(
                    workspace=self.batch.workspace,
                    batch=self.batch,
                    harvested_at=timezone.now(),
                    quantity=Decimal('1'),
                    unit_code=UnitCode.GRAM,
                    garden_square=make_garden_square(),
                    garden_row=make_garden_row(),
                )])

    def test_the_database_also_refuses_a_zero_quantity(self):
        """The positive-quantity rule survives a write that skips validation."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Harvest.objects.bulk_create([Harvest(
                    workspace=self.batch.workspace,
                    batch=self.batch,
                    harvested_at=timezone.now(),
                    quantity=Decimal('0'),
                    unit_code=UnitCode.GRAM,
                )])

    def test_a_location_is_optional(self):
        """An aggregate batch yield need not name where it came from."""
        harvest = make_harvest(batch=self.batch)
        self.assertIsNone(harvest.garden_square)
        self.assertIsNone(harvest.garden_row)

    def test_a_quality_rating_outside_one_to_five_is_rejected(self):
        """The subjective score is a fixed scale, not an arbitrary number."""
        for rating in (0, 6):
            with self.subTest(rating=rating):
                with self.assertRaises(ValidationError) as caught:
                    make_harvest(batch=self.batch, quality_rating=rating)
                self.assertIn('quality_rating', caught.exception.message_dict)


class HarvestWorkspaceTests(TestCase):
    """Every reference a harvest names belongs to its own workspace."""

    def setUp(self):
        super().setUp()
        self.other = Workspace.objects.create(name='Other workspace')

    def test_a_batch_from_another_workspace_is_rejected(self):
        """A harvest cannot claim yield from somebody else's crop."""
        family = make_plant_family(workspace=self.other)
        plant = make_plant(workspace=self.other, family=family)
        variety = make_plant_variety(workspace=self.other, plant=plant)
        foreign = make_production_batch(workspace=self.other, variety=variety)
        with self.assertRaises(ValidationError) as caught:
            make_harvest(batch=foreign)
        self.assertIn('batch', caught.exception.message_dict)

    def test_a_square_from_another_workspace_is_rejected(self):
        """The growing location is scoped like every other reference."""
        with self.assertRaises(ValidationError) as caught:
            make_harvest(garden_square=make_garden_square(workspace=self.other))
        self.assertIn('garden_square', caught.exception.message_dict)

    def test_a_row_from_another_workspace_is_rejected(self):
        """A row is scoped exactly as a square is."""
        with self.assertRaises(ValidationError) as caught:
            make_harvest(garden_row=make_garden_row(workspace=self.other))
        self.assertIn('garden_row', caught.exception.message_dict)


class HarvestPlantTests(TestCase):
    """An allocation attributes a harvest to plants that batch actually raised."""

    def setUp(self):
        super().setUp()
        self.plant = make_specific_plant()
        self.batch = self.plant.cell_planting.seed_tray_planting.batch

    def test_a_plant_from_the_same_batch_is_accepted(self):
        """Attribution records which of the batch's plants contributed."""
        allocation = make_harvest_plant(plant=self.plant)
        self.assertEqual(allocation.plant, self.plant)
        self.assertEqual(allocation.harvest.batch, self.batch)

    def test_a_plant_from_another_batch_is_rejected(self):
        """A harvest cannot be attributed to a crop it did not come from."""
        harvest = make_harvest(batch=make_production_batch())
        with self.assertRaises(ValidationError) as caught:
            HarvestPlant.objects.create(harvest=harvest, plant=self.plant)
        self.assertIn('plant', caught.exception.message_dict)

    def test_a_plant_from_another_workspace_is_rejected(self):
        """Attribution is scoped like every other cross-model reference."""
        other = Workspace.objects.create(name='Other workspace')
        stranger = make_specific_plant(workspace=other)
        harvest = make_harvest(
            batch=stranger.cell_planting.seed_tray_planting.batch,
        )
        with self.assertRaises(ValidationError) as caught:
            HarvestPlant.objects.create(harvest=harvest, plant=stranger)
        self.assertIn('plant', caught.exception.message_dict)

    def test_the_same_plant_cannot_be_allocated_twice(self):
        """One harvest names each contributing plant once."""
        allocation = make_harvest_plant(plant=self.plant)
        with self.assertRaises(ValidationError):
            HarvestPlant.objects.create(
                harvest=allocation.harvest,
                plant=self.plant,
            )

    def test_an_allocation_is_immutable_and_undeletable(self):
        """Attribution is part of the record the reversal keeps visible."""
        allocation = make_harvest_plant(plant=self.plant)
        with self.assertRaises(ValidationError):
            allocation.save()
        with self.assertRaises(ValidationError):
            allocation.delete()
        self.assertTrue(HarvestPlant.objects.filter(pk=allocation.pk).exists())


class RecordHarvestTests(TestCase):
    """Posting a harvest validates the crop, the time, and the plants."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='harvester')
        self.batch = make_production_batch()
        self.workspace = self.batch.workspace

    def _request(self, **overrides):
        """Build a valid harvest request for this batch."""
        values = {
            'batch': self.batch,
            'harvested_at': timezone.now(),
            'quantity': Decimal('1.5'),
            'unit_code': UnitCode.KILOGRAM,
        }
        values.update(overrides)
        return HarvestRequest(**values)

    def test_a_recorded_harvest_is_posted_immediately(self):
        """Recording is posting; there is no separate document to assemble."""
        harvest, events = record_harvest(self.workspace, self.user, self._request())
        self.assertEqual(harvest.status, Harvest.Status.POSTED)
        self.assertEqual(harvest.created_by, self.user)
        self.assertEqual(events, [])

    def test_the_quantity_is_quantized_before_it_is_validated(self):
        """A value the column would round to zero fails as a field error."""
        with self.assertRaises(ValidationError) as caught:
            record_harvest(
                self.workspace,
                self.user,
                self._request(quantity=Decimal('0.0000000004')),
            )
        self.assertIn('quantity', caught.exception.message_dict)
        self.assertFalse(Harvest.objects.exists())

    def test_a_planned_batch_cannot_be_harvested(self):
        """Nothing has been sown yet, so nothing can have come out."""
        batch = make_production_batch(status=ProductionBatch.Status.PLANNED)
        with self.assertRaises(ValidationError) as caught:
            record_harvest(self.workspace, self.user, self._request(batch=batch))
        self.assertIn('batch', caught.exception.message_dict)

    def test_a_cancelled_batch_cannot_be_harvested(self):
        """A cancelled batch has declared that it produced nothing."""
        batch = make_production_batch(
            status=ProductionBatch.Status.CANCELLED,
            cancelled_at=timezone.now(),
        )
        with self.assertRaises(ValidationError) as caught:
            record_harvest(self.workspace, self.user, self._request(batch=batch))
        self.assertIn('batch', caught.exception.message_dict)

    def test_a_finalized_or_completed_batch_can_still_be_harvested(self):
        """Finalizing output stops seedlings, not fruit."""
        for status in (ProductionBatch.Status.OUTPUT_FINALIZED,
                       ProductionBatch.Status.COMPLETED):
            with self.subTest(status=status):
                batch = make_production_batch(status=status)
                harvest, _events = record_harvest(
                    self.workspace,
                    self.user,
                    self._request(batch=batch),
                )
                self.assertEqual(harvest.batch, batch)

    def test_a_harvest_cannot_predate_its_batch(self):
        """A crop cannot be picked before it was sown."""
        before = self.batch.actual_start - timedelta(days=1)
        with self.assertRaises(ValidationError) as caught:
            record_harvest(
                self.workspace,
                self.user,
                self._request(harvested_at=before),
            )
        self.assertIn('harvested_at', caught.exception.message_dict)

    def test_a_location_is_recorded_when_supplied(self):
        """The square a crop came from is what makes a square report real."""
        square = make_garden_square()
        harvest, _events = record_harvest(
            self.workspace,
            self.user,
            self._request(garden_square=square),
        )
        self.assertEqual(harvest.garden_square, square)


class HarvestPlantSelectionTests(TestCase):
    """Attribution names only plants the harvested batch actually raised."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='picker')
        self.plant = make_specific_plant()
        self.batch = self.plant.cell_planting.seed_tray_planting.batch
        self.workspace = self.batch.workspace
        record_germination_event(self.plant, self.user)

    def _request(self, **overrides):
        """Build a valid count-harvest request for this batch."""
        values = {
            'batch': self.batch,
            'harvested_at': timezone.now(),
            'quantity': Decimal('3'),
            'unit_code': UnitCode.EACH,
        }
        values.update(overrides)
        return HarvestRequest(**values)

    def test_selected_plants_are_allocated(self):
        """The allocation records which plants contributed the yield."""
        harvest, _events = record_harvest(
            self.workspace,
            self.user,
            self._request(plant_ids=(self.plant.pk,)),
        )
        self.assertEqual(
            list(harvest.plant_allocations.values_list('plant_id', flat=True)),
            [self.plant.pk],
        )

    def test_a_plant_from_another_batch_is_refused_by_name(self):
        """The error says which plants did not come from this crop."""
        stranger = make_specific_plant()
        with self.assertRaises(ValidationError) as caught:
            record_harvest(
                self.workspace,
                self.user,
                self._request(plant_ids=(stranger.pk,)),
            )
        self.assertIn('plants', caught.exception.message_dict)
        self.assertIn(str(stranger.pk), str(caught.exception))
        self.assertFalse(Harvest.objects.exists())

    def test_a_direct_sow_batch_has_no_plants_to_allocate(self):
        """Individual plants only exist for tray sowings, never direct sowings."""
        batch = make_production_batch()
        with self.assertRaises(ValidationError) as caught:
            record_harvest(
                self.workspace,
                self.user,
                self._request(batch=batch, plant_ids=(self.plant.pk,)),
            )
        self.assertIn('plants', caught.exception.message_dict)

    def test_repeated_harvests_from_the_same_plants_are_allowed(self):
        """Many crops are picked again and again from a living plant."""
        for _ in range(2):
            record_harvest(
                self.workspace,
                self.user,
                self._request(plant_ids=(self.plant.pk,)),
            )
        self.assertEqual(Harvest.objects.count(), 2)
        self.assertEqual(
            HarvestPlant.objects.filter(plant=self.plant).count(),
            2,
        )
        summary = plant_lifecycle_summary(self.plant)
        self.assertEqual(summary.state, LifecycleState.GROWING)


class FinalHarvestTests(TestCase):
    """A harvest ends a plant only when the caller says it did."""

    def setUp(self):
        """Lay out an explicit timeline so backdating can be tested exactly."""
        super().setUp()
        self.user = get_user_model().objects.create_user(username='finisher')
        self.sown = timezone.now() - timedelta(days=60)
        self.germinated = self.sown + timedelta(days=10)
        cell_planting = make_seed_tray_cell_planting()
        self.batch = cell_planting.seed_tray_planting.batch
        ProductionBatch.objects.filter(pk=self.batch.pk).update(actual_start=self.sown)
        self.batch.refresh_from_db()
        self.plant = make_specific_plant(
            cell_planting=cell_planting,
            germinated=self.germinated,
        )
        self.other = make_specific_plant(
            cell_planting=cell_planting,
            germinated=self.germinated,
        )
        self.locations = {}
        for plant in (self.plant, self.other):
            record_germination_event(plant, self.user)
            self.locations[plant.pk] = make_specific_plant_location(
                specific_plant=plant,
                started=self.germinated,
            )

    def _request(self, **overrides):
        """Build a final-harvest request naming both plants."""
        values = {
            'batch': self.batch,
            'harvested_at': timezone.now(),
            'quantity': Decimal('2'),
            'unit_code': UnitCode.EACH,
            'plant_ids': (self.plant.pk, self.other.pk),
            'finish_plants': True,
            'finish_reason': 'Picked out at the end of the season.',
        }
        values.update(overrides)
        return HarvestRequest(**values)

    def test_finishing_records_one_event_per_plant(self):
        """Each plant gets its own fact, referencing the harvest that caused it."""
        harvest, events = record_harvest(self.batch.workspace, self.user, self._request())
        self.assertEqual(len(events), 2)
        for event in events:
            self.assertEqual(event.event_type, EventType.HARVEST_FINISHED)
            self.assertEqual(event.reference, f'harvest:{harvest.pk}')
        self.assertEqual(
            harvest_finished_plant_ids(harvest),
            sorted([self.plant.pk, self.other.pk]),
        )
        for plant in (self.plant, self.other):
            self.assertEqual(
                plant_lifecycle_summary(plant).state,
                LifecycleState.HARVESTED,
            )

    def test_finishing_closes_only_the_open_location(self):
        """Where a plant has already been stays exactly as it was recorded."""
        active = self.locations[self.plant.pk]
        SpecificPlantLocation.objects.filter(pk=active.pk).update(
            ended=self.germinated + timedelta(days=5),
        )
        moved = make_specific_plant_location(
            specific_plant=self.plant,
            started=self.germinated + timedelta(days=5),
        )
        history = list(
            SpecificPlantLocation.objects
            .filter(specific_plant=self.plant)
            .exclude(pk=moved.pk)
            .order_by('pk')
            .values_list('pk', 'started', 'ended')
        )
        harvest, _events = record_harvest(self.batch.workspace, self.user, self._request())
        self.assertEqual(
            list(
                SpecificPlantLocation.objects
                .filter(specific_plant=self.plant)
                .exclude(pk=moved.pk)
                .order_by('pk')
                .values_list('pk', 'started', 'ended')
            ),
            history,
        )
        moved.refresh_from_db()
        self.assertEqual(moved.ended, harvest.harvested_at)

    def test_finishing_without_a_selection_is_refused(self):
        """There is nothing to finish when no plant was named."""
        with self.assertRaises(ValidationError) as caught:
            record_harvest(
                self.batch.workspace,
                self.user,
                self._request(plant_ids=()),
            )
        self.assertIn('plants', caught.exception.message_dict)
        self.assertFalse(Harvest.objects.exists())

    def test_finishing_an_already_harvested_plant_rolls_the_harvest_back(self):
        """A resolved plant cannot be resolved twice, and nothing is left behind."""
        record_harvest(self.batch.workspace, self.user, self._request())
        with self.assertRaises(ValidationError) as caught:
            record_harvest(self.batch.workspace, self.user, self._request())
        self.assertIn('plants', caught.exception.message_dict)
        self.assertEqual(Harvest.objects.count(), 1)

    def test_a_backdated_final_harvest_is_refused(self):
        """A plant cannot be recorded as finishing before its latest fact."""
        earlier = self.sown + timedelta(days=1)
        with self.assertRaises(ValidationError) as caught:
            record_harvest(
                self.batch.workspace,
                self.user,
                self._request(harvested_at=earlier),
            )
        self.assertIn('plants', caught.exception.message_dict)
        self.assertFalse(Harvest.objects.exists())

    def test_a_backdated_harvest_without_finishing_is_allowed(self):
        """Only the lifecycle history is append-only in time; yield is not."""
        earlier = self.sown + timedelta(days=1)
        harvest, events = record_harvest(
            self.batch.workspace,
            self.user,
            self._request(harvested_at=earlier, finish_plants=False),
        )
        self.assertEqual(harvest.harvested_at, earlier)
        self.assertEqual(events, [])


class ReverseHarvestTests(TestCase):
    """A reversal excludes a harvest from totals without erasing it."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='reverser')
        self.plant = make_specific_plant()
        self.batch = self.plant.cell_planting.seed_tray_planting.batch
        self.workspace = self.batch.workspace
        record_germination_event(self.plant, self.user)
        make_specific_plant_location(specific_plant=self.plant)
        self.harvest, self.events = record_harvest(
            self.workspace,
            self.user,
            HarvestRequest(
                batch=self.batch,
                harvested_at=timezone.now(),
                quantity=Decimal('4'),
                unit_code=UnitCode.EACH,
                plant_ids=(self.plant.pk,),
                finish_plants=True,
            ),
        )

    def test_reversing_stamps_the_record_and_keeps_it(self):
        """The audit trail keeps the mistake visible rather than deleting it."""
        reversed_harvest = reverse_harvest(self.harvest, self.user, 'Weighed twice.')
        self.assertEqual(reversed_harvest.status, Harvest.Status.REVERSED)
        self.assertIsNotNone(reversed_harvest.reversed_at)
        self.assertEqual(reversed_harvest.reverse_reason, 'Weighed twice.')
        self.assertEqual(reversed_harvest.reversed_by, self.user)
        self.assertEqual(
            list(reversed_harvest.plant_allocations.values_list('plant_id', flat=True)),
            [self.plant.pk],
        )

    def test_a_reversal_requires_a_reason(self):
        """An unexplained correction is not an audit record."""
        for reason in ('', '   '):
            with self.subTest(reason=repr(reason)):
                with self.assertRaises(ValidationError) as caught:
                    reverse_harvest(self.harvest, self.user, reason)
                self.assertIn('reason', caught.exception.message_dict)

    def test_a_harvest_cannot_be_reversed_twice(self):
        """One correction is enough; a second would say nothing new."""
        reverse_harvest(self.harvest, self.user, 'Weighed twice.')
        with self.assertRaises(ValidationError) as caught:
            reverse_harvest(self.harvest, self.user, 'Again.')
        self.assertIn('status', caught.exception.message_dict)

    def test_reversing_leaves_the_lifecycle_history_alone(self):
        """Where a plant has been, and that it ended, both remain true."""
        before = list(
            PlantLifecycleEvent.objects.values_list('pk', 'event_type', 'occurred_at')
        )
        reverse_harvest(self.harvest, self.user, 'Weighed twice.')
        after = list(
            PlantLifecycleEvent.objects.values_list('pk', 'event_type', 'occurred_at')
        )
        self.assertEqual(before, after)
        self.assertEqual(
            plant_lifecycle_summary(self.plant).state,
            LifecycleState.HARVESTED,
        )


class CancelHarvestedBatchTests(TestCase):
    """A batch that yielded a crop produced output and cannot be cancelled."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='canceller')
        self.batch = make_production_batch()
        self.harvest, _events = record_harvest(
            self.batch.workspace,
            self.user,
            HarvestRequest(
                batch=self.batch,
                harvested_at=timezone.now(),
                quantity=Decimal('1'),
                unit_code=UnitCode.KILOGRAM,
            ),
        )

    def test_cancelling_a_harvested_batch_is_refused(self):
        """Four kilograms of crop is output, however it was grown."""
        with self.assertRaises(ValidationError) as caught:
            cancel_batch(self.batch, self.user, 'Changed my mind.')
        self.assertIn('harvests came from this batch', str(caught.exception))

    def test_cancelling_succeeds_once_the_harvest_is_reversed(self):
        """A retracted harvest no longer counts as output."""
        reverse_harvest(self.harvest, self.user, 'Recorded against the wrong crop.')
        batch = cancel_batch(self.batch, self.user, 'Changed my mind.')
        self.assertEqual(batch.status, ProductionBatch.Status.CANCELLED)
