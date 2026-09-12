"""Counted pot placements preserve anonymous stock and fixed media shares."""
# pylint: disable=duplicate-code

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from threading import Barrier

from django.core.exceptions import ValidationError
from django.db import close_old_connections, transaction
from django.db.models.deletion import ProtectedError
from django.test import skipUnlessDBFeature
from django.utils import timezone
from rest_framework.exceptions import ValidationError as RESTValidationError

from applications.services import post_application, reverse_application
from costing.models import FillDepartureRecalculation
from costing.services import effective_allocations, reallocate_batch
from inventory.ledger import IndividualizationRequest, individualize_lot_units, physical_balance, unpromised_bulk
from locations.occupancy import location_occupancy
from sales.test_counted_lines import CountedStockTestCase
from sales.test_concurrency import ReservationConcurrencyTestCase
from seedtrays.container_fills import clean_empty_fill, open_counted_fill, open_numbered_fill
from seedtrays.pot_media import pot_fill_shares, pot_fill_cost_breakdown
from seedtrays.test_pot_media import PotMediaMixin
from tests.factories import make_location, make_specific_plant, make_stock_lot
from workspaces.models import Workspace

from .counted_fills import plant_counted_fill
from .growth import current_growth
from .models import SpecificPlantLocation
from .movement import move_specific_plant
from .register import RegisterFilters, register_projection, register_queryset


