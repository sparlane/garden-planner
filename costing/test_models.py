"""Model-level rules the cost subledger cannot be talked out of."""

# The builders here mirror the shape of the real allocation path they stand in
# for, so they read the same way; the overlap is the point.
# pylint: disable=duplicate-code

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from tests.factories import (
    make_production_batch,
    make_seed_tray_cell,
    make_seed_tray_generation,
    make_specific_plant,
    make_stock_lot,
)
from workspaces.models import Workspace

from .models import (
    POOL_TARGET_TYPES,
    SOURCE_FIELDS,
    TARGET_COLUMNS,
    CostAllocation,
    CostAllocationRun,
)


class CostingFixtureTestCase(TestCase):
    """Shared builders for a run and the layers hung off it."""

    def make_run(self, **overrides):
        """Create one recalculation of one batch."""
        values = {
            'trigger': CostAllocationRun.Trigger.MANUAL_RECALCULATE,
            'reason': 'Built for tests.',
        }
        values.update(overrides)
        if 'batch' not in values:
            values['batch'] = make_production_batch()
        values.setdefault('workspace', values['batch'].workspace)
        return CostAllocationRun.objects.create(**values)

    def make_sowing_posting(self, batch=None):
        """Create the sowing-side source a seed layer draws from."""
        # Imported here so the module's own import list stays about costing.
        from tests.factories import make_seed_tray_planting  # pylint: disable=import-outside-toplevel

        from inventory.models import StockMovement  # pylint: disable=import-outside-toplevel
        from plantings.models import SowingStockPosting  # pylint: disable=import-outside-toplevel

        sowing = make_seed_tray_planting() if batch is None else make_seed_tray_planting(batch=batch)
        lot = make_stock_lot(workspace=sowing.workspace)
        movement = StockMovement.objects.get(lot=lot)
        return SowingStockPosting.objects.create(
            workspace=sowing.workspace,
            movement=movement,
            tray_planting=sowing,
        )

    def make_allocation(self, **overrides):
        """Create one layer, defaulting to a whole source into the batch pool."""
        values = {
            'source_type': CostAllocation.SourceType.SOWING_POSTING,
            'target_type': CostAllocation.TargetType.BATCH_POOL,
            'basis': CostAllocation.Basis.DIRECT,
            'basis_weight': Decimal('1'),
            'base_quantity': Decimal('10'),
            'base_unit': 'seed',
            'unit_cost': Decimal('0.5'),
            'amount': Decimal('5'),
        }
        values.update(overrides)
        if 'run' not in values:
            batch = values.get('batch') or make_production_batch()
            values['run'] = self.make_run(batch=batch)
        values.setdefault('batch', values['run'].batch)
        values.setdefault('workspace', values['batch'].workspace)
        values.setdefault('currency_code', values['workspace'].currency_code)
        if values['source_type'] == CostAllocation.SourceType.SOWING_POSTING:
            values.setdefault(
                'sowing_posting',
                self.make_sowing_posting(batch=values['batch']),
            )
        return CostAllocation.objects.create(**values)


class CostAllocationRunTests(CostingFixtureTestCase):
    """A run records what caused a recalculation and stays put afterwards."""

    def test_a_run_is_immutable(self):
        """Editing a run would rewrite why its layers exist."""
        run = self.make_run()
        run.reason = 'Something else'
        with self.assertRaises(ValidationError):
            run.save()

    def test_a_run_cannot_be_deleted(self):
        """Deleting a run would orphan the layers that reference it."""
        run = self.make_run()
        with self.assertRaises(ValidationError):
            run.delete()

    def test_a_run_stays_in_its_batch_workspace(self):
        """A run belongs to the workspace whose batch it recalculated."""
        other = Workspace.objects.create(name='Other workspace', currency_code='USD')
        with self.assertRaises(ValidationError):
            self.make_run(workspace=other, batch=make_production_batch())


