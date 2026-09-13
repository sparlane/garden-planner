"""Numbering an occupied pot preserves its stock claim and original media share."""
# pylint: disable=duplicate-code

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier, Event
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import close_old_connections, transaction
from django.shortcuts import get_object_or_404
from django.test import skipUnlessDBFeature
from rest_framework.exceptions import ValidationError as RESTValidationError

from applications.services import post_application
from costing.services import effective_allocations, reallocate_batch
from inventory.ledger import (
    IndividualizationRequest, UnitMovementRequest, bulk_balance, discard_numbering,
    individualize_lot_units, physical_balance, post_unit_movement, unpromised_bulk,
)
from inventory.models import InventoryUnit, StockMovement
from locations.occupancy import location_occupancy
from sales.test_concurrency import ReservationConcurrencyTestCase
from sales.test_counted_lines import CountedStockTestCase
from seedtrays.container_fills import open_counted_fill, open_numbered_fill, reopen_pot_fill
from seedtrays.generations import MediaDisposition
from seedtrays.pot_media import pot_fill_cost_breakdown, pot_fill_shares
from seedtrays.test_pot_media import PotMediaMixin
from tests.factories import make_location, make_specific_plant
from workspaces.models import Workspace

from .counted_fills import plant_counted_fill
from .fill_numbering import number_counted_pot
from .models import SpecificPlant, SpecificPlantLocation
from .movement import move_specific_plant
from .register import register_projection