class CountedFillPlacementTests(PotMediaMixin, CountedStockTestCase):
    """One selected plant claims one original pot, without a plant-to-pot label."""

    def setUp(self):
        super().setUp()
        self.setup_media()

    def join(self, plants=None, fill=None, **kwargs):
        """Place the selected plants using the atomic bulk backend service."""
        return plant_counted_fill(self.workspace, self.user, fill or self.fill,
                                  [plant.pk for plant in (plants or [make_specific_plant()])], **kwargs)

    def leave(self, placement):
        """Knock the plant out into an ordinary location, leaving its pot behind."""
        return move_specific_plant(placement.specific_plant, {'location_type': 'location', 'location': make_location()}, self.user)

    def test_fifty_plants_use_fifty_pots_without_numbering_or_consuming_them(self):
        """A complete potting run is fifty placements on one original stock claim."""
        plants = [make_specific_plant() for _ in range(50)]
        rows = self.join(plants)
        self.assertEqual(len(rows), 50)
        self.assertEqual({row.container_fill_id for row in rows}, {self.fill.pk})
        self.assertEqual({row.location_id for row in rows}, {self.store.pk})
        self.assertTrue(all(row.container_unit_id is None for row in rows))
        self.assertFalse(self.pots.serialized_units.exists())
        self.assertEqual(physical_balance(self.pots, self.store), 100)
        self.assertEqual(unpromised_bulk(self.pots, self.store), 50)
        self.assertEqual({row['share'] for row in pot_fill_shares(self.fill)}, {Fraction(1, 50)})
        with self.assertRaises(ValidationError):
            self.join()

    def test_departure_releases_one_pot_without_reusing_its_media_share(self):
        """Leaving returns an empty pot to availability, never to the same fill."""
        self.fill = open_counted_fill(self.workspace, self.user, self.pots, self.store, 1)
        row, = self.join()
        self.leave(row)
        self.assertEqual(unpromised_bulk(self.pots, self.store), 50)
        self.number(self.pots, 50)
        with self.assertRaises(ValidationError):
            self.join()
        with self.assertRaises(ValidationError):
            clean_empty_fill(self.workspace, self.user, self.fill, reason='Clean')

    def test_partial_run_keeps_media_on_unused_pots(self):
        """Unplanted pots do not make the first departure absorb their mix."""
        post_application(self.draft(), self.user)
        first, = self.join()
        with self.captureOnCommitCallbacks(execute=True):
            self.leave(first)
        report = pot_fill_cost_breakdown(self.fill)
        self.assertEqual(report['departed_cost'], 2)
        self.assertEqual(report['held_cost'], 98)
        layer, = effective_allocations(first.specific_plant.batch)
        self.assertEqual(layer.amount, 2)
        self.assertEqual(layer.base_quantity, 1)
        self.assertFalse(FillDepartureRecalculation.objects.exists())
        self.join()
        self.assertIsNone(reallocate_batch(first.specific_plant.batch, self.user, 'manual_recalculate'))

    def test_later_arrivals_and_cross_batch_departures_preserve_rounding(self):
        """Reserve all three shares before a second or third plant even arrives."""
        self.fill = open_counted_fill(self.workspace, self.user, self.pots, self.store, 3)
        post_application(self.draft(), self.user)
        layers = []
        for _ in range(3):
            row, = self.join()
            with self.captureOnCommitCallbacks(execute=True):
                self.leave(row)
            layer, = effective_allocations(row.specific_plant.batch)
            layers.append(layer)
            for previous in layers:
                self.assertIsNone(reallocate_batch(previous.batch, self.user, 'manual_recalculate'))
        self.assertEqual([row.amount for row in layers], [Decimal('33.3334'), Decimal('33.3333'), Decimal('33.3333')])
        self.assertEqual(sum(row.base_quantity for row in layers), 50)

    def test_failed_bulk_arrival_rolls_back_every_source_departure(self):
        """One invalid plant cannot leave earlier selected plants half moved."""
        plants = [make_specific_plant(), make_specific_plant()]
        rows = self.join(plants)
        destination = open_counted_fill(self.workspace, self.user, self.pots, self.store, 2)
        rows[1].started = timezone.now() + timedelta(days=1)
        SpecificPlantLocation.objects.filter(pk=rows[1].pk).update(started=rows[1].started)
        with self.assertRaises(RESTValidationError):
            self.join(plants, destination)
        self.assertFalse(destination.plant_locations.exists())
        self.assertEqual(self.fill.plant_locations.filter(ended__isnull=True).count(), 2)
        self.assertFalse(FillDepartureRecalculation.objects.exists())

    def test_history_cannot_be_reassigned_reopened_or_deleted(self):
        """All ordinary interval edits preserve the counted fill's evidence."""
        row, = self.join()
        row.location = make_location()
        with self.assertRaises(ValidationError):
            row.save()
        row.refresh_from_db()
        with self.assertRaises(ProtectedError), transaction.atomic():
            row.specific_plant.delete()
        self.leave(row)
        row.refresh_from_db()
        row.ended = None
        with self.assertRaises(ValidationError):
            row.save()

    def test_counted_and_numbered_moves_share_one_placement_history(self):
        """Moving between tracking modes leaves media on the original intervals."""
        plant = make_specific_plant()
        unit = self.number(self.pots, 1)[0]
        numbered = open_numbered_fill(self.workspace, self.user, unit)
        source = move_specific_plant(plant, {'location_type': 'container_unit', 'container_unit': unit})
        counted, = self.join([plant])
        source.refresh_from_db()
        self.assertEqual(source.container_fill, numbered)
        self.assertEqual(source.ended, counted.started)
        other_unit = self.number(self.pots, 1)[0]
        moved = move_specific_plant(plant, {'location_type': 'container_unit', 'container_unit': other_unit})
        counted.refresh_from_db()
        self.assertEqual(counted.ended, moved.started)

    def test_media_changes_and_clean_cannot_rewrite_served_counted_fills(self):
        """A stale media draft or reversal cannot alter a participant's basis."""
        application = self.draft()
        post_application(application, self.user)
        stale = self.draft()
        self.join()
        for action in (lambda: post_application(stale, self.user),
                       lambda: reverse_application(application, self.user, 'Wrong fill'),
                       self.clean):
            with self.assertRaises(ValidationError):
                action()

    def test_growth_register_and_area_follow_the_counted_container(self):
        """Counted plants stand at the fill's place and each occupy one pot."""
        item = self.pots.item
        item.container_footprint_m2 = Decimal('0.01')
        item.container_size_label = 'P9'
        item.save()
        rows = self.join([make_specific_plant(), make_specific_plant()])
        self.assertEqual(location_occupancy(self.store).area, Decimal('0.02'))
        growth = current_growth(rows[0].specific_plant)
        self.assertEqual(growth['container_item'], item)
        self.assertEqual(growth['container_fill'], self.fill)
        row = register_projection(self.workspace).get(pk=rows[0].specific_plant_id)
        self.assertEqual(row.current_container, item.pk)
        self.assertEqual(row.current_container_count, 1)
        self.assertEqual(row.standing_at, self.store.pk)
        self.assertEqual(register_queryset(self.workspace, RegisterFilters(container=item.pk)).count(), 2)

    def test_empty_foreign_and_backdated_requests_are_refused(self):
        """The service validates the entire selection and the fill's chronology."""
        foreign = make_specific_plant(workspace=Workspace.objects.create(name='Other'))
        for ids in ([], [foreign.pk], [make_specific_plant().pk, foreign.pk]):
            with self.assertRaises(ValidationError):
                plant_counted_fill(self.workspace, self.user, self.fill, ids)
        with self.assertRaises(RESTValidationError):
            self.join(started=self.fill.opened_at - timedelta(seconds=1))
        self.assertFalse(self.fill.plant_locations.exists())

    def test_capacity_failure_rolls_back_the_whole_selection(self):
        """Two selected plants cannot partially occupy a bench with one space."""
        self.store.capacity_basis = 'plants'
        self.store.capacity_value = 1
        self.store.save()
        with self.assertRaises(RESTValidationError):
            self.join([make_specific_plant(), make_specific_plant()])
        self.assertFalse(self.fill.plant_locations.exists())

    def test_unpriced_departure_remains_unknown(self):
        """A fixed physical share does not turn unknown mix cost into zero."""
        type(self.media).objects.filter(pk=self.media.pk).update(base_unit_cost=None)
        post_application(self.draft(), self.user)
        row, = self.join()
        with self.captureOnCommitCallbacks(execute=True):
            self.leave(row)
        layer, = effective_allocations(row.specific_plant.batch)
        self.assertIsNone(layer.amount)
        self.assertEqual(layer.base_quantity, 1)


