"""Counting what stands in a location, and refusing what will not fit."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature

from inventory.ledger import UnitMovementRequest, post_unit_movement
from inventory.models import StockMovement
from plantings.models import SpecificPlantLocation
from tests.factories import (
    make_location,
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_cell_planting,
    make_specific_plant,
    make_specific_plant_location,
)
from workspaces.models import Workspace, get_current_workspace

from .models import Location
from .occupancy import (
    check_capacity,
    location_occupancy,
    plant_contribution,
    tray_contribution,
)


class OccupancyFixture(TestCase):
    """Shared helpers for putting known things in known places."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()

    def bench(self, basis=Location.CapacityBasis.NONE, value=None, parent=None):
        """Create one bench with an optional capacity."""
        return make_location(
            workspace=self.workspace,
            location_type=Location.LocationType.BENCH,
            capacity_basis=basis,
            capacity_value=value,
            parent=parent,
        )

    def stand_plant(self, location):
        """Stand one plant directly at a location."""
        return make_specific_plant_location(
            location_type=SpecificPlantLocation.LOCATION,
            seed_tray_cell=None,
            location=location,
        )

    def stocked_tray(self, plant_count=0):
        """Create a tray standing where the ledger says it was received.

        Its unit keeps the opening movement the factory posts, so it can be
        transferred through the real ledger rather than teleported.
        """
        tray = make_seed_tray(workspace=self.workspace)
        for index in range(plant_count):
            cell = make_seed_tray_cell(tray=tray, x_position=index, y_position=0)
            planting = make_seed_tray_cell_planting(cell=cell)
            make_specific_plant_location(
                specific_plant=make_specific_plant(cell_planting=planting),
                seed_tray_cell=cell,
            )
        return tray

    def tray_at(self, location, plant_count=0):
        """Put one tray at a location, optionally holding growing plants.

        Sets the unit's location directly: these tests are about counting what
        is standing somewhere, not about how it got there.
        """
        tray = self.stocked_tray(plant_count)
        tray.inventory_unit.current_location = location
        tray.inventory_unit.save()
        return tray


class OccupancyCountingTests(OccupancyFixture):
    """Occupancy is measured in every dimension that can describe it."""

    def test_a_loose_plant_counts_as_one_plant_and_one_container(self):
        """Until containers are recorded, a standing plant is its own pot."""
        bench = self.bench()
        self.stand_plant(bench)

        occupancy = location_occupancy(bench)
        self.assertEqual(occupancy.plants, 1)
        self.assertEqual(occupancy.containers, 1)
        self.assertEqual(occupancy.trays, 0)

    def test_a_tray_counts_as_one_tray_and_all_the_plants_riding_in_it(self):
        """A bench measured in plants is just as full whether they came in a tray."""
        bench = self.bench()
        self.tray_at(bench, plant_count=3)

        occupancy = location_occupancy(bench)
        self.assertEqual(occupancy.trays, 1)
        self.assertEqual(occupancy.plants, 3)
        self.assertEqual(occupancy.containers, 0)

    def test_a_parent_reports_what_stands_on_its_benches(self):
        """"What is in this greenhouse" includes everything below it."""
        greenhouse = self.bench()
        bench = self.bench(parent=greenhouse)
        self.tray_at(bench, plant_count=2)
        self.stand_plant(greenhouse)

        self.assertEqual(location_occupancy(greenhouse).plants, 1)
        below = location_occupancy(greenhouse, subtree=True)
        self.assertEqual(below.plants, 3)
        self.assertEqual(below.trays, 1)

    def test_a_departed_plant_stops_occupying_the_bench_it_left(self):
        """Only open placements describe the present."""
        bench = self.bench()
        placement = self.stand_plant(bench)
        placement.ended = placement.started
        placement.save(update_fields=['ended'])

        self.assertEqual(location_occupancy(bench).plants, 0)