class CountedPotNumberingTests(PotMediaMixin, CountedStockTestCase):
    """A number changes identity, not the pot's cultivation cycle or valuation."""

    def setUp(self):
        super().setUp()
        self.setup_media()
        item = self.pots.item
        item.container_footprint_m2 = Decimal('0.01')
        item.save()
        post_application(self.draft(), self.user)
        self.plant = make_specific_plant()
        self.placement, = plant_counted_fill(self.workspace, self.user, self.fill, [self.plant.pk])

    def number_pot(self):
        """Number the pot the fixture plant is currently using."""
        return number_counted_pot(self.workspace, self.user, self.fill, self.plant.pk)

    def leave(self):
        """Move the plant out through the shared departure path."""
        return move_specific_plant(self.plant, {'location_type': 'location', 'location': make_location()}, self.user)

    def test_numbering_preserves_stock_occupancy_share_and_cost(self):
        """An occupied pot can be numbered even when every other pot is held."""
        open_counted_fill(self.workspace, self.user, self.pots, self.store, 50)
        shares = pot_fill_shares(self.fill)
        costs = pot_fill_cost_breakdown(self.fill)
        occupancy = location_occupancy(self.store)
        movements = StockMovement.objects.count()
        numbered = self.number_pot()
        self.assertEqual(numbered.pk, self.placement.pk)
        self.assertEqual(numbered.started, self.placement.started)
        self.assertIsNone(numbered.ended)
        self.assertIsNone(numbered.location_id)
        self.assertEqual(numbered.numbered_by, self.user)
        self.assertGreaterEqual(numbered.numbered_at, numbered.started)
        self.assertEqual(numbered.container_unit.acquisition_cost, self.pots.base_unit_cost)
        self.assertEqual(physical_balance(self.pots, self.store), 100)
        self.assertEqual(bulk_balance(self.pots, self.store), 99)
        self.assertEqual(unpromised_bulk(self.pots, self.store), 0)
        self.assertEqual(location_occupancy(self.store), occupancy)
        self.assertEqual(pot_fill_shares(self.fill), shares)
        self.assertEqual(pot_fill_cost_breakdown(self.fill), costs)
        self.assertEqual(StockMovement.objects.count(), movements)
        reallocate_batch(self.plant.batch, self.user, 'manual_recalculate')
        self.assertEqual(list(effective_allocations(self.plant.batch)), [])

    def test_retry_returns_the_same_identity_and_rollback_leaves_no_number(self):
        """Neither a lost response nor an outer rollback can strand a pot."""
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.number_pot()
            raise RuntimeError('Cancel the outer operation')
        self.assertFalse(self.pots.serialized_units.exists())
        self.placement.refresh_from_db()
        self.assertIsNone(self.placement.numbered_at)
        first = self.number_pot()
        second = self.number_pot()
        self.assertEqual(first.container_unit_id, second.container_unit_id)
        self.assertEqual(first.numbered_at, second.numbered_at)
        self.assertEqual(self.pots.serialized_units.count(), 1)

    def test_departure_releases_no_second_bulk_pot_and_media_is_charged_once(self):
        """The numbered pot becomes empty while the counted fill retains its basis."""
        numbered = self.number_pot()
        with self.captureOnCommitCallbacks(execute=True):
            self.leave()
        self.assertEqual(unpromised_bulk(self.pots, self.store), 50)
        layer, = effective_allocations(self.plant.batch)
        self.assertEqual(layer.amount, Decimal('2'))
        self.assertEqual(layer.base_quantity, Decimal('1'))
        self.assertEqual(pot_fill_cost_breakdown(self.fill)['departed_cost'], 2)
        open_numbered_fill(self.workspace, self.user, numbered.container_unit)
        reallocate_batch(self.plant.batch, self.user, 'manual_recalculate')
        self.assertEqual([row.amount for row in effective_allocations(self.plant.batch)], [Decimal('2')])
        with self.assertRaises(ValidationError):
            discard_numbering(self.workspace, numbered.container_unit)

    def test_transferring_the_numbered_pot_carries_its_plant_and_fill(self):
        """Physical movement changes the bench, never the original media owner."""
        numbered = self.number_pot()
        destination = make_location()
        post_unit_movement(self.workspace, self.user, UnitMovementRequest(
            numbered.container_unit, 'transfer', destination=destination,
        ))
        row = register_projection(self.workspace).get(pk=self.plant.pk)
        self.assertEqual(row.standing_at, destination.pk)
        self.assertEqual(row.current_container_fill, self.fill.pk)
        self.assertEqual(unpromised_bulk(self.pots, self.store), 50)
        self.assertEqual(bulk_balance(self.pots, destination), 0)

    def test_numbered_counted_pot_cannot_gain_another_participant(self):
        """A label cannot turn one fixed counted share into a shared numbered fill."""
        numbered = self.number_pot()
        other = make_specific_plant()
        with self.assertRaises(RESTValidationError):
            move_specific_plant(other, {'location_type': 'container_unit', 'container_unit': numbered.container_unit})
        with self.assertRaises(ValidationError):
            open_numbered_fill(self.workspace, self.user, numbered.container_unit)
        with self.assertRaises(ValidationError):
            post_unit_movement(self.workspace, self.user, UnitMovementRequest(numbered.container_unit, 'sale'))
        self.assertFalse(other.locations.exists())

    def test_numbering_audit_and_departure_chronology_cannot_be_rewritten(self):
        """Ordinary interval edits cannot undo numbering or predate its audit."""
        numbered = self.number_pot()
        for field, value in [('numbered_at', None), ('numbered_by', None), ('container_unit', None)]:
            numbered.refresh_from_db()
            setattr(numbered, field, value)
            with self.assertRaises(ValidationError):
                numbered.save()
        numbered.refresh_from_db()
        numbered.ended = numbered.numbered_at - timedelta(microseconds=1)
        with self.assertRaises(ValidationError):
            numbered.save(update_fields=['ended'])
        unit = numbered.container_unit
        unit.asset_code = 'REWRITTEN-NUMBER'
        with self.assertRaises(ValidationError):
            unit.save()

    def test_unknown_cost_stays_unknown_and_invalid_plants_create_no_units(self):
        """Only this fill's current participants may consume its held stock claim."""
        type(self.pots).objects.filter(pk=self.pots.pk).update(base_unit_cost=None)
        foreign = Workspace.objects.create(name='Other nursery')
        for plant in (make_specific_plant(), make_specific_plant(workspace=foreign)):
            with self.assertRaises(ValidationError):
                number_counted_pot(self.workspace, self.user, self.fill, plant.pk)
        self.assertFalse(self.pots.serialized_units.exists())
        self.assertIsNone(self.number_pot().container_unit.acquisition_cost)
        self.leave()
        with self.assertRaises(ValidationError):
            self.number_pot()

    def test_clean_correction_restores_only_unused_anonymous_pots(self):
        """A departed numbered participant never reclaims a second pot on correction."""
        self.number_pot()
        self.leave()
        self.clean(media=(MediaDisposition(self.media.pk, '49', 'waste'),))
        self.assertEqual(unpromised_bulk(self.pots, self.store), 99)
        reopen_pot_fill(self.workspace, self.user, self.fill, 'Wrong fill')
        self.assertEqual(unpromised_bulk(self.pots, self.store), 50)

    def test_numbering_after_a_departure_preserves_cross_batch_thirds(self):
        """Labelling the second participant cannot change the first one's rounded cost."""
        fill = open_counted_fill(self.workspace, self.user, self.pots, self.store, 3)
        post_application(self.draft(fill=fill), self.user)
        plants = [make_specific_plant() for _ in range(3)]
        placements = plant_counted_fill(self.workspace, self.user, fill, [plant.pk for plant in plants])
        with self.captureOnCommitCallbacks(execute=True):
            move_specific_plant(plants[0], {'location_type': 'location', 'location': make_location()})
        first_cost = list(effective_allocations(plants[0].batch))[0].amount
        shares = pot_fill_shares(fill)
        number_counted_pot(self.workspace, self.user, fill, plants[1].pk)
        self.assertEqual(pot_fill_shares(fill), shares)
        with self.captureOnCommitCallbacks(execute=True):
            for plant in plants[1:]:
                move_specific_plant(plant, {'location_type': 'location', 'location': make_location()})
        costs = [list(effective_allocations(plant.batch))[0].amount for plant in plants]
        self.assertEqual(costs[0], first_cost)
        self.assertEqual(sum(costs), Decimal('100'))
        self.assertEqual(list(fill.plant_locations.order_by('pk').values_list('pk', flat=True)), [row.pk for row in placements])


