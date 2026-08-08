"""The splitting rules, checked without a database in the way.

`costing.allocation` is pure, so these tests feed it measured facts directly.
The rules it applies to real application targets and real cells are exercised
end to end in `costing.test_services`; what is checked here is the arithmetic
those rules stand on, above all that nothing is ever lost to rounding.
"""

from decimal import Decimal
from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from inventory.ledger import MONEY_QUANTUM, QUANTITY_QUANTUM, distribute_exactly

from .allocation import (
    area_plant_shares,
    cell_volume_shares,
    combine,
    loss_shares,
    plant_shares,
    resolve_cells_to_plants,
    seed_shares,
    value_shares,
    whole_source_share,
)
from .models import CostAllocation


class StubTarget(NamedTuple):
    """The three fields `cell_volume_shares` reads off a real target.

    Named after `applications.models.InputApplicationTarget`, whose columns
    these are. The real object is used in `costing.test_services`; this stand-in
    keeps the arithmetic tests free of a tray, a fill, and an application.
    """

    seed_tray_cell_id: int
    seed_tray_generation_id: int
    weight: Decimal
    cell_volume_ml: int


def _total(values):
    """Sum a list of shares, treating it as exact."""
    return sum(values, Decimal('0'))


class DistributeExactlyTests(SimpleTestCase):
    """Splitting money must never invent or lose a cent."""

    def test_an_even_split_that_does_not_divide_reconciles(self):
        """A third of ten dollars has to come back as ten dollars."""
        parts = distribute_exactly(Decimal('10'), [1, 1, 1])
        self.assertEqual(_total(parts), Decimal('10.0000'))
        self.assertEqual(
            parts,
            [Decimal('3.3334'), Decimal('3.3333'), Decimal('3.3333')],
        )

    def test_the_remainder_goes_to_the_largest_fractional_parts(self):
        """Handing every step to one arbitrary share would misreport it."""
        parts = distribute_exactly(Decimal('1'), [1, 1, 1, 1, 1, 1, 7])
        self.assertEqual(_total(parts), Decimal('1.0000'))
        self.assertEqual(parts[-1], Decimal('0.5385'))

    def test_uneven_weights_reconcile(self):
        """Weighted splits carry the same guarantee as even ones."""
        parts = distribute_exactly(Decimal('12.3456'), [7, 11, 13, 17])
        self.assertEqual(_total(parts), Decimal('12.3456'))

    def test_ties_are_broken_by_position(self):
        """The same inputs must produce the same split, run after run."""
        first = distribute_exactly(Decimal('10'), [1, 1, 1])
        second = distribute_exactly(Decimal('10'), [1, 1, 1])
        self.assertEqual(first, second)

    def test_a_quantity_quantum_reconciles_at_nine_places(self):
        """Quantities are stored finer than money and split the same way."""
        parts = distribute_exactly(Decimal('1'), [1, 1, 3], QUANTITY_QUANTUM)
        self.assertEqual(_total(parts), Decimal('1.000000000'))

    def test_an_unknown_total_splits_into_unknowns(self):
        """A share of an unknown cost is unknown, never zero."""
        self.assertEqual(distribute_exactly(None, [1, 2]), [None, None])

    def test_weights_totalling_zero_are_refused(self):
        """There is no proportional answer when nothing has any weight."""
        with self.assertRaises(ValidationError):
            distribute_exactly(Decimal('1'), [0, 0])

    def test_a_negative_weight_is_refused(self):
        """A negative share would take cost off another destination."""
        with self.assertRaises(ValidationError):
            distribute_exactly(Decimal('1'), [2, -1])

    def test_a_single_share_takes_the_whole_total(self):
        """The degenerate case still reconciles exactly."""
        self.assertEqual(distribute_exactly(Decimal('7.77'), [3]), [Decimal('7.7700')])


