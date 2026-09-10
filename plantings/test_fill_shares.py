"""Numbered-pot media shares stay fixed as plants depart independently."""
# pylint: disable=duplicate-code

from datetime import timedelta
from fractions import Fraction

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from rest_framework.exceptions import ValidationError as RESTValidationError
from rest_framework.test import APIClient

from inventory.ledger import IndividualizationRequest, individualize_lot_units
from sales.test_counted_lines import CountedStockTestCase
from seedtrays.container_fills import open_numbered_fill
from seedtrays.models import SeedTrayGeneration
from seedtrays.pot_media import numbered_fill_shares
from tests.factories import make_inventory_item, make_location, make_specific_plant, make_stock_lot

from .models import SpecificPlantLocation
from .lifecycle import EventType, OutcomeRequest, record_lifecycle_event
from .movement import move_specific_plant
from .test_fill_placement import NumberedFillPlacementConcurrencyTests


class NumberedFillShareTests(CountedStockTestCase):
    """The first exit fixes participation; subsequent exits use the same basis."""

    def setUp(self):
        super().setUp()
        self.unit, self.other_unit = self.number(self.receive(quantity='10'), 2)
        self.fill = open_numbered_fill(self.workspace, self.user, self.unit)
        self.destination = make_location()

    def join(self, plant=None, **kwargs):
        """Place a plant through the specimen movement service."""
        return move_specific_plant(plant or make_specific_plant(), {
            'location_type': 'container_unit', 'container_unit': self.unit, **kwargs,
        }, self.user)

    def leave(self, placement, **kwargs):
        """Leave the shared fill for an ordinary bench."""
        return move_specific_plant(placement.specific_plant, {
            'location_type': 'location', 'location': self.destination, **kwargs,
        }, self.user)

    def test_three_plants_keep_one_third_each_until_the_last_exit(self):
        """No rounding or division among remaining plants changes earlier shares."""
        placements = [self.join() for _ in range(3)]
        self.assertTrue(all(row['share'] is None for row in numbered_fill_shares(self.fill)))
        for index, placement in enumerate(placements, 1):
            self.leave(placement)
            rows = numbered_fill_shares(self.fill)
            self.assertEqual([row['share'] for row in rows], [Fraction(1, 3)] * 3)
            self.assertEqual(sum(row['departed_at'] is not None for row in rows), index)
            self.assertEqual(sum(row['share'] for row in rows), 1)

    def test_newcomers_and_reentry_are_refused_after_departure(self):
        """A released pot cannot start a second media allocation in the same fill."""
        placement = self.join()
        self.leave(placement)
        for plant in (make_specific_plant(), placement.specific_plant):
            with self.subTest(plant=plant.pk), self.assertRaises(RESTValidationError):
                self.join(plant)
        self.assertEqual(self.fill.plant_locations.count(), 1)

    def test_failed_destination_rolls_back_the_frozen_basis(self):
        """A rejected move cannot close participation in the source pot."""
        placement = self.join()
        other_fill = open_numbered_fill(self.workspace, self.user, self.other_unit)
        with self.assertRaises(RESTValidationError):
            move_specific_plant(placement.specific_plant, {
                'location_type': 'container_unit', 'container_unit': self.other_unit,
                'started': other_fill.opened_at - timedelta(microseconds=1),
            }, self.user)
        self.fill.refresh_from_db()
        placement.refresh_from_db()
        self.assertIsNone(self.fill.plant_share_count)
        self.assertIsNone(placement.ended)
        self.join()

    def test_departure_cannot_precede_another_participants_arrival(self):
        """Backdating cannot count a plant that had not yet joined the pot."""
        first = self.join()
        second = self.join()
        with self.assertRaises(RESTValidationError):
            self.leave(first, started=second.started - timedelta(microseconds=1))
        self.fill.refresh_from_db()
        self.assertIsNone(self.fill.plant_share_count)

    def test_later_departures_cannot_be_inserted_before_the_first(self):
        """All departure paths preserve the fill's chronology."""
        first, second = self.join(), self.join()
        self.leave(first)
        with self.assertRaises(RESTValidationError):
            self.leave(second, started=second.started)

    def test_notes_only_save_does_not_freeze_an_unsaved_departure(self):
        """update_fields must not create a basis for an end time it did not save."""
        placement = self.join()
        placement.ended = timezone.now()
        placement.notes = 'Still growing'
        placement.save(update_fields=['notes'])
        placement.refresh_from_db()
        self.fill.refresh_from_db()
        self.assertIsNone(placement.ended)
        self.assertIsNone(self.fill.plant_share_count)

    def test_rest_end_and_patch_use_the_same_denominator(self):
        """Direct interval endpoints cannot bypass share freezing."""
        first, second = self.join(), self.join()
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post(f'/plantings/specificplantlocations/{first.pk}/end/')
        self.assertEqual(response.status_code, 200, response.data)
        response = client.patch(f'/plantings/specificplantlocations/{second.pk}/', {
            'ended': timezone.now().isoformat(),
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['share'] for row in numbered_fill_shares(self.fill)], [Fraction(1, 2)] * 2)

    def test_history_and_basis_cannot_be_deleted_or_edited(self):
        """Cascade and queryset deletion cannot silently lose a participant."""
        placement = self.join()
        for delete in (placement.delete, placement.specific_plant.delete,
                       self.fill.plant_locations.all().delete):
            with self.assertRaises(ProtectedError), transaction.atomic():
                delete()
        self.leave(placement)
        self.fill.refresh_from_db()
        self.fill.plant_share_count = 2
        with self.assertRaises(ValidationError):
            self.fill.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SeedTrayGeneration.objects.filter(pk=self.fill.pk).update(plant_share_count=0)

    def test_legacy_departures_do_not_acquire_inferred_shares(self):
        """An existing exited fill retains unknown shares when its last plant leaves."""
        first, second = self.join(), self.join()
        SpecificPlantLocation.objects.filter(pk=first.pk).update(ended=timezone.now())
        self.leave(second)
        self.assertTrue(all(row['share'] is None for row in numbered_fill_shares(self.fill)))
        with self.assertRaises(RESTValidationError):
            self.join()

    def test_new_historical_interval_cannot_skip_share_freezing(self):
        """New fill participants must arrive before a separate audited departure."""
        with self.assertRaises(RESTValidationError):
            self.join(ended=timezone.now())

    def test_lifecycle_departure_also_freezes_participation(self):
        """A culled plant consumed a share just as a moved plant did."""
        first = self.join()
        self.join()
        record_lifecycle_event(first.specific_plant, self.user, OutcomeRequest(EventType.CULLED))
        rows = numbered_fill_shares(self.fill)
        self.assertEqual([row['share'] for row in rows], [Fraction(1, 2)] * 2)
        self.assertIsNotNone(rows[0]['departed_at'])

    def test_rest_delete_explains_protected_participation(self):
        """Deleting the plant is refused with a domain error, not a server error."""
        placement = self.join()
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.delete(f'/plantings/specificplants/{placement.specific_plant_id}/')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(self.fill.plant_locations.filter(pk=placement.pk).exists())


