"""Numbered placements capture their fill and project the pot actually occupied."""
# pylint: disable=duplicate-code

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier

from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import skipUnlessDBFeature
from django.utils import timezone
from rest_framework.exceptions import ValidationError as RESTValidationError
from rest_framework.test import APIClient

from inventory.ledger import IndividualizationRequest, individualize_lot_units
from locations.occupancy import location_occupancy
from sales.test_concurrency import ReservationConcurrencyTestCase
from sales.test_counted_lines import CountedStockTestCase
from seedtrays.container_fills import clean_empty_fill, open_numbered_fill
from tests.factories import make_inventory_item, make_location, make_specific_plant, make_stock_lot
from workspaces.models import get_current_workspace

from .growth import current_growth, record_observation
from .models import SpecificPlantLocation
from .movement import move_specific_plant
from .register import RegisterFilters, register_projection, register_queryset


class NumberedFillPlacementTests(CountedStockTestCase):
    """Pot movement leaves an immutable fill interval, with no inferred old history."""

    def setUp(self):
        super().setUp()
        self.item.container_size_label = 'P9'
        self.item.container_footprint_m2 = Decimal('0.01')
        self.item.save()
        self.lot = self.receive(quantity='10')
        self.unit, self.other_unit = self.number(self.lot, 2)
        self.fill = open_numbered_fill(self.workspace, self.user, self.unit)
        self.plant = make_specific_plant()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def move(self, unit=None, **overrides):
        """Use the existing specimen movement path rather than a parallel API."""
        data = {'location_type': 'container_unit', 'container_unit': unit or self.unit}
        data.update(overrides)
        return move_specific_plant(self.plant, data, self.user)

    def test_move_captures_fill_and_keeps_it_when_the_plant_leaves(self):
        """The source placement never switches to whichever fill the pot opens next."""
        placement = self.move()
        self.assertEqual(placement.container_fill, self.fill)
        next_fill = open_numbered_fill(self.workspace, self.user, self.other_unit)
        next_placement = self.move(self.other_unit)
        placement.refresh_from_db()
        self.assertEqual(placement.container_fill, self.fill)
        self.assertEqual(placement.ended, next_placement.started)
        self.assertEqual(next_placement.container_fill, next_fill)
        with self.assertRaises(ValidationError):
            clean_empty_fill(self.workspace, self.user, self.fill, reason='Clean', occurred_at=placement.started)
        clean_empty_fill(self.workspace, self.user, self.fill, reason='Wash empty pot.')
        replacement = open_numbered_fill(self.workspace, self.user, self.unit)
        placement.refresh_from_db()
        self.assertNotEqual(replacement.pk, placement.container_fill_id)

    def test_bare_pots_and_preexisting_intervals_are_not_backfilled(self):
        """Capturing a cultivation cycle never invents one for legacy placements."""
        placement = self.move(self.other_unit)
        self.assertIsNone(placement.container_fill)
        with self.assertRaises(ValidationError):
            open_numbered_fill(self.workspace, self.user, self.other_unit)
        self.move()
        new_fill = open_numbered_fill(self.workspace, self.user, self.other_unit)
        placement.refresh_from_db()
        placement.notes = 'Historical note'
        placement.save()
        self.assertIsNone(placement.container_fill)
        self.assertNotEqual(new_fill, placement.container_fill)

    def test_invalid_backdated_move_rolls_back_source_departure(self):
        """A failed destination cannot leave a plant without its previous place."""
        source = self.move(self.other_unit, started=self.fill.opened_at - timedelta(days=2))
        with self.assertRaises(RESTValidationError):
            self.move(started=self.fill.opened_at - timedelta(days=1))
        source.refresh_from_db()
        self.assertIsNone(source.ended)
        self.assertEqual(self.plant.locations.count(), 1)

    def test_fill_identity_and_recorded_departure_cannot_be_rewritten(self):
        """Ordinary edits cannot detach the plant from the media cycle it used."""
        placement = self.move()
        for fields in ({'container_fill': None}, {'container_unit': self.other_unit}, {'started': timezone.now()}):
            for key, value in fields.items():
                setattr(placement, key, value)
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                placement.save()
            placement.refresh_from_db()
        self.move(self.other_unit)
        placement.refresh_from_db()
        placement.ended = None
        with self.assertRaises(ValidationError):
            placement.save()

    def test_backdated_arrival_cannot_bypass_a_fill_that_was_just_cleaned(self):
        """A queued move must not record bare-pot occupancy during a previous fill."""
        clean_empty_fill(self.workspace, self.user, self.fill, reason='Clean')
        with self.assertRaises(RESTValidationError):
            self.move(started=self.fill.opened_at)
        self.assertFalse(self.plant.locations.exists())

    def test_existing_interval_cannot_be_patched_into_an_open_fill(self):
        """Joining a fill requires a new placement and its own start time."""
        placement = self.move(self.other_unit)
        response = self.client.patch(f'/plantings/specificplantlocations/{placement.pk}/', {'container_unit': self.unit.pk}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        placement.refresh_from_db()
        self.assertEqual(placement.container_unit, self.other_unit)

    def test_rest_returns_captured_fill_and_rejects_history_changes(self):
        """The fill is readable through existing placement and plant detail contracts."""
        response = self.client.post(f'/plantings/specificplants/{self.plant.pk}/move/', {
            'location_type': 'container_unit', 'container_unit': self.unit.pk,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['container_fill'], self.fill.pk)
        response = self.client.get(f'/plantings/specificplants/{self.plant.pk}/')
        self.assertEqual(response.data['growth']['container_fill'], self.fill.pk)
        placement = self.plant.locations.get(ended__isnull=True)
        response = self.client.patch(f'/plantings/specificplantlocations/{placement.pk}/', {'started': timezone.now().isoformat()}, format='json')
        self.assertEqual(response.status_code, 400, response.data)

    def test_database_rejects_fill_on_an_unrelated_place(self):
        """Bypassing model validation cannot attach pot media to a garden location."""
        placement = self.move()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SpecificPlantLocation.objects.filter(pk=placement.pk).update(location_type='location', container_unit=None)

    def test_current_container_and_register_filters_follow_the_actual_pot(self):
        """A newer observation cannot override the numbered container the plant occupies."""
        old_item = make_inventory_item(category='pot_container', base_unit='each', container_size_label='Small')
        record_observation(self.workspace, self.user, plant_ids=[self.plant.pk], container_item=old_item, container_count=7)
        self.move()
        record_observation(self.workspace, self.user, plant_ids=[self.plant.pk], container_item=old_item, container_count=7)
        growth = current_growth(self.plant)
        self.assertEqual(growth['container_item'], self.item)
        self.assertEqual(growth['container_count'], 1)
        self.assertEqual(growth['container_size_label'], 'P9')
        row = register_projection(self.workspace).get(pk=self.plant.pk)
        self.assertEqual(row.current_container, self.item.pk)
        self.assertEqual(row.current_container_size, 'P9')
        self.assertEqual(row.current_container_count, 1)
        self.assertTrue(register_queryset(self.workspace, RegisterFilters(container=self.item.pk)).filter(pk=self.plant.pk).exists())
        self.assertFalse(register_queryset(self.workspace, RegisterFilters(container=old_item.pk)).filter(pk=self.plant.pk).exists())

    def test_leaving_a_numbered_pot_retires_its_old_container_observation(self):
        """The abandoned pot does not follow a plant onto a bench in the register."""
        record_observation(self.workspace, self.user, plant_ids=[self.plant.pk], container_item=self.item, container_count=1)
        self.move()
        destination = move_specific_plant(self.plant, {'location_type': 'location', 'location': make_location()}, self.user)
        self.assertIsNone(current_growth(self.plant)['container_item'])
        row = register_projection(self.workspace).get(pk=self.plant.pk)
        self.assertIsNone(row.current_container)
        self.assertIsNone(row.current_container_count)
        record_observation(self.workspace, self.user, plant_ids=[self.plant.pk], container_item=self.item, container_count=2, occurred_at=destination.started)
        self.assertEqual(current_growth(self.plant)['container_count'], 2)
        self.assertEqual(register_projection(self.workspace).get(pk=self.plant.pk).current_container_count, 2)

    def test_shared_pot_still_counts_its_footprint_once(self):
        """Three plants can capture the same fill without multiplying its area."""
        for _ in range(3):
            plant = make_specific_plant()
            location = move_specific_plant(plant, {'location_type': 'container_unit', 'container_unit': self.unit}, self.user)
            self.assertEqual(location.container_fill, self.fill)
        self.assertEqual(self.fill.plant_locations.count(), 3)
        # Both numbered pots stand here, including the empty second pot.
        self.assertEqual(location_occupancy(self.store).area, Decimal('0.02'))


@skipUnlessDBFeature('has_select_for_update')
class NumberedFillPlacementConcurrencyTests(ReservationConcurrencyTestCase):
    """A plant cannot silently arrive after a fill was counted as empty."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        location = make_location()
        item = make_inventory_item(category='pot_container', tracking_mode='mixed', base_unit='each')
        lot = make_stock_lot(item=item, location=location)
        self.unit = individualize_lot_units(self.workspace, None, IndividualizationRequest(lot, location, 1))[0]
        self.plant = make_specific_plant()

    def race(self, first, second):
        """Run competing operations on independent connections, surfacing deadlocks."""
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

    def place(self):
        """Use the same placement service as the specimen UI."""
        return move_specific_plant(self.plant, {'location_type': 'container_unit', 'container_unit': self.unit})

    def test_opening_and_placement_agree_whether_the_plant_entered_a_fill(self):
        """A bare-pot placement blocks opening; an earlier opening is captured."""
        # The fill physically happened before either submission. Otherwise a
        # move's default timestamp can predate the racing opening, correctly
        # failing chronology rather than exercising the stock-lock ordering.
        opened_at = timezone.now() - timedelta(seconds=1)
        self.race(lambda: open_numbered_fill(self.workspace, None, self.unit, opened_at=opened_at), self.place)
        placement = self.plant.locations.get(ended__isnull=True)
        fill = self.unit.container_fills.first()
        self.assertEqual(placement.container_fill_id, fill.pk if fill else None)

    def test_cleaning_and_placement_cannot_leave_an_occupied_closed_fill(self):
        """The plant either joins the open fill or arrives in the cleaned bare pot."""
        fill = open_numbered_fill(self.workspace, None, self.unit)
        cleaned_at = timezone.now()
        self.race(lambda: clean_empty_fill(self.workspace, None, fill, reason='Clean', occurred_at=cleaned_at), self.place)
        fill.refresh_from_db()
        placement = self.plant.locations.get(ended__isnull=True)
        self.assertEqual(placement.container_fill_id, fill.pk if fill.status == 'open' else None)

    def test_legacy_interval_edit_cannot_race_past_fill_opening(self):
        """A bare-pot correction must see an opening committed while it waited."""
        placement = SpecificPlantLocation.objects.create(
            specific_plant=self.plant, location_type='location', location=make_location(),
        )

        def edit():
            placement.location_type = 'container_unit'
            placement.location = None
            placement.container_unit = self.unit
            placement.save()

        results = self.race(lambda: open_numbered_fill(self.workspace, None, self.unit), edit)
        self.assertEqual(results, ['done', 'rejected'])
        placement.refresh_from_db()
        self.assertNotEqual(placement.container_unit_id == self.unit.pk, self.unit.container_fills.exists())