class ValueSharesTests(SimpleTestCase):
    """Quantity and money are split separately but both reconcile."""

    def setUp(self):
        self.shares = seed_shares(3, [(1, 9, 1), (2, 9, 1), (3, 9, 1)])

    def test_both_columns_reconcile_to_their_source(self):
        """A report can tie either number back to the movement that made it."""
        parts = value_shares(self.shares, Decimal('1'), Decimal('10'))
        self.assertEqual(
            _total([part.base_quantity for part in parts]),
            Decimal('1.000000000'),
        )
        self.assertEqual(_total([part.amount for part in parts]), Decimal('10.0000'))

    def test_an_unknown_cost_leaves_every_amount_unknown(self):
        """Quantity stays exact even when the lot never recorded a price."""
        parts = value_shares(self.shares, Decimal('1'), None)
        self.assertTrue(all(part.amount is None for part in parts))
        self.assertEqual(
            _total([part.base_quantity for part in parts]),
            Decimal('1.000000000'),
        )

    def test_a_source_with_nowhere_to_go_is_refused(self):
        """Cost that reached nothing at all is a bug, not an empty split."""
        with self.assertRaises(ValidationError):
            value_shares([], Decimal('1'), Decimal('1'))


class SeedShareTests(SimpleTestCase):
    """Seed cost follows the seed into the cells that received it."""

    def test_seed_follows_the_cells_it_was_placed_in(self):
        """A cell sown with twice the seed carries twice the cost."""
        shares = seed_shares(6, [(1, 9, 2), (2, 9, 4)])
        self.assertEqual([share.cell_id for share in shares], [1, 2])
        self.assertEqual([share.weight for share in shares], [Decimal('2'), Decimal('4')])

    def test_seed_never_placed_stays_in_the_pool(self):
        """Unplaced seed has reached no seedling and may never."""
        shares = seed_shares(10, [(1, 9, 4)])
        pool = [share for share in shares if share.target_type == CostAllocation.TargetType.BATCH_POOL]
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0].weight, Decimal('6'))

    def test_a_fully_placed_sowing_leaves_no_pool(self):
        """A zero remainder is not a layer worth carrying."""
        shares = seed_shares(4, [(1, 9, 4)])
        self.assertEqual(len(shares), 1)
        self.assertEqual(shares[0].cell_id, 1)

    def test_placing_more_seed_than_was_drawn_is_refused(self):
        """That would allocate cost the sowing never paid for."""
        with self.assertRaises(ValidationError):
            seed_shares(3, [(1, 9, 2), (2, 9, 2)])

    def test_a_cell_sown_with_nothing_earns_no_share(self):
        """A zero weight would make the proportional split meaningless."""
        shares = seed_shares(4, [(1, 9, 4), (2, 9, 0)])
        self.assertEqual([share.cell_id for share in shares], [1])


class CellVolumeShareTests(SimpleTestCase):
    """Media is divided the way the application calculated it."""

    def test_cells_are_weighted_by_weight_times_volume(self):
        """This is the basis `applications.usage` used for the quantity."""
        shares = cell_volume_shares([
            StubTarget(1, 9, Decimal('1'), 40),
            StubTarget(2, 9, Decimal('0.5'), 40),
        ])
        self.assertEqual(
            [share.weight for share in shares],
            [Decimal('40'), Decimal('20.0')],
        )

    def test_a_target_with_no_measured_volume_is_skipped(self):
        """An unmeasured cell cannot be given a proportional share."""
        shares = cell_volume_shares([StubTarget(1, 9, Decimal('1'), None)])
        self.assertEqual(shares, [])


class ResolveCellsToPlantsTests(SimpleTestCase):
    """A cell's cost reaches the seedlings that actually came up in it."""

    def test_a_multigerm_cell_splits_equally_between_its_plants(self):
        """One cell's worth of cost, three seedlings, no count changed."""
        shares = resolve_cells_to_plants(
            cell_volume_shares([StubTarget(1, 9, Decimal('1'), 30)]),
            {1: [11, 12, 13]},
        )
        self.assertEqual({share.plant_id for share in shares}, {11, 12, 13})
        self.assertEqual([share.weight for share in shares], [Decimal('10')] * 3)

    def test_an_empty_cell_keeps_its_own_share(self):
        """A seedling may still come up, so the cost stays where it is."""
        shares = resolve_cells_to_plants(
            cell_volume_shares([StubTarget(1, 9, Decimal('1'), 30)]),
            {},
        )
        self.assertEqual(shares[0].target_type, CostAllocation.TargetType.SEED_TRAY_CELL)
        self.assertEqual(shares[0].weight, Decimal('30'))

    def test_two_cells_feeding_one_plant_produce_one_layer(self):
        """A plant reached twice by one source is one destination, not two."""
        shares = resolve_cells_to_plants(
            cell_volume_shares([
                StubTarget(1, 9, Decimal('1'), 30),
                StubTarget(2, 9, Decimal('1'), 30),
            ]),
            {1: [11], 2: [11]},
        )
        self.assertEqual(len(shares), 1)
        self.assertEqual(shares[0].weight, Decimal('60'))

    def test_a_pool_share_passes_through(self):
        """Cost that never named a cell is not a cell to resolve."""
        shares = resolve_cells_to_plants(whole_source_share(), {1: [11]})
        self.assertEqual(shares[0].target_type, CostAllocation.TargetType.BATCH_POOL)