@skipUnlessDBFeature('has_select_for_update')
class CountedFillConcurrencyTests(PotMediaMixin, ReservationConcurrencyTestCase):
    """Stock locks serialize anonymous pot claims and opposite-direction moves."""

    def setUp(self):
        super().setUp()
        self.setup_media()

    def race(self, first, second):
        """Surface deadlocks while allowing either contender to reach stock first."""
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

    def test_two_selections_cannot_claim_the_last_pot(self):
        """Concurrent bulk runs cannot both see the same unused counted pot."""
        fill = open_counted_fill(self.workspace, None, self.pots, self.store, 1)
        first, second = make_specific_plant(), make_specific_plant()
        results = self.race(
            lambda: plant_counted_fill(self.workspace, None, fill, [first.pk]),
            lambda: plant_counted_fill(self.workspace, None, fill, [second.pk]),
        )
        self.assertEqual(results, ['done', 'rejected'])
        self.assertEqual(fill.plant_locations.count(), 1)

    def test_clean_and_placement_cannot_both_succeed(self):
        """A filled pot cannot be released as empty while a plant joins it."""
        plant = make_specific_plant()
        results = self.race(
            lambda: clean_empty_fill(self.workspace, None, self.fill, reason='Clean'),
            lambda: plant_counted_fill(self.workspace, None, self.fill, [plant.pk]),
        )
        self.assertEqual(results, ['done', 'rejected'])

    def test_opposite_moves_across_lots_share_one_stock_lock_order(self):
        """Each move locks both source and destination lots before ending either."""
        lot = make_stock_lot(item=self.pots.item, location=self.store, quantity='10')
        other = open_counted_fill(self.workspace, None, lot, self.store, 10)
        first, second = make_specific_plant(), make_specific_plant()
        plant_counted_fill(self.workspace, None, self.fill, [first.pk])
        plant_counted_fill(self.workspace, None, other, [second.pk])
        results = self.race(
            lambda: plant_counted_fill(self.workspace, None, other, [first.pk]),
            lambda: plant_counted_fill(self.workspace, None, self.fill, [second.pk]),
        )
        self.assertEqual(results, ['done', 'done'])

    def test_opposite_counted_and_numbered_moves_share_stock_lock_order(self):
        """Crossing tracking modes cannot lock a unit before the other move's lot."""
        unit = individualize_lot_units(self.workspace, None, IndividualizationRequest(self.pots, self.store, 1))[0]
        first, second = make_specific_plant(), make_specific_plant()
        move_specific_plant(first, {'location_type': 'container_unit', 'container_unit': unit})
        plant_counted_fill(self.workspace, None, self.fill, [second.pk])
        results = self.race(
            lambda: plant_counted_fill(self.workspace, None, self.fill, [first.pk]),
            lambda: move_specific_plant(second, {'location_type': 'container_unit', 'container_unit': unit}),
        )
        self.assertEqual(results, ['done', 'done'])