class CostAllocationIdentityTests(CostingFixtureTestCase):
    """Every layer names exactly one source and at most one target."""

    def test_source_fields_match_the_declared_choices(self):
        """The generated constraint covers every supported source exactly once."""
        self.assertEqual(
            tuple(CostAllocation.SourceType.values),
            SOURCE_FIELDS,
        )

    def test_target_fields_match_the_declared_choices(self):
        """Every individual target maps to a column; pools deliberately do not."""
        self.assertEqual(
            tuple(CostAllocation.TargetType.values),
            tuple(TARGET_COLUMNS) + POOL_TARGET_TYPES,
        )

    def test_a_layer_resolves_to_the_things_it_points_at(self):
        """One accessor per side reads whichever column the type selected."""
        plant = make_specific_plant()
        allocation = self.make_allocation(
            batch=plant.cell_planting.seed_tray_planting.batch,
            target_type=CostAllocation.TargetType.SPECIFIC_PLANT,
            specific_plant=plant,
            basis=CostAllocation.Basis.EQUAL_SHARE,
        )
        self.assertEqual(allocation.target, plant)
        self.assertEqual(allocation.target_id, plant.pk)
        self.assertEqual(allocation.source, allocation.sowing_posting)

    def test_a_layer_needs_a_source(self):
        """A layer with no source could never be reconciled to the ledger."""
        with self.assertRaises(ValidationError):
            self.make_allocation(sowing_posting=None)

    def test_a_declared_source_must_be_the_populated_one(self):
        """Declaring one source and filling another would misreport provenance."""
        posting = self.make_sowing_posting()
        with self.assertRaises(ValidationError):
            self.make_allocation(
                batch=posting.tray_planting.batch,
                source_type=CostAllocation.SourceType.APPLICATION_LINE,
                sowing_posting=posting,
            )

    def test_a_pool_layer_names_no_individual_target(self):
        """Pool cost is exactly the cost that has not reached a thing yet."""
        plant = make_specific_plant()
        with self.assertRaises(ValidationError):
            self.make_allocation(
                batch=plant.cell_planting.seed_tray_planting.batch,
                target_type=CostAllocation.TargetType.PRODUCTION_LOSS,
                specific_plant=plant,
            )

    def test_the_database_refuses_two_targets(self):
        """The identity rule is enforced below the ORM as well as inside it."""
        plant = make_specific_plant()
        cell = plant.cell_planting.cell
        run = self.make_run(batch=plant.cell_planting.seed_tray_planting.batch)
        posting = self.make_sowing_posting(batch=run.batch)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CostAllocation.objects.bulk_create([
                CostAllocation(
                    workspace=run.workspace,
                    run=run,
                    batch=run.batch,
                    source_type=CostAllocation.SourceType.SOWING_POSTING,
                    sowing_posting=posting,
                    target_type=CostAllocation.TargetType.SPECIFIC_PLANT,
                    specific_plant=plant,
                    seed_tray_cell=cell,
                    basis=CostAllocation.Basis.EQUAL_SHARE,
                    basis_weight=Decimal('1'),
                    base_quantity=Decimal('1'),
                    base_unit='seed',
                    currency_code=run.workspace.currency_code,
                ),
            ])

    def test_a_fill_belongs_to_the_cell_tray(self):
        """A fill of another tray never explains this cell's cost."""
        cell = make_seed_tray_cell()
        elsewhere = make_seed_tray_generation()
        with self.assertRaises(ValidationError):
            self.make_allocation(
                target_type=CostAllocation.TargetType.SEED_TRAY_CELL,
                seed_tray_cell=cell,
                seed_tray_generation=elsewhere,
                basis=CostAllocation.Basis.CELL_VOLUME,
            )

    def test_only_a_cell_layer_carries_a_fill(self):
        """A plant's own cost is not attributable to one fill of a tray."""
        generation = make_seed_tray_generation()
        with self.assertRaises(ValidationError):
            self.make_allocation(seed_tray_generation=generation)


class CostAllocationValueTests(CostingFixtureTestCase):
    """Unknown cost stays unknown, and reversals mirror what they cancel."""

    def test_an_unknown_lot_cost_produces_an_unknown_amount(self):
        """A zero here would quietly understate every total built on it."""
        allocation = self.make_allocation(unit_cost=None, amount=None)
        self.assertIsNone(allocation.unit_cost)
        self.assertIsNone(allocation.amount)

    def test_a_known_unit_cost_cannot_carry_an_unknown_amount(self):
        """Half-known cost would let one total treat the other half as zero."""
        with self.assertRaises(ValidationError):
            self.make_allocation(unit_cost=Decimal('0.5'), amount=None)

    def test_an_unknown_unit_cost_cannot_carry_a_known_amount(self):
        """The pairing has to hold in both directions to mean anything."""
        with self.assertRaises(ValidationError):
            self.make_allocation(unit_cost=None, amount=Decimal('5'))

    def test_a_layer_is_immutable(self):
        """A mistake is reversed and reposted, never edited in place."""
        allocation = self.make_allocation()
        allocation.amount = Decimal('9')
        with self.assertRaises(ValidationError):
            allocation.save()

    def test_a_layer_cannot_be_deleted(self):
        """What was reported last month stays readable next to its correction."""
        allocation = self.make_allocation()
        with self.assertRaises(ValidationError):
            allocation.delete()

    def test_a_reversal_carries_the_reversed_amount(self):
        """A reversal that changed the number would not cancel anything."""
        original = self.make_allocation()
        with self.assertRaises(ValidationError):
            self.make_allocation(
                run=original.run,
                sowing_posting=original.sowing_posting,
                amount=Decimal('3'),
                reversal_of=original,
            )

    def test_a_reversal_cannot_itself_be_reversed(self):
        """Undoing an undo is a new layer, not a second cancellation."""
        original = self.make_allocation()
        reversal = self.make_allocation(
            run=original.run,
            sowing_posting=original.sowing_posting,
            reversal_of=original,
        )
        with self.assertRaises(ValidationError):
            self.make_allocation(
                run=original.run,
                sowing_posting=original.sowing_posting,
                reversal_of=reversal,
            )

    def test_one_layer_is_reversed_at_most_once(self):
        """Two reversals of one layer would cancel it twice over."""
        original = self.make_allocation()
        self.make_allocation(
            run=original.run,
            sowing_posting=original.sowing_posting,
            reversal_of=original,
        )
        with self.assertRaises(ValidationError):
            self.make_allocation(
                run=original.run,
                sowing_posting=original.sowing_posting,
                reversal_of=original,
            )

    def test_a_run_recalculates_the_layer_batch(self):
        """A layer explained by another batch's run has no provenance."""
        run = self.make_run()
        other = make_production_batch(workspace=run.workspace)
        with self.assertRaises(ValidationError):
            self.make_allocation(run=run, batch=other)
