"""Tests for planting quantity invariants."""
# pylint: disable=duplicate-code
from concurrent.futures import ThreadPoolExecutor
import json

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from rest_framework import serializers
from rest_framework.test import APIClient

from garden.models import GardenArea, GardenBed, GardenRow, GardenSquare
from plants.models import Plant, PlantFamily, PlantVariety
from seeds.models import SeedPacket, Seeds
from seedtrays.models import SeedTray, SeedTrayCell, SeedTrayModel
from supplies.models import Supplier
from .models import (
    GardenRowDirectSowPlanting,
    GardenSquareDirectSowPlanting,
    GardenSquareTransplant,
    SeedTrayCellPlanting,
    SeedTrayPlanting,
    SpecificPlant,
)
from .rest import SpecificPlantSerializer


class PositiveQuantityAPITests(TestCase):  # pylint: disable=too-many-public-methods
    """Planting APIs reject quantities that cannot represent planted items."""

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username='quantity-tester'))
        family = PlantFamily.objects.create(name='Apiaceae')
        plant = Plant.objects.create(family=family, name='Carrot')
        variety = PlantVariety.objects.create(plant=plant, name='Nantes')
        self.packet = SeedPacket.objects.create(
            seeds=Seeds.objects.create(
                supplier=Supplier.objects.create(name='Quantity Supplier'),
                plant_variety=variety,
            ),
        )
        area = GardenArea.objects.create(name='Quantity garden', size_x=10, size_y=10)
        bed = GardenBed.objects.create(
            area=area,
            name='Quantity bed',
            placement_x=0,
            placement_y=0,
            size_x=10,
            size_y=10,
        )
        self.row = GardenRow.objects.create(
            bed=bed,
            name='Quantity row',
            placement_x=0,
            placement_y=0,
            size_x=10,
            size_y=1,
        )
        self.square = GardenSquare.objects.create(
            bed=bed,
            name='Quantity square',
            placement_x=0,
            placement_y=0,
            size_x=1,
            size_y=1,
        )
        tray_model = SeedTrayModel.objects.create(
            identifier='quantity-tray',
            height=10,
            x_size=20,
            y_size=30,
            x_cells=2,
            y_cells=1,
            cell_size_ml=40,
        )
        self.tray = SeedTray.objects.create(model=tray_model)
        self.cell = SeedTrayCell.objects.create(
            tray=self.tray,
            x_position=0,
            y_position=0,
        )
        self.other_cell = SeedTrayCell.objects.create(
            tray=self.tray,
            x_position=1,
            y_position=0,
        )
        self.original_planting = SeedTrayPlanting.objects.create(
            seeds_used=self.packet,
            quantity=1,
            seed_tray=self.tray,
        )

    def _quantity_endpoints(self):
        return [
            (
                '/plantings/directsowgardenrow/',
                GardenRowDirectSowPlanting,
                {'seeds_used': self.packet.pk, 'location': self.row.pk},
            ),
            (
                '/plantings/directsowgardensquare/',
                GardenSquareDirectSowPlanting,
                {'seeds_used': self.packet.pk, 'location': self.square.pk},
            ),
            (
                '/plantings/seedtray/',
                SeedTrayPlanting,
                {'seeds_used': self.packet.pk, 'seed_tray': self.tray.pk},
            ),
            (
                '/plantings/transplantedgardensquare/',
                GardenSquareTransplant,
                {
                    'original_planting': self.original_planting.pk,
                    'location': self.square.pk,
                },
            ),
        ]

    def test_create_rejects_non_positive_parent_quantities(self):
        """Every aggregate planting endpoint requires at least one item."""
        for quantity in (0, -1):
            for url, model, payload in self._quantity_endpoints():
                with self.subTest(quantity=quantity, model=model.__name__):
                    original_count = model.objects.count()
                    response = self.client.post(
                        url,
                        data=json.dumps({**payload, 'quantity': quantity}),
                        content_type='application/json',
                    )

                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(
                        response.json(),
                        {'quantity': ['Ensure this value is greater than or equal to 1.']},
                    )
                    self.assertEqual(model.objects.count(), original_count)

    def test_create_rejects_non_positive_cell_quantities(self):
        """A nested allocation cannot represent zero or fewer seeds."""
        for quantity in (0, -1):
            with self.subTest(quantity=quantity):
                response = self.client.post(
                    '/plantings/seedtray/',
                    data=json.dumps({
                        'seeds_used': self.packet.pk,
                        'quantity': 1,
                        'seed_tray': self.tray.pk,
                        'cell_plantings': [{
                            'cell': self.cell.pk,
                            'quantity': quantity,
                        }],
                    }),
                    content_type='application/json',
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json(),
                    {
                        'cell_plantings': [{
                            'quantity': [
                                'Ensure this value is greater than or equal to 1.'
                            ],
                        }],
                    },
                )

    def test_create_rejects_cell_allocation_above_parent_quantity(self):
        """Cell allocations cannot account for more seeds than were planted."""
        response = self.client.post(
            '/plantings/seedtray/',
            data=json.dumps({
                'seeds_used': self.packet.pk,
                'quantity': 1,
                'seed_tray': self.tray.pk,
                'cell_plantings': [{
                    'cell': self.cell.pk,
                    'quantity': 2,
                }],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                'cell_plantings': [
                    'Cell allocation total cannot exceed planting quantity.'
                ],
            },
        )

    def test_create_rejects_collective_cell_over_allocation(self):
        """Valid individual allocations cannot collectively exceed the parent."""
        original_count = SeedTrayPlanting.objects.count()

        response = self.client.post(
            '/plantings/seedtray/',
            data=json.dumps({
                'seeds_used': self.packet.pk,
                'quantity': 3,
                'seed_tray': self.tray.pk,
                'cell_plantings': [
                    {'cell': self.cell.pk, 'quantity': 2},
                    {'cell': self.other_cell.pk, 'quantity': 2},
                ],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                'cell_plantings': [
                    'Cell allocation total cannot exceed planting quantity.'
                ],
            },
        )
        self.assertEqual(SeedTrayPlanting.objects.count(), original_count)

    def test_update_rejects_duplicate_cell_allocations(self):
        """One request cannot allocate the same tray cell more than once."""
        self.original_planting.quantity = 2
        self.original_planting.save(update_fields=['quantity'])

        response = self.client.patch(
            f'/plantings/seedtray/{self.original_planting.pk}/',
            data=json.dumps({
                'cell_plantings': [
                    {'cell': self.cell.pk, 'quantity': 1},
                    {'cell': self.cell.pk, 'quantity': 1},
                ],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {'cell_plantings': ['Each cell may only be allocated once.']},
        )
        self.assertFalse(self.original_planting.cell_plantings.exists())

    def test_update_rejects_parent_quantity_below_retained_allocation(self):
        """Reducing a parent cannot strand a larger retained allocation."""
        self.original_planting.quantity = 2
        self.original_planting.save(update_fields=['quantity'])
        cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=self.original_planting,
            cell=self.cell,
            quantity=2,
        )

        response = self.client.patch(
            f'/plantings/seedtray/{self.original_planting.pk}/',
            data=json.dumps({'quantity': 1}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                'cell_plantings': [
                    'Cell allocation total cannot exceed planting quantity.'
                ],
            },
        )
        self.original_planting.refresh_from_db()
        cell_planting.refresh_from_db()
        self.assertEqual(self.original_planting.quantity, 2)
        self.assertEqual(cell_planting.seed_tray_planting, self.original_planting)
        self.assertEqual(cell_planting.quantity, 2)

    def test_partial_cell_allocation_is_allowed(self):
        """A planting may leave some seeds unassigned to individual cells."""
        response = self.client.post(
            '/plantings/seedtray/',
            data=json.dumps({
                'seeds_used': self.packet.pk,
                'quantity': 2,
                'seed_tray': self.tray.pk,
                'cell_plantings': [{
                    'cell': self.cell.pk,
                    'quantity': 1,
                }],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        planting = SeedTrayPlanting.objects.get(pk=response.json()['pk'])
        self.assertEqual(planting.quantity, 2)
        self.assertEqual(planting.cell_plantings.get().quantity, 1)

    def test_exact_cell_allocation_is_allowed(self):
        """Allocations may collectively use the planting's full capacity."""
        response = self.client.post(
            '/plantings/seedtray/',
            data=json.dumps({
                'seeds_used': self.packet.pk,
                'quantity': 2,
                'seed_tray': self.tray.pk,
                'cell_plantings': [
                    {'cell': self.cell.pk, 'quantity': 1},
                    {'cell': self.other_cell.pk, 'quantity': 1},
                ],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        planting = SeedTrayPlanting.objects.get(pk=response.json()['pk'])
        self.assertEqual(planting.quantity, 2)
        self.assertEqual(
            dict(planting.cell_plantings.values_list('cell_id', 'quantity')),
            {self.cell.pk: 1, self.other_cell.pk: 1},
        )

    def test_specific_plant_creation_stops_at_cell_capacity(self):
        """Only allocated seeds can become specific germinated plants."""
        cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=self.original_planting,
            cell=self.cell,
            quantity=1,
        )
        payload = json.dumps({'cell_planting': cell_planting.pk})

        first_response = self.client.post(
            '/plantings/specificplants/',
            data=payload,
            content_type='application/json',
        )
        second_response = self.client.post(
            '/plantings/specificplants/',
            data=payload,
            content_type='application/json',
        )

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 400)
        self.assertEqual(
            second_response.json(),
            {
                'cell_planting': [
                    'Germination count cannot exceed this cell allocation.'
                ],
            },
        )
        self.assertEqual(cell_planting.specific_plants.count(), 1)

    def test_specific_plant_creation_uses_full_larger_capacity(self):
        """Every slot above one is usable, and the next germination is rejected."""
        self.original_planting.quantity = 3
        self.original_planting.save(update_fields=['quantity'])
        cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=self.original_planting,
            cell=self.cell,
            quantity=3,
        )
        payload = json.dumps({'cell_planting': cell_planting.pk})

        for expected_count in range(1, 4):
            with self.subTest(expected_count=expected_count):
                response = self.client.post(
                    '/plantings/specificplants/',
                    data=payload,
                    content_type='application/json',
                )

                self.assertEqual(response.status_code, 201)
                self.assertEqual(
                    cell_planting.specific_plants.count(),
                    expected_count,
                )

        response = self.client.post(
            '/plantings/specificplants/',
            data=payload,
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                'cell_planting': [
                    'Germination count cannot exceed this cell allocation.'
                ],
            },
        )
        self.assertEqual(cell_planting.specific_plants.count(), 3)

    def test_specific_plant_patch_allows_mutable_fields(self):
        """Notes can change without altering the plant's origin allocation."""
        cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=self.original_planting,
            cell=self.cell,
            quantity=1,
        )
        create_response = self.client.post(
            '/plantings/specificplants/',
            data=json.dumps({
                'cell_planting': cell_planting.pk,
                'notes': 'Initial notes',
            }),
            content_type='application/json',
        )
        self.assertEqual(create_response.status_code, 201)
        plant = SpecificPlant.objects.get(pk=create_response.json()['pk'])

        response = self.client.patch(
            f'/plantings/specificplants/{plant.pk}/',
            data=json.dumps({'notes': 'Updated notes'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['notes'], 'Updated notes')
        plant.refresh_from_db()
        self.assertEqual(plant.notes, 'Updated notes')
        self.assertEqual(plant.cell_planting, cell_planting)

    def test_specific_plant_cannot_be_reassigned(self):
        """Changing a plant's origin cannot bypass another cell's capacity."""
        first_cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=self.original_planting,
            cell=self.cell,
            quantity=1,
        )
        other_planting = SeedTrayPlanting.objects.create(
            seeds_used=self.packet,
            quantity=1,
            seed_tray=self.tray,
        )
        second_cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=other_planting,
            cell=self.cell,
            quantity=1,
        )
        plant = SpecificPlant.objects.create(cell_planting=first_cell_planting)

        response = self.client.patch(
            f'/plantings/specificplants/{plant.pk}/',
            data=json.dumps({'cell_planting': second_cell_planting.pk}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        plant.refresh_from_db()
        self.assertEqual(plant.cell_planting, first_cell_planting)

    def test_allocation_cannot_drop_below_germination_count(self):
        """Replacement cannot remove capacity already used by plants."""
        self.original_planting.quantity = 2
        self.original_planting.save(update_fields=['quantity'])
        cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=self.original_planting,
            cell=self.cell,
            quantity=2,
        )
        SpecificPlant.objects.bulk_create([
            SpecificPlant(cell_planting=cell_planting),
            SpecificPlant(cell_planting=cell_planting),
        ])

        response = self.client.patch(
            f'/plantings/seedtray/{self.original_planting.pk}/',
            data=json.dumps({
                'cell_plantings': [{
                    'cell': self.cell.pk,
                    'quantity': 1,
                }],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                'cell_plantings': [
                    f'Cell {self.cell.pk} allocation cannot be less than '
                    'its germinated plant count (2).'
                ],
            },
        )
        cell_planting.refresh_from_db()
        self.assertEqual(cell_planting.quantity, 2)

    def test_safe_allocation_reduction_preserves_germinated_plant(self):
        """A valid replacement updates the referenced row rather than deleting it."""
        self.original_planting.quantity = 2
        self.original_planting.save(update_fields=['quantity'])
        cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=self.original_planting,
            cell=self.cell,
            quantity=2,
        )
        plant = SpecificPlant.objects.create(cell_planting=cell_planting)

        response = self.client.patch(
            f'/plantings/seedtray/{self.original_planting.pk}/',
            data=json.dumps({
                'cell_plantings': [{
                    'cell': self.cell.pk,
                    'quantity': 1,
                }],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        cell_planting.refresh_from_db()
        plant.refresh_from_db()
        self.assertEqual(cell_planting.quantity, 1)
        self.assertEqual(plant.cell_planting, cell_planting)

    def test_multi_cell_update_preserves_existing_allocations(self):
        """A mixed allocation update keeps both existing rows and plant origins."""
        self.original_planting.quantity = 4
        self.original_planting.save(update_fields=['quantity'])
        germinated_allocation = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=self.original_planting,
            cell=self.cell,
            quantity=2,
        )
        plant = SpecificPlant.objects.create(
            cell_planting=germinated_allocation,
        )
        other_allocation = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=self.original_planting,
            cell=self.other_cell,
            quantity=2,
        )
        original_ids = {
            self.cell.pk: germinated_allocation.pk,
            self.other_cell.pk: other_allocation.pk,
        }

        response = self.client.patch(
            f'/plantings/seedtray/{self.original_planting.pk}/',
            data=json.dumps({
                'cell_plantings': [
                    {'cell': self.cell.pk, 'quantity': 3},
                    {'cell': self.other_cell.pk, 'quantity': 1},
                ],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        germinated_allocation.refresh_from_db()
        other_allocation.refresh_from_db()
        plant.refresh_from_db()
        self.assertEqual(germinated_allocation.quantity, 3)
        self.assertEqual(other_allocation.quantity, 1)
        self.assertEqual(plant.cell_planting, germinated_allocation)
        self.assertEqual(
            dict(self.original_planting.cell_plantings.values_list('cell_id', 'pk')),
            original_ids,
        )

    def test_allocation_with_germination_cannot_be_removed(self):
        """Clearing allocations cannot orphan an already germinated plant."""
        cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=self.original_planting,
            cell=self.cell,
            quantity=1,
        )
        SpecificPlant.objects.create(cell_planting=cell_planting)

        response = self.client.patch(
            f'/plantings/seedtray/{self.original_planting.pk}/',
            data=json.dumps({'cell_plantings': []}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(SeedTrayCellPlanting.objects.filter(pk=cell_planting.pk).exists())

    def test_specific_plant_creation_rejects_deleted_validated_allocation(self):
        """A deleted allocation is reported when it disappears after validation."""
        cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=self.original_planting,
            cell=self.cell,
            quantity=1,
        )
        stale_id = cell_planting.pk
        serializer = SpecificPlantSerializer(data={
            'cell_planting': stale_id,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        SeedTrayCellPlanting.objects.filter(pk=stale_id).delete()

        with self.assertRaises(serializers.ValidationError) as context:
            serializer.save()

        self.assertEqual(
            context.exception.detail,
            {'cell_planting': ['This cell allocation no longer exists.']},
        )
        self.assertFalse(
            SpecificPlant.objects.filter(cell_planting_id=stale_id).exists()
        )

    def test_parent_delete_with_germination_returns_domain_error(self):
        """Protected germination data produces a client error rather than a 500."""
        cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=self.original_planting,
            cell=self.cell,
            quantity=1,
        )
        plant = SpecificPlant.objects.create(cell_planting=cell_planting)

        response = self.client.delete(
            f'/plantings/seedtray/{self.original_planting.pk}/',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                'detail': [
                    'Cannot delete a seed tray planting while dependent records exist.'
                ],
            },
        )
        self.assertTrue(
            SeedTrayPlanting.objects.filter(pk=self.original_planting.pk).exists()
        )
        self.assertTrue(SpecificPlant.objects.filter(pk=plant.pk).exists())

    def test_filtered_parent_delete_with_germination_returns_domain_error(self):
        """The tray-filtered endpoint also reports protected germination data."""
        cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=self.original_planting,
            cell=self.cell,
            quantity=1,
        )
        plant = SpecificPlant.objects.create(cell_planting=cell_planting)

        response = self.client.delete(
            f'/plantings/seedtray-data/{self.tray.pk}/plantings/'
            f'{self.original_planting.pk}/',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                'detail': [
                    'Cannot delete a seed tray planting while dependent records exist.'
                ],
            },
        )
        self.assertTrue(
            SeedTrayPlanting.objects.filter(pk=self.original_planting.pk).exists()
        )
        self.assertTrue(SpecificPlant.objects.filter(pk=plant.pk).exists())

    def test_database_rejects_non_positive_quantities(self):
        """Direct writes cannot bypass the minimum-one quantity invariant."""
        planting_rows = [
            GardenRowDirectSowPlanting.objects.create(
                seeds_used=self.packet,
                quantity=1,
                location=self.row,
            ),
            GardenSquareDirectSowPlanting.objects.create(
                seeds_used=self.packet,
                quantity=1,
                location=self.square,
            ),
            self.original_planting,
            SeedTrayCellPlanting.objects.create(
                seed_tray_planting=self.original_planting,
                cell=self.cell,
                quantity=1,
            ),
            GardenSquareTransplant.objects.create(
                original_planting=self.original_planting,
                quantity=1,
                location=self.square,
            ),
        ]

        for planting in planting_rows:
            with self.subTest(model=type(planting).__name__):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        type(planting).objects.filter(pk=planting.pk).update(quantity=0)
                planting.refresh_from_db()
                self.assertEqual(planting.quantity, 1)


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentGerminationCapacityTests(TransactionTestCase):
    """Real row locks serialize simultaneous final-capacity requests."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='concurrency-tester')
        family = PlantFamily.objects.create(name='Lamiaceae')
        plant = Plant.objects.create(family=family, name='Basil')
        variety = PlantVariety.objects.create(plant=plant, name='Genovese')
        packet = SeedPacket.objects.create(
            seeds=Seeds.objects.create(
                supplier=Supplier.objects.create(name='Concurrency Supplier'),
                plant_variety=variety,
            ),
        )
        tray_model = SeedTrayModel.objects.create(
            identifier='concurrency-tray',
            height=10,
            x_size=20,
            y_size=30,
            x_cells=1,
            y_cells=1,
            cell_size_ml=40,
        )
        tray = SeedTray.objects.create(model=tray_model)
        cell = SeedTrayCell.objects.create(
            tray=tray,
            x_position=0,
            y_position=0,
        )
        planting = SeedTrayPlanting.objects.create(
            seeds_used=packet,
            quantity=1,
            seed_tray=tray,
        )
        self.cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=planting,
            cell=cell,
            quantity=1,
        )

    def _record_germination(self):
        close_old_connections()
        client = APIClient()
        client.force_authenticate(user=self.user)
        response = client.post(
            '/plantings/specificplants/',
            {'cell_planting': self.cell_planting.pk},
            format='json',
        )
        close_old_connections()
        return response.status_code

    def test_only_one_simultaneous_final_capacity_request_succeeds(self):
        """The allocation lock prevents concurrent over-germination."""
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(
                lambda _index: self._record_germination(),
                range(2),
            ))

        self.assertEqual(sorted(statuses), [201, 400])
        self.assertEqual(self.cell_planting.specific_plants.count(), 1)