class CapacityBasisTests(OccupancyFixture):
    """Only the dimension a location declares is compared against its limit."""

    def test_a_plants_bench_admits_a_loose_plant_within_its_limit(self):
        """The ordinary case: room remains, so the plant goes down."""
        bench = self.bench(Location.CapacityBasis.PLANTS, Decimal('2'))
        self.stand_plant(bench)

        check_capacity(bench, plant_contribution(), lock=False)

    def test_a_plants_bench_refuses_the_plant_that_overfills_it(self):
        """A bench with two spaces does not hold three plants."""
        bench = self.bench(Location.CapacityBasis.PLANTS, Decimal('2'))
        self.stand_plant(bench)
        self.stand_plant(bench)

        with self.assertRaises(ValidationError) as caught:
            check_capacity(bench, plant_contribution(), lock=False)
        self.assertIn('destination', caught.exception.message_dict)

    def test_a_plants_bench_counts_a_whole_tray_against_its_limit(self):
        """Seventy-two seedlings take seventy-two spaces, tray or no tray."""
        bench = self.bench(Location.CapacityBasis.PLANTS, Decimal('5'))

        with self.assertRaises(ValidationError):
            check_capacity(bench, tray_contribution(6), lock=False)
        check_capacity(bench, tray_contribution(5), lock=False)

    def test_a_trays_bench_refuses_a_loose_plant_as_unmeasurable(self):
        """A pot is not a tray, so a tray count cannot describe it."""
        bench = self.bench(Location.CapacityBasis.TRAYS, Decimal('4'))

        with self.assertRaises(ValidationError) as caught:
            check_capacity(bench, plant_contribution(), lock=False)
        self.assertIn('destination', caught.exception.message_dict)

    def test_a_trays_bench_admits_trays_up_to_its_limit(self):
        """The dimension matches, so the comparison is meaningful."""
        bench = self.bench(Location.CapacityBasis.TRAYS, Decimal('1'))

        check_capacity(bench, tray_contribution(20), lock=False)
        self.tray_at(bench)
        with self.assertRaises(ValidationError):
            check_capacity(bench, tray_contribution(20), lock=False)

    def test_a_containers_bench_refuses_a_tray_as_unmeasurable(self):
        """A tray is not a container, and counting it as one would be a guess."""
        bench = self.bench(Location.CapacityBasis.CONTAINERS, Decimal('10'))

        with self.assertRaises(ValidationError) as caught:
            check_capacity(bench, tray_contribution(4), lock=False)
        self.assertIn('destination', caught.exception.message_dict)

    def test_a_containers_bench_admits_potted_plants(self):
        """One standing plant is one container until task 54 records real ones."""
        bench = self.bench(Location.CapacityBasis.CONTAINERS, Decimal('1'))

        check_capacity(bench, plant_contribution(), lock=False)
        self.stand_plant(bench)
        with self.assertRaises(ValidationError):
            check_capacity(bench, plant_contribution(), lock=False)

    def test_an_area_bench_limits_nothing_yet(self):
        """Nothing records a footprint, so area is planning data, not a rule."""
        bench = self.bench(Location.CapacityBasis.AREA, Decimal('0.5'))

        check_capacity(bench, plant_contribution(), lock=False)
        check_capacity(bench, tray_contribution(400), lock=False)

    def test_an_untracked_bench_admits_anything(self):
        """A place with no declared measure limits nothing."""
        bench = self.bench()

        check_capacity(bench, plant_contribution(), lock=False)
        check_capacity(bench, tray_contribution(999), lock=False)

    def test_an_override_reason_lets_an_overrun_through(self):
        """Physical reality sometimes wins; it must be explained, not silent."""
        bench = self.bench(Location.CapacityBasis.PLANTS, Decimal('1'))
        self.stand_plant(bench)

        with self.assertRaises(ValidationError):
            check_capacity(bench, plant_contribution(), lock=False)
        check_capacity(bench, plant_contribution(), 'Bench extended for the week', lock=False)