class AreaShareTests(SimpleTestCase):
    """Ground-applied cost reaches the plants standing on that ground."""

    def test_area_splits_by_area_then_equally_within_it(self):
        """Twice the ground, twice the cost, shared by whoever is on it."""
        shares = area_plant_shares([
            (Decimal('2'), [11, 12], None),
            (Decimal('1'), [13], None),
        ])
        weights = {share.plant_id: share.weight for share in shares}
        self.assertEqual(weights[11], Decimal('1'))
        self.assertEqual(weights[12], Decimal('1'))
        self.assertEqual(weights[13], Decimal('1'))

    def test_an_explicit_per_plant_weight_overrides_the_equal_split(self):
        """An item snapshot may say one plant took more than another."""
        shares = area_plant_shares([
            (Decimal('3'), [11, 12], {11: Decimal('2'), 12: Decimal('1')}),
        ])
        weights = {share.plant_id: share.weight for share in shares}
        self.assertEqual(weights[11], Decimal('2'))
        self.assertEqual(weights[12], Decimal('1'))

    def test_ground_with_nobody_on_it_stays_in_the_pool(self):
        """Charging another area's seedlings for it would misreport both."""
        shares = area_plant_shares([
            (Decimal('2'), [], None),
            (Decimal('1'), [13], None),
        ])
        pool = [share for share in shares if share.target_type == CostAllocation.TargetType.BATCH_POOL]
        self.assertEqual(pool[0].weight, Decimal('2'))

    def test_per_plant_weights_totalling_zero_are_refused(self):
        """There is no proportional answer inside that area."""
        with self.assertRaises(ValidationError):
            area_plant_shares([(Decimal('1'), [11], {11: Decimal('0')})])


class CombineAndLossTests(SimpleTestCase):
    """Merging destinations and retiring the ones that never produced."""

    def test_shares_reaching_one_place_become_one_layer(self):
        """One layer per destination keeps the ledger readable."""
        shares = combine(plant_shares([11, 12]) + plant_shares([11]))
        self.assertEqual(len(shares), 2)
        weights = {share.plant_id: share.weight for share in shares}
        self.assertEqual(weights[11], Decimal('2'))

    def test_loss_keeps_the_weight_and_drops_the_target(self):
        """The cost is real; the seedling it was waiting for is not."""
        unresolved = cell_volume_shares([StubTarget(1, 9, Decimal('1'), 30)])
        losses = loss_shares(unresolved)
        self.assertEqual(losses[0].target_type, CostAllocation.TargetType.PRODUCTION_LOSS)
        self.assertEqual(losses[0].weight, Decimal('30'))
        self.assertIsNone(losses[0].cell_id)

    def test_retired_cells_become_one_loss_carrying_all_their_weight(self):
        """Which cells were retired stays readable in the reversals."""
        unresolved = cell_volume_shares([
            StubTarget(1, 9, Decimal('1'), 30),
            StubTarget(2, 9, Decimal('1'), 10),
        ])
        losses = loss_shares(unresolved)
        self.assertEqual(len(losses), 1)
        self.assertEqual(losses[0].weight, Decimal('40'))

    def test_money_quantum_is_the_stored_currency_precision(self):
        """The exactness guarantee is stated in the unit the column keeps."""
        self.assertEqual(MONEY_QUANTUM, Decimal('0.0001'))