@skipUnlessDBFeature('has_select_for_update')
class CountedPotNumberingConcurrencyTests(PotMediaMixin, ReservationConcurrencyTestCase):
    """A numbering claim serializes with departures and ordinary bulk numbering."""

    def setUp(self):
        super().setUp()
        self.setup_media()
        self.plant = make_specific_plant()
        plant_counted_fill(self.workspace, None, self.fill, [self.plant.pk])

    @staticmethod
    def race(first, second):
        """Propagate deadlocks and unexpected failures from either transaction."""
        barrier = Barrier(2)

        def attempt(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                operation()
                return 'done'
            except (ValidationError, RESTValidationError):
                return 'rejected'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return sorted(pool.map(attempt, [first, second]))

    def number_pot(self):
        """Run the conversion in a worker's independent transaction."""
        return number_counted_pot(self.workspace, None, self.fill, self.plant.pk)

    def test_repeated_numbering_creates_one_unit(self):
        """Two requests for the same occupied pot share one resulting identity."""
        self.assertEqual(self.race(self.number_pot, self.number_pot), ['done', 'done'])
        self.assertEqual(InventoryUnit.objects.filter(source_lot=self.pots).count(), 1)
        self.assertEqual(unpromised_bulk(self.pots, self.store), 50)

    def test_ordinary_numbering_and_occupied_numbering_claim_different_stock(self):
        """An occupied pot is not borrowed from the fifty available empty ones."""
        results = self.race(self.number_pot, lambda: individualize_lot_units(
            self.workspace, None, IndividualizationRequest(self.pots, self.store, 50),
        ))
        self.assertEqual(results, ['done', 'done'])
        self.assertEqual(self.pots.serialized_units.count(), 51)
        self.assertEqual(unpromised_bulk(self.pots, self.store), 0)

    def test_departure_racing_numbering_cannot_leave_a_duplicate_claim(self):
        """Whichever locks the plant first determines whether its pot gets a number."""
        destination = make_location()
        results = self.race(self.number_pot, lambda: move_specific_plant(
            self.plant, {'location_type': 'location', 'location': destination},
        ))
        self.assertIn(results, [['done', 'done'], ['done', 'rejected']])
        placement = SpecificPlantLocation.objects.get(container_fill=self.fill)
        self.assertIsNotNone(placement.ended)
        self.assertEqual(unpromised_bulk(self.pots, self.store), 50 if placement.numbered_at else 51)

    def test_waiting_move_dates_departure_after_the_numbering_it_waited_for(self):
        """Force the lock order that previously made an automatic move look backdated."""
        attempting = Event()
        destination = make_location()

        def wait_for_plant(*args, **kwargs):
            attempting.set()
            return get_object_or_404(*args, **kwargs)

        def move():
            close_old_connections()
            try:
                return move_specific_plant(self.plant, {'location_type': 'location', 'location': destination})
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool, patch('plantings.movement.get_object_or_404', side_effect=wait_for_plant):
            with transaction.atomic():
                SpecificPlant.objects.select_for_update().get(pk=self.plant.pk)
                future = pool.submit(move)
                self.assertTrue(attempting.wait(timeout=10))
                numbered = self.number_pot()
            moved = future.result(timeout=20)
        numbered.refresh_from_db()
        self.assertEqual(numbered.ended, moved.started)
        self.assertGreaterEqual(moved.started, numbered.numbered_at)