class CapacityChainTests(OccupancyFixture):
    """A limit on a greenhouse caps its benches' total, not just its aisle."""

    def test_an_ancestor_limit_blocks_a_bench_that_has_room(self):
        """The bench is not full; the greenhouse holding it is."""
        greenhouse = self.bench(Location.CapacityBasis.TRAYS, Decimal('2'))
        bench = self.bench(Location.CapacityBasis.TRAYS, Decimal('50'), parent=greenhouse)
        self.tray_at(bench)
        self.tray_at(bench)

        with self.assertRaises(ValidationError) as caught:
            check_capacity(bench, tray_contribution(0), lock=False)
        self.assertIn(greenhouse.name, str(caught.exception.message_dict['destination']))

    def test_an_uncapped_ancestor_is_not_consulted(self):
        """Only locations that declare a limit take part in the decision."""
        greenhouse = self.bench()
        bench = self.bench(Location.CapacityBasis.TRAYS, Decimal('1'), parent=greenhouse)

        check_capacity(bench, tray_contribution(0), lock=False)


class DeactivationTests(OccupancyFixture):
    """An occupied place cannot quietly disappear from the pickers."""

    def setUp(self):
        super().setUp()
        self.client.force_login(
            get_user_model().objects.create_user(username='retirement-tester'),
        )

    def retire(self, location):
        """Attempt to retire one location through the API."""
        return self.client.patch(
            f'/locations/{location.pk}/',
            data='{"active": false}',
            content_type='application/json',
        )

    def test_an_occupied_location_cannot_be_retired(self):
        """Retiring it would strand its contents at an unofferable place."""
        bench = self.bench()
        self.stand_plant(bench)

        response = self.retire(bench)
        self.assertEqual(response.status_code, 400)
        self.assertIn('active', response.json())

    def test_a_location_holding_a_tray_below_it_cannot_be_retired(self):
        """A greenhouse is occupied when anything on its benches is."""
        greenhouse = self.bench()
        bench = self.bench(parent=greenhouse)
        self.tray_at(bench)

        response = self.retire(greenhouse)
        self.assertEqual(response.status_code, 400)

    def test_an_active_child_must_be_retired_first(self):
        """An active bench inside a retired greenhouse is a contradiction."""
        greenhouse = self.bench()
        self.bench(parent=greenhouse)

        response = self.retire(greenhouse)
        self.assertEqual(response.status_code, 400)
        self.assertIn('active', response.json())

    def test_an_empty_location_retires_cleanly(self):
        """Nothing is standing there, so nothing is stranded."""
        bench = self.bench()

        response = self.retire(bench)
        self.assertEqual(response.status_code, 200, response.json())
        bench.refresh_from_db()
        self.assertFalse(bench.active)


