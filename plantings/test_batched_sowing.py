"""Tests that every sowing joins exactly one compatible production batch."""
# pylint: disable=duplicate-code
from tests.api import RESTContractTestCase
from tests.factories import (
    make_batch_for_packet,
    make_garden_row,
    make_garden_square,
    make_production_batch,
    make_seed_packet,
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_cell_planting,
    make_seed_tray_generation,
    make_seed_tray_planting,
    make_specific_plant,
)

from .models import (
    GardenSquareDirectSowPlanting,
    GardenSquareTransplant,
    ProductionBatch,
    SeedTrayPlanting,
)


class BatchedSowingRESTTests(RESTContractTestCase):
    """Sowing writes select an active batch or create one inline."""

    def setUp(self):
        super().setUp()
        # The current-planting summaries are session-authenticated views.
        self.client.force_login(self.user)
        self.packet = make_seed_packet()
        self.variety = self.packet.seeds.plant_variety
        self.row = make_garden_row()
        self.square = make_garden_square()
        self.tray = make_seed_tray()
        make_seed_tray_generation(tray=self.tray)
        self.cell = make_seed_tray_cell(tray=self.tray)
        self.batch = make_batch_for_packet(self.packet)

    def _sow_square(self, **overrides):
        """Post one direct garden-square sowing and return the response."""
        payload = {
            'seeds_used': self.packet.pk,
            'quantity': 2,
            'location': self.square.pk,
        }
        payload.update(overrides)
        return self.client.post(
            '/plantings/directsowgardensquare/',
            payload,
            format='json',
        )

    def _sow_tray(self, **overrides):
        """Post one seed-tray sowing and return the response."""
        payload = {
            'seeds_used': self.packet.pk,
            'quantity': 2,
            'seed_tray': self.tray.pk,
            'cell_plantings': [{'cell': self.cell.pk, 'quantity': 2}],
        }
        payload.update(overrides)
        return self.client.post('/plantings/seedtray/', payload, format='json')

    def test_specialized_sowing_workflows_accept_an_existing_batch(self):
        """Row, square, and tray sowings keep working with a chosen batch."""
        requests = (
            (
                '/plantings/directsowgardenrow/',
                {'location': self.row.pk},
            ),
            (
                '/plantings/directsowgardensquare/',
                {'location': self.square.pk},
            ),
            (
                '/plantings/seedtray/',
                {
                    'seed_tray': self.tray.pk,
                    'cell_plantings': [{'cell': self.cell.pk, 'quantity': 2}],
                },
            ),
        )
        for url, extra in requests:
            with self.subTest(url=url):
                response = self.client.post(
                    url,
                    {
                        'seeds_used': self.packet.pk,
                        'batch': self.batch.pk,
                        'quantity': 2,
                        **extra,
                    },
                    format='json',
                )
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(response.data['batch'], self.batch.pk)

    def test_inline_batch_creation_is_activated_with_its_sowing(self):
        """An inline batch derives its variety and start from the sowing."""
        response = self._sow_square(
            planted='2026-03-05T08:00:00Z',
            new_batch={
                'code': 'SPRING-TOMATO',
                'planned_start': '2026-03-01',
                'notes': 'First succession',
            },
        )

        self.assertEqual(response.status_code, 201, response.data)
        batch = ProductionBatch.objects.get(code='SPRING-TOMATO')
        self.assertEqual(response.data['batch'], batch.pk)
        self.assertEqual(batch.status, ProductionBatch.Status.ACTIVE)
        self.assertEqual(batch.variety_id, self.variety.pk)
        self.assertEqual(
            batch.actual_start.isoformat(),
            '2026-03-05T08:00:00+00:00',
        )
        self.assertEqual(str(batch.planned_start), '2026-03-01')
        self.assertEqual(batch.created_by, self.user)
        self.assertEqual(batch.transitions.count(), 2)

    def test_a_blank_inline_code_is_generated(self):
        """A Basic-mode sowing can leave the code for the server to fill in."""
        response = self._sow_square(new_batch={'code': ''})

        self.assertEqual(response.status_code, 201, response.data)
        batch = ProductionBatch.objects.get(pk=response.data['batch'])
        self.assertTrue(batch.code.startswith('CROP-'))
        self.assertTrue(batch.code_is_generated)

    def test_an_omitted_inline_code_is_generated(self):
        """The code key itself is optional, not only allowed to be blank."""
        response = self._sow_square(new_batch={})

        self.assertEqual(response.status_code, 201, response.data)
        batch = ProductionBatch.objects.get(pk=response.data['batch'])
        self.assertTrue(batch.code.startswith('CROP-'))

    def test_a_sowing_needs_exactly_one_batch_choice(self):
        """Batches are never silently generated, nor chosen twice."""
        neither = self._sow_square()
        self.assertEqual(neither.status_code, 400)
        self.assertIn('batch', neither.data)

        both = self._sow_square(
            batch=self.batch.pk,
            new_batch={'code': 'SPRING-2'},
        )
        self.assertEqual(both.status_code, 400)
        self.assertIn('batch', both.data)

        self.assertFalse(GardenSquareDirectSowPlanting.objects.exists())

    def test_a_sowing_cannot_join_an_inactive_or_mismatched_batch(self):
        """Variety and lifecycle status both gate batch attachment."""
        other_variety = make_production_batch()
        mismatched = self._sow_square(batch=other_variety.pk)
        self.assertEqual(mismatched.status_code, 400)
        self.assertIn('different plant variety', str(mismatched.data['batch']))

        planned = make_production_batch(
            variety=self.variety,
            status=ProductionBatch.Status.PLANNED,
        )
        inactive = self._sow_square(batch=planned.pk)
        self.assertEqual(inactive.status_code, 400)
        self.assertIn('only join an active batch', str(inactive.data['batch']))

        self.assertFalse(GardenSquareDirectSowPlanting.objects.exists())

    def test_a_finalized_batch_cannot_gain_more_work(self):
        """Attachment is rejected once a batch stops producing seedlings."""
        make_garden_square_sowing = self._sow_square(batch=self.batch.pk)
        self.assertEqual(make_garden_square_sowing.status_code, 201)
        sowing = GardenSquareDirectSowPlanting.objects.get(
            pk=make_garden_square_sowing.data['pk'],
        )
        sowing.removed = True
        sowing.save(update_fields=['removed'])
        finalized = self.client.post(
            f'/plantings/batches/{self.batch.pk}/finalize-output/',
            {},
            format='json',
        )
        self.assertEqual(finalized.status_code, 200, finalized.data)

        response = self._sow_square(batch=self.batch.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('only join an active batch', str(response.data['batch']))
        self.assertEqual(GardenSquareDirectSowPlanting.objects.count(), 1)

    def test_an_inline_batch_rolls_back_with_a_rejected_sowing(self):
        """A rejected sowing never leaves an orphaned batch behind."""
        response = self._sow_tray(
            new_batch={'code': 'ORPHAN'},
            cell_plantings=[{'cell': self.cell.pk, 'quantity': 9}],
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(ProductionBatch.objects.filter(code='ORPHAN').exists())
        self.assertFalse(SeedTrayPlanting.objects.exists())

    def test_duplicate_inline_batch_codes_are_rejected(self):
        """Inline creation obeys the same unique-code rule as the API."""
        response = self._sow_square(new_batch={'code': self.batch.code})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(GardenSquareDirectSowPlanting.objects.count(), 0)

    def test_a_sowing_cannot_move_between_batches(self):
        """The batch a sowing joins is fixed for the life of that sowing."""
        created = self._sow_square(batch=self.batch.pk)
        self.assertEqual(created.status_code, 201)
        replacement = make_batch_for_packet(self.packet)

        response = self.client.patch(
            f'/plantings/directsowgardensquare/{created.data["pk"]}/',
            {'batch': replacement.pk},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('between batches', str(response.data['batch']))

    def test_sowing_corrections_must_keep_the_batch_variety(self):
        """A packet correction cannot silently change what a batch grows."""
        created = self._sow_square(batch=self.batch.pk)
        self.assertEqual(created.status_code, 201)
        url = f'/plantings/directsowgardensquare/{created.data["pk"]}/correct-sowing/'
        other_packet = make_seed_packet()

        rejected = self.client.post(
            url,
            {'seeds_used': other_packet.pk, 'reason': 'Wrong packet'},
            format='json',
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertIn('different variety', str(rejected.data['seeds_used']))

        same_variety_packet = make_seed_packet(seeds=self.packet.seeds)
        accepted = self.client.post(
            url,
            {'seeds_used': same_variety_packet.pk, 'reason': 'Wrong packet'},
            format='json',
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)

    def test_batch_appears_in_planting_and_plant_responses(self):
        """Existing identifiers stay, and the batch joins them."""
        sowing = make_seed_tray_planting(
            seeds_used=self.packet,
            batch=self.batch,
            seed_tray=self.tray,
        )
        cell_planting = make_seed_tray_cell_planting(
            seed_tray_planting=sowing,
            cell=self.cell,
        )
        plant = make_specific_plant(cell_planting=cell_planting)
        transplant = GardenSquareTransplant.objects.create(
            original_planting=sowing,
            quantity=1,
            location=self.square,
        )

        plant_response = self.client.get(f'/plantings/specificplants/{plant.pk}/')
        self.assertEqual(plant_response.data['pk'], plant.pk)
        self.assertEqual(plant_response.data['cell_planting'], cell_planting.pk)
        self.assertEqual(plant_response.data['batch'], self.batch.pk)

        transplant_response = self.client.get(
            f'/plantings/transplantedgardensquare/{transplant.pk}/',
        )
        self.assertEqual(transplant_response.data['batch'], self.batch.pk)

    def test_current_summaries_group_through_batches(self):
        """Both display summaries name the batch without losing their shape."""
        tray_sowing = make_seed_tray_planting(
            seeds_used=self.packet,
            batch=self.batch,
            seed_tray=self.tray,
        )
        self._sow_square(batch=self.batch.pk)

        trays = self.client.get('/plantings/seedtray/current/').json()['plantings']
        self.assertEqual(trays[0]['pk'], tray_sowing.pk)
        self.assertEqual(trays[0]['batch'], self.batch.pk)
        self.assertEqual(trays[0]['batch_code'], self.batch.code)

        squares = self.client.get(
            '/plantings/garden/squares/current/',
        ).json()['plantings']
        self.assertEqual(squares[0]['batch'], self.batch.pk)
        self.assertEqual(squares[0]['batch_code'], self.batch.code)
