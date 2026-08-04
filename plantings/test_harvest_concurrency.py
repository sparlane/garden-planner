"""Concurrency proofs for the locks harvest recording relies on.

`record_harvest` takes the batch lock before `record_bulk_outcome` takes the
plant locks, and no other path takes a plant lock before a batch lock. These
tests exercise both halves of that ordering under real row locks, so they need
a database that honours `SELECT ... FOR UPDATE`.
"""
# pylint: disable=duplicate-code
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from inventory.units import UnitCode
from tests.factories import (
    make_production_batch,
    make_specific_plant,
    make_specific_plant_location,
)
from workspaces.models import Workspace

from .batches import cancel_batch
from .harvests import HarvestRequest, record_harvest
from .lifecycle import (
    EventType,
    OutcomeRequest,
    record_germination_event,
    record_lifecycle_event,
)
from .models import Harvest, PlantLifecycleEvent, ProductionBatch, SpecificPlant


class HarvestConcurrencyTestCase(TransactionTestCase):
    """Shared fixture teardown for the harvest race tests."""

    def _post_teardown(self):
        """Restore migration seed data removed by transactional test flushing."""
        super()._post_teardown()
        if not Workspace.objects.filter(pk=settings.CURRENT_WORKSPACE_ID).exists():
            Workspace.objects.create(
                pk=settings.CURRENT_WORKSPACE_ID,
                name='My Garden',
            )


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentFinalHarvestTests(HarvestConcurrencyTestCase):
    """A final harvest cannot resolve a plant somebody else is resolving.

    Two harvests of one batch already serialise on the batch lock, so this races
    a final harvest against a plant-level outcome instead. That path takes the
    plant lock without ever touching the batch, which makes the plant lock
    inside `record_bulk_outcome` the only thing keeping one plant from ending
    twice. The idempotency reference cannot catch it: the two writers use
    different event types and different references.
    """

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='harvest-racer')
        plant = make_specific_plant()
        record_germination_event(plant, self.user)
        make_specific_plant_location(specific_plant=plant)
        self.plant_pk = plant.pk
        self.batch_pk = plant.cell_planting.seed_tray_planting.batch_id

    def _finish_harvest(self):
        """Attempt a final harvest from an independent connection."""
        close_old_connections()
        batch = ProductionBatch.objects.get(pk=self.batch_pk)
        user = get_user_model().objects.get(pk=self.user.pk)
        try:
            record_harvest(batch.workspace, user, HarvestRequest(
                batch=batch,
                harvested_at=timezone.now(),
                quantity=Decimal('1'),
                unit_code=UnitCode.EACH,
                plant_ids=(self.plant_pk,),
                finish_plants=True,
            ))
        except ValidationError:
            result = 'harvest rejected'
        else:
            result = 'harvested'
        close_old_connections()
        return result

    def _fail_plant(self):
        """Attempt to record the plant as failed from another connection."""
        close_old_connections()
        plant = SpecificPlant.objects.get(pk=self.plant_pk)
        user = get_user_model().objects.get(pk=self.user.pk)
        try:
            record_lifecycle_event(plant, user, OutcomeRequest(EventType.FAILED))
        except ValidationError:
            result = 'failure rejected'
        else:
            result = 'failed'
        close_old_connections()
        return result

    def test_a_plant_is_resolved_by_exactly_one_writer(self):
        """Whichever wins, the plant ends with a single final outcome."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(
                future.result()
                for future in [
                    pool.submit(self._finish_harvest),
                    pool.submit(self._fail_plant),
                ]
            )

        outcomes = PlantLifecycleEvent.objects.filter(
            plant_id=self.plant_pk,
            event_type__in=(
                PlantLifecycleEvent.EventType.HARVEST_FINISHED,
                PlantLifecycleEvent.EventType.FAILED,
            ),
        )
        self.assertEqual(outcomes.count(), 1)
        if outcomes.get().event_type == PlantLifecycleEvent.EventType.FAILED:
            self.assertEqual(results, ['failed', 'harvest rejected'])
            self.assertFalse(Harvest.objects.exists())
        else:
            self.assertEqual(results, ['failure rejected', 'harvested'])
            self.assertEqual(Harvest.objects.count(), 1)


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentHarvestAndCancelTests(HarvestConcurrencyTestCase):
    """Cancelling a batch and harvesting it serialise on the same lock."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='cancel-racer')
        self.batch_pk = make_production_batch().pk

    def _record(self):
        """Attempt an aggregate harvest from an independent connection."""
        close_old_connections()
        batch = ProductionBatch.objects.get(pk=self.batch_pk)
        user = get_user_model().objects.get(pk=self.user.pk)
        try:
            record_harvest(batch.workspace, user, HarvestRequest(
                batch=batch,
                harvested_at=timezone.now(),
                quantity=Decimal('4'),
                unit_code=UnitCode.KILOGRAM,
            ))
        except ValidationError:
            result = 'harvest rejected'
        else:
            result = 'harvested'
        close_old_connections()
        return result

    def _cancel(self):
        """Attempt to cancel the batch from an independent connection."""
        close_old_connections()
        batch = ProductionBatch.objects.get(pk=self.batch_pk)
        user = get_user_model().objects.get(pk=self.user.pk)
        try:
            cancel_batch(batch, user, 'Abandoned.')
        except ValidationError:
            result = 'cancel rejected'
        else:
            result = 'cancelled'
        close_old_connections()
        return result

    def test_a_batch_is_never_both_cancelled_and_harvested(self):
        """Whichever wins, the other is refused rather than half-applied."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(
                future.result()
                for future in [
                    pool.submit(self._record),
                    pool.submit(self._cancel),
                ]
            )

        batch = ProductionBatch.objects.get(pk=self.batch_pk)
        harvested = Harvest.objects.filter(
            batch=batch,
            status=Harvest.Status.POSTED,
        ).exists()
        if batch.status == ProductionBatch.Status.CANCELLED:
            self.assertEqual(results, ['cancelled', 'harvest rejected'])
            self.assertFalse(harvested)
        else:
            self.assertEqual(results, ['cancel rejected', 'harvested'])
            self.assertTrue(harvested)