class TrayTransferCapacityTests(OccupancyFixture):
    """Wheeling a tray somewhere goes through the same limit as a plant."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='tray-mover')

    def transfer(self, tray, destination, reason=''):
        """Transfer one tray's unit to a destination."""
        return post_unit_movement(
            self.workspace,
            self.user,
            UnitMovementRequest(
                unit=tray.inventory_unit,
                movement_type=StockMovement.MovementType.TRANSFER,
                destination=destination,
                occurred_at=None,
                reason=reason,
                reference='',
            ),
        )

    def test_a_tray_cannot_be_wheeled_onto_a_full_bench(self):
        """The bench holds one tray and already has it."""
        bench = self.bench(Location.CapacityBasis.TRAYS, Decimal('1'))
        self.tray_at(bench)
        moving = self.stocked_tray()

        with self.assertRaises(ValidationError) as caught:
            self.transfer(moving, bench)
        self.assertIn('destination', caught.exception.message_dict)

    def test_a_reason_lets_a_full_bench_take_one_more_tray(self):
        """The movement already records a reason; that is the audited override."""
        bench = self.bench(Location.CapacityBasis.TRAYS, Decimal('1'))
        self.tray_at(bench)
        moving = self.stocked_tray()

        movement = self.transfer(moving, bench, reason='Overflow for one afternoon')
        self.assertEqual(movement.reason, 'Overflow for one afternoon')
        moving.inventory_unit.refresh_from_db()
        self.assertEqual(moving.inventory_unit.current_location_id, bench.pk)

    def test_the_plants_riding_in_a_tray_count_against_a_plants_bench(self):
        """Two seedlings arriving in a tray fill two of the bench's spaces."""
        bench = self.bench(Location.CapacityBasis.PLANTS, Decimal('1'))
        moving = self.stocked_tray(plant_count=2)

        with self.assertRaises(ValidationError):
            self.transfer(moving, bench)


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentPlacementTests(TransactionTestCase):
    """Two plants racing for the last space must not both take it."""

    def _post_teardown(self):
        super()._post_teardown()
        if not Workspace.objects.filter(pk=settings.CURRENT_WORKSPACE_ID).exists():
            Workspace.objects.create(pk=settings.CURRENT_WORKSPACE_ID, name='My Garden')

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.bench = make_location(
            workspace=self.workspace,
            location_type=Location.LocationType.BENCH,
            capacity_basis=Location.CapacityBasis.PLANTS,
            capacity_value=Decimal('1'),
        )

    def place(self):
        """Take the last space if it is still free, reporting which happened."""
        try:
            with transaction.atomic():
                check_capacity(self.bench, plant_contribution())
                make_specific_plant_location(
                    location_type=SpecificPlantLocation.LOCATION,
                    seed_tray_cell=None,
                    location=self.bench,
                )
            return 'placed'
        except ValidationError:
            return 'rejected'
        finally:
            close_old_connections()

    def test_only_one_of_two_racing_placements_takes_the_last_space(self):
        """Counting without locking would let both read the bench as free."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(future.result() for future in [
                pool.submit(self.place),
                pool.submit(self.place),
            ])

        self.assertEqual(results, ['placed', 'rejected'])
        self.assertEqual(
            SpecificPlantLocation.objects.filter(
                location=self.bench,
                ended__isnull=True,
            ).count(),
            1,
        )


class AncestorBasisTests(OccupancyFixture):
    """An ancestor measured in something else does not forbid a placement."""

    def test_a_greenhouse_counted_in_trays_admits_a_potted_plant_on_its_bench(self):
        """It has no opinion about pots; it simply does not count them."""
        greenhouse = self.bench(Location.CapacityBasis.TRAYS, Decimal('2'))
        bench = self.bench(Location.CapacityBasis.PLANTS, Decimal('4'), parent=greenhouse)

        check_capacity(bench, plant_contribution(), lock=False)

    def test_the_chosen_place_still_refuses_what_it_cannot_measure(self):
        """A category error at the destination is worth saying out loud."""
        bench = self.bench(Location.CapacityBasis.TRAYS, Decimal('4'))

        with self.assertRaises(ValidationError) as caught:
            check_capacity(bench, plant_contribution(), lock=False)
        self.assertIn('destination', caught.exception.message_dict)

    def test_a_capacity_message_names_a_plain_number(self):
        """"Holds 2 trays" reads like a person; "holds 2.000 trays" does not."""
        bench = self.bench(Location.CapacityBasis.TRAYS, Decimal('2'))
        self.tray_at(bench)
        self.tray_at(bench)

        with self.assertRaises(ValidationError) as caught:
            check_capacity(bench, tray_contribution(0), lock=False)
        self.assertIn('holds 2 trays', str(caught.exception.message_dict['destination']))

    def test_a_capacity_message_counts_in_the_number_it_is_about(self):
        """"Holds 1 plants" is the kind of thing software says, not a person."""
        bench = self.bench(Location.CapacityBasis.PLANTS, Decimal('1'))
        self.stand_plant(bench)

        with self.assertRaises(ValidationError) as caught:
            check_capacity(bench, plant_contribution(), lock=False)
        self.assertIn('holds 1 plant and', str(caught.exception.message_dict['destination']))
