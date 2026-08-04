"""Smoke tests for planting and specific-plant REST resources."""
from tests.api import RESTContractTestCase
from tests.factories import (
    make_batch_for_packet,
    make_garden_row,
    make_garden_square,
    make_seed_packet,
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_cell_planting,
    make_seed_tray_planting,
    make_specific_plant,
    make_specific_plant_location,
)

from .models import GardenSquareTransplant, SpecificPlantLocation


class PlantingRESTContractTests(RESTContractTestCase):
    """Exercise the common contract for every planting REST registration."""
    # pylint: disable=too-many-instance-attributes

    def setUp(self):
        super().setUp()
        self.packet = make_seed_packet()
        self.row = make_garden_row()
        self.square = make_garden_square()
        self.tray = make_seed_tray()
        self.cell = make_seed_tray_cell(tray=self.tray)
        self.tray_planting = make_seed_tray_planting(
            seeds_used=self.packet,
            quantity=4,
            seed_tray=self.tray,
        )
        self.cell_planting = make_seed_tray_cell_planting(
            seed_tray_planting=self.tray_planting,
            cell=self.cell,
            quantity=4,
        )
        self.specific_plant = make_specific_plant(
            cell_planting=self.cell_planting,
        )
        self.location = make_specific_plant_location(
            specific_plant=self.specific_plant,
        )

    @property
    def list_urls(self):
        """Return every planting collection registered with the REST router."""
        return (
            '/plantings/directsowgardenrow/',
            '/plantings/directsowgardensquare/',
            '/plantings/seedtray/',
            '/plantings/transplantedgardensquare/',
            '/plantings/specificplants/',
            '/plantings/specificplantlocations/',
            '/plantings/harvests/',
            f'/plantings/seedtray-data/{self.tray.pk}/plantings/',
            f'/plantings/seedtray-data/{self.tray.pk}/specificplants/',
            f'/plantings/specificplants/{self.specific_plant.pk}/locations/',
        )

    def test_list_routes_require_authentication(self):
        """Anonymous requests cannot list planting resources."""
        self.assert_authentication_required(self.list_urls)

    def test_list_routes_return_lists(self):
        """Authenticated planting collections use the common list contract."""
        self.assert_list_contract(self.list_urls)

    def test_writable_planting_resources_round_trip(self):
        """Each current aggregate planting resource survives create and retrieve."""
        common_fields = {
            'seeds_used': self.packet.pk,
            'batch': make_batch_for_packet(self.packet).pk,
            'quantity': 3,
            'removed': False,
        }
        self.assert_create_retrieve(
            '/plantings/directsowgardenrow/',
            {
                **common_fields,
                'location': self.row.pk,
                'notes': 'Sown along the row',
            },
        )
        self.assert_create_retrieve(
            '/plantings/directsowgardensquare/',
            {
                **common_fields,
                'location': self.square.pk,
                'notes': 'Sown in a square',
            },
        )
        tray_planting = self.assert_create_retrieve(
            '/plantings/seedtray/',
            {
                **common_fields,
                'seed_tray': self.tray.pk,
                'location': 'Greenhouse bench',
                'notes': 'Started under cover',
                'cell_plantings': [{
                    'cell': self.cell.pk,
                    'quantity': 3,
                }],
            },
            {
                **common_fields,
                'seed_tray': self.tray.pk,
                'location': 'Greenhouse bench',
                'notes': 'Started under cover',
            },
        )
        self.assertEqual(
            tray_planting['cell_plantings'],
            [{
                'pk': tray_planting['cell_plantings'][0]['pk'],
                'cell': self.cell.pk,
                'quantity': 3,
            }],
        )

    def test_legacy_transplants_are_read_only(self):
        """The REST API exposes legacy transplants without accepting mutations."""
        transplant = GardenSquareTransplant.objects.create(
            original_planting=self.tray_planting,
            quantity=2,
            location=self.square,
            notes='Transplanted outside',
        )

        response = self.client.get(
            f'/plantings/transplantedgardensquare/{transplant.pk}/'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['pk'], transplant.pk)

        collection_url = '/plantings/transplantedgardensquare/'
        mutation_cases = (
            ('post', collection_url, {
                'original_planting': self.tray_planting.pk,
                'quantity': 1,
                'location': self.square.pk,
            }),
            ('put', f'{collection_url}{transplant.pk}/', {
                'original_planting': self.tray_planting.pk,
                'quantity': 1,
                'location': self.square.pk,
            }),
            ('patch', f'{collection_url}{transplant.pk}/', {'notes': 'Changed'}),
            ('delete', f'{collection_url}{transplant.pk}/', None),
        )
        for method, url, data in mutation_cases:
            with self.subTest(method=method):
                response = getattr(self.client, method)(url, data, format='json')
                self.assertEqual(response.status_code, 405)

        transplant.refresh_from_db()
        self.assertEqual(transplant.quantity, 2)
        self.assertEqual(transplant.notes, 'Transplanted outside')

    def test_specific_plant_and_location_resources_round_trip(self):
        """Specific plants and location history survive create and retrieve."""
        plant = self.assert_create_retrieve(
            '/plantings/specificplants/',
            {
                'cell_planting': self.cell_planting.pk,
                'germinated': '2026-04-01T08:00:00Z',
                'notes': 'Second seedling',
            },
            {
                'cell_planting': self.cell_planting.pk,
                'notes': 'Second seedling',
            },
        )
        self.assertEqual(len(plant['locations']), 1)
        self.assertEqual(
            plant['locations'][0]['location_type'],
            SpecificPlantLocation.SEED_TRAY_CELL,
        )
        self.assertEqual(
            plant['locations'][0]['seed_tray_cell'],
            self.cell.pk,
        )

        unlocated_plant = make_specific_plant()
        self.assert_create_retrieve(
            '/plantings/specificplantlocations/',
            {
                'specific_plant': unlocated_plant.pk,
                'location_type': SpecificPlantLocation.GARDEN_SQUARE,
                'seed_tray_cell': None,
                'garden_square': self.square.pk,
                'started': '2026-04-02T09:30:00Z',
                'ended': None,
                'notes': 'Moved outside',
            },
        )

    def test_filtered_routes_only_expose_parent_resources(self):
        """Filtered collection and detail routes enforce their URL parent."""
        other_tray = make_seed_tray()
        other_cell = make_seed_tray_cell(tray=other_tray)
        other_planting = make_seed_tray_planting(
            seed_tray=other_tray,
        )
        other_cell_planting = make_seed_tray_cell_planting(
            seed_tray_planting=other_planting,
            cell=other_cell,
        )
        other_plant = make_specific_plant(
            cell_planting=other_cell_planting,
        )
        other_location = make_specific_plant_location(
            specific_plant=other_plant,
        )

        route_cases = (
            (
                f'/plantings/seedtray-data/{self.tray.pk}/plantings/',
                self.tray_planting.pk,
                other_planting.pk,
            ),
            (
                f'/plantings/seedtray-data/{self.tray.pk}/specificplants/',
                self.specific_plant.pk,
                other_plant.pk,
            ),
            (
                f'/plantings/specificplants/{self.specific_plant.pk}/locations/',
                self.location.pk,
                other_location.pk,
            ),
        )
        for url, expected_pk, excluded_pk in route_cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    {resource['pk'] for resource in response.data},
                    {expected_pk},
                )
                response = self.client.get(f'{url}{expected_pk}/')
                self.assertEqual(response.status_code, 200)
                response = self.client.get(f'{url}{excluded_pk}/')
                self.assertEqual(response.status_code, 404)
