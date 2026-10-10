"""Concurrency proofs for the lock order the subledger relies on.

Posting a plant-targeted layer takes a key-share lock on that plant through the
foreign key, and recording a lifecycle fact takes one on the batch through its
own. A reallocation that held the batch exclusively and then reached for a plant
would therefore deadlock against an outcome that held the plant and reached for
the batch — which is why `plantings.batches.lock_batch_with_plants` takes the
whole plant set in key order first.

These exercise that under real row locks, so they need a database that honours
`SELECT ... FOR UPDATE`. On SQLite they are skipped, which is why the suite has
to be run against PostgreSQL to mean anything here.
"""

# pylint: disable=duplicate-code

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from inventory.models import InventoryItem, StockMovement
from inventory.units import UnitCode
from plantings.batches import finalize_batch_output
from plantings.bulk_operations import (
    BulkOperationConflict,
    BulkOperationRequest,
    execute_bulk_operation,
)
from plantings.lifecycle import (
    EventType,
    OutcomeRequest,
    record_germination_event,
    record_lifecycle_event,
)
from plantings.models import (
    BulkPlantOperation,
    ProductionBatch,
    SeedTrayCellPlanting,
    SeedTrayPlanting,
    SowingStockPosting,
    SpecificPlant,
)
from tests.factories import (
    make_inventory_item,
    make_location,
    make_specific_plant,
    make_specific_plant_location,
    make_stock_lot,
)
from workspaces.models import Workspace

from .models import CostAllocation, CostAllocationRun
from .services import reallocate_batch


class CostingConcurrencyTestCase(TransactionTestCase):
    """Shared fixture teardown, and one batch whose seed actually cost money."""

    user = None

    def _post_teardown(self):
        """Restore migration seed data removed by transactional flushing."""
        super()._post_teardown()
        if not Workspace.objects.filter(pk=settings.CURRENT_WORKSPACE_ID).exists():
            Workspace.objects.create(
                pk=settings.CURRENT_WORKSPACE_ID,
                name='My Garden',
            )

    def make_costed_plant(self, username):
        """Return one germinated plant whose sowing drew priced seed.

        The cost matters: without it the reallocation posts no layers, writes no
        row referencing the plant, and takes none of the locks these tests exist
        to exercise.
        """
        self.user = get_user_model().objects.create_user(username=username)
        plant = make_specific_plant()
        record_germination_event(plant, self.user)
        make_specific_plant_location(specific_plant=plant)
        sowing = plant.cell_planting.seed_tray_planting
        location = make_location(workspace=sowing.workspace)
        lot = make_stock_lot(
            item=make_inventory_item(
                workspace=sowing.workspace,
                category=InventoryItem.Category.SEED,
                base_unit=UnitCode.SEED_CLUSTER,
            ),
            location=location,
            quantity='20',
            base_unit_cost=Decimal('0.25'),
            acquisition_total=Decimal('5'),
        )
        SowingStockPosting.objects.create(
            workspace=sowing.workspace,
            movement=StockMovement.objects.create(
                workspace=sowing.workspace,
                lot=lot,
                movement_type=StockMovement.MovementType.CONSUMPTION,
                quantity=Decimal(sowing.quantity),
                source=location,
                occurred_at=timezone.now(),
            ),
            tray_planting=sowing,
        )
        return plant


