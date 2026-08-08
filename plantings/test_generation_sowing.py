"""Every tray sowing names the fill of the tray it went into.

A sowing that does not is the ambiguity the whole feature exists to remove: the
seedlings it raises have no way to say which media they grew in, so a later fill
of the same cells would look like theirs.
"""
# pylint: disable=duplicate-code

from django.utils import timezone

from seedtrays.generations import open_generation
from seedtrays.models import SeedTrayGeneration
from tests.api import RESTContractTestCase
from tests.factories import (
    make_batch_for_packet,
    make_seed_packet,
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_generation,
)

from .models import SeedTrayPlanting


class SowingGenerationTests(RESTContractTestCase):
    """The API attaches a sowing to the tray's open fill, or refuses it."""

    def setUp(self):
        super().setUp()
        self.packet = make_seed_packet()
        self.batch = make_batch_for_packet(self.packet)
        self.tray = make_seed_tray()
        self.cell = make_seed_tray_cell(tray=self.tray)

    def sow(self, **overrides):
        """Post one tray sowing through the API."""
        payload = {
            'seeds_used': self.packet.pk,
            'batch': self.batch.pk,
            'quantity': 4,
            'seed_tray': self.tray.pk,
        }
        payload.update(overrides)
        if payload.get('seed_tray') is None:
            payload.pop('seed_tray', None)
        return self.client.post('/plantings/seedtray/', payload, format='json')

    def test_a_sowing_joins_the_open_generation_without_being_told(self):
        """There is only one open fill, so asking twice invites disagreement."""
        generation = make_seed_tray_generation(tray=self.tray)

        response = self.sow()

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['generation'], generation.pk)
        sowing = SeedTrayPlanting.objects.get(pk=response.data['pk'])
        self.assertEqual(sowing.generation_id, generation.pk)

    def test_sowing_into_an_unfilled_tray_is_refused(self):
        """Seed sown into media nobody recorded has no cost to inherit."""
        response = self.sow()

        self.assertEqual(response.status_code, 400)
        self.assertIn('no open generation', str(response.data['generation']))
        self.assertFalse(SeedTrayPlanting.objects.exists())

    def test_sowing_into_a_cleaned_generation_is_refused(self):
        """A closed fill has been emptied; nothing can be sown into it."""
        generation = make_seed_tray_generation(tray=self.tray)
        SeedTrayGeneration.objects.filter(pk=generation.pk).update(
            status=SeedTrayGeneration.Status.CLOSED,
            closed_at=timezone.now(),
        )

        response = self.sow(generation=generation.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('cleaned', str(response.data['generation']))

    def test_a_generation_from_another_tray_is_refused(self):
        """Cells and media would otherwise cross between two trays."""
        make_seed_tray_generation(tray=self.tray)
        other = make_seed_tray_generation()

        response = self.sow(generation=other.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('another tray', str(response.data['generation']))

    def test_a_generation_needs_the_tray_it_belongs_to(self):
        """A sowing with no tray has no fill to join."""
        generation = make_seed_tray_generation(tray=self.tray)

        response = self.sow(seed_tray=None, generation=generation.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('name the tray too', str(response.data['generation']))

    def test_the_tray_derived_from_cells_supplies_the_generation(self):
        """A sowing that names only its cells still lands in the right fill."""
        generation = make_seed_tray_generation(tray=self.tray)

        response = self.sow(
            seed_tray=None,
            cell_plantings=[{'cell': self.cell.pk, 'quantity': 4}],
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['generation'], generation.pk)

    def test_a_sowing_cannot_move_between_generations(self):
        """Its media history would move with it."""
        generation = make_seed_tray_generation(tray=self.tray)
        created = self.sow()
        SeedTrayGeneration.objects.filter(pk=generation.pk).update(
            status=SeedTrayGeneration.Status.CLOSED,
            closed_at=timezone.now(),
        )
        following = open_generation(self.tray, None)

        response = self.client.patch(
            f'/plantings/seedtray/{created.data["pk"]}/',
            {'generation': following.pk},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('Cannot move', str(response.data['generation']))

    def test_a_sowing_recorded_before_generations_keeps_its_unknown_fill(self):
        """Migration never guesses, so the API must tolerate a null generation."""
        sowing = SeedTrayPlanting.objects.create(
            seeds_used=self.packet,
            batch=self.batch,
            quantity=4,
            seed_tray=self.tray,
        )

        response = self.client.get(f'/plantings/seedtray/{sowing.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['generation'])