class NumberedFillShareConcurrencyTests(NumberedFillPlacementConcurrencyTests):
    """All pot participants serialize on the unit without locking one another."""

    def test_two_departures_freeze_the_same_basis(self):
        """Competing exits cannot each count only whoever remains."""
        fill = open_numbered_fill(self.workspace, None, self.unit)
        first = self.place()
        second = move_specific_plant(make_specific_plant(), {
            'location_type': 'container_unit', 'container_unit': self.unit,
        })
        ended = timezone.now()

        def leave(placement):
            placement.ended = ended
            placement.save(update_fields=['ended'])

        self.assertEqual(self.race(lambda: leave(first), lambda: leave(second)), ['done', 'done'])
        self.assertEqual([row['share'] for row in numbered_fill_shares(fill)], [Fraction(1, 2)] * 2)

    def test_arrival_racing_departure_is_counted_or_rejected(self):
        """There is no window to join after the first exit counted participants."""
        fill = open_numbered_fill(self.workspace, None, self.unit)
        placement = self.place()
        newcomer = make_specific_plant()
        started = timezone.now()
        ended = timezone.now()

        def leave():
            placement.ended = ended
            placement.save(update_fields=['ended'])

        self.race(leave, lambda: move_specific_plant(newcomer, {
            'location_type': 'container_unit', 'container_unit': self.unit, 'started': started,
        }))
        rows = numbered_fill_shares(fill)
        self.assertEqual(fill.plant_share_count, len(rows))
        self.assertEqual(sum(row['share'] for row in rows), 1)

    def test_opposite_moves_do_not_deadlock_on_source_pots(self):
        """Both unit locks are acquired in order before freezing either source."""
        location = make_location()
        lot = make_stock_lot(item=make_inventory_item(category='pot_container', tracking_mode='mixed', base_unit='each'), location=location)
        other = individualize_lot_units(self.workspace, None, IndividualizationRequest(lot, location, 1))[0]
        open_numbered_fill(self.workspace, None, self.unit)
        open_numbered_fill(self.workspace, None, other)
        self.place()
        second = make_specific_plant()
        move_specific_plant(second, {'location_type': 'container_unit', 'container_unit': other})
        started = timezone.now()
        results = self.race(
            lambda: move_specific_plant(self.plant, {'location_type': 'container_unit', 'container_unit': other, 'started': started}),
            lambda: move_specific_plant(second, {'location_type': 'container_unit', 'container_unit': self.unit, 'started': started}),
        )
        self.assertEqual(results, ['done', 'rejected'])