@skipUnlessDBFeature('has_select_for_update')
class ReallocationAgainstOutcomeTests(CostingConcurrencyTestCase):
    """A reallocation and a plant outcome never deadlock against each other.

    Both writers touch the same two rows in opposite roles: the reallocation
    holds the batch and writes rows referencing the plant, and the outcome holds
    the plant and writes a row referencing the batch. Taking plants before the
    batch on both sides is what makes one of them wait rather than both.
    """

    def setUp(self):
        super().setUp()
        plant = self.make_costed_plant('costing-racer')
        self.plant_pk = plant.pk
        self.batch_pk = plant.cell_planting.seed_tray_planting.batch_id

    def _reallocate(self):
        """Recompute the batch's allocations on an independent connection."""
        close_old_connections()
        batch = ProductionBatch.objects.get(pk=self.batch_pk)
        user = get_user_model().objects.get(pk=self.user.pk)
        try:
            reallocate_batch(
                batch,
                user,
                CostAllocationRun.Trigger.MANUAL_RECALCULATE,
                'Racing.',
            )
        except ValidationError:
            result = 'reallocation rejected'
        else:
            result = 'reallocated'
        close_old_connections()
        return result

    def _fail_plant(self):
        """Record the plant as failed on another connection."""
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

    def test_both_writers_finish_without_deadlocking(self):
        """Neither is refused: they serialise on the plants, then the batch."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(
                future.result()
                for future in [
                    pool.submit(self._reallocate),
                    pool.submit(self._fail_plant),
                ]
            )
        self.assertEqual(results, ['failed', 'reallocated'])


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentReallocationTests(CostingConcurrencyTestCase):
    """Two reallocations of one batch cannot both post the same layer.

    They serialise on the batch row, so the second one recomputes against what
    the first wrote and finds nothing left to do. Without that, both would see
    an empty ledger and post the same cost twice.
    """

    def setUp(self):
        super().setUp()
        plant = self.make_costed_plant('double-racer')
        self.batch_pk = plant.cell_planting.seed_tray_planting.batch_id

    def _reallocate(self):
        """Recompute the batch's allocations on an independent connection."""
        close_old_connections()
        batch = ProductionBatch.objects.get(pk=self.batch_pk)
        user = get_user_model().objects.get(pk=self.user.pk)
        run = reallocate_batch(
            batch,
            user,
            CostAllocationRun.Trigger.MANUAL_RECALCULATE,
            'Racing.',
        )
        close_old_connections()
        return run is not None

    def test_only_one_of_them_writes_anything(self):
        """The loser recomputes against the winner's rows and stops."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            wrote = [
                future.result()
                for future in [
                    pool.submit(self._reallocate),
                    pool.submit(self._reallocate),
                ]
            ]
        self.assertEqual(sum(1 for value in wrote if value), 1)
        effective = CostAllocation.objects.filter(
            batch_id=self.batch_pk,
            reversal_of__isnull=True,
            reversal__isnull=True,
        )
        self.assertEqual(
            sum((row.amount for row in effective), Decimal('0')),
            Decimal('0.5000'),
        )


@skipUnlessDBFeature('has_select_for_update')
class GerminationAgainstFinalizationTests(CostingConcurrencyTestCase):
    """A germination racing a finalization never lands on a frozen batch.

    Task 147 refuses a new output against a batch whose output is final, and it
    asks the batch `lock_batch_with_plants` just returned rather than the one
    the cell allocation points at. That is the whole difference between a rule
    and a race: a finalization committed while the germination was in flight
    leaves the in-memory batch row saying the output is still provisional, and a
    refusal read off that row would wave the seedling through.

    So exactly one of these two writers gets its way. Either the batch freezes
    and the germination is refused, or the seedling is recorded and the freeze
    divides the cell over it — and the layers add back up to what the seed cost
    either way, which is the figure the whole task exists to protect.
    """

    def setUp(self):
        super().setUp()
        plant = self.make_costed_plant('freeze-racer')
        sowing = plant.cell_planting.seed_tray_planting
        self.allocation_pk = plant.cell_planting_id
        self.batch_pk = sowing.batch_id
        self.workspace_pk = sowing.workspace_id
        # Finalizing refuses while any sowing is still open, so the fixture is
        # put in the state an operator would have it in before pressing it.
        SeedTrayPlanting.objects.filter(batch_id=self.batch_pk).update(removed=True)
        reallocate_batch(
            ProductionBatch.objects.get(pk=self.batch_pk),
            self.user,
            CostAllocationRun.Trigger.MANUAL_RECALCULATE,
        )

    def _germinate(self):
        """Record a second seedling through the real bulk operation."""
        close_old_connections()
        user = get_user_model().objects.get(pk=self.user.pk)
        allocation = SeedTrayCellPlanting.objects.get(pk=self.allocation_pk)
        request = BulkOperationRequest(
            action=BulkPlantOperation.Action.GERMINATE,
            atomicity=BulkPlantOperation.Atomicity.ALL_OR_NOTHING,
            occurred_at=timezone.now(),
            reason='A straggler came up.',
            selection_source={
                'mode': 'cell_plantings', 'cell_plantings': [self.allocation_pk],
            },
            action_payload={
                'germinations': ({'cell_planting': allocation, 'quantity': 1},),
                'notes': '',
            },
            idempotency_key=uuid4(),
        )
        try:
            execute_bulk_operation(
                Workspace.objects.get(pk=self.workspace_pk), user, request,
            )
        except (ValidationError, BulkOperationConflict):
            result = 'germination refused'
        else:
            result = 'germinated'
        close_old_connections()
        return result

    def _finalize(self):
        """Declare the batch's output final on another connection."""
        close_old_connections()
        user = get_user_model().objects.get(pk=self.user.pk)
        try:
            finalize_batch_output(
                ProductionBatch.objects.get(pk=self.batch_pk), user, 'Done sowing.',
            )
        except ValidationError:
            result = 'finalize refused'
        else:
            result = 'finalized'
        close_old_connections()
        return result

    def test_the_seedling_is_refused_or_the_freeze_counts_it(self):
        """Both orders are correct; a frozen batch plus a free seedling is not."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(
                future.result()
                for future in [
                    pool.submit(self._germinate),
                    pool.submit(self._finalize),
                ]
            )
        self.assertIn(
            results,
            [['finalized', 'germinated'], ['finalized', 'germination refused']],
        )
        batch = ProductionBatch.objects.get(pk=self.batch_pk)
        self.assertIsNotNone(batch.output_finalized_at)
        self.assertEqual(
            SpecificPlant.objects.filter(batch_id=self.batch_pk).count(),
            2 if results[1] == 'germinated' else 1,
        )
        effective = CostAllocation.objects.filter(
            batch_id=self.batch_pk,
            reversal_of__isnull=True,
            reversal__isnull=True,
        )
        self.assertEqual(
            sum((row.amount for row in effective), Decimal('0')),
            Decimal('0.5000'),
        )
